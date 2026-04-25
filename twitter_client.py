import os
import tweepy
import logging

from database import set_stream_status

logger = logging.getLogger(__name__)

os.environ.setdefault("X_CONSUMER_KEY", "ktEoKBEXfP1lbbX28me3G8A6S")
os.environ.setdefault("X_CONSUMER_SECRET", "rE0rF3rytVXHOWcFTykhbd7AlZuBurtx1jukv3ZA1Ea9893YnJ")
os.environ.setdefault("X_BEARER_TOKEN", "AAAAAAAAAAAAAAAAAAAAABn29AEAAAAAKGQvXZeBG%2BACbPmzIavXy0nHv0s%3DeGCjisYzRkKvYmr7a4gspr7BLjTeKymEzJLWzdubFpo06NEbFg")
os.environ.setdefault("X_ACCESS_TOKEN", "997357889927888898-K4DUwj3Wgpu1IoP8F3GA5OQrdQfVYNj")
os.environ.setdefault("X_ACCESS_TOKEN_SECRET", "MS4UTY7G9S8QW7KgLTdELDAfZabGBR7dZxxs9ecppZCP8")
os.environ.setdefault("X_CLIENT_ID", "RzVrN2tVbmtaYnBvV3J5TzZvUmY6MTpjaQ")
os.environ.setdefault("X_CLIENT_SECRET", "63hVNoMiHwRNbbI8-uwIJikLSX138Nuo_iptr1Ix9mAzGD4kXd")

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


def _media_kwargs(media_path: str | None) -> dict:
    """Return media_ids kwarg dict if media_path is given and upload succeeds."""
    if not media_path:
        return {}
    media_id = upload_media(media_path)
    return {"media_ids": [media_id]} if media_id else {}


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


def post_reply(reply_text: str, in_reply_to_tweet_id: str, media_path: str | None = None) -> str | None:
    client = get_v2_client()
    try:
        response = client.create_tweet(
            text=reply_text,
            in_reply_to_tweet_id=in_reply_to_tweet_id,
            **_media_kwargs(media_path),
        )
        tweet_id = response.data["id"]
        logger.info(f"Posted reply {tweet_id} (media={'yes' if media_path else 'no'}): {reply_text[:60]}...")
        return tweet_id
    except tweepy.TweepyException as e:
        logger.error(f"Failed to post reply: {e}")
        return None


def post_tweet(text: str, media_path: str | None = None) -> str | None:
    client = get_v2_client()
    try:
        response = client.create_tweet(text=text, **_media_kwargs(media_path))
        tweet_id = response.data["id"]
        logger.info(f"Posted original tweet {tweet_id} (media={'yes' if media_path else 'no'}): {text[:60]}...")
        return tweet_id
    except tweepy.TweepyException as e:
        logger.error(f"Failed to post tweet: {e}")
        return None


def post_quote_tweet(text: str, quote_tweet_id: str, media_path: str | None = None) -> str | None:
    client = get_v2_client()
    try:
        response = client.create_tweet(text=text, quote_tweet_id=quote_tweet_id, **_media_kwargs(media_path))
        tweet_id = response.data["id"]
        logger.info(f"Posted quote tweet {tweet_id} (media={'yes' if media_path else 'no'}): {text[:60]}...")
        return tweet_id
    except tweepy.TweepyException as e:
        logger.error(f"Failed to post quote tweet: {e}")
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


def fetch_list_tweets(list_id: str, since_id: str | None = None) -> list[dict]:
    client = get_v2_client()
    kwargs = {
        "id": list_id,
        "max_results": 100,
        "tweet_fields": ["author_id", "created_at", "text", "referenced_tweets", "attachments"],
        "expansions": ["author_id", "referenced_tweets.id", "attachments.media_keys"],
        "user_fields": ["username"],
        "media_fields": ["url", "type", "preview_image_url"],
    }
    if since_id:
        kwargs["since_id"] = since_id

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
            })
        return tweets
    except tweepy.TweepyException as e:
        logger.error(f"Failed to fetch list tweets: {e}")
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
            logger.error(f"Failed to fetch tweet {current_id}: {e}")
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

    def on_tweet(self, tweet):
        in_reply_to_tweet_id = None
        for ref in (getattr(tweet, "referenced_tweets", None) or []):
            if hasattr(ref, "type") and ref.type == "replied_to":
                in_reply_to_tweet_id = str(ref.id)
                break
        self._on_tweet({
            "id": str(tweet.id),
            "text": tweet.text,
            "author_id": str(tweet.author_id) if tweet.author_id else "",
            "author_handle": "stream_user",
            "in_reply_to_tweet_id": in_reply_to_tweet_id,
        })

    def on_errors(self, errors):
        logger.error(f"Stream error: {errors}")
        set_stream_status("disconnected")

    def on_connection_error(self):
        logger.error("Stream connection error")
        set_stream_status("disconnected")
