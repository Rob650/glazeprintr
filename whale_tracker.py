"""
GlazePrintr Whale Wallet Tracking.

Detects anomalous large-volume activity (>$5K, 3x hourly average) across all
ecosystem tokens using DexScreener. Optionally enriches with Solana RPC top-holder
data to identify wallets holding >5% of supply.

Feature flag: ENABLE_WHALE_TRACKING (default: false)
"""

import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone

from scraper import DEXSCREENER_API, KNOWN_CONTRACTS, _fetch_url_sync

logger = logging.getLogger(__name__)

ENABLE_WHALE_TRACKING = os.environ.get("ENABLE_WHALE_TRACKING", "false").lower() == "true"

_SOLANA_RPC = os.environ.get("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
_WHALE_TX_THRESHOLD_USD = float(os.environ.get("WHALE_TX_THRESHOLD_USD", "5000"))
_WHALE_VOLUME_MULTIPLIER = 3.0
_WHALE_SUPPLY_PCT = 5.0


def _solana_rpc(method: str, params: list) -> dict | None:
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    try:
        req = urllib.request.Request(
            _SOLANA_RPC,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.loads(resp.read()).get("result")
    except Exception as e:
        logger.debug(f"Solana RPC {method} failed: {e}")
        return None


def _refresh_whale_wallets_for_token(ticker: str, contract: str) -> None:
    """Store top holders (>5% supply) from Solana RPC getTokenLargestAccounts."""
    from database import upsert_whale_wallet
    result = _solana_rpc("getTokenLargestAccounts", [contract])
    if not result:
        return
    accounts = result.get("value") or []
    total = sum(float(a.get("amount", 0)) for a in accounts)
    if total <= 0:
        return
    for acc in accounts:
        address = acc.get("address", "")
        pct = float(acc.get("amount", 0)) / total * 100
        if pct >= _WHALE_SUPPLY_PCT and address:
            upsert_whale_wallet(address, f"{ticker.upper()} whale ({pct:.1f}%)")


def _detect_token_whale_move(ticker: str, contract: str) -> dict | None:
    """Return a whale-move dict if 1h volume is anomalously large, else None."""
    data = _fetch_url_sync(DEXSCREENER_API.format(contract), timeout=8)
    if not data:
        return None

    pairs = data.get("pairs") or []
    if not pairs:
        return None

    pair = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))

    volume = pair.get("volume") or {}
    vol_1h = float(volume.get("h1") or 0)
    vol_24h = float(volume.get("h24") or 0)

    if vol_1h < _WHALE_TX_THRESHOLD_USD:
        return None

    if vol_24h > 0:
        hourly_avg = vol_24h / 24
        if hourly_avg > 0 and vol_1h < hourly_avg * _WHALE_VOLUME_MULTIPLIER:
            return None

    price_change = pair.get("priceChange") or {}
    change_1h = float(price_change.get("h1") or 0)

    txns_1h = (pair.get("txns") or {}).get("h1") or {}
    buys_1h = int(txns_1h.get("buys") or 0)
    sells_1h = int(txns_1h.get("sells") or 0)

    if buys_1h + sells_1h == 0:
        return None

    action = "BUY" if buys_1h >= sells_1h else "SELL"

    return {
        "ticker": ticker,
        "contract": contract,
        "action": action,
        "amount_usd": vol_1h,
        "price_change_1h": change_1h,
        "buys_1h": buys_1h,
        "sells_1h": sells_1h,
        "market_cap": float(pair.get("marketCap") or pair.get("fdv") or 0),
        "price_usd": float(pair.get("priceUsd") or 0),
    }


def detect_whale_activity() -> list[dict]:
    """
    Scan all ecosystem tokens for anomalous 1h volume (>$5K and 3x hourly average).
    New moves are written to DB (deduped per token per hour bucket).
    Returns list of newly-recorded moves.
    """
    if not ENABLE_WHALE_TRACKING:
        return []

    from database import record_whale_transaction

    hour_bucket = int(time.time() // 3600)
    new_moves: list[dict] = []

    for ticker, contract in KNOWN_CONTRACTS.items():
        try:
            move = _detect_token_whale_move(ticker, contract)
            if not move:
                continue

            # Best-effort wallet refresh via Solana RPC
            try:
                _refresh_whale_wallets_for_token(ticker, contract)
            except Exception:
                pass

            wallet_key = f"detected_{ticker}_{move['action'].lower()}_{hour_bucket}"
            recorded = record_whale_transaction(
                wallet_address=wallet_key,
                token_ticker=ticker,
                action=move["action"],
                amount_usd=move["amount_usd"],
            )
            if recorded:
                new_moves.append(move)
                logger.info(
                    f"whale_tracker: new move ${ticker.upper()} {move['action']} "
                    f"~${move['amount_usd']:,.0f} vol_1h change_1h={move['price_change_1h']:+.1f}%"
                )
        except Exception as e:
            logger.warning(f"whale_tracker: error on {ticker}: {e}")

    return new_moves


def format_whale_tweet_context(move: dict) -> str:
    """Format a whale move dict into a prompt snippet for Claude."""
    dominance = "buy-dominant" if move["action"] == "BUY" else "sell-dominant"
    lines = [
        f"VOLUME ANOMALY — ${move['ticker'].upper()}:",
        f"  Signal: anomalous volume spike — {dominance} activity detected",
        f"  1h volume: ${move['amount_usd']:,.0f} (well above hourly average)",
        f"  1h price change: {move['price_change_1h']:+.1f}%",
        f"  Buy/sell split: {move['buys_1h']} buys / {move['sells_1h']} sells",
    ]
    if move.get("market_cap"):
        mc = move["market_cap"]
        lines.append(f"  Market cap: ${mc/1e6:.2f}M" if mc >= 1e6 else f"  Market cap: ${mc:,.0f}")
    lines.append(
        "\nGenerate a tweet about this volume anomaly. "
        "Be honest: this is unusual buying/selling activity, not a confirmed single wallet. "
        "Example tone: '$FATCHOI seeing anomalous buy pressure — $12K in the last hour, 3x the hourly average.' "
        "Be specific with the numbers. Bullish spin if buy-dominant, neutral-observational if sell-dominant. "
        "Max 240 chars. No URLs. Glaze vocab mandatory."
    )
    return "\n".join(lines)
