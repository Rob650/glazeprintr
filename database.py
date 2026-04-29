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
        # Migrate existing databases that predate the staking_pct column.
        # On a fresh DB the table doesn't exist yet (CREATE TABLE runs below) — that's fine,
        # the CREATE statement already includes the column.
        try:
            conn.execute("ALTER TABLE memory_project_data ADD COLUMN staking_pct REAL")
            conn.commit()
        except sqlite3.OperationalError as e:
            msg = str(e)
            if "already exists" not in msg and "no such table" not in msg:
                raise

        # Migrate: add our_reply_tweet_id column to replied_tweets (same fresh-DB caveat)
        try:
            conn.execute("ALTER TABLE replied_tweets ADD COLUMN our_reply_tweet_id TEXT")
            conn.commit()
        except sqlite3.OperationalError as e:
            msg = str(e)
            if "already exists" not in msg and "no such table" not in msg:
                raise

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
            CREATE TABLE IF NOT EXISTS burn_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                contract_address TEXT,
                tx_hash TEXT UNIQUE,
                burned_amount REAL,
                bought_back_amount REAL,
                block_timestamp TEXT,
                detected_at TEXT DEFAULT CURRENT_TIMESTAMP,
                tweeted_about INTEGER DEFAULT 0,
                tweeted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_burn_detected ON burn_events(detected_at);
        """)
        conn.commit()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS whale_wallets (
                address TEXT PRIMARY KEY,
                label TEXT,
                first_seen TEXT DEFAULT (datetime('now')),
                total_transactions INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS whale_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT,
                token_ticker TEXT,
                action TEXT,
                amount_usd REAL,
                detected_at TEXT DEFAULT (datetime('now')),
                tweeted_about INTEGER DEFAULT 0,
                tweeted_at TEXT,
                UNIQUE(wallet_address, token_ticker)
            );
            CREATE INDEX IF NOT EXISTS idx_whale_tx_detected ON whale_transactions(detected_at);

            CREATE TABLE IF NOT EXISTS competitor_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT,
                date TEXT,
                launches_24h INTEGER DEFAULT 0,
                rugs_24h INTEGER DEFAULT 0,
                avg_survival_hours REAL DEFAULT 0,
                survival_rate_pct REAL DEFAULT 0,
                recorded_at TEXT DEFAULT (datetime('now')),
                UNIQUE(platform, date)
            );
        """)
        conn.commit()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS detected_launches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address TEXT UNIQUE,
                ticker TEXT,
                name TEXT,
                detected_at TEXT DEFAULT (datetime('now')),
                first_price REAL,
                first_liquidity REAL,
                fee_model TEXT,
                tweeted_about INTEGER DEFAULT 0,
                tweeted_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_launches_detected ON detected_launches(detected_at);
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
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('last_ticker', '');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('recent_tickers', '[]');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('recent_openers', '');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('recent_topics', '[]');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('last_post_time', '');

            CREATE INDEX IF NOT EXISTS idx_replied_created ON replied_tweets(created_at);
            CREATE INDEX IF NOT EXISTS idx_glaze_created ON glaze_scores(created_at);

            DELETE FROM memory_project_data WHERE created_at < datetime('now', '-7 days');

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

            CREATE TABLE IF NOT EXISTS thread_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                first_tweet_id TEXT,
                ticker TEXT,
                thread_type TEXT,
                dry_run INTEGER DEFAULT 0,
                posted_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tweet_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tweet_id TEXT UNIQUE,
                posted_hour_utc INTEGER,
                posted_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS correlation_events (
                id INTEGER PRIMARY KEY,
                event_type TEXT,
                tokens_involved TEXT,
                magnitude REAL,
                detected_at TEXT DEFAULT (datetime('now')),
                tweeted_about INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS staking_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                staking_pct REAL,
                staked_usd REAL,
                snapshot_at TEXT DEFAULT (datetime('now'))
            );

            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('last_correlation_tweet_time', '');
            INSERT OR IGNORE INTO bot_state (key, value) VALUES ('last_staking_tweet_time', '');
        """)

        # Feature: Historical snapshot system
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS token_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                price REAL,
                volume_1h REAL,
                volume_24h REAL,
                market_cap REAL,
                buys_1h INTEGER,
                sells_1h INTEGER,
                staking_pct REAL,
                liquidity REAL,
                snapshot_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_token_snapshots ON token_snapshots(ticker, snapshot_at);
        """)
        conn.commit()

        # Feature: Smart wallet profiling
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS wallet_profiles (
                wallet_address TEXT UNIQUE,
                label TEXT,
                tokens_staked TEXT DEFAULT '[]',
                total_staked_usd REAL DEFAULT 0,
                max_lock_days INTEGER DEFAULT 0,
                first_seen TEXT DEFAULT (datetime('now')),
                last_seen TEXT DEFAULT (datetime('now')),
                conviction_score INTEGER DEFAULT 0
            );
        """)
        conn.commit()

        # Feature: Auto-claim staking rewards
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reward_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_ticker TEXT,
                position_id TEXT,
                amount_claimed REAL,
                claimed_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_reward_claims_at ON reward_claims(claimed_at);
        """)
        conn.commit()

        # Feature: Research cache — persistent store of URL/domain/topic knowledge gathered during reply research
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS research_cache (
                key TEXT PRIMARY KEY,
                key_type TEXT NOT NULL DEFAULT 'url',
                content TEXT NOT NULL,
                hit_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now')),
                last_used_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_research_cache_type ON research_cache(key_type, last_used_at);
            DELETE FROM research_cache WHERE created_at < datetime('now', '-30 days');
        """)
        conn.commit()

        # Feature: Wallet glazing — bot's own deposit wallet tracking
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS wallet_holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                mint_address TEXT,
                balance REAL DEFAULT 0,
                usd_value REAL DEFAULT 0,
                tier TEXT,
                last_scanned TEXT,
                last_glazed_at TEXT,
                UNIQUE(ticker)
            );

            CREATE TABLE IF NOT EXISTS staking_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT,
                amount REAL,
                lock_days INTEGER,
                tx_hash TEXT,
                status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()


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


# --- last ticker (prevents back-to-back same ticker in original tweets) ---

def get_last_ticker() -> str:
    return get_state("last_ticker")


def set_last_ticker(ticker: str):
    set_state("last_ticker", ticker or "")


# --- recent tickers cooldown (excludes last N tickers from selection) ---

def get_recent_tickers(limit: int = 4) -> list[str]:
    val = get_state("recent_tickers")
    if not val:
        return []
    try:
        tickers = json.loads(val)
        return tickers[-limit:] if len(tickers) > limit else tickers
    except (json.JSONDecodeError, TypeError):
        return []


def set_recent_tickers(tickers: list[str]):
    set_state("recent_tickers", json.dumps(tickers))


def add_recent_ticker(ticker: str, keep: int = 4):
    if not ticker:
        return
    tickers = get_recent_tickers(keep)
    tickers = [t for t in tickers if t != ticker]  # dedupe: move to end instead of duplicating
    tickers.append(ticker)
    if len(tickers) > keep:
        tickers = tickers[-keep:]
    set_state("recent_tickers", json.dumps(tickers))


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


# --- detected_launches (launch detection feature) ---

def record_launch(contract_address: str, ticker: str, name: str,
                  first_price: float | None, first_liquidity: float | None,
                  fee_model: str | None) -> bool:
    """Insert a newly detected launch. Returns True if it was new, False if already recorded."""
    with db() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO detected_launches
               (contract_address, ticker, name, first_price, first_liquidity, fee_model)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (contract_address, ticker, name, first_price, first_liquidity, fee_model)
        )
        return cursor.rowcount == 1


def get_untweeted_launches(hours: int = 2) -> list[dict]:
    """Return launches detected within the last N hours that haven't been tweeted about."""
    with db() as conn:
        rows = conn.execute(
            """SELECT contract_address, ticker, name, detected_at,
                      first_price, first_liquidity, fee_model
               FROM detected_launches
               WHERE tweeted_about = 0
               AND detected_at >= datetime('now', ?)
               ORDER BY detected_at ASC""",
            (f"-{hours} hours",)
        ).fetchall()
        return [dict(r) for r in rows]


def mark_launch_tweeted(contract_address: str):
    """Mark a detected launch as tweeted."""
    with db() as conn:
        conn.execute(
            """UPDATE detected_launches
               SET tweeted_about = 1, tweeted_at = datetime('now')
               WHERE contract_address = ?""",
            (contract_address,)
        )


def count_launch_alerts_last_hour() -> int:
    """Count launch alert tweets posted in the last hour (for rate limiting)."""
    with db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM detected_launches
               WHERE tweeted_about = 1
               AND tweeted_at >= datetime('now', '-1 hour')"""
        ).fetchone()
        return row[0] if row else 0


def upsert_whale_wallet(address: str, label: str) -> None:
    with db() as conn:
        conn.execute(
            """INSERT INTO whale_wallets (address, label) VALUES (?, ?)
               ON CONFLICT(address) DO UPDATE SET total_transactions = total_transactions + 1""",
            (address, label)
        )


def record_whale_transaction(wallet_address: str, token_ticker: str, action: str, amount_usd: float) -> bool:
    """Record a detected whale move. Returns True if new (deduped per wallet+token per hour)."""
    with db() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO whale_transactions
               (wallet_address, token_ticker, action, amount_usd)
               VALUES (?, ?, ?, ?)""",
            (wallet_address, token_ticker, action, amount_usd)
        )
        return cursor.rowcount == 1


def get_untweeted_whale_transactions(limit: int = 5) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT id, wallet_address, token_ticker, action, amount_usd, detected_at
               FROM whale_transactions
               WHERE tweeted_about = 0
               AND detected_at >= datetime('now', '-2 hours')
               ORDER BY amount_usd DESC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def mark_whale_transaction_tweeted(tx_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE whale_transactions SET tweeted_about = 1, tweeted_at = datetime('now') WHERE id = ?",
            (tx_id,)
        )


def count_whale_tweets_last_hour() -> int:
    with db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM whale_transactions
               WHERE tweeted_about = 1
               AND tweeted_at >= datetime('now', '-1 hour')"""
        ).fetchone()
        return row[0] if row else 0


def upsert_competitor_stats(platform: str, date: str, launches_24h: int, rugs_24h: int,
                             avg_survival_hours: float, survival_rate_pct: float) -> None:
    with db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO competitor_stats
               (platform, date, launches_24h, rugs_24h, avg_survival_hours, survival_rate_pct)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (platform, date, launches_24h, rugs_24h, avg_survival_hours, survival_rate_pct)
        )


def get_latest_competitor_stats(platform: str) -> dict | None:
    with db() as conn:
        row = conn.execute(
            """SELECT platform, date, launches_24h, rugs_24h, avg_survival_hours, survival_rate_pct, recorded_at
               FROM competitor_stats WHERE platform = ? ORDER BY recorded_at DESC LIMIT 1""",
            (platform,)
        ).fetchone()
        return dict(row) if row else None


def record_thread_post(first_tweet_id: str, ticker: str, thread_type: str, dry_run: bool):
    with db() as conn:
        conn.execute(
            "INSERT INTO thread_posts (first_tweet_id, ticker, thread_type, dry_run) VALUES (?, ?, ?, ?)",
            (first_tweet_id, ticker, thread_type, int(dry_run))
        )


def count_threads_today() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM thread_posts WHERE posted_at LIKE ?",
            (f"{today}%",)
        ).fetchone()
        return row[0] if row else 0


def record_tweet_performance(tweet_id: str, posted_hour_utc: int):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tweet_performance (tweet_id, posted_hour_utc) VALUES (?, ?)",
            (tweet_id, posted_hour_utc)
        )



def record_correlation_event(event_type: str, tokens_involved: str, magnitude: float) -> int:
    with db() as conn:
        cursor = conn.execute(
            """INSERT INTO correlation_events (event_type, tokens_involved, magnitude)
               VALUES (?, ?, ?)""",
            (event_type, tokens_involved, magnitude)
        )
        return cursor.lastrowid


def mark_correlation_tweeted(event_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE correlation_events SET tweeted_about = 1 WHERE id = ?",
            (event_id,)
        )


def get_last_correlation_tweet_time() -> str:
    return get_state("last_correlation_tweet_time")


def set_last_correlation_tweet_time() -> None:
    set_state("last_correlation_tweet_time", datetime.now(timezone.utc).isoformat())


def record_staking_snapshot(ticker: str, staking_pct: float, staked_usd: float) -> None:
    with db() as conn:
        conn.execute(
            """INSERT INTO staking_snapshots (ticker, staking_pct, staked_usd)
               VALUES (?, ?, ?)""",
            (ticker, staking_pct, staked_usd)
        )


def get_last_staking_tweet_time() -> str:
    return get_state("last_staking_tweet_time")


def set_last_staking_tweet_time() -> None:
    set_state("last_staking_tweet_time", datetime.now(timezone.utc).isoformat())


def record_burn_event(ticker: str, contract_address: str, tx_hash: str,
                      burned_amount: float, bought_back_amount: float,
                      block_timestamp: str) -> bool:
    """Insert a new burn event. Returns True if new (deduped by tx_hash)."""
    with db() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO burn_events
               (ticker, contract_address, tx_hash, burned_amount, bought_back_amount, block_timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticker.lower(), contract_address, tx_hash, burned_amount, bought_back_amount, block_timestamp)
        )
        return cursor.rowcount == 1


def get_untweeted_burns(min_burned: float = 0) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT id, ticker, contract_address, tx_hash,
                      burned_amount, bought_back_amount, block_timestamp
               FROM burn_events
               WHERE tweeted_about = 0 AND burned_amount >= ?
               ORDER BY detected_at ASC""",
            (min_burned,)
        ).fetchall()
        return [dict(r) for r in rows]


def mark_burn_tweeted(burn_id: int) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE burn_events SET tweeted_about = 1, tweeted_at = datetime('now') WHERE id = ?",
            (burn_id,)
        )


def count_burn_tweets_last_hour() -> int:
    with db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM burn_events
               WHERE tweeted_about = 1
               AND tweeted_at >= datetime('now', '-1 hour')"""
        ).fetchone()
        return row[0] if row else 0


def recently_tweeted_about_token(ticker: str, window_hours: int = 2) -> bool:
    """Returns True if any feature tweeted about this ticker within the last window_hours."""
    ticker_lower = ticker.lower()
    ticker_cashtag_lower = f"${ticker_lower}"
    ticker_cashtag_upper = f"${ticker.upper()}"
    window = f"-{window_hours} hours"
    with db() as conn:
        row = conn.execute(
            """SELECT 1 FROM original_tweets
               WHERE (LOWER(tweet_text) LIKE ? OR tweet_text LIKE ?)
               AND created_at >= datetime('now', ?) LIMIT 1""",
            (f"%{ticker_cashtag_lower}%", f"%{ticker_cashtag_upper}%", window)
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            """SELECT 1 FROM whale_transactions
               WHERE LOWER(token_ticker) = ? AND tweeted_about = 1
               AND detected_at >= datetime('now', ?) LIMIT 1""",
            (ticker_lower, window)
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            """SELECT 1 FROM burn_events
               WHERE LOWER(ticker) = ? AND tweeted_about = 1
               AND detected_at >= datetime('now', ?) LIMIT 1""",
            (ticker_lower, window)
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            """SELECT 1 FROM correlation_events
               WHERE LOWER(tokens_involved) LIKE ? AND tweeted_about = 1
               AND detected_at >= datetime('now', ?) LIMIT 1""",
            (f"%{ticker_lower}%", window)
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            """SELECT 1 FROM thread_posts
               WHERE LOWER(ticker) = ?
               AND posted_at >= datetime('now', ?) LIMIT 1""",
            (ticker_lower, window)
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            """SELECT 1 FROM qt_glazer_quotes
               WHERE (LOWER(tweet_text) LIKE ? OR tweet_text LIKE ?)
               AND created_at >= datetime('now', ?) LIMIT 1""",
            (f"%{ticker_cashtag_lower}%", f"%{ticker_cashtag_upper}%", window)
        ).fetchone()
        if row:
            return True
    return False


def get_recent_original_tweets(limit: int = 3) -> list[str]:
    """Return tweet_text of the most recent original tweets (for variety injection)."""
    with db() as conn:
        rows = conn.execute(
            "SELECT tweet_text FROM original_tweets ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [r["tweet_text"] for r in rows]


def get_recent_topics(limit: int = 4) -> list[str]:
    val = get_state("recent_topics")
    if not val:
        return []
    try:
        topics = json.loads(val)
        return topics[-limit:] if len(topics) > limit else topics
    except (json.JSONDecodeError, TypeError):
        return []


def add_recent_topic(topic_key: str, keep: int = 4):
    if not topic_key:
        return
    topics = get_recent_topics(keep)
    topics = [t for t in topics if t != topic_key]
    topics.append(topic_key)
    if len(topics) > keep:
        topics = topics[-keep:]
    set_state("recent_topics", json.dumps(topics))


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


# --- token_snapshots ---

def store_token_snapshot(ticker: str, price: float = None, volume_1h: float = None,
                         volume_24h: float = None, market_cap: float = None,
                         buys_1h: int = None, sells_1h: int = None,
                         staking_pct: float = None, liquidity: float = None):
    with db() as conn:
        conn.execute(
            """INSERT INTO token_snapshots
               (ticker, price, volume_1h, volume_24h, market_cap,
                buys_1h, sells_1h, staking_pct, liquidity)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker.lower(), price, volume_1h, volume_24h, market_cap,
             buys_1h, sells_1h, staking_pct, liquidity)
        )


def get_token_snapshots(ticker: str, hours: int = 168) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT price, volume_1h, volume_24h, market_cap,
                      buys_1h, sells_1h, staking_pct, liquidity, snapshot_at
               FROM token_snapshots
               WHERE ticker = ? AND snapshot_at >= datetime('now', ?)
               ORDER BY snapshot_at ASC""",
            (ticker.lower(), f"-{hours} hours")
        ).fetchall()
        return [dict(r) for r in rows]


def get_ecosystem_snapshots(hours: int = 168) -> list[dict]:
    """Return hourly-binned total MC and volume across all tokens."""
    with db() as conn:
        rows = conn.execute(
            """SELECT
                 strftime('%Y-%m-%d %H:00:00', snapshot_at) as hour,
                 SUM(market_cap) as total_mc,
                 SUM(volume_24h) as total_volume_24h
               FROM token_snapshots
               WHERE snapshot_at >= datetime('now', ?)
               GROUP BY strftime('%Y-%m-%d %H', snapshot_at)
               ORDER BY hour ASC""",
            (f"-{hours} hours",)
        ).fetchall()
        return [dict(r) for r in rows]


def cleanup_old_snapshots(retention_days: int = 30) -> int:
    with db() as conn:
        result = conn.execute(
            "DELETE FROM token_snapshots WHERE snapshot_at < datetime('now', ?)",
            (f"-{retention_days} days",)
        )
        return result.rowcount


# --- wallet_profiles ---

def upsert_wallet_profile(wallet_address: str, label: str, tokens_staked: list,
                          total_staked_usd: float, max_lock_days: int,
                          conviction_score: int):
    with db() as conn:
        conn.execute(
            """INSERT INTO wallet_profiles
               (wallet_address, label, tokens_staked, total_staked_usd,
                max_lock_days, conviction_score, last_seen)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(wallet_address) DO UPDATE SET
                 label=excluded.label,
                 tokens_staked=excluded.tokens_staked,
                 total_staked_usd=excluded.total_staked_usd,
                 max_lock_days=excluded.max_lock_days,
                 conviction_score=excluded.conviction_score,
                 last_seen=datetime('now')""",
            (wallet_address, label, json.dumps(tokens_staked),
             total_staked_usd, max_lock_days, conviction_score)
        )


def get_wallet_profiles(label: str = None) -> list[dict]:
    with db() as conn:
        if label:
            rows = conn.execute(
                "SELECT * FROM wallet_profiles WHERE label = ? ORDER BY conviction_score DESC",
                (label,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM wallet_profiles ORDER BY conviction_score DESC"
            ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["tokens_staked"] = json.loads(d.get("tokens_staked") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["tokens_staked"] = []
            results.append(d)
        return results


# --- wallet_holdings ---

def upsert_wallet_holding(ticker: str, mint_address: str, balance: float,
                          usd_value: float, tier: str | None, last_scanned: str):
    with db() as conn:
        conn.execute(
            """INSERT INTO wallet_holdings
               (ticker, mint_address, balance, usd_value, tier, last_scanned)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                 mint_address=excluded.mint_address,
                 balance=excluded.balance,
                 usd_value=excluded.usd_value,
                 tier=excluded.tier,
                 last_scanned=excluded.last_scanned""",
            (ticker.lower(), mint_address, balance, usd_value, tier, last_scanned)
        )


def get_wallet_holdings() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """SELECT ticker, mint_address, balance, usd_value, tier,
                      last_scanned, last_glazed_at
               FROM wallet_holdings"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_glaze_queue() -> list[dict]:
    """Return wallet holdings with a recognized tier, ordered by priority (legendary first)."""
    with db() as conn:
        rows = conn.execute(
            """SELECT ticker, mint_address, balance, usd_value, tier,
                      last_scanned, last_glazed_at
               FROM wallet_holdings
               WHERE tier IS NOT NULL
               ORDER BY CASE tier
                 WHEN 'legendary' THEN 1
                 WHEN 'gold'      THEN 2
                 WHEN 'silver'    THEN 3
                 WHEN 'bronze'    THEN 4
                 ELSE 5
               END"""
        ).fetchall()
        return [dict(r) for r in rows]


def update_last_glazed(ticker: str):
    with db() as conn:
        conn.execute(
            "UPDATE wallet_holdings SET last_glazed_at = datetime('now') WHERE ticker = ?",
            (ticker.lower(),)
        )


# --- staking_transactions ---

def record_staking_tx(ticker: str, amount: float, lock_days: int,
                      tx_hash: str, status: str):
    with db() as conn:
        conn.execute(
            """INSERT INTO staking_transactions (ticker, amount, lock_days, tx_hash, status)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker.lower(), amount, lock_days, tx_hash or "", status)
        )


def get_confirmed_staked_amounts() -> dict[str, float]:
    """Return {ticker: total_staked_tokens} from confirmed staking transactions in DB."""
    with db() as conn:
        rows = conn.execute(
            """SELECT ticker, SUM(amount) as total
               FROM staking_transactions
               WHERE status = 'confirmed'
               GROUP BY ticker"""
        ).fetchall()
        return {r["ticker"]: r["total"] for r in rows}


# --- reward_claims ---

def record_reward_claim(token_ticker: str, position_id: str, amount_claimed: float):
    with db() as conn:
        conn.execute(
            """INSERT INTO reward_claims (token_ticker, position_id, amount_claimed)
               VALUES (?, ?, ?)""",
            (token_ticker.lower(), position_id, amount_claimed)
        )


# --- research_cache (persistent knowledge gathered during reply research) ---

def store_research_finding(key: str, content: str, key_type: str = "url") -> None:
    """Persist a research finding keyed by URL, domain, or topic.

    Upserts on conflict: updates content and bumps last_used_at but preserves
    hit_count so we can rank high-value cached entries.
    """
    with db() as conn:
        conn.execute(
            """INSERT INTO research_cache (key, key_type, content)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 content=excluded.content,
                 last_used_at=datetime('now')""",
            (key, key_type, content),
        )


def get_research_finding(key: str, max_age_days: int = 7) -> str | None:
    """Return cached content for key if it exists and is younger than max_age_days.

    Increments hit_count so frequently-used entries can be surfaced first.
    Returns None if not found or expired.
    """
    with db() as conn:
        row = conn.execute(
            """SELECT content FROM research_cache
               WHERE key = ? AND created_at >= datetime('now', ?)""",
            (key, f"-{max_age_days} days"),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE research_cache
                   SET hit_count = hit_count + 1, last_used_at = datetime('now')
                   WHERE key = ?""",
                (key,),
            )
            return row[0]
    return None


def get_all_research_findings(limit: int = 30) -> list[dict]:
    """Return all non-expired research findings, most-used first.

    Used by the intelligence layer to inject accumulated knowledge into prompts.
    """
    with db() as conn:
        rows = conn.execute(
            """SELECT key, key_type, content, hit_count, created_at, last_used_at
               FROM research_cache
               WHERE created_at >= datetime('now', '-30 days')
               ORDER BY hit_count DESC, last_used_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
