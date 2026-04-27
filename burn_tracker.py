"""
Buyback & Burn tracker for the Printr ecosystem.

Polls /v1/telecoin/buyback-burn-detail per known token, stores new burn
events in the DB, and provides summary helpers for tweet generation.

Feature flag: ENABLE_BURN_TRACKING
"""
import logging
from database import record_burn_event, db

logger = logging.getLogger(__name__)


def detect_new_burns() -> int:
    """
    Fetch burn history for every known token and store events not yet in DB.
    Returns count of newly discovered burns.
    """
    from scraper import KNOWN_CONTRACTS
    from printr_api import fetch_buyback_burns

    new_count = 0
    for ticker, mint_address in KNOWN_CONTRACTS.items():
        try:
            burns = fetch_buyback_burns(mint_address)
        except Exception as e:
            logger.warning(f"fetch_buyback_burns failed for {ticker}: {e}")
            continue

        for burn in burns:
            tx_hash = burn.get("tx", "")
            if not tx_hash:
                continue
            burned_amount = float(burn.get("burned", 0) or 0)
            bought_back_amount = float(burn.get("bought_back", 0) or 0)
            block_timestamp = str(burn.get("block_timestamp", "") or "")
            try:
                if record_burn_event(ticker, mint_address, tx_hash,
                                     burned_amount, bought_back_amount, block_timestamp):
                    new_count += 1
                    logger.info(f"New burn detected: ${ticker.upper()} tx={tx_hash[:16]}... burned={burned_amount:,.0f}")
            except Exception as e:
                logger.warning(f"record_burn_event failed for {ticker} tx={tx_hash}: {e}")

    if new_count:
        logger.info(f"detect_new_burns: found {new_count} new burn event(s)")
    return new_count


def get_burn_summary(ticker: str) -> dict:
    """Return aggregate burn stats for a single ticker."""
    with db() as conn:
        row = conn.execute(
            """SELECT
               COUNT(*) as burn_count,
               COALESCE(SUM(burned_amount), 0) as total_burned,
               COALESCE(SUM(bought_back_amount), 0) as total_bought_back,
               MAX(block_timestamp) as last_burn_at
               FROM burn_events WHERE ticker = ?""",
            (ticker.lower(),)
        ).fetchone()
    return dict(row) if row else {}


def get_recent_burns(hours: int = 24) -> list[dict]:
    """Return all burn events across the ecosystem detected in the last N hours."""
    with db() as conn:
        rows = conn.execute(
            """SELECT ticker, burned_amount, bought_back_amount, block_timestamp, tx_hash
               FROM burn_events
               WHERE detected_at >= datetime('now', ?)
               ORDER BY detected_at DESC""",
            (f"-{hours} hours",)
        ).fetchall()
    return [dict(r) for r in rows]
