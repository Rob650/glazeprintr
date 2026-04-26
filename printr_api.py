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


def fetch_staking_totals_sync(max_pages: int = 60, page_size: int = 100) -> dict[str, float]:
    """
    Paginate through all staking positions and return total staked per token.

    Returns {contract_address: total_staked_tokens}.
    Positions are returned newest-first; we paginate until exhausted or max_pages.
    """
    global _cached_cookie

    cookie = get_cookie()
    if not cookie:
        logger.warning("Printr API: no cookie, staking data unavailable")
        return {}

    totals: dict[str, float] = {}
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
                except (ValueError, TypeError):
                    pass

        fetched += len(positions)
        cursor = data.get("next_cursor")
        if not cursor or not positions:
            logger.info(f"Printr staking: completed {page + 1} pages, {fetched} positions, {len(totals)} tokens")
            break

    if cursor:
        logger.info(f"Printr staking: fetched {fetched} positions ({max_pages} pages), {len(totals)} tokens (more exist)")

    return totals


async def fetch_staking_totals_async(max_pages: int = 60, page_size: int = 100) -> dict[str, float]:
    """Async wrapper: runs fetch_staking_totals_sync in a thread executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: fetch_staking_totals_sync(max_pages, page_size))


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
