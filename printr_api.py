"""
Printr Partner API client.

Auth: form-encoded POST /login with inviteCode → JWT cookie.
Staking: POST /v1/staking/list-positions → paginate, aggregate by contract.

API key (invite code): set PRINTR_API_KEY env var.
"""
import os
import asyncio
import logging
import urllib.request
import urllib.parse
import urllib.error
import json
from typing import Optional

logger = logging.getLogger(__name__)

_PRINTR_API_BASE = "https://api-preview.printr.money/v1"
_PRINTR_LOGIN_URL = "https://api-preview.printr.money/login"
_PRINTR_INVITE_CODE = os.environ.get("PRINTR_API_KEY", "")
if not _PRINTR_INVITE_CODE:
    logging.getLogger(__name__).warning("PRINTR_API_KEY not set — staking data will be unavailable")

# Solana mainnet CAIP-2 chain ID used by the Printr API
_SOLANA_CHAIN_ID = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"

_REQ_UA = "Mozilla/5.0 (compatible; glazeprintr/1.0)"

# Module-level cookie cache (persists for process lifetime — no expiry claim in JWT)
_cached_cookie: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _authenticate_sync() -> Optional[str]:
    """POST /login with form-encoded inviteCode, return session cookie value."""
    body = urllib.parse.urlencode({"inviteCode": _PRINTR_INVITE_CODE}).encode()
    req = urllib.request.Request(
        _PRINTR_LOGIN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _REQ_UA,
            "Accept": "text/html,application/json",
        },
    )
    # The login endpoint returns 307 redirect with a Set-Cookie header.
    # We must NOT follow redirects so we can read the cookie before it's dropped.
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=15) as resp:
            set_cookie = resp.headers.get("Set-Cookie", "")
            for part in set_cookie.split(";"):
                part = part.strip()
                if part.startswith("printr-integrator-token="):
                    token = part[len("printr-integrator-token="):]
                    logger.info("Printr API: authenticated successfully")
                    return token
    except urllib.error.HTTPError as e:
        # 307 raises an HTTPError when redirect is disabled
        set_cookie = e.headers.get("Set-Cookie", "")
        for part in set_cookie.split(";"):
            part = part.strip()
            if part.startswith("printr-integrator-token="):
                token = part[len("printr-integrator-token="):]
                logger.info("Printr API: authenticated (via redirect capture)")
                return token
        logger.warning(f"Printr API auth failed: {e}")
    except Exception as e:
        logger.warning(f"Printr API auth error: {e}")
    return None


def get_cookie() -> Optional[str]:
    """Return a valid session cookie, authenticating if needed."""
    global _cached_cookie
    if not _cached_cookie:
        _cached_cookie = _authenticate_sync()
    return _cached_cookie


def invalidate_cookie():
    global _cached_cookie
    _cached_cookie = None


# ---------------------------------------------------------------------------
# Staking positions
# ---------------------------------------------------------------------------

def _post_api(path: str, payload: dict, cookie: str, timeout: int = 20) -> Optional[dict]:
    """POST to the Printr Partner API with cookie auth. Returns parsed JSON or None."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_PRINTR_API_BASE}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Cookie": f"printr-integrator-token={cookie}",
            "User-Agent": _REQ_UA,
            "Accept": "application/json",
        },
    )
    # Disable redirects — a 307 means session expired
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 307:
            return None  # session expired — caller should re-auth
        logger.debug(f"Printr API {path}: HTTP {e.code}")
    except Exception as e:
        logger.debug(f"Printr API {path}: {e}")
    return None


_STAKING_TIERS = [7, 14, 30, 60, 90, 180]
_STAKING_TIER_KEYS = {d: f"{d}d" for d in _STAKING_TIERS}


def _nearest_staking_tier(days: int) -> str:
    """Map a lock duration in days to the nearest standard POB tier key."""
    nearest = min(_STAKING_TIERS, key=lambda t: abs(t - days))
    return _STAKING_TIER_KEYS[nearest]


def fetch_staking_totals_sync(
    max_pages: int = 60,
    page_size: int = 100,
) -> tuple[dict[str, float], dict[str, float]]:
    """
    Paginate through all staking positions and return:
      (totals, breakdown)
    where:
      totals    = {contract_address: total_staked_tokens}
      breakdown = {"7d": total, "14d": total, ... "180d": total}
                  aggregated across all tokens/positions by lock duration tier.
    """
    global _cached_cookie

    cookie = get_cookie()
    if not cookie:
        logger.warning("Printr API: no cookie, staking data unavailable")
        return {}, {k: 0.0 for k in ["7d", "14d", "30d", "60d", "90d", "180d"]}

    totals: dict[str, float] = {}
    breakdown: dict[str, float] = {k: 0.0 for k in ["7d", "14d", "30d", "60d", "90d", "180d"]}
    cursor: Optional[str] = None
    fetched = 0

    for page in range(max_pages):
        payload: dict = {"limit": page_size}
        if cursor:
            payload["cursor"] = cursor

        data = _post_api("/staking/list-positions", payload, cookie)
        if data is None:
            # Might be session expiry — retry once with fresh auth
            invalidate_cookie()
            cookie = get_cookie()
            if not cookie:
                break
            data = _post_api("/staking/list-positions", payload, cookie)
            if data is None:
                break

        positions = data.get("positions") or []
        for pos in positions:
            if pos.get("was_closed"):
                continue
            acct = (pos.get("staked") or {}).get("asset", {}).get("account", "")
            # Format: "solana:chainId:contractAddress"
            parts = acct.split(":")
            if len(parts) == 3:
                contract = parts[2]
                try:
                    atomic = int((pos.get("staked") or {}).get("atomic", 0))
                    decimals = int((pos.get("staked") or {}).get("decimals", 9))
                    amount = atomic / (10 ** decimals)
                    totals[contract] = totals.get(contract, 0.0) + amount

                    # Extract lock duration and accumulate into breakdown
                    raw_days = (
                        pos.get("lock_duration_days")
                        or pos.get("duration_days")
                        or (pos.get("lock") or {}).get("duration_days")
                        or (pos.get("lock") or {}).get("days")
                    )
                    if raw_days is not None:
                        try:
                            tier_key = _nearest_staking_tier(int(raw_days))
                            breakdown[tier_key] = breakdown.get(tier_key, 0.0) + amount
                        except (ValueError, TypeError):
                            pass
                except (ValueError, TypeError):
                    pass

        fetched += len(positions)
        cursor = data.get("next_cursor")
        if not cursor or not positions:
            logger.info(f"Printr staking: completed {page + 1} pages, {fetched} positions, {len(totals)} tokens")
            break

    if cursor:
        logger.info(f"Printr staking: fetched {fetched} positions ({max_pages} pages), {len(totals)} tokens (more exist)")

    return totals, breakdown


async def fetch_staking_totals_async(
    max_pages: int = 60,
    page_size: int = 100,
) -> tuple[dict[str, float], dict[str, float]]:
    """Async wrapper: runs fetch_staking_totals_sync in a thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fetch_staking_totals_sync(max_pages, page_size))


def _post_api_public(path: str, payload: dict, timeout: int = 20) -> Optional[dict]:
    """POST to a Printr API endpoint that requires no auth. Returns parsed JSON or None."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_PRINTR_API_BASE}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": _REQ_UA,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.debug(f"Printr API (public) {path}: HTTP {e.code}")
    except Exception as e:
        logger.debug(f"Printr API (public) {path}: {e}")
    return None


def _get_api_public(path: str, timeout: int = 20) -> Optional[dict]:
    """GET a Printr API endpoint that requires no auth. Returns parsed JSON or None."""
    req = urllib.request.Request(
        f"{_PRINTR_API_BASE}{path}",
        headers={
            "User-Agent": _REQ_UA,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.debug(f"Printr API (GET public) {path}: HTTP {e.code}")
    except Exception as e:
        logger.debug(f"Printr API (GET public) {path}: {e}")
    return None


def fetch_token_list_sync() -> list[dict]:
    """
    Try multiple Printr API endpoints to get a full token listing.
    Returns a list of raw token dicts (may have ticker, symbol, mint, address, etc.).
    Falls back to empty list if all endpoints fail — caller uses staking positions as backup.
    """
    # Candidate endpoints in priority order (GET then POST variants)
    candidates: list[tuple[str, str, Optional[dict]]] = [
        ("GET",  "/telecoin/list",            None),
        ("POST", "/telecoin/list",            {}),
        ("GET",  "/telecoin",                 None),
        ("GET",  "/tokens",                   None),
        ("POST", "/tokens/list",              {}),
        ("GET",  "/marketplace",              None),
        ("POST", "/marketplace/list",         {}),
        ("GET",  "/launchpad/tokens",         None),
        ("POST", "/launchpad/list",           {}),
    ]

    for method, path, payload in candidates:
        try:
            if method == "GET":
                data = _get_api_public(path)
            else:
                data = _post_api_public(path, payload or {})

            if not data:
                continue

            # Normalise: the response may be a list or a wrapper dict
            tokens: list[dict] = []
            if isinstance(data, list):
                tokens = data
            elif isinstance(data, dict):
                for key in ("tokens", "telecoins", "items", "data", "results", "list"):
                    if isinstance(data.get(key), list):
                        tokens = data[key]
                        break

            if tokens:
                logger.info(f"Printr API token list: {len(tokens)} tokens from {method} {path}")
                return tokens

        except Exception as e:
            logger.debug(f"Printr token list {method} {path}: {e}")

    logger.info("Printr API: no token list endpoint available — relying on staking positions + KNOWN_CONTRACTS")
    return []


def fetch_buyback_burns(mint_address: str) -> list[dict]:
    """POST /v1/telecoin/buyback-burn-detail — returns burn tx history for a token (no auth)."""
    data = _post_api_public("/telecoin/buyback-burn-detail", {"mint_address": mint_address})
    if not data:
        return []
    if isinstance(data, list):
        return data
    return data.get("burns") or data.get("data") or []


def fetch_positions_with_rewards(mint_address: str) -> list[dict]:
    """POST /v1/staking/list-positions-with-rewards — positions with claimable reward data (no auth)."""
    data = _post_api_public("/staking/list-positions-with-rewards", {"mint_address": mint_address, "limit": 100})
    if not data:
        return []
    if isinstance(data, list):
        return data
    return data.get("positions") or data.get("data") or []


def claim_rewards(position_id: str, wallet_address: str, mint_address: str) -> Optional[dict]:
    """POST /v1/staking/claim-rewards — claim pending rewards for a position. Returns result or None."""
    global _cached_cookie
    cookie = get_cookie()
    if not cookie:
        logger.warning("Printr API: no cookie — cannot claim rewards")
        return None

    payload = {
        "position_id":   position_id,
        "wallet_address": wallet_address,
        "mint_address":  mint_address,
    }
    data = _post_api("/staking/claim-rewards", payload, cookie)
    if data is None:
        # Might be session expiry — retry once with fresh auth
        invalidate_cookie()
        cookie = get_cookie()
        if not cookie:
            return None
        data = _post_api("/staking/claim-rewards", payload, cookie)
    return data


# ---------------------------------------------------------------------------
# Token discovery from staking data
# ---------------------------------------------------------------------------

def extract_active_tokens(staking_totals: dict[str, float]) -> list[dict]:
    """
    Convert staking totals into a list of token dicts compatible with scraper.py format.
    Returns tokens sorted by total staked (descending).
    """
    tokens = []
    for contract, total_staked in staking_totals.items():
        tokens.append({
            "contract_address": contract,
            "total_staked_tokens": total_staked,
        })
    tokens.sort(key=lambda t: t["total_staked_tokens"], reverse=True)
    return tokens


def compute_staking_pct(contract: str, staking_totals: dict[str, float], total_supply: float) -> Optional[float]:
    """Compute staking % for a contract given total staked and total supply."""
    if not staking_totals or not total_supply or total_supply <= 0:
        return None
    staked = staking_totals.get(contract, 0.0)
    if staked <= 0:
        return None
    pct = staked / total_supply * 100
    return min(pct, 100.0)
