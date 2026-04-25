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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://app.printr.money/",
}

# Known contract addresses to always track via DexScreener even if the printr.money API is down.
# Add real Solana/Base/etc. addresses here as you find them.
KNOWN_CONTRACTS: dict[str, str] = {
    # "belief": "SOLANA_ADDRESS_HERE",
    # "fatchoi": "SOLANA_ADDRESS_HERE",
}


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
    }


async def scrape_all_data() -> list[dict]:
    """
    Fetch and enrich project data from printr.money + DexScreener.
    Returns a list of project dicts sorted by market cap descending.
    Returns an empty list on total failure — callers must handle this gracefully.
    """
    results: list[dict] = []

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        raw_tokens = await _try_printr_api(session)
        tokens = [t for t in (_normalize_token(r) for r in raw_tokens) if t]

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
                })

        with_contract = [t for t in tokens if t.get("contract_address")]
        without_contract = [t for t in tokens if not t.get("contract_address")]

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
    logger.info(f"scrape_all_data: {len(results)} projects collected")
    return results
