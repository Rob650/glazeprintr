import os
import asyncio
import logging
import threading

from database import (
    has_replied, record_reply, count_replies_today,
    count_replies_to_account_last_hour, is_paused,
    set_stream_status, has_scored, record_score, count_scores_today,
    record_original_tweet, has_replied_mention, record_replied_mention,
    get_mentions_since_id, set_mentions_since_id,
)
from claude_client import (
    generate_reply, select_mode, generate_original_tweet, score_glaze,
    classify_tweet_intent,
)
from twitter_client import (
    fetch_list_tweets, fetch_mentions, post_reply, post_tweet, post_quote_tweet,
    setup_stream_rules, GlazePrintrStream, fetch_tweet_chain,
)
import memory as mem
from scraper import scrape_all_data

logger = logging.getLogger(__name__)

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
X_LIST_ID = os.environ.get("X_LIST_ID", "")
MAX_REPLIES_PER_DAY = int(os.environ.get("MAX_REPLIES_PER_DAY", "50"))
MAX_REPLIES_PER_ACCOUNT_HOUR = int(os.environ.get("MAX_REPLIES_PER_ACCOUNT_HOUR", "5"))
MAX_SCORES_PER_DAY = int(os.environ.get("MAX_SCORES_PER_DAY", "20"))

STREAM_KEYWORDS = [
    "$belief", "$ooo", "$rotus", "$fatchoi", "$deployr",
    "$patapim", "$roi", "$noob", "$print", "$cmyk", "$pve",
    "$ket", "$fsjal", "$marmot",
    "pump.fun", "pumpfun",
    "printr",
]

_list_poll_since_id: str | None = None
_mentions_since_id: str | None = None
_mentions_since_id_loaded: bool = False
_stream_thread: threading.Thread | None = None
_stream_instance: GlazePrintrStream | None = None

SKIP_HANDLES = {"printrglazr", "printr_money"}


def _should_skip_reply(tweet: dict) -> tuple[bool, str]:
    handle = tweet.get("author_handle", "").lower().lstrip("@")
    if handle in SKIP_HANDLES:
        return True, f"skip handle @{handle}"
    if has_replied(tweet["id"]):
        return True, "already replied"
    if count_replies_today() >= MAX_REPLIES_PER_DAY:
        return True, f"daily limit {MAX_REPLIES_PER_DAY} reached"
    if tweet.get("author_id") and count_replies_to_account_last_hour(tweet["author_id"]) >= MAX_REPLIES_PER_ACCOUNT_HOUR:
        return True, "hourly account limit reached"
    return False, ""


def _get_thread_context(tweet: dict) -> list[dict] | None:
    in_reply_to = tweet.get("in_reply_to_tweet_id")
    if not in_reply_to:
        return None
    try:
        chain = fetch_tweet_chain(in_reply_to, max_depth=4)
        if chain:
            return chain + [{
                "id": tweet["id"],
                "text": tweet.get("text", ""),
                "author_handle": tweet.get("author_handle", "unknown"),
            }]
    except Exception as e:
        logger.warning(f"Thread fetch failed for {tweet['id']}: {e}")
    return None


def process_tweet(tweet: dict, thread_context: list[dict] | None = None):
    if is_paused():
        logger.info("Bot paused — skipping tweet")
        return

    tweet_text = tweet.get("text", "")
    author_handle = tweet.get("author_handle", "unknown")
    author_id = tweet.get("author_id", "")

    # Store in memory regardless of reply eligibility
    mem.remember_tweet(tweet["id"], author_handle, tweet_text)

    skip, reason = _should_skip_reply(tweet)
    if skip:
        logger.debug(f"Skipping reply to {tweet['id']}: {reason}")
        return

    if thread_context is None:
        thread_context = _get_thread_context(tweet)
    memory_context = mem.get_memory_context()

    try:
        reply_text, mode = generate_reply(
            tweet_text,
            author_handle,
            thread_context=thread_context,
            memory_context=memory_context,
        )
    except Exception as e:
        logger.error(f"Claude error for tweet {tweet['id']}: {e}")
        return

    if DRY_RUN:
        logger.info(
            f"[DRY RUN] Would reply to @{author_handle} ({tweet['id']}) "
            f"mode={mode}: {reply_text[:80]}..."
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


def score_tweet(tweet: dict, thread_context: list[dict] | None = None):
    if is_paused():
        return

    handle = tweet.get("author_handle", "").lower().lstrip("@")
    if handle in SKIP_HANDLES:
        return

    tweet_id = tweet["id"]
    if has_scored(tweet_id):
        return

    if count_scores_today() >= MAX_SCORES_PER_DAY:
        logger.debug("Daily glaze score limit reached")
        return

    tweet_text = tweet.get("text", "")
    author_handle = tweet.get("author_handle", "unknown")
    if thread_context is None:
        thread_context = _get_thread_context(tweet)

    try:
        result = score_glaze(tweet_text, author_handle, thread_context=thread_context)
    except Exception as e:
        logger.error(f"Claude scoring error for {tweet_id}: {e}")
        return

    if result is None:
        logger.debug(f"Tweet {tweet_id} not Printr-relevant — no score card")
        return

    score, tier, score_card = result

    if DRY_RUN:
        logger.info(
            f"[DRY RUN] Glaze score @{author_handle}: {score}/100 [{tier}] — "
            f"{score_card[:60]}..."
        )
        record_score(tweet_id, author_handle, score, tier, score_card, None, dry_run=True)
    else:
        quote_id = post_quote_tweet(score_card, tweet_id)
        record_score(tweet_id, author_handle, score, tier, score_card, quote_id, dry_run=False)
        logger.info(f"Glaze scored @{author_handle}: {score}/100 [{tier}]")


def _handle_tweet(tweet: dict):
    """Fetch full thread context once, classify intent, then route to score card or reply."""
    thread_context = _get_thread_context(tweet)

    try:
        intent = classify_tweet_intent(
            tweet.get("text", ""), tweet.get("author_handle", ""),
            thread_context=thread_context,
        )
    except Exception as e:
        logger.warning(f"Intent classification failed for {tweet['id']}: {e} — defaulting to reply")
        intent = "conversation"

    if intent == "opinion":
        score_tweet(tweet, thread_context=thread_context)
    else:
        process_tweet(tweet, thread_context=thread_context)


def poll_mentions():
    global _mentions_since_id, _mentions_since_id_loaded

    if not _mentions_since_id_loaded:
        _mentions_since_id = get_mentions_since_id()
        _mentions_since_id_loaded = True

    logger.info(f"Polling mentions since_id={_mentions_since_id}")
    tweets = fetch_mentions(since_id=_mentions_since_id)

    if tweets:
        _mentions_since_id = tweets[0]["id"]
        set_mentions_since_id(_mentions_since_id)
        for tweet in tweets:
            if has_replied_mention(tweet["id"]):
                logger.debug(f"Mention {tweet['id']} already processed — skip")
                continue
            record_replied_mention(tweet["id"], tweet.get("author_id", ""))
            _handle_tweet(tweet)


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
            _handle_tweet(tweet)


async def post_original_tweet():
    if is_paused():
        logger.info("Bot paused — skipping original tweet job")
        return

    logger.info("Running original tweet job...")
    try:
        projects = await scrape_all_data()

        for proj in projects:
            if proj.get("contract_address") or proj.get("market_cap"):
                mem.remember_project_data(
                    proj["name"],
                    proj.get("contract_address", ""),
                    price=proj.get("price"),
                    volume=proj.get("volume"),
                    market_cap=proj.get("market_cap"),
                    price_change_24h=proj.get("price_change_24h"),
                    liquidity=proj.get("liquidity"),
                    staking_pct=proj.get("staking_pct"),
                )

        memory_context = mem.get_memory_context()
        tweet_text = generate_original_tweet(market_data=projects, memory_context=memory_context)

        if DRY_RUN:
            logger.info(f"[DRY RUN] Original tweet: {tweet_text}")
            record_original_tweet("dry_run", tweet_text, dry_run=True)
        else:
            tweet_id = post_tweet(tweet_text)
            if tweet_id:
                record_original_tweet(tweet_id, tweet_text, dry_run=False)
                logger.info(f"Posted original tweet {tweet_id}: {tweet_text[:60]}...")
            else:
                logger.warning("Failed to post original tweet")
    except Exception as e:
        logger.error(f"Original tweet job error: {e}")


def _stream_callback(tweet: dict):
    _handle_tweet(tweet)


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
                tweet_fields=["author_id", "text", "referenced_tweets"],
                expansions=["author_id", "referenced_tweets.id"],
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
