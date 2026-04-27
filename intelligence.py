"""
GlazePrintr ecosystem intelligence layer.

get_optimal_interval() is also exported for time-based posting optimization.

Scores and ranks all 16 ecosystem tokens by a composite heat score,
detects movers (pump/dump/volume anomaly/ATH/breakout), maintains an
ecosystem health dashboard, injects macro context into tweet generation,
and exports dynamic keyword weights for all three bot functions.

Refresh cycle: every 15 minutes via APScheduler (see main.py).
"""

import logging
import random
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Token list ────────────────────────────────────────────────────────────────

ECOSYSTEM_TOKENS = [
    "belief", "ooo", "rotus", "fatchoi", "deployr", "patapim",
    "roi", "print", "cmyk", "pve", "fsjal",
    "brrr", "quack", "lfp", "stakr", "pob500",
]

# ── Heat score component weights (must sum to 1.0) ───────────────────────────

_W_MOMENTUM_1H  = 0.35
_W_MOMENTUM_6H  = 0.20
_W_MOMENTUM_24H = 0.15
_W_VOLUME_SURGE = 0.15
_W_STAKING      = 0.08
_W_LIQUIDITY    = 0.07

# ── Mover detection thresholds ───────────────────────────────────────────────

_PUMP_1H_PCT       = 20.0   # ≥ 20% in 1h → "pumping"
_DUMP_1H_PCT       = -15.0  # ≤ −15% in 1h → "dumping"
_VOLUME_SURGE_MULT = 3.0    # 1h vol ≥ 3× hourly average → "volume_anomaly"
_ATH_PROXY_24H    = 50.0   # ≥ 50% in 24h + positive 1h → "new_ath" (proxy)
_BREAKOUT_24H_MIN  = 10.0   # 10–49% in 24h + ≥ 5% in 1h → "breakout"
_BREAKOUT_1H_MIN   = 5.0

# ── MC tier thresholds ───────────────────────────────────────────────────────

_MC_WHALE = 1_000_000  # > $1M → whale
_MC_MID   =   100_000  # $100K–$1M → mid

# ── Macro knowledge base ─────────────────────────────────────────────────────

_MACRO: dict[str, object] = {
    "regulatory": [
        "CLARITY Act progress → token classification clarity → launchpad legal risk drops → Printr's utility-first model benefits most",
        "Stablecoin legislation advancing → on-chain commerce rails strengthen → launch and trading volume follow stable payment layers",
        "SEC spot BTC ETF precedent signals regulatory normalization → institutional allocation to alt-layer plays like launchpads accelerates",
        "Crypto-friendly Congress sessions historically precede 6–12 month altcoin outperformance cycles — launchpad volume is a leading indicator",
    ],
    "monetary_risk_on": [
        "Rate cuts or dovish Fed pivot → risk-on rotation → capital cascades into high-beta crypto → memecoin and launchpad volume spikes",
        "Falling DXY + declining real yields historically correlate with alt season — launchpad activity (launches, stakes, trades) surges first",
        "Liquidity injection cycles historically pump small-cap altcoins 5–10× before majors — Printr ecosystem tokens are the highest-conviction small caps on Solana",
        "M2 expansion historically leads crypto market caps by ~3 months — when the printer goes brrr for real, Printr captures it",
    ],
    "monetary_risk_off": [
        "Fed hawkishness compresses memecoin speculation — but real-yield staking (POB fee distribution) outperforms inflationary tokenomics in this environment",
        "Risk-off favors conviction plays over pure speculation — 180d POB lockers earning real trading fees beats chasing pumps on zero-utility tokens",
        "Higher rates actually validate Printr's 100% fee-share model: when yields matter, on-chain real yield (not printed emissions) wins the capital allocation argument",
    ],
    "ai": [
        "Printr's MCP server lets AI agents launch tokens, stake, and manage positions programmatically — the only launchpad built natively for autonomous agents",
        "TypeScript SDK + white-label API means any AI app integrates Printr with zero friction — programmable fee structures give agents optimization levers no other launchpad has",
        "As AI agents become crypto-native market participants, API-first infrastructure (Printr) captures disproportionate volume vs UI-only platforms that require human interaction",
        "The next wave of token launches won't be human-initiated — AI agents will deploy tokens, configure bonding curves, and manage POB staking autonomously via Printr's API",
        "Composable staking (POB) gives AI agents a yield-optimization primitive that doesn't exist elsewhere — 2.5× multiplier at 180d is a programmatic edge, not just a UI feature",
        "Every other launchpad is built for humans with browsers. Printr is the first one built for agents with API keys. That's not a roadmap item — it's live",
    ],
    "solana": [
        "Solana network throughput improvements reduce friction for token launches and staking interactions — Printr's Solana-native architecture benefits first",
        "Jupiter/Raydium DEX integrations deepen the liquidity universe Printr tokens graduate into — better post-graduation liquidity improves launch economics for every creator",
        "Firedancer client increases Solana validator decentralization and uptime — more reliable infrastructure for Printr's Solana launchpad and staking contracts",
        "Solana DeFi TVL growth expands the capital available for ecosystem token trading — more liquidity → better price discovery → better launch outcomes",
        "Solana's sub-cent fees make micro-staking viable at every MC tier — no other L1 makes 180d conviction this cheap to execute",
    ],
}

# ── Module-level cache ────────────────────────────────────────────────────────

_cached_intelligence: Optional["EcosystemIntelligence"] = None


# ── EcosystemIntelligence ─────────────────────────────────────────────────────


class EcosystemIntelligence:
    """
    Immutable snapshot of ecosystem intelligence built from one scrape cycle.
    All downstream consumers read from the module-level cache.
    """

    __slots__ = ("timestamp", "projects", "ranked_tokens", "top_movers", "health")

    def __init__(self, projects: list[dict]):
        self.timestamp: float = time.time()
        self.projects: list[dict] = projects
        self.ranked_tokens: list[dict] = []
        self.top_movers: dict[str, list[dict]] = {
            "pumping": [],
            "dumping": [],
            "volume_anomaly": [],
            "new_ath": [],
            "breakout": [],
        }
        self.health: dict = {}
        self._build(projects)

    # ── Scoring ───────────────────────────────────────────────────────────────

    @staticmethod
    def _norm_pct(v: float) -> float:
        """Clamp ±100% change and normalize to [0, 1]."""
        return (max(-100.0, min(100.0, v)) + 100.0) / 200.0

    def _score_token(self, token: dict) -> float:
        chg_1h  = token.get("price_change_1h")  or 0.0
        chg_6h  = token.get("price_change_6h")  or 0.0
        chg_24h = token.get("price_change_24h") or 0.0

        # Weighted momentum — normalized to 0..100
        momentum_raw = (
            self._norm_pct(chg_1h)  * _W_MOMENTUM_1H
            + self._norm_pct(chg_6h)  * _W_MOMENTUM_6H
            + self._norm_pct(chg_24h) * _W_MOMENTUM_24H
        )
        # Divide by the total momentum weight to keep range 0..1, then scale to 100
        momentum_sum = _W_MOMENTUM_1H + _W_MOMENTUM_6H + _W_MOMENTUM_24H
        momentum_score = (momentum_raw / momentum_sum) * 100.0

        # Volume surge vs hourly average
        vol_24h = token.get("volume")   or 0.0
        vol_1h  = token.get("volume_1h") or 0.0
        if vol_24h > 0 and vol_1h > 0:
            surge = vol_1h / (vol_24h / 24.0)
            vol_score = min(surge / _VOLUME_SURGE_MULT, 1.0) * 100.0
        else:
            vol_score = 0.0

        # Staking conviction
        staking_pct   = token.get("staking_pct") or 0.0
        staking_score = min(staking_pct, 100.0)

        # Liquidity depth ($50K → full score)
        liq       = token.get("liquidity") or 0.0
        liq_score = min(liq / 50_000.0, 1.0) * 100.0

        score = (
            momentum_score * momentum_sum
            + vol_score    * _W_VOLUME_SURGE
            + staking_score * _W_STAKING
            + liq_score    * _W_LIQUIDITY
        )
        return max(0.0, min(100.0, score))

    # ── Mover detection ───────────────────────────────────────────────────────

    @staticmethod
    def _detect_flags(token: dict) -> list[str]:
        flags: list[str] = []
        chg_1h  = token.get("price_change_1h")  or 0.0
        chg_24h = token.get("price_change_24h") or 0.0
        vol_24h = token.get("volume")            or 0.0
        vol_1h  = token.get("volume_1h")         or 0.0

        if chg_1h >= _PUMP_1H_PCT:
            flags.append("pumping")
        if chg_1h <= _DUMP_1H_PCT:
            flags.append("dumping")
        if vol_24h > 0 and vol_1h > 0:
            hourly_avg = vol_24h / 24.0
            if hourly_avg > 0 and vol_1h >= hourly_avg * _VOLUME_SURGE_MULT:
                flags.append("volume_anomaly")
        if chg_24h >= _ATH_PROXY_24H and chg_1h > 0:
            flags.append("new_ath")
        elif _BREAKOUT_24H_MIN <= chg_24h < _ATH_PROXY_24H and chg_1h >= _BREAKOUT_1H_MIN:
            flags.append("breakout")

        return flags

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self, projects: list[dict]) -> None:
        if not projects:
            return

        scored: list[dict] = []
        for token in projects:
            heat  = self._score_token(token)
            flags = self._detect_flags(token)
            enriched = {**token, "heat_score": round(heat, 1), "mover_flags": flags}
            scored.append(enriched)
            for flag in flags:
                self.top_movers[flag].append(enriched)

        self.ranked_tokens = sorted(scored, key=lambda t: t["heat_score"], reverse=True)

        # ── Ecosystem health dashboard ────────────────────────────────────────
        active        = [p for p in projects if (p.get("volume") or 0) >= 1_000]
        total_mc      = sum(p.get("market_cap") or 0 for p in projects)
        total_vol     = sum(p.get("volume")     or 0 for p in projects)
        staking_tkns  = [p for p in projects if p.get("staking_pct") is not None]
        total_staked_usd = sum(
            (p.get("market_cap") or 0) * (p["staking_pct"] / 100.0)
            for p in staking_tkns
        )
        avg_staking = (
            sum(p["staking_pct"] for p in staking_tkns) / len(staking_tkns)
            if staking_tkns else 0.0
        )

        # MC-weighted 24h momentum → ecosystem momentum score (50 = neutral)
        mc_movers    = [p for p in projects if p.get("price_change_24h") is not None and (p.get("market_cap") or 0) > 0]
        total_weight = sum(p["market_cap"] for p in mc_movers)
        weighted_mom = (
            sum(p["price_change_24h"] * p["market_cap"] / total_weight for p in mc_movers)
            if total_weight > 0 else 0.0
        )
        momentum_score = max(0.0, min(100.0, 50.0 + weighted_mom))
        trending = "up" if weighted_mom > 5 else "down" if weighted_mom < -5 else "flat"

        # Average holder count (proxy for community size)
        holders_data = [p for p in projects if p.get("holder_count")]
        avg_holders = (
            sum(p["holder_count"] for p in holders_data) / len(holders_data)
            if holders_data else 0.0
        )

        self.health = {
            "total_mc": total_mc,
            "total_vol_24h": total_vol,
            "active_token_count": len(active),
            "tracked_token_count": len(projects),
            "total_staked_usd": total_staked_usd,
            "avg_staking_pct": avg_staking,
            "avg_holder_count": avg_holders,
            "ecosystem_momentum_score": round(momentum_score, 1),
            "ecosystem_trending": trending,
            "timestamp": self.timestamp,
        }

    # ── Public accessors ──────────────────────────────────────────────────────

    def get_priority_tickers_for_originals(self, n: int = 5) -> list[str]:
        """Tickers for original tweets: movers first, then heat-ranked fill."""
        priority: list[str] = []
        for flag in ("pumping", "new_ath", "volume_anomaly", "breakout"):
            for t in self.top_movers[flag]:
                name = t.get("name", "").lower()
                if name and name not in priority:
                    priority.append(name)
        for t in self.ranked_tokens:
            if len(priority) >= n:
                break
            name = t.get("name", "").lower()
            if name and name not in priority:
                priority.append(name)
        return priority[:n]

    def get_qt_search_tickers(self, n: int = 8) -> list[str]:
        """Tickers to prioritize in QT keyword searches and Twitter search queries."""
        tickers: list[str] = []
        for flag in ("pumping", "volume_anomaly", "new_ath", "breakout"):
            for t in self.top_movers[flag]:
                name = t.get("name", "").lower()
                if name and name not in tickers:
                    tickers.append(name)
        for t in self.ranked_tokens:
            if len(tickers) >= n:
                break
            name = t.get("name", "").lower()
            if name and name not in tickers:
                tickers.append(name)
        return tickers[:n]

    def get_token_intelligence(self, token_name: str) -> Optional[dict]:
        """Full intelligence record for a token — for reply context enrichment."""
        lower = token_name.lower()
        for rank, t in enumerate(self.ranked_tokens, start=1):
            if t.get("name", "").lower() == lower:
                mc = t.get("market_cap") or 0
                return {
                    **t,
                    "ecosystem_rank": rank,
                    "mc_tier": (
                        "whale" if mc >= _MC_WHALE
                        else "mid" if mc >= _MC_MID
                        else "micro"
                    ),
                }
        return None

    def format_for_prompt(self) -> str:
        """Format intelligence as a Claude-injectable context block."""
        lines: list[str] = []

        # ── Priority movers ──
        seen: set[str] = set()
        hot: list[dict] = []
        for flag in ("pumping", "new_ath", "volume_anomaly", "breakout"):
            for t in self.top_movers[flag]:
                name = t.get("name", "").lower()
                if name and name not in seen:
                    hot.append(t)
                    seen.add(name)

        if hot:
            lines.append("PRIORITY MOVERS — tweet about these FIRST (live signals):")
            for t in hot[:5]:
                name    = t.get("name", "?").upper()
                flags   = ", ".join(t.get("mover_flags", []))
                heat    = t.get("heat_score", 0)
                chg_1h  = t.get("price_change_1h")
                chg_24h = t.get("price_change_24h")
                mc      = t.get("market_cap") or 0
                mc_str  = f"${mc/1e6:.2f}M" if mc >= 1e6 else f"${mc:,.0f}"
                vol_1h  = t.get("volume_1h")
                line = f"  ${name} [heat={heat:.0f}/100, {flags}] MC={mc_str}"
                if chg_1h  is not None: line += f" 1h:{chg_1h:+.1f}%"
                if chg_24h is not None: line += f" 24h:{chg_24h:+.1f}%"
                if vol_1h:              line += f" vol1h:${vol_1h:,.0f}"
                lines.append(line)

        # ── Top by heat score (excluding movers already listed) ──
        top_others = [t for t in self.ranked_tokens if t.get("name", "").lower() not in seen][:5]
        if top_others:
            lines.append("TOP TOKENS BY HEAT SCORE:")
            for t in top_others:
                name    = t.get("name", "?").upper()
                heat    = t.get("heat_score", 0)
                mc      = t.get("market_cap") or 0
                mc_str  = f"${mc/1e6:.2f}M" if mc >= 1e6 else f"${mc:,.0f}"
                chg_24h = t.get("price_change_24h")
                staking = t.get("staking_pct")
                line = f"  ${name} heat={heat:.0f}/100 MC={mc_str}"
                if chg_24h is not None: line += f" 24h:{chg_24h:+.1f}%"
                if staking is not None: line += f" staked:{staking:.0f}%"
                lines.append(line)

        # ── Ecosystem health ──
        h        = self.health
        mc_t     = h.get("total_mc", 0)
        vol_t    = h.get("total_vol_24h", 0)
        staked_t = h.get("total_staked_usd", 0)
        mc_str   = f"${mc_t/1e6:.2f}M"     if mc_t     >= 1e6 else f"${mc_t:,.0f}"
        vol_str  = f"${vol_t/1e6:.2f}M"    if vol_t    >= 1e6 else f"${vol_t:,.0f}"
        st_str   = f"${staked_t/1e6:.2f}M" if staked_t >= 1e6 else f"${staked_t:,.0f}"
        lines.append(
            f"ECOSYSTEM HEALTH: Total MC={mc_str} | 24h Vol={vol_str} | "
            f"Active tokens={h.get('active_token_count', 0)} | "
            f"Est. staked≈{st_str} | "
            f"Trending {h.get('ecosystem_trending', 'flat').upper()} "
            f"(momentum {h.get('ecosystem_momentum_score', 50):.0f}/100)"
        )

        return "\n".join(lines)


# ── Macro context ─────────────────────────────────────────────────────────────


def get_macro_context(trending: str = "flat") -> str:
    """
    Return a prompt-injectable macro context string.
    Always includes the AI agent narrative (Printr's strongest differentiator)
    and a Solana catalyst. Monetary framing adapts to ecosystem trend.
    Regulatory context injected ~30% of the time.
    """
    parts: list[str] = ["MACRO CONTEXT (weave relevant narratives naturally into your take):"]

    # AI agent narrative — always, strongest Printr differentiator
    parts.append(f"AI Agent Infra: {random.choice(_MACRO['ai'])}")  # type: ignore[arg-type]

    # Monetary framing adapts to ecosystem trend
    if trending == "up":
        parts.append(f"Market Catalyst: {random.choice(_MACRO['monetary_risk_on'])}")  # type: ignore[arg-type]
    elif trending == "down":
        parts.append(f"Macro Defense: {random.choice(_MACRO['monetary_risk_off'])}")  # type: ignore[arg-type]

    # Solana catalyst — always relevant
    parts.append(f"Solana Catalyst: {random.choice(_MACRO['solana'])}")  # type: ignore[arg-type]

    # Regulatory tailwind — occasional
    if random.random() < 0.30:
        parts.append(f"Regulatory Tailwind: {random.choice(_MACRO['regulatory'])}")  # type: ignore[arg-type]

    return "\n".join(parts)


# ── Cache management ──────────────────────────────────────────────────────────


async def refresh_intelligence(
    projects: Optional[list[dict]] = None,
) -> Optional[EcosystemIntelligence]:
    """
    Build a fresh EcosystemIntelligence snapshot and update the module cache.

    If `projects` is supplied (e.g. from an already-running scrape), reuse it
    to avoid a duplicate DexScreener fetch.  Otherwise scrape fresh.

    Returns the new snapshot on success, or the stale cache on failure.
    """
    global _cached_intelligence

    if projects is None:
        from scraper import scrape_all_data  # local import avoids circular dep
        try:
            projects = await scrape_all_data()
        except Exception as exc:
            logger.error(f"intelligence.refresh: scrape_all_data failed: {exc}")
            return _cached_intelligence

    if not projects:
        logger.warning("intelligence.refresh: empty project list — keeping stale cache")
        return _cached_intelligence

    try:
        intel = EcosystemIntelligence(projects)
        _cached_intelligence = intel
        pumping = len(intel.top_movers["pumping"])
        anomaly = len(intel.top_movers["volume_anomaly"])
        breakout = len(intel.top_movers["breakout"])
        logger.info(
            f"intelligence.refresh: {len(intel.ranked_tokens)} tokens | "
            f"pumping={pumping} volume_anomaly={anomaly} breakout={breakout} | "
            f"trending={intel.health.get('ecosystem_trending')} "
            f"momentum={intel.health.get('ecosystem_momentum_score')}"
        )
        return intel
    except Exception as exc:
        logger.error(f"intelligence.refresh: build failed: {exc}")
        return _cached_intelligence


def get_intelligence() -> Optional[EcosystemIntelligence]:
    """Return the cached snapshot, or None if not yet built."""
    return _cached_intelligence


def get_optimal_interval() -> int:
    """Return the optimal posting interval in minutes based on current UTC time.

    Peak CT hours post every 45 min; dead hours back off to 90 min.
    Peak windows (UTC): 13:00–15:00 (9–11am EST) and 23:00–02:00 (7–10pm EST).
    Off-peak: 09:00–13:00 UTC (4–9am EST).
    """
    hour = datetime.now(timezone.utc).hour
    # Peak: 9–11am EST = 13:00–15:00 UTC; 7–10pm EST = 23:00–02:00 UTC
    if 13 <= hour < 15 or hour >= 23 or hour < 2:
        return 45
    # Off-peak: 4–9am EST = 09:00–13:00 UTC
    if 9 <= hour < 13:
        return 90
    return 60


def get_keyword_weights() -> dict[str, list[str]]:
    """
    Return prioritized ticker lists for the three bot functions.
    Falls back to static ECOSYSTEM_TOKENS order if no cache exists.
    """
    intel = _cached_intelligence
    if not intel:
        return {
            "originals":      ECOSYSTEM_TOKENS[:5],
            "qt_search":      ECOSYSTEM_TOKENS[:8],
            "reply_priority": ECOSYSTEM_TOKENS[:5],
        }
    return {
        "originals":      intel.get_priority_tickers_for_originals(5),
        "qt_search":      intel.get_qt_search_tickers(8),
        "reply_priority": [t.get("name", "").lower() for t in intel.ranked_tokens[:5]],
    }
