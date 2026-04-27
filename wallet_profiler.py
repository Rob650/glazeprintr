"""
GlazePrintr smart wallet profiler.

Calls the Printr staking API (list-positions-with-rewards) for every known
token, aggregates positions by wallet address, and classifies wallets into
conviction tiers:
  - max_conviction  : 180d stakers across 2+ tokens
  - whale_staker    : largest total staked USD
  - smart_accumulator: new staking positions in the last 7 days

Controlled by ENABLE_WALLET_PROFILING env var (default: false).
Refresh interval: every 6 hours (wired in main.py).
"""

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

ENABLE_WALLET_PROFILING = os.environ.get("ENABLE_WALLET_PROFILING", "false").lower() == "true"

_LOCK_MULTIPLIERS = {7: 1.0, 14: 1.15, 30: 1.3, 60: 1.5, 90: 1.75, 180: 2.5}


def _extract_wallet_address(pos: dict) -> str | None:
    """Try common field patterns to extract a wallet address from a position."""
    for key in ("staker", "wallet", "wallet_address", "owner", "staker_address", "account"):
        val = pos.get(key)
        if val and isinstance(val, str):
            # Handle CAIP-2 format: "solana:chainId:address"
            parts = val.split(":")
            return parts[2] if len(parts) == 3 else val
    return None


def _extract_lock_days(pos: dict) -> int:
    """Extract lock duration in days from a position."""
    raw = (
        pos.get("lock_duration_days")
        or pos.get("duration_days")
        or (pos.get("lock") or {}).get("duration_days")
        or (pos.get("lock") or {}).get("days")
        or 0
    )
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


def _compute_conviction_score(max_lock_days: int, token_count: int, total_usd: float,
                               whale_threshold_usd: float) -> int:
    """Compute a 0–100 conviction score for a wallet."""
    # Lock duration component (0–40)
    if max_lock_days >= 180:
        lock_score = 40
    elif max_lock_days >= 90:
        lock_score = 30
    elif max_lock_days >= 60:
        lock_score = 20
    elif max_lock_days >= 30:
        lock_score = 15
    elif max_lock_days >= 14:
        lock_score = 10
    else:
        lock_score = 5

    # Multi-token diversification (0–30)
    multi_score = min((token_count - 1) * 10, 30)

    # Whale size component (0–30)
    if whale_threshold_usd > 0 and total_usd >= whale_threshold_usd:
        whale_score = 30
    elif whale_threshold_usd > 0 and total_usd >= whale_threshold_usd * 0.5:
        whale_score = 15
    else:
        whale_score = 0

    return min(lock_score + multi_score + whale_score, 100)


def refresh_wallet_profiles(projects: list[dict] | None = None) -> None:
    """
    Fetch staking positions for all known tokens, aggregate by wallet,
    classify, and persist to wallet_profiles table.

    projects: optional list of current token data dicts (for USD price lookups).
    """
    if not ENABLE_WALLET_PROFILING:
        return

    from scraper import KNOWN_CONTRACTS
    from printr_api import fetch_positions_with_rewards
    from database import upsert_wallet_profile

    # Build price lookup from current projects (for USD computation)
    price_map: dict[str, float] = {}
    if projects:
        for p in projects:
            name = (p.get("name") or "").lower()
            price = p.get("price") or 0.0
            if name and price:
                price_map[name] = price

    # wallet_address → {tokens: {ticker: {staked_tokens, lock_days}}, first_seen: str}
    wallet_data: dict[str, dict] = {}

    logger.info(f"wallet_profiler: scanning {len(KNOWN_CONTRACTS)} tokens...")
    for ticker, mint_address in KNOWN_CONTRACTS.items():
        try:
            positions = fetch_positions_with_rewards(mint_address)
        except Exception as exc:
            logger.debug(f"wallet_profiler: fetch failed for {ticker}: {exc}")
            continue

        for pos in positions:
            if pos.get("was_closed"):
                continue
            wallet = _extract_wallet_address(pos)
            if not wallet:
                continue

            lock_days = _extract_lock_days(pos)
            try:
                atomic = int((pos.get("staked") or {}).get("atomic", 0))
                decimals = int((pos.get("staked") or {}).get("decimals", 9))
                staked_tokens = atomic / (10 ** decimals)
            except (ValueError, TypeError):
                staked_tokens = 0.0

            if wallet not in wallet_data:
                wallet_data[wallet] = {"tokens": {}, "first_seen": None}

            entry = wallet_data[wallet]
            existing = entry["tokens"].get(ticker, {"staked_tokens": 0.0, "lock_days": 0})
            entry["tokens"][ticker] = {
                "staked_tokens": existing["staked_tokens"] + staked_tokens,
                "lock_days": max(existing["lock_days"], lock_days),
            }

            # Detect first-seen from position timestamps if available
            for ts_key in ("created_at", "staked_at", "start_time", "opened_at"):
                ts = pos.get(ts_key)
                if ts and isinstance(ts, str):
                    if entry["first_seen"] is None or ts < entry["first_seen"]:
                        entry["first_seen"] = ts
                    break

    if not wallet_data:
        logger.info("wallet_profiler: no wallet data found (API may not expose wallet addresses)")
        return

    # Compute USD values and whale threshold (top 10% by staked USD)
    usd_values: list[float] = []
    for wallet, data in wallet_data.items():
        total_usd = 0.0
        for ticker, tok in data["tokens"].items():
            token_price = price_map.get(ticker.lower(), 0.0)
            total_usd += tok["staked_tokens"] * token_price
        data["total_staked_usd"] = total_usd
        usd_values.append(total_usd)

    usd_values.sort(reverse=True)
    whale_threshold = usd_values[max(0, len(usd_values) // 10)] if usd_values else 0.0

    # Seven days ago for accumulator detection
    seven_days_ago = None
    try:
        from datetime import timedelta
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    except Exception:
        pass

    saved = 0
    for wallet, data in wallet_data.items():
        tokens = data["tokens"]
        token_count = len(tokens)
        if token_count == 0:
            continue

        total_usd = data.get("total_staked_usd", 0.0)
        max_lock = max((t["lock_days"] for t in tokens.values()), default=0)
        first_seen = data.get("first_seen")

        # Classify label
        is_max_conviction = max_lock >= 180 and token_count >= 2
        is_whale = total_usd >= whale_threshold and total_usd > 0
        is_accumulator = (
            seven_days_ago is not None
            and first_seen is not None
            and first_seen >= seven_days_ago
        )

        if is_max_conviction:
            label = "max_conviction"
        elif is_whale:
            label = "whale_staker"
        elif is_accumulator:
            label = "smart_accumulator"
        else:
            label = "holder"

        conviction = _compute_conviction_score(max_lock, token_count, total_usd, whale_threshold)
        token_list = sorted(tokens.keys())

        try:
            upsert_wallet_profile(
                wallet_address=wallet,
                label=label,
                tokens_staked=token_list,
                total_staked_usd=total_usd,
                max_lock_days=max_lock,
                conviction_score=conviction,
            )
            saved += 1
        except Exception as exc:
            logger.debug(f"wallet_profiler: upsert failed for {wallet}: {exc}")

    logger.info(
        f"wallet_profiler: processed {len(wallet_data)} wallets, saved {saved} profiles, "
        f"whale_threshold=${whale_threshold:,.0f}"
    )


def get_smart_wallet_summary() -> dict:
    """
    Summarize smart wallet activity for Claude injection.

    Returns:
      max_conviction_count   — number of max-conviction wallets
      max_conviction_tokens  — dict of {ticker: count} for max-conviction concentration
      whale_count            — number of whale stakers
      smart_accumulator_count— number of new stakers in last 7 days
      total_smart_tvl_usd    — total USD staked by non-"holder" wallets
      formatted              — ready-to-inject prompt string
    """
    if not ENABLE_WALLET_PROFILING:
        return {}

    from database import get_wallet_profiles

    all_profiles = get_wallet_profiles()
    if not all_profiles:
        return {}

    max_conviction = [p for p in all_profiles if p["label"] == "max_conviction"]
    whale_stakers  = [p for p in all_profiles if p["label"] == "whale_staker"]
    accumulators   = [p for p in all_profiles if p["label"] == "smart_accumulator"]
    smart_wallets  = [p for p in all_profiles if p["label"] != "holder"]

    # Token concentration for max-conviction wallets
    token_counts: dict[str, int] = {}
    for p in max_conviction:
        for ticker in p.get("tokens_staked") or []:
            token_counts[ticker] = token_counts.get(ticker, 0) + 1

    top_tokens = sorted(token_counts.items(), key=lambda x: x[1], reverse=True)[:5]
    total_smart_tvl = sum(p.get("total_staked_usd", 0) for p in smart_wallets)

    summary = {
        "max_conviction_count": len(max_conviction),
        "max_conviction_tokens": dict(top_tokens),
        "whale_count": len(whale_stakers),
        "smart_accumulator_count": len(accumulators),
        "total_smart_tvl_usd": total_smart_tvl,
    }

    # Build prompt-injectable string
    parts: list[str] = []
    if max_conviction:
        token_str = ", ".join(f"${t.upper()} ({c})" for t, c in top_tokens[:3]) if top_tokens else "various"
        parts.append(
            f"Max-conviction stakers (180d, 2+ tokens): {len(max_conviction)} wallets "
            f"concentrated in {token_str}"
        )
    if whale_stakers:
        parts.append(f"Whale stakers: {len(whale_stakers)} wallets (large USD positions)")
    if accumulators:
        parts.append(f"New accumulators (last 7d): {len(accumulators)} wallets entering positions")
    if total_smart_tvl > 0:
        tvl_str = f"${total_smart_tvl/1e6:.2f}M" if total_smart_tvl >= 1e6 else f"${total_smart_tvl:,.0f}"
        parts.append(f"Total smart-wallet TVL: {tvl_str}")

    summary["formatted"] = "\n".join(parts) if parts else ""
    return summary
