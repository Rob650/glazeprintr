import os
import re
import tweepy
import logging
from datetime import datetime, timezone, timedelta

from database import set_stream_status

logger = logging.getLogger(__name__)

_REQUIRED_ENV_VARS = [
    "X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_BEARER_TOKEN",
    "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
]
_missing = [v for v in _REQUIRED_ENV_VARS if not os.environ.get(v)]
if _missing:
    logging.getLogger(__name__).error(
        "Missing required Twitter API env vars: %s — Twitter client will not work",
        ", ".join(_missing),
    )

_HTTPS_RE = re.compile(r'https?://\S+', re.IGNORECASE)
_PUMP_FUN_RE = re.compile(r'\bpump\.fun\S*', re.IGNORECASE)
_PRINTR_MONEY_RE = re.compile(r'\bapp\.printr\.money\S*', re.IGNORECASE)

BOT_HANDLE = os.environ.get("BOT_HANDLE", "printrglazr")
SKIP_HANDLES = {BOT_HANDLE.lower(), "printr_money"}

_v2_client = None
_bot_user_id: str | None = None
_v1_api = None


def get_v2_client() -> tweepy.Client:
    global _v2_client
    if _v2_client is None:
        _v2_client = tweepy.Client(
            bearer_token=os.environ.get("X_BEARER_TOKEN"),
            consumer_key=os.environ.get("X_CONSUMER_KEY"),
            consumer_secret=os.environ.get("X_CONSUMER_SECRET"),
            access_token=os.environ.get("X_ACCESS_TOKEN"),
            access_token_secret=os.environ.get("X_ACCESS_TOKEN_SECRET"),
            wait_on_rate_limit=True,
        )
    return _v2_client


def get_v1_api() -> tweepy.API:
    global _v1_api
    if _v1_api is None:
        auth = tweepy.OAuth1UserHandler(
            consumer_key=os.environ.get("X_CONSUMER_KEY"),
            consumer_secret=os.environ.get("X_CONSUMER_SECRET"),
            access_token=os.environ.get("X_ACCESS_TOKEN"),
            access_token_secret=os.environ.get("X_ACCESS_TOKEN_SECRET"),
        )
        _v1_api = tweepy.API(auth)
    return _v1_api


def upload_media(image_path: str) -> str | None:
    """Upload image via v1.1 API. Returns media_id string or None on failure."""
    try:
        api = get_v1_api()
        media = api.media_upload(filename=image_path)
        logger.info(f"Uploaded media {image_path} → id={media.media_id}")
        return str(media.media_id)
    except Exception as e:
        logger.error(f"Media upload failed for {image_path}: {e}")
        return None


def _media_kwargs(media_path: str | None) -> tuple[dict, bool]:
    """Return (media_ids kwarg dict, upload_succeeded). Dict is empty if no path or upload failed."""
    if not media_path:
        return {}, False
    media_id = upload_media(media_path)
    if media_id:
        return {"media_ids": [media_id]}, True
    return {}, False


def get_bot_user_id() -> str | None:
    global _bot_user_id
    if _bot_user_id is not None:
        return _bot_user_id
    client = get_v2_client()
    try:
        me = client.get_me()
        if me.data:
            _bot_user_id = str(me.data.id)
            logger.info(f"Bot user ID: {_bot_user_id}")
            return _bot_user_id
    except tweepy.TweepyException as e:
        logger.error(f"Failed to get bot user ID: {e}")
    return None


def fetch_mentions(since_id: str | None = None) -> list[dict]:
    client = get_v2_client()
    user_id = get_bot_user_id()
    if not user_id:
        logger.error("Cannot fetch mentions: bot user ID unknown")
        return []

    kwargs = {
        "id": user_id,
        "max_results": 100,
        "tweet_fields": ["author_id", "created_at", "text", "referenced_tweets", "attachments"],
        "expansions": ["author_id", "referenced_tweets.id", "attachments.media_keys"],
        "user_fields": ["username"],
        "media_fields": ["url", "type", "preview_image_url"],
    }
    if since_id:
        kwargs["since_id"] = since_id
    else:
        # On cold start (no since_id), fetch last 2 hours so we catch up after downtime
        kwargs["start_time"] = datetime.now(timezone.utc) - timedelta(hours=2)

    try:
        response = client.get_users_mentions(**kwargs)
        if not response.data:
            return []

        users = {}
        if response.includes and "users" in response.includes:
            for u in response.includes["users"]:
                users[u.id] = u.username

        media_map = _build_media_map(response)

        tweets = []
        for tweet in response.data:
            in_reply_to_tweet_id = None
            for ref in (getattr(tweet, "referenced_tweets", None) or []):
                if ref.type == "replied_to":
                    in_reply_to_tweet_id = str(ref.id)
                    break
            tweets.append({
                "id": str(tweet.id),
                "text": tweet.text,
                "author_id": str(tweet.author_id),
                "author_handle": users.get(tweet.author_id, "unknown"),
                "in_reply_to_tweet_id": in_reply_to_tweet_id,
                "created_at": tweet.created_at,
                "media_url": _extract_media_url(tweet, media_map),
            })
        return tweets
    except tweepy.TweepyException as e:
        logger.error(f"Failed to fetch mentions: {e}")
        return []


def _clean_tweet(text: str) -> str:
    """Strip URLs, normalize whitespace, ensure ends with \\n\\n🙏, truncate to 280 chars."""
    text = _HTTPS_RE.sub('', text)
    text = _PUMP_FUN_RE.sub('pumpfun', text)
    text = _PRINTR_MONEY_RE.sub('Printr', text)
    text = re.sub(r'[ \t]+', ' ', text).strip()
    text = re.sub(r'\n{3,}', '\n\n', text)
    if not text.rstrip().endswith('🙏'):
        text = text.rstrip() + '\n\n🙏'
    return text[:280]


def _log_post_error(action: str, e: tweepy.TweepyException) -> None:
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    if status == 402:
        logger.error(
            f"{action}: 402 Payment Required — ACTION REQUIRED: check Twitter Developer Portal "
            f"(developer.twitter.com). Possible causes: (1) API subscription plan expired or "
            f"insufficient tier, (2) monthly write quota exhausted, (3) OAuth tokens generated "
            f"before write permissions were enabled (regenerate tokens after enabling Read+Write)."
        )
        return
    if status == 403:
        try:
            body = resp.json() if resp else None
        except Exception:
            body = getattr(resp, "text", None)
        codes = getattr(e, "api_codes", [])
        msgs = getattr(e, "api_messages", [])
        logger.error(
            f"{action}: 403 Forbidden — ACTION REQUIRED: check Twitter Developer Portal. "
            f"Raw response: {body} | api_codes={codes} | api_messages={msgs} | exception={e}"
        )
        return
    codes = getattr(e, "api_codes", [])
    msgs = getattr(e, "api_messages", [])
    if codes or msgs:
        logger.error(f"{action}: {e} | twitter_codes={codes} | twitter_messages={msgs}")
    else:
        logger.error(f"{action}: {e}")


def post_reply(reply_text: str, in_reply_to_tweet_id: str, media_path: str | None = None) -> str | None:
    client = get_v2_client()
    clean = _clean_tweet(reply_text)
    media_kwargs, media_ok = _media_kwargs(media_path)
    try:
        response = client.create_tweet(
            text=clean,
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            **media_kwargs,
        )
        tweet_id = response.data["id"]
        media_status = "uploaded" if media_ok else ("upload_failed" if media_path else "no_media")
        logger.info(f"Posted reply {tweet_id} (media={media_status}): {clean[:60]}...")
        return tweet_id
    except tweepy.TweepyException as e:
        _log_post_error("Failed to post reply", e)
        return None


def post_tweet(text: str, media_path: str | None = None) -> str | None:
    client = get_v2_client()
    clean = _clean_tweet(text)
    media_kwargs, media_ok = _media_kwargs(media_path)
    try:
        response = client.create_tweet(text=clean, **media_kwargs)
        tweet_id = response.data["id"]
        media_status = "uploaded" if media_ok else ("upload_failed" if media_path else "no_media")
        logger.info(f"Posted original tweet {tweet_id} (media={media_status}): {clean[:60]}...")
        return tweet_id
    except tweepy.TweepyException as e:
        _log_post_error("Failed to post tweet", e)
        return None


QUOTE_TWEET_FORBIDDEN = "QUOTE_TWEET_FORBIDDEN"


def post_quote_tweet(text: str, quote_tweet_id: str, media_path: str | None = None) -> str | None:
    client = get_v2_client()
    clean = _clean_tweet(text)
    media_kwargs, media_ok = _media_kwargs(media_path)
    try:
        response = client.create_tweet(text=clean, quote_tweet_id=quote_tweet_id, **media_kwargs)
        tweet_id = response.data["id"]
        media_status = "uploaded" if media_ok else ("upload_failed" if media_path else "no_media")
        logger.info(f"Posted quote tweet {tweet_id} (media={media_status}): {clean[:60]}...")
        return tweet_id
    except tweepy.TweepyException as e:
        logger.error(f"post_quote_tweet failed quoting tweet_id={quote_tweet_id}")
        _log_post_error("Failed to post quote tweet", e)
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        if status == 403:
            try:
                body = resp.json() if resp else {}
            except Exception:
                body = {}
            # Detect specific "quote not allowed" restriction on the target tweet
            error_text = str(e) + str(body)
            if "Quoting this post is not allowed" in error_text:
                return QUOTE_TWEET_FORBIDDEN
        return None


def _build_media_map(response) -> dict:
    """Build {media_key: url} from a tweepy response's includes."""
    media_map = {}
    if not (response.includes and "media" in response.includes):
        return media_map
    for m in response.includes["media"]:
        url = None
        if m.type == "photo" and getattr(m, "url", None):
            url = m.url
        elif getattr(m, "preview_image_url", None):
            url = m.preview_image_url
        if url:
            media_map[m.media_key] = url
    return media_map


def _extract_media_url(tweet, media_map: dict) -> str | None:
    """Return the first media URL for a tweet given the media_map."""
    attachments = getattr(tweet, "attachments", None)
    if not attachments:
        return None
    for mk in (attachments.get("media_keys") or []):
        if mk in media_map:
            return media_map[mk]
    return None


def fetch_tweet_media_url(tweet_id: str) -> str | None:
    """Fetch the first photo/preview URL from a tweet. Returns None if no media."""
    client = get_v2_client()
    try:
        resp = client.get_tweet(
            id=tweet_id,
            expansions=["attachments.media_keys"],
            media_fields=["url", "type", "preview_image_url"],
        )
        if not resp.data:
            return None
        return _extract_media_url(resp.data, _build_media_map(resp))
    except tweepy.TweepyException as e:
        logger.warning(f"Failed to fetch media for tweet {tweet_id}: {e}")
    return None


def fetch_list_tweets(list_id: str) -> list[dict]:
    client = get_v2_client()
    kwargs = {
        "id": list_id,
        "max_results": 100,
        "tweet_fields": ["author_id", "created_at", "text", "referenced_tweets", "attachments", "reply_settings"],
        "expansions": ["author_id", "referenced_tweets.id", "attachments.media_keys"],
        "user_fields": ["username"],
        "media_fields": ["url", "type", "preview_image_url"],
    }

    try:
        response = client.get_list_tweets(**kwargs)
        if not response.data:
            return []

        users = {}
        if response.includes and "users" in response.includes:
            for u in response.includes["users"]:
                users[u.id] = u.username

        media_map = _build_media_map(response)

        tweets = []
        for tweet in response.data:
            in_reply_to_tweet_id = None
            for ref in (getattr(tweet, "referenced_tweets", None) or []):
                if ref.type == "replied_to":
                    in_reply_to_tweet_id = str(ref.id)
                    break
            tweets.append({
                "id": str(tweet.id),
                "text": tweet.text,
                "author_id": str(tweet.author_id),
                "author_handle": users.get(tweet.author_id, "unknown"),
                "in_reply_to_tweet_id": in_reply_to_tweet_id,
                "created_at": tweet.created_at,
                "media_url": _extract_media_url(tweet, media_map),
                "reply_settings": getattr(tweet, "reply_settings", "everyone") or "everyone",
            })
        return tweets
    except tweepy.TweepyException as e:
        logger.error(f"Failed to fetch list tweets: {e}")
        return []


def search_keyword_tweets(
    query: str,
    since_id: str | None = None,
    start_time: datetime | None = None,
) -> list[dict]:
    """Search recent tweets matching query. Returns normalized tweet dicts."""
    client = get_v2_client()
    kwargs: dict = {
        "query": query,
        "max_results": 100,
        "tweet_fields": ["author_id", "created_at", "text", "referenced_tweets", "attachments", "reply_settings"],
        "expansions": ["author_id", "referenced_tweets.id", "attachments.media_keys"],
        "user_fields": ["username"],
        "media_fields": ["url", "type", "preview_image_url"],
    }
    if since_id:
        kwargs["since_id"] = since_id
    if start_time:
        kwargs["start_time"] = start_time

    try:
        response = client.search_recent_tweets(**kwargs)
        if not response.data:
            return []

        users = {}
        if response.includes and "users" in response.includes:
            for u in response.includes["users"]:
                users[u.id] = u.username

        media_map = _build_media_map(response)

        tweets = []
        for tweet in response.data:
            in_reply_to_tweet_id = None
            for ref in (getattr(tweet, "referenced_tweets", None) or []):
                if ref.type == "replied_to":
                    in_reply_to_tweet_id = str(ref.id)
                    break
            tweets.append({
                "id": str(tweet.id),
                "text": tweet.text,
                "author_id": str(tweet.author_id),
                "author_handle": users.get(tweet.author_id, "unknown"),
                "in_reply_to_tweet_id": in_reply_to_tweet_id,
                "created_at": tweet.created_at,
                "media_url": _extract_media_url(tweet, media_map),
                "reply_settings": getattr(tweet, "reply_settings", "everyone") or "everyone",
            })
        return tweets
    except tweepy.TweepyException as e:
        logger.error(f"Failed to search keyword tweets: {e}")
        return []


def fetch_bot_followers(max_results: int = 500) -> list[dict]:
    """Fetch the bot's followers. Returns list of {id, username} dicts."""
    client = get_v2_client()
    user_id = get_bot_user_id()
    if not user_id:
        logger.error("Cannot fetch followers: bot user ID unknown")
        return []
    followers = []
    try:
        for user in tweepy.Paginator(
            client.get_users_followers,
            id=user_id,
            max_results=1000,
            user_fields=["username"],
        ).flatten(limit=max_results):
            followers.append({"id": str(user.id), "username": user.username})
        logger.info(f"Fetched {len(followers)} followers")
        return followers
    except tweepy.TweepyException as e:
        logger.error(f"Failed to fetch followers: {e}")
        return []


def fetch_user_tweets(username: str, max_results: int = 20) -> list[dict]:
    """Fetch recent tweets from a user by username. Returns list of tweet dicts."""
    client = get_v2_client()
    try:
        user_resp = client.get_user(username=username, user_fields=["id", "name"])
        if not user_resp.data:
            logger.warning(f"User @{username} not found")
            return []
        user_id = user_resp.data.id

        resp = client.get_users_tweets(
            id=user_id,
            max_results=max_results,
            tweet_fields=["created_at", "text", "public_metrics"],
            exclude=["retweets", "replies"],
        )
        if not resp.data:
            return []

        tweets = []
        for t in resp.data:
            metrics = getattr(t, "public_metrics", {}) or {}
            tweets.append({
                "id": str(t.id),
                "text": t.text,
                "author_handle": username,
                "created_at": str(t.created_at) if t.created_at else "",
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
            })
        logger.info(f"Fetched {len(tweets)} tweets from @{username}")
        return tweets
    except tweepy.TweepyException as e:
        logger.error(f"Failed to fetch tweets for @{username}: {e}")
        return []


def fetch_tweet_chain(tweet_id: str, max_depth: int = 5) -> list[dict]:
    """Fetch the chain of parent tweets up to max_depth levels, returning oldest first."""
    client = get_v2_client()
    chain = []
    current_id = tweet_id
    seen = set()

    for _ in range(max_depth):
        if current_id in seen:
            break
        seen.add(current_id)

        try:
            response = client.get_tweet(
                id=current_id,
                tweet_fields=["author_id", "text", "referenced_tweets"],
                expansions=["author_id", "referenced_tweets.id"],
                user_fields=["username"],
            )
            if not response.data:
                break

            tweet = response.data
            users = {}
            if response.includes and "users" in response.includes:
                for u in response.includes["users"]:
                    users[u.id] = u.username

            chain.append({
                "id": str(tweet.id),
                "text": tweet.text,
                "author_id": str(tweet.author_id) if tweet.author_id else "",
                "author_handle": users.get(tweet.author_id, "unknown"),
            })

            parent_ref = next(
                (r for r in (getattr(tweet, "referenced_tweets", None) or [])
                 if r.type == "replied_to"),
                None,
            )
            if parent_ref:
                current_id = str(parent_ref.id)
            else:
                break

        except tweepy.TweepyException as e:
            _log_post_error(f"Failed to fetch tweet {current_id}", e)
            break

    return list(reversed(chain))


def setup_stream_rules(keywords: list[str]):
    streaming_client = tweepy.StreamingClient(bearer_token=os.environ.get("X_BEARER_TOKEN"))
    try:
        existing = streaming_client.get_rules()
        if existing.data:
            ids = [r.id for r in existing.data]
            streaming_client.delete_rules(ids)
            logger.info(f"Deleted {len(ids)} existing stream rules")
    except tweepy.TweepyException as e:
        logger.error(f"Error clearing stream rules: {e}")

    rule_parts = [f'"{kw}"' if " " in kw else kw for kw in keywords]
    rule = " OR ".join(rule_parts)
    rule += f" -is:retweet -from:{BOT_HANDLE}"

    try:
        streaming_client.add_rules(tweepy.StreamRule(rule))
        logger.info(f"Added stream rule: {rule[:120]}...")
    except tweepy.TweepyException as e:
        logger.error(f"Failed to add stream rules: {e}")


class GlazePrintrStream(tweepy.StreamingClient):
    def __init__(self, on_tweet_callback, **kwargs):
        super().__init__(
            bearer_token=os.environ.get("X_BEARER_TOKEN"),
            **kwargs
        )
        self._on_tweet = on_tweet_callback

    def on_response(self, response):
        if response.errors:
            self.on_errors(response.errors)

        tweet = response.data
        if tweet is None:
            return

        # Skip tweets with restricted reply settings — we'd get a 403 anyway.
        reply_settings = getattr(tweet, "reply_settings", "everyone")
        if reply_settings and reply_settings != "everyone":
            logger.debug(f"Skipping tweet {tweet.id} — reply_settings={reply_settings}")
            return

        users = {}
        if response.includes and "users" in response.includes:
            for u in response.includes["users"]:
                users[u.id] = u.username

        in_reply_to_tweet_id = None
        for ref in (getattr(tweet, "referenced_tweets", None) or []):
            if hasattr(ref, "type") and ref.type == "replied_to":
                in_reply_to_tweet_id = str(ref.id)
                break

        author_handle = users.get(tweet.author_id, "unknown") if tweet.author_id else "unknown"

        self._on_tweet({
            "id": str(tweet.id),
            "text": tweet.text,
            "author_id": str(tweet.author_id) if tweet.author_id else "",
            "author_handle": author_handle,
            "in_reply_to_tweet_id": in_reply_to_tweet_id,
            "created_at": getattr(tweet, "created_at", None),
        })

    def on_errors(self, errors):
        logger.error(f"Stream error: {errors}")
        set_stream_status("disconnected")

    def on_connection_error(self):
        logger.error("Stream connection error")
        set_stream_status("disconnected")
