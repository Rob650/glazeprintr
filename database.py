import sqlite3
import os
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "glazeprintr.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
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
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS replied_tweets (
                tweet_id TEXT PRIMARY KEY,
                author_id TEXT,
                author_handle TEXT,
                tweet_text TEXT,
                reply_text TEXT,
                mode TEXT,
                dry_run INTEGER DEFAULT 0,
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
        """)


def has_replied(tweet_id: str) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM replied_tweets WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
        return row is not None


def record_reply(tweet_id: str, author_id: str, author_handle: str,
                 tweet_text: str, reply_text: str, mode: str, dry_run: bool):
    with db() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO replied_tweets
               (tweet_id, author_id, author_handle, tweet_text, reply_text, mode, dry_run)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tweet_id, author_id, author_handle, tweet_text, reply_text, mode, int(dry_run))
        )


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


def count_replies_to_account_last_hour(author_id: str) -> int:
    with db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) FROM replied_tweets
               WHERE author_id = ?
               AND created_at >= datetime('now', '-1 hour')""",
            (author_id,)
        ).fetchone()
        return row[0] if row else 0


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
