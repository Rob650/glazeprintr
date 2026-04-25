import os
import asyncio
import logging
import threading
from datetime import datetime, timezone

from database import (
    has_replied, record_reply, count_replies_today,
    count_replies_to_account_last_hour, is_paused,
    set_stream_status
)
from claude_client import generate_reply, select_mode
from twitter_client import (
    fetch_list_tweets, post_reply, setup_stream_rules, GlazePrintrStream
)

logger = logging.getLogger(__name__)

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
X_LIST_ID = os.environ.get("X_LIST_ID", "")
MAX_REPLIES_PER_DAY = int(os.environ.get("MAX_REPLIES_PER_DAY", "50"))
MAX_REPLIES_PER_ACCOUNT_HOUR = int(os.environ.get("MAX_REPLIES_PER_ACCOUNT_HOUR", "5"))

STREAM_KEYWORDS = [
    "$belief", "$ooo", "$rotus", "$fatchoi", "$deployr",
    "$patapim", "$roi", "$noob", "$print", "$cmyk", "$pve",
    "$ket", "$fsjal", "$marmot",
    "pump.fun", "pumpfun",
    "printr",
]

_list_poll_since_id: str | None = None
_stream_thread: threading.Thread | None = None
_stream_instance: GlazePrintrStream | None = None


def _should_skip(tweet: dict) -> tuple[bool, str]:
    handle = tweet.get("author_handle", "").lower().lstrip("@")
    if handle in {"printrglazr", "printr_money"}:
        return True, f"skip handle @{handle}"
    if has_replied(tweet["id"]):
        return True, "already replied"
    if count_replies_today() >= MAX_REPLIES_PER_DAY:
        return True, f"daily limit {MAX_REPLIES_PER_DAY} reached"
    if tweet.get("author_id") and count_replies_to_account_last_hour(tweet["author_id"]) >= MAX_REPLIES_PER_ACCOUNT_HOUR:
        return True, f"hourly limit {MAX_REPLIES_PER_ACCOUNT_HOUR} for account reached"
    return False, ""


def process_tweet(tweet: dict):
    if is_paused():
        logger.info("Bot paused — skipping tweet")
        return

    skip, reason = _should_skip(tweet)
    if skip:
        logger.debug(f"Skipping {tweet['id']}: {reason}")
        return

    tweet_text = tweet.get("text", "")
    author_handle = tweet.get("author_handle", "unknown")
    author_id = tweet.get("author_id", "")

    try:
        reply_text, mode = generate_reply(tweet_text, author_handle)
    except Exception as e:
        logger.error(f"Claude error for tweet {tweet['id']}: {e}")
        return

    if DRY_RUN:
        logger.info(
            f"[DRY RUN] Would reply to @{author_handle} ({tweet['id']}) "
            f"in mode={mode}: {reply_text[:80]}..."
        )
    else:
        result = post_reply(reply_text, tweet["id"])
        if not result:
            logger.warning(f"Failed to post reply to {tweet['id']}")
            return

    record_reply(
        tweet_id=tweet["id"],
        author_id=author_id,
        author_handle=author_handle,
        tweet_text=tweet_text[:500],
        reply_text=reply_text,
        mode=mode,
        dry_run=DRY_RUN,
    )
    logger.info(
        f"{'[DRY] ' if DRY_RUN else ''}Replied to @{author_handle} "
        f"mode={mode}: {reply_text[:60]}..."
    )


def poll_list():
    global _list_poll_since_id
    if not X_LIST_ID:
        logger.warning("X_LIST_ID not set — list polling disabled")
        return

    logger.info(f"Polling list {X_LIST_ID} since_id={_list_poll_since_id}")
    tweets = fetch_list_tweets(X_LIST_ID, since_id=_list_poll_since_id)

    if tweets:
        _list_poll_since_id = tweets[0]["id"]
        for tweet in tweets:
            process_tweet(tweet)


def _stream_callback(tweet: dict):
    process_tweet(tweet)


def start_stream():
    global _stream_instance, _stream_thread

    if _stream_thread and _stream_thread.is_alive():
        logger.info("Stream already running")
        return

    setup_stream_rules(STREAM_KEYWORDS)

    _stream_instance = GlazePrintrStream(
        on_tweet_callback=_stream_callback,
        wait_on_rate_limit=True,
    )

    def _run():
        set_stream_status("connected")
        logger.info("Filtered stream started")
        try:
            _stream_instance.filter(
                tweet_fields=["author_id", "text"],
                expansions=["author_id"],
                user_fields=["username"],
                threaded=False,
            )
        except Exception as e:
            logger.error(f"Stream crashed: {e}")
        finally:
            set_stream_status("disconnected")
            logger.info("Stream stopped")

    _stream_thread = threading.Thread(target=_run, daemon=True, name="glazeprintr-stream")
    _stream_thread.start()


def stop_stream():
    global _stream_instance
    if _stream_instance:
        _stream_instance.disconnect()
        set_stream_status("disconnected")
        logger.info("Stream disconnected")


def stream_is_alive() -> bool:
    return _stream_thread is not None and _stream_thread.is_alive()
