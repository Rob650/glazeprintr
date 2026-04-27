"""
GlazePrintr historical snapshot tracker.

Stores per-token snapshots every 15 min (piggybacking on the intelligence
refresh). Provides 7-day look-back comparisons for volume, price, and
ecosystem market-cap share.
"""

import logging
from database import db, store_token_snapshot, get_token_snapshots, cleanup_old_snapshots

logger = logging.getLogger(__name__)


def store_snapshots(projects: list[dict]) -> None:
    """Store one snapshot row per token from a scrape cycle."""
    if not projects:
        return
    stored = 0
    for p in projects:
        ticker = (p.get("name") or "").lower()
        if not ticker:
            continue
        try:
            store_token_snapshot(
                ticker=ticker,
                price=p.get("price"),
                volume_1h=p.get("volume_1h"),
                volume_24h=p.get("volume"),
                market_cap=p.get("market_cap"),
                buys_1h=p.get("buys_1h"),
                sells_1h=p.get("sells_1h"),
                staking_pct=p.get("staking_pct"),
                liquidity=p.get("liquidity"),
            )
            stored += 1
        except Exception as exc:
            logger.debug(f"history_tracker: failed to snapshot {ticker}: {exc}")
    logger.debug(f"history_tracker: stored {stored}/{len(projects)} snapshots")


def get_token_history(ticker: str, hours: int = 168) -> list[dict]:
    """Return the last N hours of snapshots for a single token (default 7 days)."""
    return get_token_snapshots(ticker, hours=hours)


def get_ecosystem_history(hours: int = 168) -> list[dict]:
    """Return hourly-binned total MC and volume across all tracked tokens."""
    from database import get_ecosystem_snapshots
    return get_ecosystem_snapshots(hours=hours)


def compare_to_historical(ticker: str, current: dict) -> dict:
    """
    Compare current token state against its 7-day history.

    Returns a dict with any/all of:
      volume_vs_7d_avg   — current 1h vol as multiple of 7d hourly average
      price_vs_7d_high   — current price % vs 7d high (negative = below high)
      price_vs_7d_low    — current price % vs 7d low (positive = above low)
      mc_rank_change     — change in token's % share of ecosystem MC vs 7d ago
    """
    history = get_token_history(ticker, hours=168)
    if not history:
        return {}

    result: dict = {}

    # Volume vs 7-day average
    vol_samples = [h["volume_1h"] for h in history if h.get("volume_1h") is not None]
    cur_vol = current.get("volume_1h")
    if vol_samples and cur_vol is not None:
        avg = sum(vol_samples) / len(vol_samples)
        if avg > 0:
            result["volume_vs_7d_avg"] = round(cur_vol / avg, 2)

    # Price vs 7-day high/low
    prices = [h["price"] for h in history if h.get("price") is not None]
    cur_price = current.get("price")
    if prices and cur_price is not None:
        hi = max(prices)
        lo = min(prices)
        if hi > 0:
            result["price_vs_7d_high"] = round((cur_price / hi - 1) * 100, 1)
        if lo > 0:
            result["price_vs_7d_low"] = round((cur_price / lo - 1) * 100, 1)

    # MC rank change: compare token's ecosystem MC share now vs 7d ago
    if len(history) >= 2:
        old_snap = history[0]
        recent_snap = history[-1]
        old_mc = old_snap.get("market_cap") or 0
        recent_mc = recent_snap.get("market_cap") or 0

        if old_mc > 0 and recent_mc > 0:
            old_time = old_snap["snapshot_at"]
            recent_time = recent_snap["snapshot_at"]
            try:
                with db() as conn:
                    old_eco = conn.execute(
                        """SELECT SUM(market_cap) as total_mc FROM token_snapshots
                           WHERE snapshot_at BETWEEN datetime(?, '-15 minutes')
                                               AND datetime(?, '+15 minutes')""",
                        (old_time, old_time)
                    ).fetchone()
                    recent_eco = conn.execute(
                        """SELECT SUM(market_cap) as total_mc FROM token_snapshots
                           WHERE snapshot_at BETWEEN datetime(?, '-15 minutes')
                                               AND datetime(?, '+15 minutes')""",
                        (recent_time, recent_time)
                    ).fetchone()

                old_eco_mc = (old_eco["total_mc"] or 0) if old_eco else 0
                recent_eco_mc = (recent_eco["total_mc"] or 0) if recent_eco else 0

                if old_eco_mc > 0 and recent_eco_mc > 0:
                    old_share = old_mc / old_eco_mc * 100
                    recent_share = recent_mc / recent_eco_mc * 100
                    result["mc_rank_change"] = round(recent_share - old_share, 4)
            except Exception as exc:
                logger.debug(f"compare_to_historical mc_rank_change failed for {ticker}: {exc}")

    return result


def format_historical_comparisons(projects: list[dict]) -> str:
    """
    Run compare_to_historical for all tokens and return a compact
    prompt-injectable block for Claude. Only includes notable findings.
    """
    lines: list[str] = []

    for p in projects:
        ticker = (p.get("name") or "").lower()
        if not ticker:
            continue
        try:
            comp = compare_to_historical(ticker, p)
        except Exception:
            continue
        if not comp:
            continue

        parts: list[str] = []
        vol_mult = comp.get("volume_vs_7d_avg")
        if vol_mult is not None and vol_mult >= 2.0:
            parts.append(f"vol={vol_mult:.1f}x 7d-avg")
        ph = comp.get("price_vs_7d_high")
        pl = comp.get("price_vs_7d_low")
        if ph is not None and ph < -30:
            parts.append(f"price {ph:+.0f}% off 7d-high")
        elif pl is not None and pl > 30:
            parts.append(f"price {pl:+.0f}% off 7d-low")
        mc_chg = comp.get("mc_rank_change")
        if mc_chg is not None and abs(mc_chg) >= 0.5:
            direction = "gaining" if mc_chg > 0 else "losing"
            parts.append(f"{direction} ecosystem MC share ({mc_chg:+.2f}pp)")

        if parts:
            lines.append(f"  ${ticker.upper()}: {' | '.join(parts)}")

    if not lines:
        return ""
    return "7-DAY HISTORICAL SIGNALS:\n" + "\n".join(lines)
