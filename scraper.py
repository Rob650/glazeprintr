import os
import re
import json as _json
import aiohttp
import asyncio
import logging
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/{}"
DEXSCREENER_SEARCH_API = "https://api.dexscreener.com/latest/dex/search?q={}"
DUNE_DASHBOARD_URL = "https://dune.com/defioasis/printr"

_EVM_CONTRACT_RE = re.compile(r'^0x[0-9a-fA-F]{40,}$')
_SOLANA_CONTRACT_RE = re.compile(r'^[1-9A-HJ-NP-Za-km-z]{32,44}$')

# Legacy scraping endpoints kept for reference but no longer used in active code
PRINTR_TOKEN_STAKING_TEMPLATES: list[str] = []

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

# Known contract addresses — primary token list. All Printr ecosystem contracts end in "brrr".
# Marketplace scraping is replaced by the official Partner API; this is our canonical list.
KNOWN_CONTRACTS: dict[str, str] = {
    "belief":  "29CWsqH84TykHDDwA6DtETUtXQPuKbVgKCmxtkBsbrrr",
    "rotus":   "C8Lwj83fBz9bPKSUxNLEc2QkLF7oVkV7Ja9UKSFLbrrr",
    "deployr": "8JvDVZK6CHFhwwBUgZcEy18i1xXQzAHfimYarmoobrrr",
    "fatchoi": "57dYAUq7Y4hiCSdAB7iBDg4gcYFq7HeUaEs3XnNkbrrr",
    "ooo":     "G6mNZN8o16QBcTqfuEx6FzjiWa94B1XWhfyDxjDibrrr",
    "patapim": "W6wjBw8HJ65PyyHr9RkTXK7dKvrP1CNkgPWVEHDbrrr",
    "roi":     "BdqNyg2k9TrYUGjadxMRjxv7xn1pPYpfeDXj5wSnbrrr",
    "cmyk":    "4AMw5Rb14KLe8L9jSXMJpDX5q8dy9rFqh7W8b1tubrrr",
    "print":   "DU3xkZs5jqzPCmovs6F5rAiBt8dQwoxwLHTw9cyBbrrr",
    "pve":     "J5tUvJp3CH5dtyQSAsuRqXDLb2cdWDwerDj83w1gbrrr",
    "brrr":    "3o1V1iFqHk3pu6vDDv85ctU2q28uepGdsFbch7uKbrrr",
    "quack":   "5ZDkPQjiUM4ukKnBwzi6EX8WS5x39by2pYVFa7ivbrrr",
    "lfp":     "8jPSBB5Ebp6bRWJzwstUEm4Xs2p2jQadewyyxedxbrrr",
    "stakr":   "Da2Vkk5u3zMkyfa61mqs6Kgtdpf7akKK1FrZvzkDbrrr",
    "pob500":  "B8ErKF68PpedTmRMdbhRzTgJ8u5XfHRp2v8krg8Qbrrr",
    "fsjal":   "AiNFufCfmKADdtq3cz2Xaj94EVWfKG1iHyyWZLFEbrrr",
}

# Key tokens for priority staking data lookups
KEY_TOKENS = {"belief", "fatchoi", "ooo", "print", "rotus", "deployr"}

# Reverse mapping: contract address → ticker name (built from KNOWN_CONTRACTS)
_CONTRACT_TO_NAME: dict[str, str] = {v: k for k, v in KNOWN_CONTRACTS.items()}


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


def _compute_staking_pct_from_api(
    contract: str,
    staking_totals: dict[str, float],
    dex_data: dict,
) -> Optional[float]:
    """
    Compute staking % using Printr API total-staked and DexScreener total supply.
    total_supply is derived from fdv / price (DexScreener fully-diluted valuation).
    """
    staked = staking_totals.get(contract, 0.0)
    if staked <= 0:
        return None
    price = dex_data.get("price") or 0.0
    fdv = dex_data.get("market_cap") or 0.0  # scraper stores fdv as market_cap
    if price > 0 and fdv > 0:
        total_supply = fdv / price
        if total_supply > 0:
            return min(staked / total_supply * 100, 100.0)
    return None


async def _fetch_dexscreener(session: aiohttp.ClientSession, contract_address: str) -> dict:
    """Fetch rich token data from DexScreener — price, volume, txns, buy/sell ratio, age, etc."""
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
                    result = {
                        "price": float(pair.get("priceUsd") or 0),
                        "market_cap": float(pair.get("fdv") or pair.get("marketCap") or 0),
                        "liquidity": float((pair.get("liquidity") or {}).get("usd") or 0),
                        "chain": pair.get("chainId", ""),
                        "dex": pair.get("dexId", ""),
                    }

                    # Volume at multiple intervals
                    vol = pair.get("volume") or {}
                    result["volume"] = float(vol.get("h24") or 0)
                    result["volume_6h"] = float(vol.get("h6") or 0)
                    result["volume_1h"] = float(vol.get("h1") or 0)
                    result["volume_5m"] = float(vol.get("m5") or 0)

                    # Price changes at multiple intervals
                    chg = pair.get("priceChange") or {}
                    result["price_change_24h"] = float(chg.get("h24") or 0)
                    result["price_change_6h"] = float(chg.get("h6") or 0)
                    result["price_change_1h"] = float(chg.get("h1") or 0)
                    result["price_change_5m"] = float(chg.get("m5") or 0)

                    # Transaction counts — buy/sell ratio is alpha
                    for period_key, period_label in [("h24", "24h"), ("h6", "6h"), ("h1", "1h"), ("m5", "5m")]:
                        txns = (pair.get("txns") or {}).get(period_key) or {}
                        buys = int(txns.get("buys") or 0)
                        sells = int(txns.get("sells") or 0)
                        if buys or sells:
                            result[f"buys_{period_label}"] = buys
                            result[f"sells_{period_label}"] = sells
                            result[f"txns_{period_label}"] = buys + sells

                    # Pair age
                    created_at = pair.get("pairCreatedAt")
                    if created_at:
                        result["pair_created_at"] = created_at
                        try:
                            import time as _time
                            age_seconds = _time.time() - created_at / 1000 if created_at > 1e10 else 0
                            if age_seconds > 0:
                                result["age_days"] = age_seconds / 86400
                        except Exception:
                            pass

                    # Number of pairs (indicates trading activity breadth)
                    result["num_pairs"] = len(pairs)

                    return result
    except Exception as e:
        logger.warning(f"DexScreener error for {contract_address}: {e}")
    return {}


async def _fetch_staking_from_partner_api() -> dict[str, float]:
    """
    Fetch staking totals per token from the Printr Partner API.
    Returns {contract_address: total_staked_tokens}.
    Runs in a thread since the Partner API client is sync.
    """
    from printr_api import fetch_staking_totals_async
    try:
        totals = await fetch_staking_totals_async(max_pages=60, page_size=100)
        logger.info(f"Printr Partner API: staking totals for {len(totals)} tokens")
        return totals
    except Exception as e:
        logger.warning(f"Printr Partner API staking fetch failed: {e}")
        return {}


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


async def fetch_dune_context(session: aiohttp.ClientSession) -> str:
    """
    Fetch Printr on-chain analytics from Dune API.
    Requires DUNE_API_KEY env var and optionally DUNE_QUERY_IDS (comma-separated query IDs).
    Falls back to a placeholder if credentials are absent.
    """
    api_key = os.environ.get("DUNE_API_KEY", "")
    if not api_key:
        return ""

    query_ids_env = os.environ.get("DUNE_QUERY_IDS", "")
    if not query_ids_env:
        return ""

    query_ids = [q.strip() for q in query_ids_env.split(",") if q.strip()]
    headers = {"X-DUNE-API-KEY": api_key, "Content-Type": "application/json"}
    parts: list[str] = []

    for query_id in query_ids:
        try:
            url = f"https://api.dune.com/api/v1/query/{query_id}/results"
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result") or {}
                    rows = result.get("rows") or []
                    metadata = result.get("metadata") or {}
                    col_names = metadata.get("column_names") or []
                    if rows:
                        # Format as readable metrics instead of raw JSON
                        formatted_rows = []
                        for row in rows[:8]:  # top 8 rows
                            formatted = ", ".join(
                                f"{k}={_format_dune_value(v)}" for k, v in row.items()
                                if v is not None and k not in ("_col0",)
                            )
                            if formatted:
                                formatted_rows.append(f"  {formatted}")
                        if formatted_rows:
                            parts.append(f"[Dune query {query_id}]:\n" + "\n".join(formatted_rows))
                        logger.info(f"Dune query {query_id}: {len(rows)} rows, cols={col_names}")
        except Exception as e:
            logger.warning(f"Dune query {query_id} failed: {e}")

    if parts:
        return "ON-CHAIN ANALYTICS (Dune):\n" + "\n".join(parts)
    return ""


def _format_dune_value(v) -> str:
    """Format a Dune analytics value for readability."""
    if isinstance(v, float):
        if v >= 1e6:
            return f"${v/1e6:.2f}M"
        elif v >= 1e3:
            return f"${v/1e3:.1f}K"
        elif v < 1 and v > 0:
            return f"{v:.6f}"
        return f"{v:,.0f}"
    return str(v)


def compute_comparative_stats(projects: list[dict]) -> dict:
    """
    Compute comparative/relative stats across the ecosystem.
    Returns a dict of insights that can be injected into Claude prompts.
    """
    if not projects:
        return {}

    stats: dict = {}

    # Ecosystem aggregate
    total_mc = sum(p.get("market_cap") or 0 for p in projects)
    total_vol = sum(p.get("volume") or 0 for p in projects)
    total_liq = sum(p.get("liquidity") or 0 for p in projects)
    tokens_with_staking = [p for p in projects if p.get("staking_pct") is not None]
    avg_staking = (
        sum(p["staking_pct"] for p in tokens_with_staking) / len(tokens_with_staking)
        if tokens_with_staking else None
    )

    stats["ecosystem_total_mc"] = total_mc
    stats["ecosystem_total_vol_24h"] = total_vol
    stats["ecosystem_total_liquidity"] = total_liq
    stats["ecosystem_token_count"] = len(projects)
    if avg_staking is not None:
        stats["ecosystem_avg_staking_pct"] = avg_staking

    # Biggest mover
    movers = [p for p in projects if p.get("price_change_24h") is not None]
    if movers:
        best = max(movers, key=lambda p: p["price_change_24h"])
        worst = min(movers, key=lambda p: p["price_change_24h"])
        stats["biggest_gainer"] = {"name": best["name"], "change_24h": best["price_change_24h"]}
        stats["biggest_loser"] = {"name": worst["name"], "change_24h": worst["price_change_24h"]}

    # Volume concentration
    if total_vol > 0:
        top_vol = sorted(projects, key=lambda p: p.get("volume") or 0, reverse=True)
        if top_vol:
            top_share = (top_vol[0].get("volume") or 0) / total_vol * 100
            stats["top_volume_token"] = top_vol[0]["name"]
            stats["top_volume_share_pct"] = top_share

    # Buy/sell pressure across ecosystem
    total_buys = sum(p.get("buys_24h") or 0 for p in projects)
    total_sells = sum(p.get("sells_24h") or 0 for p in projects)
    if total_buys + total_sells > 0:
        stats["ecosystem_buy_pct"] = total_buys / (total_buys + total_sells) * 100
        stats["ecosystem_total_txns_24h"] = total_buys + total_sells

    # Tokens with short-term momentum (1h price change > 5%)
    hot_tokens = [
        p["name"] for p in projects
        if (p.get("price_change_1h") or 0) > 5
    ]
    if hot_tokens:
        stats["hot_tokens_1h"] = hot_tokens

    # Highest staking conviction
    if tokens_with_staking:
        top_staker = max(tokens_with_staking, key=lambda p: p["staking_pct"])
        stats["highest_staking"] = {"name": top_staker["name"], "pct": top_staker["staking_pct"]}

    # Recent launches (age < 7 days)
    new_launches = [
        p for p in projects
        if p.get("age_days") is not None and p["age_days"] < 7
    ]
    if new_launches:
        stats["new_launches_7d"] = [{"name": p["name"], "age_days": p["age_days"], "mc": p.get("market_cap", 0)} for p in new_launches]

    return stats


def format_comparative_context(stats: dict) -> str:
    """Format comparative stats into a prompt-injectable string."""
    if not stats:
        return ""

    lines = ["ECOSYSTEM COMPARATIVE DATA (use these for context and comparisons):"]

    mc = stats.get("ecosystem_total_mc", 0)
    if mc:
        mc_str = f"${mc/1e6:.2f}M" if mc >= 1e6 else f"${mc:,.0f}"
        lines.append(f"  Total ecosystem market cap: {mc_str}")

    vol = stats.get("ecosystem_total_vol_24h", 0)
    if vol:
        vol_str = f"${vol/1e6:.2f}M" if vol >= 1e6 else f"${vol:,.0f}"
        lines.append(f"  Total 24h volume across ecosystem: {vol_str}")

    liq = stats.get("ecosystem_total_liquidity", 0)
    if liq:
        liq_str = f"${liq/1e6:.2f}M" if liq >= 1e6 else f"${liq:,.0f}"
        lines.append(f"  Total ecosystem liquidity: {liq_str}")

    lines.append(f"  Tracked tokens: {stats.get('ecosystem_token_count', 0)}")

    avg_s = stats.get("ecosystem_avg_staking_pct")
    if avg_s is not None:
        lines.append(f"  Avg staking across ecosystem: {avg_s:.1f}%")

    bg = stats.get("biggest_gainer")
    if bg:
        lines.append(f"  Biggest 24h gainer: ${bg['name'].upper()} at {bg['change_24h']:+.1f}%")

    bl = stats.get("biggest_loser")
    if bl and bl["change_24h"] < 0:
        lines.append(f"  Biggest 24h decliner: ${bl['name'].upper()} at {bl['change_24h']:+.1f}%")

    bp = stats.get("ecosystem_buy_pct")
    txns = stats.get("ecosystem_total_txns_24h")
    if bp is not None and txns:
        lines.append(f"  Ecosystem buy pressure: {bp:.0f}% buys across {txns:,} txns in 24h")

    hs = stats.get("highest_staking")
    if hs:
        lines.append(f"  Highest conviction (staking): ${hs['name'].upper()} at {hs['pct']:.0f}% staked")

    hot = stats.get("hot_tokens_1h")
    if hot:
        lines.append(f"  Hot in last hour (>5% move): {', '.join(f'${t.upper()}' for t in hot)}")

    nl = stats.get("new_launches_7d")
    if nl:
        launch_str = ", ".join(
            f"${l['name'].upper()} ({l['age_days']:.1f}d old, MC={'${:.0f}'.format(l['mc']) if l['mc'] < 1e6 else '${:.2f}M'.format(l['mc']/1e6)})"
            for l in nl[:3]
        )
        lines.append(f"  New launches (<7d): {launch_str}")

    return "\n".join(lines) if len(lines) > 1 else ""


async def scrape_all_data() -> list[dict]:
    """
    Fetch and enrich token data using the Printr Partner API (staking) +
    DexScreener (price/volume/MC) + Dune analytics.

    Token list: KNOWN_CONTRACTS + any new contracts discovered from Partner API staking data.
    Staking %: computed from Partner API total-staked and DexScreener FDV/price.

    Returns a list of project dicts sorted by market cap descending.
    Returns an empty list on total failure — callers must handle this gracefully.
    """
    results: list[dict] = []

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        # Fetch staking totals and Dune analytics in parallel
        staking_totals, dune_ctx = await asyncio.gather(
            _fetch_staking_from_partner_api(),
            fetch_dune_context(session),
        )

        # Build token list: start from KNOWN_CONTRACTS, then add any new contracts
        # discovered from recent staking activity (all-brrr Solana addresses).
        tokens: list[dict] = []
        known_contracts_lower = {v.lower() for v in KNOWN_CONTRACTS.values()}

        for name, addr in KNOWN_CONTRACTS.items():
            tokens.append({
                "name": name,
                "contract_address": addr,
                "market_cap": 0,
                "price": 0,
                "price_change_24h": 0,
                "volume": 0,
                "liquidity": 0,
                "staking_pct": None,
            })

        # Add tokens discovered from staking positions (new Printr launches)
        for contract in staking_totals:
            if contract.lower() not in known_contracts_lower:
                # Only add Solana-style addresses (not EVM, not chain prefixes)
                if _SOLANA_CONTRACT_RE.match(contract) and contract not in _CONTRACT_TO_NAME:
                    tokens.append({
                        "name": contract[:8].lower(),  # short placeholder name
                        "contract_address": contract,
                        "market_cap": 0,
                        "price": 0,
                        "price_change_24h": 0,
                        "volume": 0,
                        "liquidity": 0,
                        "staking_pct": None,
                    })
                    known_contracts_lower.add(contract.lower())

        with_contract = [t for t in tokens if t.get("contract_address")]
        without_contract = [t for t in tokens if not t.get("contract_address")]

        # Enrich all tokens with DexScreener data in parallel
        if with_contract:
            dex_coros = [
                _fetch_dexscreener(session, t["contract_address"])
                for t in with_contract
            ]
            dex_results = await asyncio.gather(*dex_coros, return_exceptions=True)
            for token, dex in zip(with_contract, dex_results):
                if isinstance(dex, dict) and dex:
                    token.update({k: v for k, v in dex.items() if v})
                # Compute staking % from Partner API totals + DexScreener supply
                contract = token.get("contract_address", "")
                if contract and staking_totals:
                    pct = _compute_staking_pct_from_api(contract, staking_totals, token)
                    if pct is not None:
                        token["staking_pct"] = pct
                results.append(token)

        results.extend(without_contract)

    results.sort(key=lambda t: t.get("market_cap") or 0, reverse=True)
    staking_count = sum(1 for t in results if t.get("staking_pct") is not None)
    logger.info(
        f"scrape_all_data: {len(results)} projects collected, "
        f"{staking_count} with staking data"
    )

    # Attach ecosystem-level metadata to the result set for downstream use
    if results:
        results[0]["_dune_context"] = dune_ctx or ""
        results[0]["_comparative_stats"] = compute_comparative_stats(results)

    return results


def _fetch_url_sync(url: str, timeout: int = 8) -> Optional[dict]:
    """Fetch a JSON URL synchronously. Returns parsed dict or None."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode())
    except Exception as e:
        logger.debug(f"Sync fetch failed for {url}: {e}")
        return None


def fetch_token_data_sync(query: str, timeout: int = 8) -> Optional[dict]:
    """
    Synchronously look up a token by contract address or $ticker.
    Primary source: DexScreener (price/volume/MC/age/txns).
    Returns a dict with all available fields, or None if nothing found.

    Used for real-time lookups when the bot sees a $ticker or contract address
    in an incoming tweet/mention.
    """
    query = query.strip()
    if not query:
        return None

    is_contract = bool(_EVM_CONTRACT_RE.match(query) or _SOLANA_CONTRACT_RE.match(query))
    url = DEXSCREENER_API.format(query) if is_contract else DEXSCREENER_SEARCH_API.format(query.upper())

    dex_data = _fetch_url_sync(url, timeout=timeout)
    pairs = (dex_data or {}).get("pairs") or []

    result: dict = {}

    if pairs:
        if not is_contract:
            # Printr contracts always end in "brrr" — filter out wrong tokens from search
            brrr_pairs = [
                p for p in pairs
                if (p.get("baseToken") or {}).get("address", "").endswith("brrr")
            ]
            if not brrr_pairs:
                logger.warning(f"fetch_token_data_sync: no brrr-suffix address for {query!r} — skipping (wrong token)")
            else:
                pairs = brrr_pairs
        pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
        pair = pairs[0]
        base = pair.get("baseToken") or {}
        result["name"] = base.get("symbol", query)
        result["contract_address"] = base.get("address", query if is_contract else "")
        result["chain"] = pair.get("chainId", "")
        result["dex"] = pair.get("dexId", "")
        result["price"] = float(pair.get("priceUsd") or 0)
        result["market_cap"] = float(pair.get("fdv") or pair.get("marketCap") or 0)
        result["liquidity"] = float((pair.get("liquidity") or {}).get("usd") or 0)

        vol = pair.get("volume") or {}
        result["volume"] = float(vol.get("h24") or 0)
        result["volume_6h"] = float(vol.get("h6") or 0)
        result["volume_1h"] = float(vol.get("h1") or 0)
        result["volume_5m"] = float(vol.get("m5") or 0)

        chg = pair.get("priceChange") or {}
        result["price_change_24h"] = float(chg.get("h24") or 0)
        result["price_change_6h"] = float(chg.get("h6") or 0)
        result["price_change_1h"] = float(chg.get("h1") or 0)
        result["price_change_5m"] = float(chg.get("m5") or 0)

        for period_key, period_label in [("h24", "24h"), ("h6", "6h"), ("h1", "1h"), ("m5", "5m")]:
            txns = (pair.get("txns") or {}).get(period_key) or {}
            buys = int(txns.get("buys") or 0)
            sells = int(txns.get("sells") or 0)
            if buys or sells:
                result[f"txns_{period_label}"] = buys + sells
                result[f"buys_{period_label}"] = buys
                result[f"sells_{period_label}"] = sells

        created_at = pair.get("pairCreatedAt")
        if created_at:
            result["pair_created_at"] = created_at
            try:
                import time as _time
                age_seconds = _time.time() - created_at / 1000 if created_at > 1e10 else 0
                if age_seconds > 0:
                    result["age_days"] = age_seconds / 86400
            except Exception:
                pass

        result["num_pairs"] = len(pairs)

    if not result:
        logger.warning(f"No data found for token query: {query!r}")
        return None

    logger.info(
        f"fetch_token_data_sync({query!r}): name={result.get('name')} "
        f"mc={result.get('market_cap')} staking={result.get('staking_pct')}"
    )
    return result
