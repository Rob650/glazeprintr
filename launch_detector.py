"""
Launch detector for new Printr ecosystem tokens.

Scans DexScreener for Solana tokens with addresses ending in 'brrr'
that are not already in KNOWN_CONTRACTS. Records discoveries to the
detected_launches DB table and generates tweet alerts via Claude.

All public functions are no-ops when ENABLE_LAUNCH_DETECTION is false.
"""
import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import database
from scraper import _fetch_url_sync, DEXSCREENER_SEARCH_API, KNOWN_CONTRACTS
from claude_client import _call_claude, SYSTEM_PROMPT_BASE, _clean_reply

logger = logging.getLogger(__name__)

ENABLE_LAUNCH_DETECTION = os.environ.get("ENABLE_LAUNCH_DETECTION", "false").lower() == "true"

_LAUNCH_ALERT_SYSTEM = SYSTEM_PROMPT_BASE + """
LAUNCH ALERT MODE: A brand new Printr ecosystem token just appeared on-chain. This is breaking news.
Write a launch alert tweet:
- Open with the token being live: "$TICKER just launched", "new drop", "just hit Printr", etc.
- Include initial liquidity if provided
- Mention fee model if provided (POB staking = maximum glaze, buyback & burn = bullish mechanics, etc.)
- One punchy take on why this matters (fresh blood, ecosystem expanding, early mover advantage)
- Close with energy: LFP, gong hei fat choi, or 🖨️
- Under 280 characters. No URLs. No contract addresses. No hashtags except ticker cashtags.
"""


def _scan_dexscreener_for_new_brrr_tokens() -> list[dict]:
    """Sync: scan DexScreener for recently launched Solana tokens with 'brrr' addresses."""
    url = DEXSCREENER_SEARCH_API.format("brrr")
    try:
        data = _fetch_url_sync(url, timeout=10)
        pairs = (data or {}).get("pairs") or []
    except Exception as e:
        logger.warning(f"DexScreener brrr scan failed: {e}")
        return []

    known_addresses = set(KNOWN_CONTRACTS.values())
    cutoff = datetime.now(timezone.utc) - timedelta(hours=2)
    seen: set[str] = set()
    results: list[dict] = []

    for pair in pairs:
        base = pair.get("baseToken") or {}
        address = base.get("address", "")

        if not address.endswith("brrr"):
            continue
        if pair.get("chainId") != "solana":
            continue
        if address in known_addresses:
            continue
        if address in seen:
            continue
        seen.add(address)

        # Only alert on tokens created within the last 2 hours to avoid stale alerts
        created_ms = pair.get("pairCreatedAt")
        if not created_ms:
            continue
        created_dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
        if created_dt < cutoff:
            continue

        ticker = base.get("symbol", "").lower()
        name = base.get("name", "") or ticker
        liquidity = float((pair.get("liquidity") or {}).get("usd") or 0)
        price = float(pair.get("priceUsd") or 0)

        results.append({
            "contract_address": address,
            "ticker": ticker,
            "name": name,
            "first_price": price,
            "first_liquidity": liquidity,
            "fee_model": None,
        })
        logger.info(f"New brrr token found: ${ticker.upper()} ({address}) liq=${liquidity:,.0f}")

    return results


async def detect_new_launches() -> list[dict]:
    """
    Async: scan for new Printr launches and record any not yet in the DB.
    Returns list of newly recorded token dicts (empty list if feature flag is off).
    """
    if not ENABLE_LAUNCH_DETECTION:
        return []

    loop = asyncio.get_running_loop()
    candidates = await loop.run_in_executor(None, _scan_dexscreener_for_new_brrr_tokens)

    new_launches: list[dict] = []
    for token in candidates:
        try:
            is_new = database.record_launch(
                contract_address=token["contract_address"],
                ticker=token["ticker"],
                name=token["name"],
                first_price=token["first_price"],
                first_liquidity=token["first_liquidity"],
                fee_model=token.get("fee_model"),
            )
            if is_new:
                new_launches.append(token)
                logger.info(f"Recorded new launch: ${token['ticker'].upper()} ({token['contract_address']})")
        except Exception as e:
            logger.warning(f"record_launch failed for {token.get('ticker')}: {e}")

    return new_launches


def generate_launch_alert_tweet(token: dict) -> str:
    """Generate a launch alert tweet for a new token using Claude."""
    ticker = (token.get("ticker") or "").upper()
    name = token.get("name") or ticker
    liquidity = token.get("first_liquidity") or 0
    fee_model = token.get("fee_model")

    liq_str = f"${liquidity:,.0f}" if liquidity >= 100 else "early liquidity"
    lines = [
        "NEW LAUNCH DATA:",
        f"- Token: ${ticker} ({name})",
        f"- Initial liquidity: {liq_str}",
    ]
    if fee_model:
        lines.append(f"- Fee model: {fee_model}")
    lines.append("\nWrite the launch alert tweet. High energy. Under 280 chars.")

    raw = _call_claude(_LAUNCH_ALERT_SYSTEM, "\n".join(lines), max_tokens=120)
    return _clean_reply(raw)
