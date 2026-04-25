import os
import asyncio
import logging
import random
import re
import threading
from datetime import datetime, timezone, timedelta

from database import (
    has_replied, record_reply, count_replies_today,
    count_replies_to_account_last_hour, is_paused,
    set_stream_status, has_scored, record_score, count_scores_today,
    record_original_tweet, has_replied_mention, record_replied_mention,
    get_mentions_since_id, set_mentions_since_id,
    get_list_since_id, set_list_since_id,
    has_replied_to_author_in_chain,
)
from claude_client import (
    generate_reply, select_mode, generate_original_tweet, score_glaze,
    classify_tweet_intent,
)
from twitter_client import (
    fetch_list_tweets, fetch_mentions, post_reply, post_tweet, post_quote_tweet,
    setup_stream_rules, GlazePrintrStream, fetch_tweet_chain, fetch_tweet_media_url,
)
from image_generator import (
    generate_glaze_score_card, generate_ecosystem_stats_card, remix_tweet_image,
)
import memory as mem
from scraper import scrape_all_data

logger = logging.getLogger(__name__)

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
X_LIST_ID = os.environ.get("X_LIST_ID", "")
MAX_REPLIES_PER_DAY = int(os.environ.get("MAX_REPLIES_PER_DAY", "200"))
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
_list_poll_since_id_loaded: bool = False
_mentions_since_id: str | None = None
_mentions_since_id_loaded: bool = False
_stream_thread: threading.Thread | None = None
_stream_instance: GlazePrintrStream | None = None

SKIP_HANDLES = {"printrglazr", "printr_money"}
MAX_MENTION_AGE_MINUTES = 120
MAX_LIST_AGE_HOURS = 1
MAX_STREAM_AGE_MINUTES = 5

# Probability of attaching images (40% of eligible tweets get images)
IMAGE_ORIGINAL_PROB = 0.40
IMAGE_REPLY_PROB = 0.40

ECOSYSTEM_TOKENS = [
    "belief", "ooo", "rotus", "fatchoi", "deployr", "patapim",
    "roi", "noob", "print", "cmyk", "pve", "ket", "fsjal", "marmot",
]

_latest_projects: list[dict] = []


def _extract_token(text: str, extra: str = "") -> str:
    combined = (text + " " + extra).lower()
    for tok in ECOSYSTEM_TOKENS:
        if f"${tok}" in combined:
            return tok
    m = re.search(r"\$([a-zA-Z]{2,12})", combined)
    return m.group(1).lower() if m else ""


def _get_token_metrics(token_name: str) -> dict | None:
    if not token_name or not _latest_projects:
        return None
    for proj in _latest_projects:
        if proj.get("name", "").lower() == token_name.lower():
            return {
                "staking_pct": proj.get("staking_pct"),
                "market_cap": proj.get("market_cap"),
                "volume": proj.get("volume"),
                "price_change_24h": proj.get("price_change_24h"),
            }
    return None


def _tweet_age_minutes(tweet: dict) -> float | None:
    created_at = tweet.get("created_at")
    if not created_at:
        return None
    try:
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if getattr(created_at, "tzinfo", None) is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - created_at).total_seconds() / 60
    except (ValueError, TypeError, AttributeError):
        return None


def _is_recent_tweet(tweet: dict, max_age_minutes: int) -> bool:
    age = _tweet_age_minutes(tweet)
    if age is None:
        # No timestamp on stream tweets means we can't verify age — reject to be safe
        logger.warning(f"Tweet {tweet.get('id')} has no created_at — SKIPPING (no timestamp)")
        return False
    return age <= max_age_minutes


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
    # Don't reply again unless they replied directly to one of our replies
    if tweet.get("author_id") and has_replied_to_author_in_chain(
        tweet["author_id"], tweet.get("in_reply_to_tweet_id")
    ):
        return True, "already replied to this author in thread (no response from them)"
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

    # Optionally remix source image (40% chance, only if tweet has media)
    img_path = None
    if random.random() < IMAGE_REPLY_PROB:
        source_media_url = tweet.get("media_url")
        if not source_media_url and tweet.get("in_reply_to_tweet_id"):
            source_media_url = fetch_tweet_media_url(tweet["in_reply_to_tweet_id"])
        if source_media_url:
            token = _extract_token(tweet_text)
            try:
                img_path = remix_tweet_image(source_media_url, token_name=token)
            except Exception as e:
                logger.debug(f"Meme remix failed: {e}")

    our_reply_id = None
    if DRY_RUN:
        logger.info(
            f"[DRY RUN] Would reply to @{author_handle} ({tweet['id']}) "
            f"mode={mode} img={'yes' if img_path else 'no'}: {reply_text[:80]}..."
        )
    else:
        our_reply_id = post_reply(reply_text, tweet["id"], media_path=img_path)
        if not our_reply_id:
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
        our_reply_tweet_id=our_reply_id,
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

    # Always generate a score card image
    token_name = _extract_token(tweet_text, score_card)
    metrics = _get_token_metrics(token_name)
    img_path = None
    try:
        img_path = generate_glaze_score_card(score, tier, token_name, score_card, metrics)
    except Exception as e:
        logger.warning(f"Score card image generation failed: {e}")

    if DRY_RUN:
        logger.info(
            f"[DRY RUN] Glaze score @{author_handle}: {score}/100 [{tier}] img={'yes' if img_path else 'no'} — "
            f"{score_card[:60]}..."
        )
        record_score(tweet_id, author_handle, score, tier, score_card, None, dry_run=True)
    else:
        quote_id = post_quote_tweet(score_card, tweet_id, media_path=img_path)
        record_score(tweet_id, author_handle, score, tier, score_card, quote_id, dry_run=False)
        logger.info(f"Glaze scored @{author_handle}: {score}/100 [{tier}] img={'yes' if img_path else 'no'}")


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
        _mentions_since_id = str(max(int(t["id"]) for t in tweets))
        set_mentions_since_id(_mentions_since_id)
        for tweet in tweets:
            if has_replied_mention(tweet["id"]):
                logger.debug(f"Mention {tweet['id']} already processed — skip")
                continue
            if not _is_recent_tweet(tweet, MAX_MENTION_AGE_MINUTES):
                age = _tweet_age_minutes(tweet)
                age_str = f"{age:.1f}" if age is not None else "no timestamp"
                logger.info(f"SKIPPING mention {tweet['id']} — too old ({age_str} minutes, max {MAX_MENTION_AGE_MINUTES})")
                record_replied_mention(tweet["id"], tweet.get("author_id", ""))
                continue
            record_replied_mention(tweet["id"], tweet.get("author_id", ""))
            _handle_tweet(tweet)


def poll_list():
    global _list_poll_since_id, _list_poll_since_id_loaded
    if not X_LIST_ID:
        logger.warning("X_LIST_ID not set — list polling disabled")
        return

    if not _list_poll_since_id_loaded:
        _list_poll_since_id = get_list_since_id()
        _list_poll_since_id_loaded = True

    logger.info(f"Polling list {X_LIST_ID} since_id={_list_poll_since_id}")
    tweets = fetch_list_tweets(X_LIST_ID, since_id=_list_poll_since_id)

    if tweets:
        _list_poll_since_id = str(max(int(t["id"]) for t in tweets))
        set_list_since_id(_list_poll_since_id)
        for tweet in tweets:
            if not _is_recent_tweet(tweet, MAX_LIST_AGE_HOURS * 60):
                age = _tweet_age_minutes(tweet)
                age_str = f"{age:.1f}" if age is not None else "no timestamp"
                logger.info(f"SKIPPING list tweet {tweet['id']} — too old ({age_str} minutes, max {MAX_LIST_AGE_HOURS}h)")
                continue
            _handle_tweet(tweet)


async def post_original_tweet():
    global _latest_projects
    if is_paused():
        logger.info("Bot paused — skipping original tweet job")
        return

    logger.info("Running original tweet job...")
    try:
        projects = await scrape_all_data()
        _latest_projects = projects  # cache for score card metric lookups

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

        # 40% chance to attach ecosystem stats card
        img_path = None
        if random.random() < IMAGE_ORIGINAL_PROB:
            try:
                img_path = generate_ecosystem_stats_card(projects)
            except Exception as e:
                logger.warning(f"Ecosystem stats card failed: {e}")

        if DRY_RUN:
            logger.info(f"[DRY RUN] Original tweet img={'yes' if img_path else 'no'}: {tweet_text}")
            record_original_tweet("dry_run", tweet_text, dry_run=True)
        else:
            tweet_id = post_tweet(tweet_text, media_path=img_path)
            if tweet_id:
                record_original_tweet(tweet_id, tweet_text, dry_run=False)
                logger.info(f"Posted original tweet {tweet_id} img={'yes' if img_path else 'no'}: {tweet_text[:60]}...")
            else:
                logger.warning("Failed to post original tweet")
    except Exception as e:
        logger.error(f"Original tweet job error: {e}")


def _stream_callback(tweet: dict):
    if is_paused():
        return
    if not _is_recent_tweet(tweet, MAX_STREAM_AGE_MINUTES):
        age = _tweet_age_minutes(tweet)
        age_str = f"{age:.1f}" if age is not None else "unknown"
        logger.info(f"SKIPPING stream tweet {tweet.get('id')} — too old ({age_str} minutes, max {MAX_STREAM_AGE_MINUTES})")
        return
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
        logger.info("Filtered stream starting...")
        try:
            set_stream_status("connected")
            _stream_instance.filter(
                tweet_fields=["author_id", "created_at", "text", "referenced_tweets"],
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
