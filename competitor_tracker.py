"""
GlazePrintr Competitor Comparison Engine.

Tracks pump.fun and LetsBonk recent token launch/rug statistics using DexScreener.
Provides formatted competitor context for injection into original tweet generation.

Feature flag: ENABLE_COMPETITOR_DATA (default: false)
Refresh: every 2 hours via scheduler.
"""

import logging
import os
import time
from datetime import datetime, timezone

from scraper import _fetch_url_sync

logger = logging.getLogger(__name__)

ENABLE_COMPETITOR_DATA = os.environ.get("ENABLE_COMPETITOR_DATA", "false").lower() == "true"

_DEXSCREENER_SEARCH = "https://api.dexscreener.com/latest/dex/search?q={}"

_PLATFORMS = {
    "pumpfun": "pump",
    "letsbonk": "letsbonk",
}

# DexScreener dexId substrings that indicate a pair is genuinely on that platform
# (either the native AMM or post-graduation on Raydium). Filters out unrelated results
# that happen to match the search keyword but trade on unrelated DEXes.
_PLATFORM_DEX_FILTERS: dict[str, set[str]] = {
    "pumpfun":   {"pump", "raydium"},
    "letsbonk":  {"letsbonk", "bonk", "raydium"},
}

_MIN_CREATION_LIQUIDITY_USD = 100.0  # exclude dust/test pairs

_RUG_PRICE_DROP_PCT = -90.0
_RUG_MIN_LIQUIDITY = 50.0
_RUG_MIN_AGE_HOURS = 0.5


def _default_stats() -> dict:
    return {"launches_24h": 0, "rugs_24h": 0, "avg_survival_hours": 0.0, "survival_rate_pct": 100.0}


def _analyze_platform_launches(search_term: str, platform_key: str = "") -> dict:
    """Query DexScreener for recent Solana launches matching the search term.

    NOTE: DexScreener's search endpoint returns any pair whose name/symbol/address
    contains the query string — results are NOT restricted to a single platform. We
    apply strict dexId + recency + minimum liquidity filters to exclude unrelated
    pairs that happen to share a keyword.
    """
    url = _DEXSCREENER_SEARCH.format(search_term)
    data = _fetch_url_sync(url, timeout=10)
    if not data:
        return _default_stats()

    pairs = data.get("pairs") or []
    # 48h window — wider than 24h to capture slower-moving rug patterns
    cutoff_ms = (time.time() - 48 * 3600) * 1000

    dex_filters = _PLATFORM_DEX_FILTERS.get(platform_key, set())

    recent: list[dict] = []
    for p in pairs:
        if p.get("chainId") != "solana":
            continue
        if (p.get("pairCreatedAt") or 0) <= cutoff_ms:
            continue
        # Only count pairs genuinely on this platform's AMM or Raydium (post-graduation)
        dex_id = (p.get("dexId") or "").lower()
        if dex_filters and not any(f in dex_id for f in dex_filters):
            continue
        # Exclude test/dust pairs with no meaningful liquidity at creation
        liq_usd = float((p.get("liquidity") or {}).get("usd") or 0)
        if liq_usd < _MIN_CREATION_LIQUIDITY_USD:
            continue
        recent.append(p)

    if not recent:
        return _default_stats()

    rugs = 0
    survival_hours: list[float] = []

    for pair in recent:
        change_24h = float((pair.get("priceChange") or {}).get("h24") or 0)
        liq = float((pair.get("liquidity") or {}).get("usd") or 0)
        created_ms = pair.get("pairCreatedAt") or 0
        age_hours = (time.time() * 1000 - created_ms) / 3_600_000 if created_ms else 48.0

        is_rug = (change_24h <= _RUG_PRICE_DROP_PCT) or (liq < _RUG_MIN_LIQUIDITY and age_hours >= _RUG_MIN_AGE_HOURS)
        if is_rug:
            rugs += 1
        else:
            survival_hours.append(min(age_hours, 48.0))

    launches_24h = len(recent)
    avg_survival = sum(survival_hours) / len(survival_hours) if survival_hours else 0.0
    survival_rate = (launches_24h - rugs) / launches_24h * 100 if launches_24h > 0 else 100.0

    return {
        "launches_24h": launches_24h,
        "rugs_24h": rugs,
        "avg_survival_hours": round(avg_survival, 1),
        "survival_rate_pct": round(survival_rate, 1),
    }


def refresh_competitor_stats() -> None:
    """Fetch and store current competitor rug stats. Called every 2 hours."""
    if not ENABLE_COMPETITOR_DATA:
        return

    from database import upsert_competitor_stats

    today = datetime.now(timezone.utc).date().isoformat()

    for platform, search_term in _PLATFORMS.items():
        try:
            stats = _analyze_platform_launches(search_term, platform_key=platform)
            upsert_competitor_stats(
                platform=platform,
                date=today,
                launches_24h=stats["launches_24h"],
                rugs_24h=stats["rugs_24h"],
                avg_survival_hours=stats["avg_survival_hours"],
                survival_rate_pct=stats["survival_rate_pct"],
            )
            rug_rate = 100 - stats["survival_rate_pct"]
            logger.info(
                f"competitor_tracker: {platform} — {stats['launches_24h']} launches, "
                f"{stats['rugs_24h']} rugs ({rug_rate:.1f}% rug rate), "
                f"avg survival {stats['avg_survival_hours']:.1f}h"
            )
        except Exception as e:
            logger.warning(f"competitor_tracker: failed for {platform}: {e}")


def get_competitor_comparison() -> str:
    """Return formatted competitor context string for injection into Claude prompts."""
    if not ENABLE_COMPETITOR_DATA:
        return ""

    from database import get_latest_competitor_stats

    lines: list[str] = []

    pump = get_latest_competitor_stats("pumpfun")
    bonk = get_latest_competitor_stats("letsbonk")

    if not pump and not bonk:
        return ""

    lines.append("COMPETITOR LAUNCH DATA (recent 24h — use for contrast, not as primary topic):")

    if pump and pump["launches_24h"] > 0:
        rug_rate = 100 - pump["survival_rate_pct"]
        lines.append(
            f"  pump.fun: {pump['launches_24h']} new tokens launched — "
            f"{pump['rugs_24h']} effectively dead ({rug_rate:.0f}% rug rate), "
            f"avg survival {pump['avg_survival_hours']:.1f}h"
        )

    if bonk and bonk["launches_24h"] > 0:
        rug_rate = 100 - bonk["survival_rate_pct"]
        lines.append(
            f"  letsbonk: {bonk['launches_24h']} new tokens launched — "
            f"{bonk['rugs_24h']} effectively dead ({rug_rate:.0f}% rug rate), "
            f"avg survival {bonk['avg_survival_hours']:.1f}h"
        )

    lines.append(
        "  Printr contrast: POB staking requires creator skin-in-the-game, "
        "anti-vamp 48h lock, LP auto-locks on graduation — structurally rug-resistant by design."
    )

    return "\n".join(lines)
