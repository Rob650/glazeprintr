import os
import asyncio
import logging
import random
import re
from datetime import datetime, timezone, timedelta

from database import (
    has_replied, record_reply,
    count_replies_to_account_last_hour, is_paused,
    has_scored, record_score, count_scores_today,
    record_original_tweet, has_replied_mention, record_replied_mention,
    get_mentions_since_id, set_mentions_since_id,
    try_claim_mention, try_claim_reply,
)
from claude_client import (
    generate_reply, select_mode, generate_original_tweet, score_glaze,
    classify_tweet_intent,
)
from twitter_client import (
    fetch_list_tweets, fetch_mentions, post_reply, post_tweet, post_quote_tweet,
    fetch_tweet_chain, fetch_tweet_media_url,
)
from image_generator import (
    generate_glaze_score_card, generate_ecosystem_stats_card, remix_tweet_image,
)
import memory as mem
from scraper import scrape_all_data, fetch_token_data_sync

logger = logging.getLogger(__name__)

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
X_LIST_ID = os.environ.get("X_LIST_ID", "")
MAX_REPLIES_PER_ACCOUNT_HOUR = int(os.environ.get("MAX_REPLIES_PER_ACCOUNT_HOUR", "5"))
MAX_SCORES_PER_DAY = int(os.environ.get("MAX_SCORES_PER_DAY", "20"))

_BOT_START_TIME = datetime.now(timezone.utc)

_list_poll_since_id: str | None = None  # in-memory only; List API doesn't support since_id

_mentions_since_id: str | None = None
_mentions_since_id_loaded: bool = False
_mentions_since_id_was_fresh: bool = False  # True when DB was wiped (since_id not persisted)

SKIP_HANDLES = {"printrglazr", "printr_money"}
MAX_MENTION_AGE_MINUTES = 120
MAX_LIST_AGE_HOURS = 1

# Probability of attaching images (40% of eligible tweets get images)
IMAGE_ORIGINAL_PROB = 0.40
IMAGE_REPLY_PROB = 0.40

ECOSYSTEM_TOKENS = [
    "belief", "ooo", "rotus", "fatchoi", "deployr", "patapim",
    "roi", "noob", "print", "cmyk", "pve", "ket", "fsjal", "marmot",
]

_latest_projects: list[dict] = []

_CONTRACT_RE = re.compile(
    r'\b(0x[0-9a-fA-F]{40,}|[1-9A-HJ-NP-Za-km-z]{32,44})\b'
)


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
                "name": proj.get("name"),
                "staking_pct": proj.get("staking_pct"),
                "market_cap": proj.get("market_cap"),
                "volume": proj.get("volume"),
                "price_change_24h": proj.get("price_change_24h"),
                "price": proj.get("price"),
            }
    return None


def _get_tweet_token_data(tweet_text: str) -> dict | None:
    """Look up live token data for any contract address or ticker mentioned in a tweet."""
    contract_match = _CONTRACT_RE.search(tweet_text)
    if contract_match:
        data = fetch_token_data_sync(contract_match.group(1))
        if data:
            return data

    token_name = _extract_token(tweet_text)
    if not token_name:
        return None

    cached = _get_token_metrics(token_name)
    if cached and any(v is not None for v in cached.values()):
        return cached

    return fetch_token_data_sync(token_name)


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


def _tweet_predates_startup(tweet: dict) -> bool:
    """True if tweet was created before this process started (DB may have been wiped)."""
    age_minutes = _tweet_age_minutes(tweet)
    if age_minutes is None:
        return False
    uptime_minutes = (datetime.now(timezone.utc) - _BOT_START_TIME).total_seconds() / 60
    return age_minutes > uptime_minutes + 1  # +1 min buffer for clock skew


def _should_skip_reply(tweet: dict) -> tuple[bool, str]:
    handle = tweet.get("author_handle", "").lower().lstrip("@")
    if handle in SKIP_HANDLES:
        return True, f"skip handle @{handle}"
    if has_replied(tweet["id"]):
        return True, "already replied"
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

    token_data = None
    try:
        token_data = _get_tweet_token_data(tweet_text)
        if token_data:
            logger.info(f"Got token data for reply: {token_data.get('name')} mc={token_data.get('market_cap')}")
    except Exception as e:
        logger.warning(f"Token lookup failed for tweet {tweet['id']}: {e}")

    try:
        reply_text, mode = generate_reply(
            tweet_text,
            author_handle,
            thread_context=thread_context,
            memory_context=memory_context,
            token_data=token_data,
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
        # Final safety net: atomically pre-claim the reply slot before posting.
        # If another thread already claimed it, abort — no double reply under any circumstance.
        if not try_claim_reply(tweet["id"]):
            logger.warning(f"ABORT: reply slot for {tweet['id']} already claimed — double-reply prevented")
            return
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
    logger.info(f"Processing tweet {tweet['id']} from @{tweet.get('author_handle', 'unknown')}: {tweet.get('text', '')[:80]}")
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
    global _mentions_since_id, _mentions_since_id_loaded, _mentions_since_id_was_fresh

    if not _mentions_since_id_loaded:
        _mentions_since_id = get_mentions_since_id()
        _mentions_since_id_loaded = True
        if _mentions_since_id is None:
            _mentions_since_id_was_fresh = True
            logger.warning("mentions since_id not found in DB — DB may be fresh/wiped; will silence pre-startup tweets")

    logger.info(f"Polling mentions since_id={_mentions_since_id}")
    tweets = fetch_mentions(since_id=_mentions_since_id)

    if tweets:
        _mentions_since_id = str(max(int(t["id"]) for t in tweets))
        set_mentions_since_id(_mentions_since_id)
        for tweet in tweets:
            # Atomic claim — INSERT OR IGNORE returns rowcount=0 if already claimed.
            # Prevents race with concurrent poll_list processing the same tweet.
            if not try_claim_mention(tweet["id"], tweet.get("author_id", "")):
                logger.debug(f"Mention {tweet['id']} already claimed — skip")
                continue
            # Railway wipes SQLite on every deploy. If since_id was lost, we re-fetch
            # old tweets whose replies are already posted. Silently claim (already done above).
            if _mentions_since_id_was_fresh and _tweet_predates_startup(tweet):
                logger.info(f"Silently claimed pre-startup mention {tweet['id']} (fresh DB, avoiding double-reply)")
                continue
            if not _is_recent_tweet(tweet, MAX_MENTION_AGE_MINUTES):
                age = _tweet_age_minutes(tweet)
                age_str = f"{age:.1f}" if age is not None else "no timestamp"
                logger.info(f"SKIPPING mention {tweet['id']} — too old ({age_str} minutes, max {MAX_MENTION_AGE_MINUTES})")
                continue
            _handle_tweet(tweet)


def poll_list():
    global _list_poll_since_id
    if not X_LIST_ID:
        logger.warning("X_LIST_ID not set — list polling disabled")
        return

    # The Twitter List Tweets API does NOT support since_id — filtered client-side instead.
    logger.info(f"Polling list {X_LIST_ID} (in-memory since_id={_list_poll_since_id})")
    tweets = fetch_list_tweets(X_LIST_ID)

    if tweets:
        max_id = str(max(int(t["id"]) for t in tweets))
        # Client-side since_id filter: skip tweets we've already seen this session
        if _list_poll_since_id:
            tweets = [t for t in tweets if int(t["id"]) > int(_list_poll_since_id)]
        _list_poll_since_id = max_id

        for tweet in tweets:
            # Atomic claim — prevents race with concurrent poll_mentions on same tweet.
            if not try_claim_mention(tweet["id"], tweet.get("author_id", "")):
                logger.debug(f"List tweet {tweet['id']} already claimed — skip")
                continue
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

