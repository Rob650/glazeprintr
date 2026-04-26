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
    get_list_since_id, set_list_since_id,
    get_keyword_search_since_id, set_keyword_search_since_id,
    get_follower_search_since_id, set_follower_search_since_id,
    try_claim_mention, try_claim_reply,
    store_ecosystem_tweet, get_ecosystem_context_age_hours,
    try_claim_quote, record_quote_tweet,
    get_qt_glazer_since_id, set_qt_glazer_since_id,
)
from claude_client import (
    generate_reply, select_mode, generate_original_tweet, score_glaze,
    classify_tweet_intent, generate_quote_tweet,
)
from twitter_client import (
    fetch_list_tweets, fetch_mentions, post_reply, post_tweet, post_quote_tweet,
    fetch_tweet_chain, fetch_user_tweets, search_keyword_tweets, fetch_bot_followers,
    QUOTE_TWEET_FORBIDDEN,
)
# image generation disabled
# from image_generator import (
#     generate_glaze_score_card, generate_ecosystem_stats_card, remix_tweet_image,
# )
import memory as mem
from scraper import scrape_all_data, fetch_token_data_sync, format_comparative_context

logger = logging.getLogger(__name__)

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
X_LIST_ID = os.environ.get("X_LIST_ID", "")
QT_GLAZER_LIST_ID = os.environ.get("QT_GLAZER_LIST_ID", "2048242501333799282")
MAX_REPLIES_PER_ACCOUNT_HOUR = int(os.environ.get("MAX_REPLIES_PER_ACCOUNT_HOUR", "5"))
MAX_SCORES_PER_DAY = int(os.environ.get("MAX_SCORES_PER_DAY", "20"))

_BOT_START_TIME = datetime.now(timezone.utc)

_list_poll_since_id: str | None = None  # loaded from DB on first call; List API doesn't support since_id natively
_list_poll_since_id_loaded: bool = False

_mentions_since_id: str | None = None
_mentions_since_id_loaded: bool = False
_mentions_since_id_was_fresh: bool = False  # True when DB was wiped (since_id not persisted)

_keyword_search_since_id: str | None = None
_keyword_search_since_id_loaded: bool = False

SKIP_HANDLES = {"printrglazr", "printr_money"}
MAX_MENTION_AGE_MINUTES = 120

ECOSYSTEM_TOKENS = [
    "belief", "ooo", "rotus", "fatchoi", "deployr", "patapim",
    "roi", "noob", "print", "cmyk", "pve", "ket", "fsjal", "marmot",
]

_latest_projects: list[dict] = []
_latest_dune_context: str = ""
_latest_comparative_context: str = ""

_CONTRACT_RE = re.compile(
    r'\b(0x[0-9a-fA-F]{40,}|[1-9A-HJ-NP-Za-km-z]{32,44})\b'
)

# Core keywords — tweets matching any of these (or any cashtag, or any top-10 ticker) are engaged.
_LIST_RELEVANCE_KEYWORDS = frozenset(["printr", "pob", "brrr", "belief"])

# QT Glazer list — broader keyword set covering the full Printr/Fed ecosystem.
_QT_GLAZER_KEYWORDS = frozenset([
    "printr", "brrr", "belief", "rotus", "deployr", "fatchoi",
    "stakrr", "masterprintr", "glaze", "glazeprintr", "staking",
    "pob", "print", "noob", "cmyk", "patapim", "marmot", "fsjal", "ket",
    "pve", "roi", "ooo", "prinaboratory", "quack",
])
_QT_GLAZER_WINDOW_MINUTES = 20  # wider window for 10-min poll interval

_qt_glazer_since_id: str | None = None
_qt_glazer_since_id_loaded: bool = False


def _get_top_tickers(n: int = 10) -> list[str]:
    """Return the top N ticker names (lowercase) from the most recent Printr scrape."""
    return [p["name"].lower() for p in _latest_projects[:n]]


def _is_relevant_tweet(text: str) -> bool:
    """Returns True if the tweet mentions a cashtag, a core keyword, or a current top-10 token."""
    if re.search(r'\$[a-zA-Z]{2,}', text):
        return True
    lower = text.lower()
    if any(kw in lower for kw in _LIST_RELEVANCE_KEYWORDS):
        return True
    return any(ticker in lower for ticker in _get_top_tickers(10))


def _is_qt_glazer_relevant(text: str) -> bool:
    """Returns True if the tweet is about the Printr/Fed ecosystem (for QT Glazer list)."""
    if re.search(r'\$[a-zA-Z]{2,}', text):
        return True
    lower = text.lower()
    if any(kw in lower for kw in _QT_GLAZER_KEYWORDS):
        return True
    # Also check known contract address pattern
    if _CONTRACT_RE.search(text):
        return True
    return any(ticker in lower for ticker in _get_top_tickers(10))


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
            ecosystem_comparative=_latest_comparative_context,
            dune_context=_latest_dune_context,
        )
    except Exception as e:
        logger.error(f"Claude error for tweet {tweet['id']}: {e}")
        return

    img_path = None  # image generation disabled

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


def score_tweet(tweet: dict, thread_context: list[dict] | None = None) -> bool:
    """Score the tweet with a glaze score quote-tweet. Returns True if scored, False if not Printr-relevant."""
    if is_paused():
        return False

    handle = tweet.get("author_handle", "").lower().lstrip("@")
    if handle in SKIP_HANDLES:
        return False

    tweet_id = tweet["id"]
    if has_scored(tweet_id):
        return True  # already scored — no need to fall back to reply

    if count_scores_today() >= MAX_SCORES_PER_DAY:
        logger.debug("Daily glaze score limit reached")
        return False

    tweet_text = tweet.get("text", "")
    author_handle = tweet.get("author_handle", "unknown")
    if thread_context is None:
        thread_context = _get_thread_context(tweet)

    try:
        result = score_glaze(tweet_text, author_handle, thread_context=thread_context)
    except Exception as e:
        logger.error(f"Claude scoring error for {tweet_id}: {e}")
        return False

    if result is None:
        logger.debug(f"Tweet {tweet_id} not Printr-relevant — no score card")
        return False

    score, tier, score_card = result

    token_name = _extract_token(tweet_text, score_card)
    img_path = None  # image generation disabled

    if DRY_RUN:
        logger.info(
            f"[DRY RUN] Glaze score @{author_handle}: {score}/100 [{tier}] img={'yes' if img_path else 'no'} — "
            f"{score_card[:60]}..."
        )
        record_score(tweet_id, author_handle, score, tier, score_card, None, dry_run=True)
    else:
        quote_id = post_quote_tweet(score_card, tweet_id, media_path=img_path)
        if not quote_id:
            logger.warning(f"Failed to post glaze score for @{author_handle} ({tweet_id})")
            return False
        record_score(tweet_id, author_handle, score, tier, score_card, quote_id, dry_run=False)
        logger.info(f"Glaze scored @{author_handle}: {score}/100 [{tier}] img={'yes' if img_path else 'no'}")
    return True


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
        scored = score_tweet(tweet, thread_context=thread_context)
        if not scored:
            # Not Printr-relevant for a glaze score — fall back to conversational reply
            process_tweet(tweet, thread_context=thread_context)
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
    global _list_poll_since_id, _list_poll_since_id_loaded
    if not X_LIST_ID:
        logger.warning("X_LIST_ID not set — list polling disabled")
        return

    # Load persisted since_id once per session (survives restarts if DB is not wiped)
    if not _list_poll_since_id_loaded:
        _list_poll_since_id = get_list_since_id()
        _list_poll_since_id_loaded = True

    # The Twitter List Tweets API does NOT support since_id — filtered client-side instead.
    logger.info(f"Polling list {X_LIST_ID} (since_id={_list_poll_since_id})")
    tweets = fetch_list_tweets(X_LIST_ID)
    logger.info(f"fetch_list_tweets returned {len(tweets)} tweet(s)")

    if not tweets:
        return

    max_id = str(max(int(t["id"]) for t in tweets))
    # Client-side since_id filter: skip tweets we've already seen this session
    if _list_poll_since_id:
        tweets = [t for t in tweets if int(t["id"]) > int(_list_poll_since_id)]
    _list_poll_since_id = max_id
    set_list_since_id(max_id)  # persist across restarts

    logger.info(f"After since_id filter: {len(tweets)} candidate tweet(s)")

    bot_handle_lower = os.environ.get("BOT_HANDLE", "printrglazr").lower()
    for tweet in tweets:
        # Hard 5-min window — same as keyword search; prevents stale replies after restarts
        if not _is_recent_tweet(tweet, _KEYWORD_SEARCH_WINDOW_MINUTES):
            age = _tweet_age_minutes(tweet)
            age_str = f"{age:.1f}" if age is not None else "no timestamp"
            logger.info(f"SKIPPING list tweet {tweet['id']} — too old ({age_str} min, max {_KEYWORD_SEARCH_WINDOW_MINUTES}min)")
            continue
        # Skip tweets that are replies in conversations we're not part of — Twitter 403s if we
        # weren't @mentioned or haven't engaged in the thread.
        if tweet.get("in_reply_to_tweet_id") and f"@{bot_handle_lower}" not in tweet.get("text", "").lower():
            logger.debug(f"SKIPPING list tweet {tweet['id']} — reply thread, bot not mentioned")
            continue
        # Skip tweets with restricted reply settings — we'd get a 403 immediately.
        if tweet.get("reply_settings", "everyone") != "everyone":
            logger.debug(f"SKIPPING list tweet {tweet['id']} — reply_settings={tweet.get('reply_settings')}")
            continue
        # Atomic claim — prevents race with concurrent poll_mentions on same tweet
        if not try_claim_mention(tweet["id"], tweet.get("author_id", "")):
            logger.debug(f"List tweet {tweet['id']} already claimed — skip")
            continue
        _handle_tweet(tweet)


# Static keywords for keyword search polling (cashtag variants are added dynamically from top tickers).
# Keep these specific — generic words like "belief"/"pob" match K-pop/religious tweets and cause 403 spam.
_KEYWORD_SEARCH_STATIC = ["printr", "brrr"]
_KEYWORD_SEARCH_WINDOW_MINUTES = 5


def _build_keyword_query() -> str:
    """Build a Twitter recent-search query from static keywords + dynamic top-10 tickers."""
    bot_handle = os.environ.get("BOT_HANDLE", "printrglazr")
    tickers = _get_top_tickers(10)

    # Plain-word static terms + cashtag versions of dynamic tickers
    terms: list[str] = list(_KEYWORD_SEARCH_STATIC)
    seen_lower = set(t.lower() for t in terms)
    for ticker in tickers:
        cashtag = f"${ticker.upper()}"
        if cashtag.lower() not in seen_lower:
            terms.append(cashtag)
            seen_lower.add(cashtag.lower())

    query = f"({' OR '.join(terms)}) -is:retweet -is:reply -from:{bot_handle}"
    # Twitter v2 recent search has a 512-char query limit — trim tickers if needed
    while len(query) > 512 and terms:
        terms.pop()
        query = f"({' OR '.join(terms)}) -is:retweet -is:reply -from:{bot_handle}"
    return query


def poll_keyword_search():
    global _keyword_search_since_id, _keyword_search_since_id_loaded

    if not _keyword_search_since_id_loaded:
        _keyword_search_since_id = get_keyword_search_since_id()
        _keyword_search_since_id_loaded = True

    query = _build_keyword_query()
    # Twitter v2 doesn't allow since_id AND start_time together. Use since_id when available
    # (it already provides recency guarantee); fall back to start_time on fresh DB.
    start_time = None if _keyword_search_since_id else (
        datetime.now(timezone.utc) - timedelta(minutes=_KEYWORD_SEARCH_WINDOW_MINUTES)
    )

    logger.info(f"Polling keyword search since_id={_keyword_search_since_id} query={query[:80]}...")
    tweets = search_keyword_tweets(query, since_id=_keyword_search_since_id, start_time=start_time)
    logger.info(f"search_keyword_tweets returned {len(tweets)} tweet(s)")

    if not tweets:
        return

    _keyword_search_since_id = str(max(int(t["id"]) for t in tweets))
    set_keyword_search_since_id(_keyword_search_since_id)

    for tweet in tweets:
        rs = tweet.get("reply_settings", "everyone")
        # Skip tweets with restricted reply settings — we'd get a 403 immediately.
        if rs != "everyone":
            logger.debug(f"SKIPPING keyword tweet {tweet['id']} — reply_settings={rs}")
            continue
        if not try_claim_mention(tweet["id"], tweet.get("author_id", "")):
            logger.debug(f"Keyword tweet {tweet['id']} already claimed — skip")
            continue
        _handle_tweet(tweet)


_follower_cache: list[str] = []  # cached follower usernames
_follower_cache_refreshed_at: datetime | None = None
_FOLLOWER_CACHE_TTL_MINUTES = 60

# All four keywords for follower search — followers have prior engagement so noise is less of a concern
_FOLLOWER_SEARCH_STATIC = ["printr", "brrr", "belief", "pob"]

_follower_search_since_id: str | None = None
_follower_search_since_id_loaded: bool = False


def _maybe_refresh_follower_cache():
    global _follower_cache, _follower_cache_refreshed_at
    now = datetime.now(timezone.utc)
    if _follower_cache_refreshed_at is not None:
        age_min = (now - _follower_cache_refreshed_at).total_seconds() / 60
        if age_min < _FOLLOWER_CACHE_TTL_MINUTES:
            return
    try:
        followers = fetch_bot_followers(max_results=500)
        if followers:
            _follower_cache = [f["username"] for f in followers]
            _follower_cache_refreshed_at = now
            logger.info(f"Follower cache refreshed: {len(_follower_cache)} followers")
        elif not _follower_cache:
            logger.warning("Follower cache refresh returned empty — no followers yet")
    except Exception as e:
        logger.error(f"Follower cache refresh failed: {e}")


def _build_follower_query_batches(usernames: list[str]) -> list[str]:
    """Split followers into batches, each producing a search query that fits in 512 chars."""
    bot_handle = os.environ.get("BOT_HANDLE", "printrglazr")
    tickers = _get_top_tickers(10)

    terms: list[str] = list(_FOLLOWER_SEARCH_STATIC)
    seen_lower = set(t.lower() for t in terms)
    for ticker in tickers:
        cashtag = f"${ticker.upper()}"
        if cashtag.lower() not in seen_lower:
            terms.append(cashtag)
            seen_lower.add(cashtag.lower())

    keyword_part = f"({' OR '.join(terms)})"
    suffix = f" -is:retweet -is:reply -from:{bot_handle}"
    # budget for the (from:u1 OR from:u2 OR ...) block including its surrounding space
    budget = 512 - len(keyword_part) - 1 - len(suffix)

    batches: list[str] = []
    batch: list[str] = []
    # track running length of the from-block: 2 for "()", then entries
    used = 2

    for username in usernames:
        entry_len = len(f"from:{username}") + (4 if batch else 0)  # " OR " = 4
        if used + entry_len > budget and batch:
            from_part = "(" + " OR ".join(f"from:{u}" for u in batch) + ")"
            batches.append(f"{keyword_part} {from_part}{suffix}")
            batch = [username]
            used = 2 + len(f"from:{username}")
        else:
            batch.append(username)
            used += entry_len

    if batch:
        from_part = "(" + " OR ".join(f"from:{u}" for u in batch) + ")"
        batches.append(f"{keyword_part} {from_part}{suffix}")

    return batches


def poll_follower_tweets():
    global _follower_search_since_id, _follower_search_since_id_loaded

    if not _follower_search_since_id_loaded:
        _follower_search_since_id = get_follower_search_since_id()
        _follower_search_since_id_loaded = True

    _maybe_refresh_follower_cache()

    if not _follower_cache:
        logger.info("Follower tweet poll skipped — no followers cached yet")
        return

    queries = _build_follower_query_batches(_follower_cache)
    if not queries:
        return

    start_time = None if _follower_search_since_id else (
        datetime.now(timezone.utc) - timedelta(minutes=_KEYWORD_SEARCH_WINDOW_MINUTES)
    )

    logger.info(
        f"Polling follower tweets — {len(_follower_cache)} followers, "
        f"{len(queries)} batch(es), since_id={_follower_search_since_id}"
    )

    all_tweets: list[dict] = []
    for query in queries:
        batch = search_keyword_tweets(query, since_id=_follower_search_since_id, start_time=start_time)
        all_tweets.extend(batch)

    logger.info(f"Follower tweet poll found {len(all_tweets)} tweet(s)")

    if not all_tweets:
        return

    new_since_id = str(max(int(t["id"]) for t in all_tweets))
    if _follower_search_since_id is None or int(new_since_id) > int(_follower_search_since_id):
        _follower_search_since_id = new_since_id
        set_follower_search_since_id(_follower_search_since_id)

    bot_handle_lower = os.environ.get("BOT_HANDLE", "printrglazr").lower()
    for tweet in all_tweets:
        if tweet.get("reply_settings", "everyone") != "everyone":
            logger.debug(f"SKIPPING follower tweet {tweet['id']} — reply_settings={tweet.get('reply_settings')}")
            continue
        if tweet.get("in_reply_to_tweet_id") and f"@{bot_handle_lower}" not in tweet.get("text", "").lower():
            logger.debug(f"SKIPPING follower tweet {tweet['id']} — reply thread, bot not mentioned")
            continue
        if not try_claim_mention(tweet["id"], tweet.get("author_id", "")):
            logger.debug(f"Follower tweet {tweet['id']} already claimed — skip")
            continue
        _handle_tweet(tweet)


def poll_qt_glazer_list():
    global _qt_glazer_since_id, _qt_glazer_since_id_loaded

    if is_paused():
        logger.info("Bot paused — skipping QT Glazer poll")
        return

    if not _qt_glazer_since_id_loaded:
        _qt_glazer_since_id = get_qt_glazer_since_id()
        _qt_glazer_since_id_loaded = True

    logger.info(f"Polling QT Glazer list {QT_GLAZER_LIST_ID} (since_id={_qt_glazer_since_id})")
    tweets = fetch_list_tweets(QT_GLAZER_LIST_ID)
    logger.info(f"QT Glazer fetch returned {len(tweets)} tweet(s)")

    if not tweets:
        return

    max_id = str(max(int(t["id"]) for t in tweets))
    if _qt_glazer_since_id:
        tweets = [t for t in tweets if int(t["id"]) > int(_qt_glazer_since_id)]
    _qt_glazer_since_id = max_id
    set_qt_glazer_since_id(max_id)

    logger.info(f"QT Glazer: {len(tweets)} new tweet(s) after since_id filter")

    bot_handle_lower = os.environ.get("BOT_HANDLE", "printrglazr").lower()
    for tweet in tweets:
        tweet_id = tweet["id"]
        tweet_text = tweet.get("text", "")
        author_handle = tweet.get("author_handle", "unknown").lower().lstrip("@")

        # Skip our own tweets and known bot handles
        if author_handle in SKIP_HANDLES:
            logger.debug(f"QT skip: @{author_handle} is in SKIP_HANDLES")
            continue

        # Skip retweets (native RTs show as "RT @...")
        if tweet_text.startswith("RT @"):
            logger.debug(f"QT skip {tweet_id}: retweet")
            continue

        # Age gate
        if not _is_recent_tweet(tweet, _QT_GLAZER_WINDOW_MINUTES):
            age = _tweet_age_minutes(tweet)
            age_str = f"{age:.1f}" if age is not None else "no timestamp"
            logger.info(f"QT skip {tweet_id}: too old ({age_str} min, max {_QT_GLAZER_WINDOW_MINUTES})")
            continue

        # Ecosystem relevance filter
        if not _is_qt_glazer_relevant(tweet_text):
            logger.debug(f"QT skip {tweet_id}: not ecosystem-relevant — {tweet_text[:60]}")
            continue

        # Atomic dedup claim
        if not try_claim_quote(tweet_id):
            logger.debug(f"QT skip {tweet_id}: already claimed")
            continue

        logger.info(f"QT Glazer: quoting @{author_handle} ({tweet_id}): {tweet_text[:80]}")

        token_data = None
        try:
            token_data = _get_tweet_token_data(tweet_text)
            if token_data:
                logger.info(f"QT token data: {token_data.get('name')} mc={token_data.get('market_cap')}")
        except Exception as e:
            logger.warning(f"QT token lookup failed for {tweet_id}: {e}")

        try:
            quote_text = generate_quote_tweet(
                tweet_text,
                author_handle,
                token_data=token_data,
                ecosystem_comparative=_latest_comparative_context,
                dune_context=_latest_dune_context,
            )
        except Exception as e:
            logger.error(f"QT Claude error for {tweet_id}: {e}")
            continue

        qt_tweet_id = None
        if DRY_RUN:
            logger.info(f"[DRY RUN] QT Glazer @{author_handle}: {quote_text[:80]}...")
        else:
            qt_tweet_id = post_quote_tweet(quote_text, tweet_id)
            if qt_tweet_id == QUOTE_TWEET_FORBIDDEN:
                logger.warning(f"QT forbidden for {tweet_id}: Twitter rejected quote — skipping this tweet")
                continue
            if not qt_tweet_id:
                logger.warning(f"QT post failed for {tweet_id}")
                continue
            logger.info(f"QT Glazer posted: {qt_tweet_id} — {quote_text[:60]}...")

        record_quote_tweet(
            tweet_id=tweet_id,
            author_handle=author_handle,
            tweet_text=tweet_text,
            quote_text=quote_text,
            qt_tweet_id=qt_tweet_id,
            dry_run=DRY_RUN,
        )


ECOSYSTEM_ACCOUNTS = ["masterprintr", "printr"]
ECOSYSTEM_REFRESH_INTERVAL_HOURS = 6


def refresh_ecosystem_context():
    """Fetch recent tweets from @printr and @masterprintr and store in DB."""
    age = get_ecosystem_context_age_hours()
    if age is not None and age < ECOSYSTEM_REFRESH_INTERVAL_HOURS:
        logger.info(f"Ecosystem context is {age:.1f}h old — skipping refresh (interval={ECOSYSTEM_REFRESH_INTERVAL_HOURS}h)")
        return

    logger.info("Refreshing ecosystem context from @printr and @masterprintr...")
    total_stored = 0
    for handle in ECOSYSTEM_ACCOUNTS:
        try:
            tweets = fetch_user_tweets(handle, max_results=20)
            for t in tweets:
                store_ecosystem_tweet(
                    tweet_id=t["id"],
                    author_handle=t["author_handle"],
                    text=t["text"],
                    tweet_created_at=t["created_at"],
                    likes=t.get("likes", 0),
                    retweets=t.get("retweets", 0),
                )
            total_stored += len(tweets)
        except Exception as e:
            logger.error(f"Ecosystem context refresh failed for @{handle}: {e}")
    logger.info(f"Ecosystem context refreshed — stored {total_stored} tweets total")


async def refresh_top_tickers(max_attempts: int = 3):
    """Fetch current top tokens by market cap from Printr and cache for keyword scanning."""
    global _latest_projects, _latest_dune_context, _latest_comparative_context
    logger.info("Refreshing top tickers from Printr marketplace...")
    delays = [30, 60]
    for attempt in range(max_attempts):
        try:
            projects = await scrape_all_data()
            if projects:
                _latest_projects = projects
                # Extract and cache ecosystem-level context
                _latest_dune_context = projects[0].pop("_dune_context", "") or ""
                comp_stats = projects[0].pop("_comparative_stats", {}) or {}
                _latest_comparative_context = format_comparative_context(comp_stats)
                top = _get_top_tickers(10)
                logger.info(f"Top tickers updated: {', '.join(f'${t.upper()}' for t in top)}")
                return
            logger.warning(f"refresh_top_tickers: scrape returned empty (attempt {attempt + 1}/{max_attempts})")
        except Exception as e:
            logger.error(f"refresh_top_tickers error (attempt {attempt + 1}/{max_attempts}): {e}")
        if attempt < max_attempts - 1:
            delay = delays[min(attempt, len(delays) - 1)]
            logger.info(f"refresh_top_tickers: retrying in {delay}s...")
            await asyncio.sleep(delay)
    logger.warning("refresh_top_tickers: all attempts failed — keeping previous cache")


async def post_original_tweet():
    global _latest_projects, _latest_dune_context, _latest_comparative_context
    if is_paused():
        logger.info("Bot paused — skipping original tweet job")
        return

    logger.info("Running original tweet job...")
    try:
        projects = await scrape_all_data()
        _latest_projects = projects  # cache for score card metric lookups

        # Extract ecosystem-level context from the scrape results
        dune_ctx = ""
        comparative_ctx = ""
        if projects:
            dune_ctx = projects[0].pop("_dune_context", "") or ""
            comp_stats = projects[0].pop("_comparative_stats", {}) or {}
            comparative_ctx = format_comparative_context(comp_stats)
            _latest_dune_context = dune_ctx
            _latest_comparative_context = comparative_ctx

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
        top_tickers = _get_top_tickers(10)
        tweet_text = generate_original_tweet(
            market_data=projects,
            memory_context=memory_context,
            top_tickers=top_tickers,
            ecosystem_comparative=comparative_ctx,
            dune_context=dune_ctx,
        )

        img_path = None  # image generation disabled

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

