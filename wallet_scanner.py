"""
Wallet scanner and glaze priority engine for GlazePrintr.

Scans the bot's Solana wallet for Printr ecosystem tokens and calculates
glaze tiers based on TOTAL holdings (wallet balance + any pre-existing
staked positions).

RECEIVE-ONLY — the bot never stakes, transfers, or moves tokens. It only
reads on-chain balances and uses them to drive glaze priority.
"""
import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

import database as db
from scraper import KNOWN_CONTRACTS, fetch_token_data_sync

logger = logging.getLogger(__name__)

# RECEIVE-ONLY — wallet is deposit only. No staking, no transfers.
# Public address is hardcoded so the bot can share it openly. Env override allowed for testing.
BOT_WALLET_ADDRESS_DEFAULT = "8V9eDTUG8ZFa7sC8SZxgHs8bqEUTet7aHjZT9zsFq3Mv"
BOT_WALLET_ADDRESS: str = os.environ.get("BOT_WALLET_ADDRESS") or BOT_WALLET_ADDRESS_DEFAULT

_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
_PRINTR_API_BASE = "https://api-preview.printr.money/v1"

# Reverse mapping: mint address → ticker
_MINT_TO_TICKER: dict[str, str] = {v: k for k, v in KNOWN_CONTRACTS.items()}

# (min_usd, tier_name, daily_glazes)
# Tier based on TOTAL value (wallet balance + staked) per token
_TIERS: list[tuple[float, str, int]] = [
    (1000.0, "legendary", 0),  # $1000+ — dedicated threads, priority QTs; 0 = unlimited/priority
    (200.0,  "gold",      5),  # $200–$1000 — 5 glazes/day
    (50.0,   "silver",    2),  # $50–$200 — 2–3 glazes/day
    (10.0,   "bronze",    1),  # $10–$50 — 1 glaze/day
]

# Minimum hours between glazes per tier
_TIER_INTERVAL_HOURS: dict[str, float] = {
    "legendary": 3.0,
    "gold":      4.8,   # ~5/day
    "silver":    8.0,   # ~3/day
    "bronze":    24.0,  # 1/day
}

# ---------------------------------------------------------------------------
# Solana RPC
# ---------------------------------------------------------------------------

def _rpc_call(method: str, params: list, timeout: int = 15) -> Optional[dict]:
    """Make a Solana JSON-RPC call. Returns parsed response or None on error."""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode()
    req = urllib.request.Request(
        _SOLANA_RPC,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.warning(f"Solana RPC {method} error: {e}")
    return None


# ---------------------------------------------------------------------------
# Wallet balance scanning
# ---------------------------------------------------------------------------

def scan_wallet_balances() -> list[dict]:
    """
    Scan bot wallet for Printr ecosystem tokens via Solana RPC getTokenAccountsByOwner.
    Returns list of {ticker, mint, balance, usd_value, decimals, price}.
    usd_value is wallet-only (unstaked). Tier calc adds staked on top separately.
    """
    if not BOT_WALLET_ADDRESS:
        logger.info("BOT_WALLET_ADDRESS not set — skipping wallet scan")
        return []

    resp = _rpc_call("getTokenAccountsByOwner", [
        BOT_WALLET_ADDRESS,
        {"programId": _TOKEN_PROGRAM_ID},
        {"encoding": "jsonParsed"},
    ])
    if not resp or "result" not in resp:
        logger.warning("getTokenAccountsByOwner: no result")
        return []

    holdings: list[dict] = []
    for item in resp["result"].get("value") or []:
        info = (
            (item.get("account") or {})
            .get("data", {})
            .get("parsed", {})
            .get("info") or {}
        )
        mint = info.get("mint", "")
        ticker = _MINT_TO_TICKER.get(mint)
        if not ticker:
            continue  # not a known ecosystem token

        token_amount = info.get("tokenAmount") or {}
        decimals = int(token_amount.get("decimals", 9))
        ui_amount = float(token_amount.get("uiAmount") or 0)
        if ui_amount <= 0:
            continue

        token_data = fetch_token_data_sync(mint, timeout=8)
        price = (token_data or {}).get("price") or 0.0
        usd_value = ui_amount * price

        holdings.append({
            "ticker":   ticker,
            "mint":     mint,
            "balance":  ui_amount,
            "usd_value": usd_value,
            "decimals": decimals,
            "price":    price,
        })
        logger.info(f"Wallet: {ticker} balance={ui_amount:.4f} (${usd_value:.2f})")

    return holdings


# ---------------------------------------------------------------------------
# Bot staking positions
# ---------------------------------------------------------------------------

def _fetch_bot_staking_positions() -> dict[str, float]:
    """
    Fetch active staking positions owned by the bot wallet.
    Returns {ticker: staked_token_amount}.
    Tries Printr API first (wallet filter), falls back to DB staking_transactions.
    """
    from printr_api import get_cookie, invalidate_cookie  # type: ignore

    staked: dict[str, float] = {}

    if not BOT_WALLET_ADDRESS:
        return staked

    cookie = get_cookie()
    if cookie:
        try:
            payload = json.dumps({
                "walletAddress": BOT_WALLET_ADDRESS,
                "limit": 200,
            }).encode()
            req = urllib.request.Request(
                f"{_PRINTR_API_BASE}/staking/list-positions",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Cookie": f"printr-integrator-token={cookie}",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
            for pos in data.get("positions") or []:
                if pos.get("was_closed"):
                    continue
                acct = (pos.get("staked") or {}).get("asset", {}).get("account", "")
                parts = acct.split(":")
                if len(parts) == 3:
                    mint = parts[2]
                    ticker = _MINT_TO_TICKER.get(mint)
                    if not ticker:
                        continue
                    try:
                        atomic = int((pos.get("staked") or {}).get("atomic", 0))
                        decimals = int((pos.get("staked") or {}).get("decimals", 9))
                        amount = atomic / (10 ** decimals)
                        staked[ticker] = staked.get(ticker, 0.0) + amount
                    except (ValueError, TypeError):
                        pass
            if staked:
                logger.info(f"Bot staking positions (API): {staked}")
                return staked
        except urllib.error.HTTPError as e:
            if e.code == 307:
                invalidate_cookie()
            logger.debug(f"Bot staking API error: HTTP {e.code}")
        except Exception as e:
            logger.debug(f"Bot staking API fetch failed: {e}")

    # Fallback: confirmed staking transactions recorded in DB
    try:
        staked = db.get_confirmed_staked_amounts()
        if staked:
            logger.info(f"Bot staking positions (DB fallback): {staked}")
    except Exception as e:
        logger.debug(f"DB staking amounts lookup failed: {e}")

    return staked


# ---------------------------------------------------------------------------
# Tier assignment
# ---------------------------------------------------------------------------

def _assign_tier(total_usd: float) -> Optional[tuple[str, int]]:
    """Return (tier_name, daily_glazes) or None if below $10 minimum."""
    for threshold, tier_name, daily_glazes in _TIERS:
        if total_usd >= threshold:
            return tier_name, daily_glazes
    return None


# ---------------------------------------------------------------------------
# Glaze priority — lightweight DB reads (populated by scan_wallet)
# ---------------------------------------------------------------------------

def get_glaze_priority() -> dict[str, dict]:
    """
    Returns {ticker: {tier, usd_value, daily_glazes, last_glazed_at}} from DB cache.
    Tier is based on TOTAL value (wallet balance + any pre-existing staked positions).
    Updated by scan_wallet.
    """
    result: dict[str, dict] = {}
    for holding in db.get_wallet_holdings():
        if not holding.get("tier"):
            continue
        tier = holding["tier"]
        daily_glazes = next((g for _, t, g in _TIERS if t == tier), 0)
        result[holding["ticker"]] = {
            "tier":          tier,
            "usd_value":     holding.get("usd_value") or 0.0,
            "daily_glazes":  daily_glazes,
            "last_glazed_at": holding.get("last_glazed_at") or "",
        }
    return result


def should_glaze_now(ticker: str) -> bool:
    """True if enough time has elapsed since last glaze for this ticker's tier (DB cache)."""
    holdings = {h["ticker"]: h for h in db.get_wallet_holdings()}
    info = holdings.get(ticker.lower())
    if not info or not info.get("tier"):
        return False

    tier = info["tier"]
    last_glazed = info.get("last_glazed_at") or ""
    if not last_glazed:
        return True

    interval_hours = _TIER_INTERVAL_HOURS.get(tier, 24.0)
    try:
        last_dt = datetime.fromisoformat(last_glazed)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
        return elapsed >= interval_hours
    except (ValueError, TypeError):
        return True


# ---------------------------------------------------------------------------
# Full scan pipeline (RECEIVE-ONLY — no staking, no transfers)
# ---------------------------------------------------------------------------

def _scan_wallet_sync() -> None:
    """
    Full sync pipeline:
      1. Scan wallet balances via Solana RPC
      2. Fetch any pre-existing staked positions via Printr API (or DB fallback)
      3. Compute total USD per token (wallet + staked) → assign glaze tier
      4. Upsert wallet_holdings in DB
    """
    if not BOT_WALLET_ADDRESS:
        logger.info("WALLET GLAZING: BOT_WALLET_ADDRESS not set — skipping")
        return

    wallet_balances = scan_wallet_balances()
    staked_amounts  = _fetch_bot_staking_positions()

    # Build price lookup from wallet scan
    prices: dict[str, float] = {h["ticker"]: h["price"] for h in wallet_balances}

    # Fetch prices for tokens that are only staked (not in wallet)
    for ticker in staked_amounts:
        if ticker not in prices:
            mint = KNOWN_CONTRACTS.get(ticker, "")
            if mint:
                token_data = fetch_token_data_sync(mint, timeout=8)
                prices[ticker] = (token_data or {}).get("price") or 0.0

    wallet_by_ticker = {h["ticker"]: h for h in wallet_balances}
    all_tickers = set(list(wallet_by_ticker.keys()) + list(staked_amounts.keys()))

    now_str = datetime.now(timezone.utc).isoformat()
    for ticker in all_tickers:
        wallet_bal = (wallet_by_ticker.get(ticker) or {}).get("balance", 0.0)
        staked_bal = staked_amounts.get(ticker, 0.0)
        price      = prices.get(ticker, 0.0)
        # Tier is based on TOTAL value: wallet balance + any pre-existing staked positions
        total_usd  = (wallet_bal + staked_bal) * price
        mint       = KNOWN_CONTRACTS.get(ticker, "")

        tier_result = _assign_tier(total_usd)
        tier = tier_result[0] if tier_result else None

        db.upsert_wallet_holding(
            ticker=ticker,
            mint_address=mint,
            balance=wallet_bal,
            usd_value=total_usd,
            tier=tier,
            last_scanned=now_str,
        )

        if tier:
            logger.info(
                f"Wallet holding: {ticker} wallet={wallet_bal:.4f} "
                f"staked={staked_bal:.4f} total=${total_usd:.2f} tier={tier}"
            )


async def scan_wallet() -> None:
    """Async entry point: runs the full receive-only scan pipeline in a thread executor."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _scan_wallet_sync)


# ---------------------------------------------------------------------------
# Auto-claim staking rewards (default off — gated by ENABLE_AUTO_CLAIM_REWARDS).
# Reading + claiming earned rewards on pre-existing positions is read-side only;
# it does not create new stake positions, so this coexists with receive-only mode.
# ---------------------------------------------------------------------------

def _claim_staking_rewards_sync() -> None:
    """
    For every known ecosystem token, fetch positions with unclaimed rewards belonging
    to the bot wallet, call claim-rewards for each, and record in reward_claims table.
    """
    from printr_api import fetch_positions_with_rewards, claim_rewards  # type: ignore
    from scraper import KNOWN_CONTRACTS  # type: ignore

    if not BOT_WALLET_ADDRESS:
        logger.info("AUTO-CLAIM: BOT_WALLET_ADDRESS not set — skipping")
        return

    total_claimed = 0
    for ticker, mint_address in KNOWN_CONTRACTS.items():
        try:
            positions = fetch_positions_with_rewards(mint_address)
        except Exception as e:
            logger.warning(f"AUTO-CLAIM: fetch_positions_with_rewards {ticker}: {e}")
            continue

        for pos in positions:
            # Only claim positions belonging to the bot wallet
            pos_wallet = (
                pos.get("wallet_address")
                or pos.get("owner")
                or (pos.get("user") or {}).get("wallet_address", "")
            )
            if pos_wallet and pos_wallet != BOT_WALLET_ADDRESS:
                continue

            position_id = pos.get("position_id") or pos.get("id") or ""
            if not position_id:
                continue

            # Flexible reward amount extraction
            rewards = pos.get("rewards") or pos.get("unclaimed_rewards") or {}
            if isinstance(rewards, dict):
                amount = float(rewards.get("amount") or rewards.get("unclaimed") or 0)
            else:
                try:
                    amount = float(rewards)
                except (TypeError, ValueError):
                    amount = 0.0

            if amount <= 0:
                continue

            logger.info(f"AUTO-CLAIM: claiming {amount:.6f} {ticker} for position {position_id}")
            try:
                result = claim_rewards(str(position_id), BOT_WALLET_ADDRESS, mint_address)
                if result is not None:
                    tx = result.get("tx_hash") or result.get("signature") or "submitted"
                    db.record_reward_claim(ticker, str(position_id), amount)
                    total_claimed += 1
                    logger.info(f"AUTO-CLAIM: {ticker} {amount:.6f} tokens claimed — tx={tx}")
                else:
                    logger.warning(f"AUTO-CLAIM: claim_rewards returned None for {ticker} position {position_id}")
            except Exception as e:
                logger.warning(f"AUTO-CLAIM: error claiming {ticker} position {position_id}: {e}")

    logger.info(f"AUTO-CLAIM: complete — {total_claimed} positions claimed")


async def claim_staking_rewards() -> None:
    """Async entry point: claims pending staking rewards for all eligible bot positions."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _claim_staking_rewards_sync)
