import json
import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "glazeprintr.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode: concurrent readers don't block on a writer, reducing BUSY errors
    # when API-trigger threads and the scheduler overlap.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        # Migrate existing databases that predate the staking_pct column
        try:
            conn.execute("ALTER TABLE memory_project_data ADD COLUMN staking_pct REAL")
            conn.commit()
        except Exception:
            pass  # Column already exists

        # Migrate: add our_reply_tweet_id column to replied_tweets
        try:
            conn.execute("ALTER TABLE replied_tweets ADD COLUMN our_reply_tweet_id TEXT")
            conn.commit()
        except Exception:
            pass  # Column already exists

        # Migrate: add ecosystem_tweets table if missing
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ecosystem_tweets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tweet_id TEXT UNIQUE,
                author_handle TEXT,
                text TEXT,
                tweet_created_at TEXT,
                likes INTEGER DEFAULT 0,
                retweets INTEGER DEFAULT 0,
                fetched_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS replied_tweets (
                tweet_id TEXT PRIMARY KEY,
                author_id TEXT,
                author_handle TEXT,
                tweet_text TEXT,
                reply_text TEXT,
                mode TEXT,
                dry_run INTEGER DEFAULT 0,
                our_reply_tweet_id TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('paused', 'false');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('stream_status', 'disconnected');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('replies_today', '0');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('replies_today_date', '');

            CREATE TABLE IF NOT EXISTS memory_tweets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tweet_id TEXT UNIQUE,
                author_handle TEXT,
                summary TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS memory_project_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT,
                contract_address TEXT,
                price REAL,
                volume REAL,
                market_cap REAL,
                price_change_24h REAL,
                liquidity REAL,
                staking_pct REAL,
                source TEXT DEFAULT 'dexscreener',
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS memory_narratives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                narrative TEXT,
                strength INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS original_tweets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tweet_id TEXT,
                tweet_text TEXT,
                dry_run INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS glaze_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tweet_id TEXT UNIQUE,
                author_handle TEXT,
                score INTEGER,
                tier TEXT,
                commentary TEXT,
                quote_tweet_id TEXT,
                dry_run INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS replied_mentions (
                tweet_id TEXT PRIMARY KEY,
                user_id TEXT,
                responded_at TEXT DEFAULT (datetime('now'))
            );

            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('mentions_since_id', '');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('list_since_id', '');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('keyword_search_since_id', '');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('follower_search_since_id', '');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('qt_glazer_since_id', '');

            CREATE TABLE IF NOT EXISTS qt_glazer_quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tweet_id TEXT UNIQUE,
                author_handle TEXT,
                tweet_text TEXT,
                quote_text TEXT,
                qt_tweet_id TEXT,
                dry_run INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)


# --- replied_tweets ---

def has_replied(tweet_id: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM replied_tweets WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
        return row is not None


def record_reply(tweet_id: str, author_id: str, author_handle: str,
                 tweet_text: str, reply_text: str, mode: str, dry_run: bool,
                 our_reply_tweet_id: str | None = None):
    with db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO replied_tweets
               (tweet_id, author_id, author_handle, tweet_text, reply_text, mode, dry_run, our_reply_tweet_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (tweet_id, author_id, author_handle, tweet_text, reply_text, mode, int(dry_run), our_reply_tweet_id)
        )


def is_reply_to_our_tweet(in_reply_to_tweet_id: str) -> bool:
    """Returns True if in_reply_to_tweet_id is the ID of one of our reply tweets."""
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM replied_tweets WHERE our_reply_tweet_id = ?",
            (in_reply_to_tweet_id,)
        ).fetchone()
        return row is not None


def has_replied_to_author_in_chain(author_id: str, in_reply_to_tweet_id: str | None) -> bool:
    """Returns True if we've already replied to this author AND the current tweet
    is not a direct reply to one of our replies (i.e. they haven't responded to us)."""
    if not author_id:
        return False
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM replied_tweets WHERE author_id = ? LIMIT 1",
            (author_id,)
        ).fetchone()
        if not row:
            return False
    # We have replied to this author before.
    # Only allow if their tweet is a direct reply to one of our replies.
    if in_reply_to_tweet_id and is_reply_to_our_tweet(in_reply_to_tweet_id):
        return False  # They replied back to us — allow the reply
    return True  # We've replied before and they haven't responded to us


def get_recent_replies(limit: int = 20):
    with db() as conn:
        rows = conn.execute(
            """SELECT tweet_id, author_handle, tweet_text, reply_text, mode, dry_run, created_at
               FROM replied_tweets ORDER BY created_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def count_replies_today() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM replied_tweets WHERE created_at LIKE ?",
            (f"{today}%",)
        ).fetchone()
        return row[0] if row else 0


def reset_daily_reply_counter() -> int:
    """Shift today's replied_tweets timestamps to yesterday so the daily cap resets.
    Keeps tweet_ids in DB so the bot won't re-reply to the same tweets."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db() as conn:
        result = conn.execute(
            """UPDATE replied_tweets
               SET created_at = datetime(created_at, '-1 day')
               WHERE created_at LIKE ?""",
            (f"{today}%",)
        )
        return result.rowcount


def count_replies_to_account_last_hour(author_id: str) -> int:
    with db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM replied_tweets
               WHERE author_id = ?
               AND created_at >= datetime('now', '-1 hour')""",
            (author_id,)
        ).fetchone()
        return row[0] if row else 0


# --- bot_state ---

def get_state(key: str) -> str:
    with db() as conn:
        row = conn.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else ""


def set_state(key: str, value: str):
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
            (key, value)
        )


def is_paused() -> bool:
    return get_state("paused") == "true"


def set_paused(paused: bool):
    set_state("paused", "true" if paused else "false")


def set_stream_status(status: str):
    set_state("stream_status", status)


def get_stream_status() -> str:
    return get_state("stream_status")


# --- memory_tweets ---

def store_tweet_summary(tweet_id: str, author_handle: str, summary: str):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO memory_tweets (tweet_id, author_handle, summary) VALUES (?, ?, ?)",
            (tweet_id, author_handle, summary)
        )


def get_recent_tweet_summaries(limit: int = 20) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT tweet_id, author_handle, summary, created_at
               FROM memory_tweets ORDER BY created_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- memory_project_data ---

def store_project_data(project_name: str, contract_address: str, price: float = None,
                       volume: float = None, market_cap: float = None,
                       price_change_24h: float = None, liquidity: float = None,
                       staking_pct: float = None, source: str = "dexscreener"):
    with db() as conn:
        conn.execute(
            """INSERT INTO memory_project_data
               (project_name, contract_address, price, volume, market_cap,
                price_change_24h, liquidity, staking_pct, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_name, contract_address, price, volume, market_cap,
             price_change_24h, liquidity, staking_pct, source)
        )


def get_latest_project_data(limit: int = 20) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT project_name, contract_address, price, volume, market_cap,
                      price_change_24h, liquidity, staking_pct, source, MAX(created_at) as created_at
               FROM memory_project_data
               GROUP BY project_name
               ORDER BY market_cap DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_project_history(project_name: str, hours: int = 24) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT price, volume, market_cap, price_change_24h, liquidity, created_at
               FROM memory_project_data
               WHERE project_name = ? AND created_at >= datetime('now', ?)
               ORDER BY created_at DESC""",
            (project_name, f"-{hours} hours")
        ).fetchall()
        return [dict(r) for r in rows]


# --- memory_narratives ---

def store_narrative(narrative: str, strength: int = 1):
    with db() as conn:
        conn.execute(
            "INSERT INTO memory_narratives (narrative, strength) VALUES (?, ?)",
            (narrative, strength)
        )


def get_recent_narratives(limit: int = 10) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT narrative, SUM(strength) as total_strength, MAX(created_at) as last_seen
               FROM memory_narratives
               WHERE created_at >= datetime('now', '-24 hours')
               GROUP BY narrative
               ORDER BY total_strength DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# --- original_tweets ---

def record_original_tweet(tweet_id: str, tweet_text: str, dry_run: bool):
    with db() as conn:
        conn.execute(
            "INSERT INTO original_tweets (tweet_id, tweet_text, dry_run) VALUES (?, ?, ?)",
            (tweet_id, tweet_text, int(dry_run))
        )


# --- glaze_scores ---

def has_scored(tweet_id: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM glaze_scores WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
        return row is not None


def record_score(tweet_id: str, author_handle: str, score: int, tier: str,
                 commentary: str, quote_tweet_id: str | None, dry_run: bool):
    with db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO glaze_scores
               (tweet_id, author_handle, score, tier, commentary, quote_tweet_id, dry_run)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tweet_id, author_handle, score, tier, commentary, quote_tweet_id, int(dry_run))
        )


def count_scores_today() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM glaze_scores WHERE created_at LIKE ?",
            (f"{today}%",)
        ).fetchone()
        return row[0] if row else 0


# --- replied_mentions ---

def has_replied_mention(tweet_id: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM replied_mentions WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
        return row is not None


def record_replied_mention(tweet_id: str, user_id: str):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO replied_mentions (tweet_id, user_id) VALUES (?, ?)",
            (tweet_id, user_id)
        )


def try_claim_mention(tweet_id: str, user_id: str) -> bool:
    """Atomically claim a tweet for processing. Returns True only for the first caller."""
    with db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO replied_mentions (tweet_id, user_id) VALUES (?, ?)",
            (tweet_id, user_id)
        )
        return cursor.rowcount == 1


def try_claim_reply(tweet_id: str) -> bool:
    """Atomically pre-claim a slot in replied_tweets before posting.
    Returns True if safe to post, False if already replied."""
    with db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO replied_tweets (tweet_id) VALUES (?)",
            (tweet_id,)
        )
        return cursor.rowcount == 1


# --- mentions since_id persistence ---

def get_mentions_since_id() -> str | None:
    val = get_state("mentions_since_id")
    return val if val else None


def set_mentions_since_id(since_id: str):
    set_state("mentions_since_id", since_id)


# --- list since_id persistence ---

def get_list_since_id() -> str | None:
    val = get_state("list_since_id")
    return val if val else None


def set_list_since_id(since_id: str):
    set_state("list_since_id", since_id)


# --- keyword search since_id persistence ---

def get_keyword_search_since_id() -> str | None:
    val = get_state("keyword_search_since_id")
    return val if val else None


def set_keyword_search_since_id(since_id: str):
    set_state("keyword_search_since_id", since_id)


# --- follower search since_id persistence ---

def get_follower_search_since_id() -> str | None:
    val = get_state("follower_search_since_id")
    return val if val else None


def set_follower_search_since_id(since_id: str):
    set_state("follower_search_since_id", since_id)


# --- qt_glazer_since_id persistence ---

def get_qt_glazer_since_id() -> str | None:
    val = get_state("qt_glazer_since_id")
    return val if val else None


def set_qt_glazer_since_id(since_id: str):
    set_state("qt_glazer_since_id", since_id)


# --- qt_glazer_quotes (dedup + record for QT Glazer list) ---

def try_claim_quote(tweet_id: str) -> bool:
    """Atomically claim a tweet for quote-tweeting. Returns True only for the first caller."""
    with db() as conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO qt_glazer_quotes (tweet_id) VALUES (?)",
            (tweet_id,)
        )
        return cursor.rowcount == 1


def record_quote_tweet(tweet_id: str, author_handle: str, tweet_text: str,
                       quote_text: str, qt_tweet_id: str | None, dry_run: bool):
    with db() as conn:
        conn.execute(
            """UPDATE qt_glazer_quotes
               SET author_handle=?, tweet_text=?, quote_text=?, qt_tweet_id=?, dry_run=?
               WHERE tweet_id=?""",
            (author_handle, tweet_text[:500], quote_text, qt_tweet_id, int(dry_run), tweet_id)
        )


# --- recent openers tracking (prevents repeated opening words) ---

def get_recent_openers(limit: int = 8) -> list[str]:
    val = get_state("recent_openers")
    if not val:
        return []
    try:
        openers = json.loads(val)
        return openers[-limit:] if len(openers) > limit else openers
    except (json.JSONDecodeError, TypeError):
        return []


def add_opener(word: str, keep: int = 8):
    openers = get_recent_openers(keep)
    if word and word not in openers:
        openers.append(word)
    if len(openers) > keep:
        openers = openers[-keep:]
    set_state("recent_openers", json.dumps(openers))


# --- ecosystem_tweets (context from @printr and @masterprintr) ---

def store_ecosystem_tweet(tweet_id: str, author_handle: str, text: str,
                          tweet_created_at: str, likes: int = 0, retweets: int = 0):
    with db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO ecosystem_tweets
               (tweet_id, author_handle, text, tweet_created_at, likes, retweets)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tweet_id, author_handle, text, tweet_created_at, likes, retweets)
        )


def get_ecosystem_tweets(limit: int = 40) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT tweet_id, author_handle, text, tweet_created_at, likes, retweets, fetched_at
               FROM ecosystem_tweets
               ORDER BY fetched_at DESC, tweet_created_at DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_ecosystem_context_age_hours() -> float | None:
    """Returns how many hours ago ecosystem tweets were last fetched, or None if never."""
    with db() as conn:
        row = conn.execute(
            "SELECT fetched_at FROM ecosystem_tweets ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
        except (ValueError, TypeError):
            return None
