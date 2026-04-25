import os
import tweepy
import aiohttp
import asyncio
import logging

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


def post_reply(reply_text: str, in_reply_to_tweet_id: str) -> str | None:
    client = get_v2_client()
    try:
        response = client.create_tweet(
            text=reply_text,
            in_reply_to_tweet_id=in_reply_to_tweet_id,
        )
        tweet_id = response.data["id"]
        logger.info(f"Posted reply {tweet_id}: {reply_text[:60]}...")
        return tweet_id
    except tweepy.TweepyException as e:
        logger.error(f"Failed to post reply: {e}")
        return None


def fetch_list_tweets(list_id: str, since_id: str | None = None) -> list[dict]:
    client = get_v2_client()
    kwargs = {
        "id": list_id,
        "max_results": 100,
        "tweet_fields": ["author_id", "created_at", "text"],
        "expansions": ["author_id"],
        "user_fields": ["username"],
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

        tweets = []
        for tweet in response.data:
            tweets.append({
                "id": str(tweet.id),
                "text": tweet.text,
                "author_id": str(tweet.author_id),
                "author_handle": users.get(tweet.author_id, "unknown"),
            })
        return tweets
    except tweepy.TweepyException as e:
        logger.error(f"Failed to fetch list tweets: {e}")
        return []


def setup_stream_rules(keywords: list[str]):
    client = get_v2_client()
    try:
        existing = client.get_rules()
        if existing.data:
            ids = [r.id for r in existing.data]
            client.delete_rules(ids)
            logger.info(f"Deleted {len(ids)} existing stream rules")
    except tweepy.TweepyException as e:
        logger.error(f"Error clearing stream rules: {e}")

    rule_parts = [f'"{kw}"' if " " in kw else kw for kw in keywords]
    rule = " OR ".join(rule_parts)
    rule += f" -is:retweet -from:{BOT_HANDLE}"

    try:
        client.add_rules(tweepy.StreamRule(rule))
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
        self._on_tweet({
            "id": str(tweet.id),
            "text": tweet.text,
            "author_id": str(tweet.author_id) if tweet.author_id else "",
            "author_handle": "stream_user",
        })

    def on_errors(self, errors):
        logger.error(f"Stream error: {errors}")

    def on_connection_error(self):
        logger.error("Stream connection error")
