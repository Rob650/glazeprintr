"""
Auto-discovery of Printr ecosystem tokens.

Discovery sources (in priority order):
1. Printr Partner API — /v1/telecoin/list and other token-listing endpoints
2. Printr staking positions — every contract with active staking is an eco token
3. KNOWN_CONTRACTS (scraper.py) — hardcoded seed list, always included

For each newly discovered contract address, DexScreener resolves the real ticker/name.
All discovered tokens are persisted in the `discovered_tokens` DB table so they survive
redeploys. New tokens are flagged needs_memes=True and logged for Robert.

Usage:
    from token_discovery import discover_and_persist_tokens_async, get_all_ecosystem_contracts
    # Called by the scheduler every 30 min:
    new_tokens = await discover_and_persist_tokens_async()
    # Called by scraper.py to get the full merged contract list:
    contracts = get_all_ecosystem_contracts()  # {name: address, ...}
"""

import asyncio
import logging
from typing import Optional

import aiohttp

import database
from scraper import KNOWN_CONTRACTS, HEADERS, DEXSCREENER_API, _SOLANA_CONTRACT_RE

logger = logging.getLogger(__name__)

# Lower-cased seed: addr_lower → ticker name (always included)
_SEED_LOWER: dict[str, str] = {v.lower(): k for k, v in KNOWN_CONTRACTS.items()}


# ---------------------------------------------------------------------------
# DexScreener name resolution
# ---------------------------------------------------------------------------

async def _resolve_name_dex(session: aiohttp.ClientSession, contract: str) -> Optional[str]:
    """Resolve token ticker/symbol from DexScreener. Returns lowercase symbol or None."""
    url = DEXSCREENER_API.format(contract)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=12)) as resp:
            if resp.status == 200:
                data = await resp.json()
                pairs = data.get("pairs") or []
                if pairs:
                    pairs.sort(
                        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
                        reverse=True,
                    )
                    base = pairs[0].get("baseToken") or {}
                    symbol = base.get("symbol", "").strip().lstrip("$")
                    if symbol:
                        return symbol.lower()
    except Exception as e:
        logger.debug(f"DexScreener name lookup failed for {contract[:12]}: {e}")
    return None


# ---------------------------------------------------------------------------
# Token normalisation helpers
# ---------------------------------------------------------------------------

def _extract_contract(raw: dict) -> str:
    """Pull a contract/mint address from various field name patterns."""
    return (
        raw.get("mint")
        or raw.get("contract_address")
        or raw.get("contractAddress")
        or raw.get("address")
        or raw.get("token_address")
        or raw.get("tokenAddress")
        or ""
    )


def _extract_ticker(raw: dict) -> str:
    """Pull a ticker/symbol from various field name patterns, lowercased."""
    name = (
        raw.get("ticker")
        or raw.get("symbol")
        or raw.get("token_symbol")
        or raw.get("name")
        or raw.get("token_name")
        or ""
    )
    return name.lower().lstrip("$").strip()


# ---------------------------------------------------------------------------
# Main discovery function
# ---------------------------------------------------------------------------

async def discover_and_persist_tokens_async() -> list[dict]:
    """
    Run the full token discovery pipeline. Returns a list of newly-discovered
    token dicts (not in KNOWN_CONTRACTS and not seen before in the DB).

    Steps:
      1. Seed from KNOWN_CONTRACTS (always included, needs_memes=False)
      2. Try Printr Partner API token-list endpoints
      3. Pull staking positions — every staked contract is an eco token
      4. Resolve real ticker names via DexScreener for unknowns
      5. Persist all to discovered_tokens table
      6. Log and return new tokens (needs_memes=True)
    """
    from printr_api import fetch_token_list_sync, fetch_staking_totals_async

    # all_contracts: addr_lower → ticker (or None if unknown)
    all_contracts: dict[str, Optional[str]] = {}
    staking_addrs: set[str] = set()

    # --- Step 1: seed from KNOWN_CONTRACTS ---
    for name, addr in KNOWN_CONTRACTS.items():
        all_contracts[addr.lower()] = name

    # --- Step 2: Printr Partner API token list ---
    try:
        loop = asyncio.get_running_loop()
        api_tokens: list[dict] = await loop.run_in_executor(None, fetch_token_list_sync)
        for tok in api_tokens:
            addr = _extract_contract(tok)
            ticker = _extract_ticker(tok)
            if addr and _SOLANA_CONTRACT_RE.match(addr):
                key = addr.lower()
                # Only override if we don't already have a real name for this address
                if key not in all_contracts:
                    all_contracts[key] = ticker or None
                elif not all_contracts[key] and ticker:
                    all_contracts[key] = ticker
        logger.info(f"Token discovery: {len(api_tokens)} candidates from Printr API token list")
    except Exception as e:
        logger.warning(f"Token discovery: Printr API token list failed: {e}")

    # --- Step 3: staking positions ---
    try:
        staking_totals, _ = await fetch_staking_totals_async(max_pages=60, page_size=100)
        for addr in staking_totals:
            if _SOLANA_CONTRACT_RE.match(addr):
                staking_addrs.add(addr.lower())
                if addr.lower() not in all_contracts:
                    all_contracts[addr.lower()] = None  # name TBD
        logger.info(
            f"Token discovery: {len(staking_totals)} contracts from staking positions "
            f"({len(staking_addrs)} Solana)"
        )
    except Exception as e:
        logger.warning(f"Token discovery: staking positions failed: {e}")

    # --- Step 4: resolve unknown names via DexScreener ---
    unknowns = [(addr, ) for addr, name in all_contracts.items() if not name]
    if unknowns:
        # Build a proper-case address list for DexScreener
        # (use the seed address if available, otherwise use lower-case; Solana addresses are case-sensitive)
        seed_by_lower: dict[str, str] = {v.lower(): v for v in KNOWN_CONTRACTS.values()}
        existing_by_lower: dict[str, str] = {
            t["contract_address"].lower(): t["contract_address"]
            for t in database.get_discovered_tokens()
        }

        addrs_to_resolve = []
        for (addr_lower,) in unknowns:
            proper = (
                seed_by_lower.get(addr_lower)
                or existing_by_lower.get(addr_lower)
                or addr_lower
            )
            addrs_to_resolve.append((addr_lower, proper))

        async with aiohttp.ClientSession(headers=HEADERS) as session:
            coros = [_resolve_name_dex(session, proper) for _, proper in addrs_to_resolve]
            resolved = await asyncio.gather(*coros, return_exceptions=True)

        resolved_count = 0
        for (addr_lower, _), result in zip(addrs_to_resolve, resolved):
            if isinstance(result, str) and result:
                all_contracts[addr_lower] = result
                resolved_count += 1
            else:
                # Use short address placeholder so name is never blank
                all_contracts[addr_lower] = addr_lower[:8]

        logger.info(
            f"Token discovery: resolved {resolved_count}/{len(unknowns)} "
            "unknown contract names via DexScreener"
        )

    # --- Step 5: persist to DB, detect new tokens ---
    existing_db = database.get_discovered_tokens()
    existing_lower_set: set[str] = {t["contract_address"].lower() for t in existing_db}

    # Build proper-case address map (seed takes priority, then existing DB, then lower)
    seed_addr_map: dict[str, str] = {v.lower(): v for v in KNOWN_CONTRACTS.values()}
    db_addr_map: dict[str, str] = {
        t["contract_address"].lower(): t["contract_address"] for t in existing_db
    }

    new_tokens: list[dict] = []

    for addr_lower, ticker in all_contracts.items():
        ticker = ticker or addr_lower[:8]
        proper_addr = seed_addr_map.get(addr_lower) or db_addr_map.get(addr_lower) or addr_lower

        is_seed = addr_lower in _SEED_LOWER
        is_new = addr_lower not in existing_lower_set

        if is_seed:
            source = "seed"
            needs_memes = False  # seed tokens already have images
        elif addr_lower in staking_addrs:
            source = "staking"
            needs_memes = is_new
        else:
            source = "printr_api"
            needs_memes = is_new

        database.upsert_discovered_token(
            contract_address=proper_addr,
            ticker=ticker,
            source=source,
            needs_memes=needs_memes,
        )

        if is_new and not is_seed:
            new_tokens.append({
                "contract_address": proper_addr,
                "ticker": ticker,
                "source": source,
                "needs_memes": needs_memes,
            })

    if new_tokens:
        tickers_str = ", ".join(f"${t['ticker'].upper()}" for t in new_tokens)
        logger.warning(
            f"TOKEN DISCOVERY: {len(new_tokens)} NEW Printr ecosystem token(s) found! "
            f"{tickers_str} — flagged needs_memes=True. "
            f"Add meme images and call mark_memes_provided() when ready."
        )
    else:
        logger.info(
            f"Token discovery complete: {len(all_contracts)} total ecosystem tokens tracked, 0 new"
        )

    return new_tokens


# ---------------------------------------------------------------------------
# Runtime query helpers (used by scraper.py + bot.py)
# ---------------------------------------------------------------------------

def get_all_ecosystem_contracts() -> dict[str, str]:
    """
    Return a merged {ticker: contract_address} dict combining:
      - KNOWN_CONTRACTS (seed, always included)
      - All DB-discovered tokens

    This is the authoritative list for scraping and tweeting.
    KNOWN_CONTRACTS takes priority if there's a ticker collision.
    """
    result: dict[str, str] = {}

    # Start with DB-discovered tokens (lower priority)
    for tok in database.get_discovered_tokens():
        ticker = tok["ticker"]
        addr = tok["contract_address"]
        if ticker and addr:
            result[ticker] = addr

    # Seed always wins on collision
    for ticker, addr in KNOWN_CONTRACTS.items():
        result[ticker] = addr

    return result


def get_all_ecosystem_tickers() -> list[str]:
    """Return all known ecosystem ticker names (lowercase), seed + discovered."""
    return list(get_all_ecosystem_contracts().keys())
