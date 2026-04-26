"""
One-off test: post a QT Glazer commentary as a regular tweet with the tweet URL appended.
Twitter auto-embeds the linked tweet as a quote in the feed.

Run with: railway run -- .venv/bin/python3 test_qt_link_post.py
"""
import os
import sys
import re
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from database import init_db
from twitter_client import fetch_list_tweets, get_v2_client
from claude_client import generate_quote_tweet

QT_GLAZER_LIST_ID = "2048242501333799282"

# Word-boundary keyword patterns to avoid false positives (e.g. "ooo" inside "lmfaoooo")
_QT_GLAZER_KW_RE = re.compile(
    r'\b(' + '|'.join([
        "printr", "brrr", "belief", "rotus", "deployr", "fatchoi",
        "stakrr", "masterprintr", "glaze", "glazeprintr", "staking",
        r"pob", "noob", "cmyk", "patapim", "marmot", "fsjal", "ket",
        "pve", "ooo", "prinaboratory",
    ]) + r')\b',
    re.IGNORECASE,
)
# "print" and "roi" are too generic — only match as cashtags
_CASHTAG_RE = re.compile(r'\$[a-zA-Z]{2,}')
_CONTRACT_RE = re.compile(r'\b(0x[0-9a-fA-F]{40,}|[1-9A-HJ-NP-Za-km-z]{32,44})\b')
_HTTPS_RE = re.compile(r'https?://\S+', re.IGNORECASE)
_PUMP_FUN_RE = re.compile(r'\bpump\.fun\S*', re.IGNORECASE)
_PRINTR_MONEY_RE = re.compile(r'\bapp\.printr\.money\S*', re.IGNORECASE)


def is_qt_glazer_relevant(text: str) -> bool:
    if _CASHTAG_RE.search(text):
        return True
    if _QT_GLAZER_KW_RE.search(text):
        return True
    return bool(_CONTRACT_RE.search(text))


def clean_commentary(text: str) -> str:
    """Clean the Claude-generated commentary — strip any URLs Claude sneaks in."""
    text = _HTTPS_RE.sub('', text)
    text = _PUMP_FUN_RE.sub('pumpfun', text)
    text = _PRINTR_MONEY_RE.sub('Printr', text)
    text = re.sub(r'[ \t]+', ' ', text).strip()
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


def post_tweet_with_url(commentary: str, tweet_url: str) -> str | None:
    """Post a regular tweet: commentary + tweet URL on a new line."""
    # Twitter counts t.co-wrapped URLs as 23 chars regardless of actual length
    max_commentary_chars = 280 - 23 - 2  # 23 for URL, 2 for "\n\n"
    if len(commentary) > max_commentary_chars:
        commentary = commentary[:max_commentary_chars].rstrip()

    full_text = f"{commentary}\n\n{tweet_url}"

    client = get_v2_client()
    import tweepy
    try:
        response = client.create_tweet(text=full_text)
        tweet_id = response.data["id"]
        logger.info(f"Posted tweet {tweet_id}: {full_text[:80]}...")
        return tweet_id
    except tweepy.TweepyException as e:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        try:
            body = resp.json() if resp else None
        except Exception:
            body = getattr(resp, "text", None)
        logger.error(f"Failed to post tweet (HTTP {status}): {e} | body={body}")
        return None


def main():
    logger.info(f"Fetching tweets from QT Glazer list {QT_GLAZER_LIST_ID}...")
    tweets = fetch_list_tweets(QT_GLAZER_LIST_ID)
    logger.info(f"Fetched {len(tweets)} tweets")

    if not tweets:
        logger.error("No tweets fetched — check API credentials and list ID")
        sys.exit(1)

    # Find first ecosystem-relevant, non-RT, non-pure-URL tweet
    candidate = None
    for tweet in tweets:
        text = tweet.get("text", "")
        if text.startswith("RT @"):
            continue
        # Skip tweets that are only a URL (no real text content)
        stripped = _HTTPS_RE.sub('', text).strip()
        if len(stripped) < 5:
            continue
        if not is_qt_glazer_relevant(text):
            logger.debug(f"  skip (not relevant): @{tweet.get('author_handle')}: {text[:60]}")
            continue
        candidate = tweet
        break

    if not candidate:
        logger.error("No ecosystem-relevant tweet found. Showing all fetched tweets:")
        for t in tweets[:10]:
            logger.info(f"  @{t.get('author_handle')}: {t.get('text', '')[:80]}")
        sys.exit(1)

    tweet_id = candidate["id"]
    author_handle = candidate.get("author_handle", "unknown").lstrip("@")
    tweet_text = candidate.get("text", "")
    tweet_url = f"https://x.com/{author_handle}/status/{tweet_id}"

    logger.info(f"\n=== Candidate tweet ===")
    logger.info(f"  Author: @{author_handle}")
    logger.info(f"  ID: {tweet_id}")
    logger.info(f"  Text: {tweet_text[:120]}")
    logger.info(f"  URL: {tweet_url}")

    logger.info("\nGenerating commentary via Claude...")
    commentary_raw, _ = generate_quote_tweet(tweet_text, author_handle)
    commentary = clean_commentary(commentary_raw)
    logger.info(f"  Commentary ({len(commentary)} chars): {commentary}")

    full_preview = f"{commentary}\n\n{tweet_url}"
    logger.info(f"\n=== Full tweet preview ({len(full_preview)} chars) ===\n{full_preview}\n")

    posted_id = post_tweet_with_url(commentary, tweet_url)
    if posted_id:
        logger.info(f"\nSuccess! Posted tweet ID: {posted_id}")
        logger.info(f"View at: https://x.com/printrglazr/status/{posted_id}")
    else:
        logger.error("Failed to post tweet")
        sys.exit(1)


if __name__ == "__main__":
    init_db()
    main()
