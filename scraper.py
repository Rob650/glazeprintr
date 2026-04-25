import aiohttp
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/{}"

PRINTR_ENDPOINTS = [
    "https://api.printr.money/v1/tokens?sort=marketCap&order=desc&limit=50",
    "https://app.printr.money/api/tokens?sort=market_cap&limit=50",
    "https://app.printr.money/api/v1/tokens?sort=market_cap",
]

PRINTR_STAKING_ENDPOINTS = [
    "https://api.printr.money/v1/staking",
    "https://api.printr.money/v1/tokens/staking",
    "https://app.printr.money/api/staking",
    "https://app.printr.money/api/v1/staking",
]

PRINTR_TOKEN_STAKING_TEMPLATES = [
    "https://api.printr.money/v1/tokens/{}/staking",
    "https://api.printr.money/v1/staking/{}",
    "https://app.printr.money/api/tokens/{}/staking",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://app.printr.money/",
}

# Known contract addresses to always track via DexScreener even if the printr.money API is down.
KNOWN_CONTRACTS: dict[str, str] = {
    # "belief": "SOLANA_ADDRESS_HERE",
    # "fatchoi": "SOLANA_ADDRESS_HERE",
}

# Key tokens to attempt per-token staking fetch if bulk staking endpoint fails
KEY_TOKENS = {"belief", "fatchoi", "ooo", "print", "rotus", "deployr"}


def _extract_staking_pct(data: dict) -> Optional[float]:
    """Extract staking % from a dict using known field name variations."""
    pct = (
        data.get("staking_pct")
        or data.get("staked_pct")
        or data.get("pob_staked_pct")
        or data.get("locked_pct")
        or data.get("stakedPercentage")
        or data.get("lockedPercentage")
        or data.get("staked_percentage")
        or data.get("pob_percentage")
    )
    if pct is not None:
        return float(pct)
    total_staked = data.get("total_staked") or data.get("totalStaked")
    total_supply = data.get("total_supply") or data.get("totalSupply")
    if total_staked and total_supply:
        try:
            return float(total_staked) / float(total_supply) * 100
        except (ZeroDivisionError, TypeError, ValueError):
            pass
    return None


async def _fetch_dexscreener(session: aiohttp.ClientSession, contract_address: str) -> dict:
    url = DEXSCREENER_API.format(contract_address)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                pairs = data.get("pairs") or []
                if pairs:
                    pairs.sort(
                        key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
                        reverse=True,
                    )
                    pair = pairs[0]
                    return {
                        "price": float(pair.get("priceUsd") or 0),
                        "volume": float((pair.get("volume") or {}).get("h24") or 0),
                        "market_cap": float(pair.get("fdv") or pair.get("marketCap") or 0),
                        "liquidity": float((pair.get("liquidity") or {}).get("usd") or 0),
                        "price_change_24h": float(
                            (pair.get("priceChange") or {}).get("h24") or 0
                        ),
                    }
    except Exception as e:
        logger.warning(f"DexScreener error for {contract_address}: {e}")
    return {}


async def _try_printr_api(session: aiohttp.ClientSession) -> list[dict]:
    for endpoint in PRINTR_ENDPOINTS:
        try:
            async with session.get(
                endpoint, timeout=aiohttp.ClientTimeout(total=12)
            ) as resp:
                if resp.status == 200 and "json" in resp.headers.get("content-type", ""):
                    data = await resp.json()
                    tokens = (
                        data
                        if isinstance(data, list)
                        else (
                            data.get("tokens")
                            or data.get("data")
                            or data.get("results")
                            or []
                        )
                    )
                    if tokens:
                        logger.info(f"Got {len(tokens)} tokens from {endpoint}")
                        return tokens
        except Exception as e:
            logger.debug(f"printr endpoint {endpoint}: {e}")
    logger.info("All printr.money endpoints failed — will use known contracts only")
    return []


async def _fetch_bulk_staking(session: aiohttp.ClientSession) -> dict[str, float]:
    """Try bulk staking endpoints. Returns {token_name -> staking_pct} or empty dict."""
    for endpoint in PRINTR_STAKING_ENDPOINTS:
        try:
            async with session.get(
                endpoint, timeout=aiohttp.ClientTimeout(total=12)
            ) as resp:
                if resp.status == 200 and "json" in resp.headers.get("content-type", ""):
                    data = await resp.json()
                    items = (
                        data
                        if isinstance(data, list)
                        else (
                            data.get("staking")
                            or data.get("data")
                            or data.get("tokens")
                            or data.get("results")
                            or []
                        )
                    )
                    result: dict[str, float] = {}
                    for item in items:
                        name = (
                            item.get("ticker")
                            or item.get("symbol")
                            or item.get("name")
                            or ""
                        ).lower().lstrip("$")
                        pct = _extract_staking_pct(item)
                        if name and pct is not None:
                            result[name] = pct
                    if result:
                        logger.info(f"Got staking data for {len(result)} tokens from {endpoint}")
                        return result
        except Exception as e:
            logger.debug(f"Staking endpoint {endpoint}: {e}")
    logger.info("Bulk staking endpoints failed — will try per-token for key tokens")
    return {}


async def _fetch_token_staking(
    session: aiohttp.ClientSession, name: str, contract: str
) -> Optional[float]:
    """Try per-token staking endpoints. Returns staking_pct (0-100) or None."""
    identifiers = [ident for ident in [contract, name] if ident]
    for ident in identifiers:
        for tmpl in PRINTR_TOKEN_STAKING_TEMPLATES:
            try:
                url = tmpl.format(ident)
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200 and "json" in resp.headers.get("content-type", ""):
                        data = await resp.json()
                        pct = _extract_staking_pct(data)
                        if pct is not None:
                            logger.info(f"Got staking data for {name}: {pct:.1f}% from {url}")
                            return pct
            except Exception:
                pass
    return None


def _normalize_token(raw: dict) -> Optional[dict]:
    name = (
        raw.get("ticker")
        or raw.get("symbol")
        or raw.get("name")
        or raw.get("token_name")
        or ""
    ).lower().lstrip("$")
    contract = (
        raw.get("contract_address")
        or raw.get("contractAddress")
        or raw.get("address")
        or raw.get("mint")
        or ""
    )
    if not name:
        return None

    staking_pct = _extract_staking_pct(raw)

    return {
        "name": name,
        "contract_address": contract,
        "market_cap": float(
            raw.get("market_cap") or raw.get("marketCap") or raw.get("mc") or 0
        ),
        "price": float(raw.get("price") or raw.get("priceUsd") or 0),
        "price_change_24h": float(
            raw.get("price_change_24h")
            or raw.get("priceChange24h")
            or raw.get("change24h")
            or 0
        ),
        "volume": float(
            raw.get("volume_24h") or raw.get("volume24h") or raw.get("volume") or 0
        ),
        "liquidity": 0.0,
        "staking_pct": staking_pct,
    }


async def scrape_all_data() -> list[dict]:
    """
    Fetch and enrich project data from printr.money + DexScreener + staking endpoints.
    Returns a list of project dicts sorted by market cap descending.
    Each dict may include staking_pct (float, 0-100) if available.
    Returns an empty list on total failure — callers must handle this gracefully.
    """
    results: list[dict] = []

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        raw_tokens, staking_map = await asyncio.gather(
            _try_printr_api(session),
            _fetch_bulk_staking(session),
        )
        tokens = [t for t in (_normalize_token(r) for r in raw_tokens) if t]

        # Enrich tokens with bulk staking data
        for token in tokens:
            if token["name"] in staking_map and token.get("staking_pct") is None:
                token["staking_pct"] = staking_map[token["name"]]

        found_names = {t["name"] for t in tokens}
        for name, addr in KNOWN_CONTRACTS.items():
            if name not in found_names:
                tokens.append({
                    "name": name,
                    "contract_address": addr,
                    "market_cap": 0,
                    "price": 0,
                    "price_change_24h": 0,
                    "volume": 0,
                    "liquidity": 0,
                    "staking_pct": staking_map.get(name),
                })

        with_contract = [t for t in tokens if t.get("contract_address")]
        without_contract = [t for t in tokens if not t.get("contract_address")]

        # Per-token staking fetch for key tokens that still have no staking data
        key_missing_staking = [
            t for t in tokens
            if t["name"] in KEY_TOKENS and t.get("staking_pct") is None
        ]
        if key_missing_staking:
            staking_coros = [
                _fetch_token_staking(session, t["name"], t.get("contract_address", ""))
                for t in key_missing_staking
            ]
            staking_results = await asyncio.gather(*staking_coros, return_exceptions=True)
            for token, pct in zip(key_missing_staking, staking_results):
                if isinstance(pct, float):
                    token["staking_pct"] = pct

        if with_contract:
            dex_coros = [
                _fetch_dexscreener(session, t["contract_address"])
                for t in with_contract
            ]
            dex_results = await asyncio.gather(*dex_coros, return_exceptions=True)
            for token, dex in zip(with_contract, dex_results):
                if isinstance(dex, dict) and dex:
                    token.update({k: v for k, v in dex.items() if v})
                results.append(token)

        results.extend(without_contract)

    results.sort(key=lambda t: t.get("market_cap") or 0, reverse=True)
    staking_count = sum(1 for t in results if t.get("staking_pct") is not None)
    logger.info(
        f"scrape_all_data: {len(results)} projects collected, "
        f"{staking_count} with staking data"
    )
    return results
