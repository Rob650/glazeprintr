"""
Launchpad evolution knowledge base for GlazePrintr.

Imported by claude_client.py and injected into Claude prompts so tweets
feel like veteran trader analysis — recognising which chapter of the story
we're in and drawing parallels across launchpad generations.
"""

from __future__ import annotations

# ── Era knowledge base ────────────────────────────────────────────────────────

_ERA_1 = {
    "name": "Pump.fun",
    "period": "Jan 2024",
    "tagline": "Democratize Creation",
    "innovation": "No-code token launch, bonding curve handles liquidity, anyone can launch in seconds",
    "fee_share_to_creator": "0.05%",
    "runners": [
        ("FWOG", "74M"),
        ("Daddy Tate", "45M"),
        ("Michi", "57M"),
        ("Smoking Chicken Fish", "39M"),
        ("Billy", None),
        ("Shark Cat", None),
    ],
    "meta": "Absurdist humor, shock value, livestream stunts, zero utility, pure speculation casino",
    "stats": {
        "graduation_rate_pct": 1.4,
        "death_rate_pct": 98.6,
        "tokens_created": "11.5M+",
        "platform_revenue": "$317M",
    },
    "failure_mode": "No lock mechanics — anyone dumps instantly. Creators took fees and ghosted.",
    "what_killed_momentum": "Trust erosion from constant rugs; holders had zero recourse or upside",
}

_ERA_2 = {
    "name": "LetsBonk",
    "period": "Apr 2025",
    "tagline": "Incentivize Creators",
    "innovation": "1% creator fee sharing (vs pump's 0.05%), Raydium LaunchLab integration",
    "fee_share_to_creator": "1%",
    "runners": [
        ("USELESS", "275M"),
        ("Hosico Cat", "69M"),
        ("IKUN", "25M"),
    ],
    "meta": "Ironic transparency, cute animals (Ghibli-fied), community IP plays, BONK OGs leading concepts",
    "stats": {
        "first_24h_tokens": 19620,
        "pump_first_24h_tokens": 9249,
        "note": "flipped pump.fun in daily token creation within 24 hours of launch",
    },
    "failure_mode": "Creators still eventually dump after farming fees — holder alignment was still missing",
    "what_improved": "Creator alignment through revenue sharing; meta became more community-driven",
}

_ERA_3 = {
    "name": "Printr",
    "period": "Apr 2026",
    "tagline": "Align Everyone On-Chain",
    "innovation": (
        "Proof of Belief staking, 5 fee models, anti-vamp 48h relaunch lock, "
        "omnichain (8 chains via LayerZero), creator MUST stake alongside community"
    ),
    "ecosystem_tokens": [
        "FATCHOI", "ROTUS", "OOO", "DEPLOYR", "ROI", "CMYK", "PRINT",
        "PVE", "BELIEF", "BRRR", "QUACK", "LFP", "STAKR", "POB500", "FSJAL",
    ],
    "meta": "Conviction plays, staking as signal, data-driven community, verifiable on-chain commitment",
    "stats": {
        "fee_pct_to_stakers": 100,
        "lock_range_days": "7–180",
        "max_multiplier": "2.5x (at 180d)",
        "chains": 8,
        "fee_models": 5,
    },
    "key_differentiator": (
        "Before buying, anyone can verify on-chain: how much supply is staked, "
        "who is locked, and for how long. Conviction is no longer a claim — it's a fact."
    ),
    "fee_models": [
        "POB Staking Pool (100% to stakers)",
        "Buyback & Burn",
        "Liquidity Compounding",
        "Creator Keeps Fees",
        "No Custom Fee",
    ],
    "what_it_solves": (
        "Pump.fun tokens had no lock mechanics — 98.6% died. "
        "LetsBonk aligned creators but left holders unprotected. "
        "Printr makes conviction verifiable and rewards it mathematically."
    ),
}

_EVOLUTION_ARC = [_ERA_1, _ERA_2, _ERA_3]

# ── Trading UX evolution parallel ─────────────────────────────────────────────

_TRADING_UX_ARC = {
    "phase_1": {
        "label": "Manual DEX swaps (Uniswap / Raydium)",
        "edge": "Basic access — you could trade, but you were slow",
    },
    "phase_2": {
        "label": "TG bots (Maestro / Unibot)",
        "edge": "Faster sniping, wallet speed, convenience — edge over manual traders",
    },
    "phase_3": {
        "label": "Terminals (Photon / BullX)",
        "edge": "Charts, analytics, copy trading, multi-position, better execution — edge over TG bots",
    },
    "thesis": "Every phase won because it gave traders more edge. Simple tools eventually lose share to power tools.",
    "launchpad_parallel": {
        "phase_1": "Direct network launches = basic access, dev-only",
        "phase_2": "Legacy launchpads (pump.fun) = simplified launch, made it trivial for anyone",
        "phase_3": "Next-gen infrastructure (Printr) = omnichain, anti-vamp, customizable, builder controls, POB alignment",
        "conclusion": "Simple launchpads eventually lose share to power infrastructure. We've seen this movie.",
    },
}

# ── Pattern-matching functions ─────────────────────────────────────────────────

def get_evolution_context(ticker: str, token_data: dict) -> str:
    """
    Returns a concise pattern-recognition string based on live token metrics.
    Injected into Claude's user message for ticker spotlight tweets.
    """
    lines: list[str] = []
    ticker_upper = ticker.upper()

    staking_pct = token_data.get("staking_pct")
    volume_24h = token_data.get("volume") or 0
    volume_1h = token_data.get("volume_1h") or 0
    price_change_24h = token_data.get("price_change_24h")
    age_days = token_data.get("age_days")
    market_cap = token_data.get("market_cap") or 0

    lines.append("PATTERN RECOGNITION CONTEXT (use to draw informed historical parallels):")

    # Pattern 1: high staking conviction
    if staking_pct is not None and staking_pct >= 25:
        lines.append(
            f"• CONVICTION PLAY: ${ticker_upper} has {staking_pct:.0f}% supply staked. "
            f"Compare: pump.fun had ZERO lock mechanics — 98.6% of tokens died because anyone could dump instantly. "
            f"When supply is locked and earning, circulating float shrinks. "
            f"The 180d POB lockers here are getting up to 2.5x fee share. "
            f"This is a structurally different animal from the pump.fun casino."
        )

    # Pattern 2: volume spike (1h disproportionate to 24h)
    if volume_1h > 0 and volume_24h > 0:
        hourly_avg = volume_24h / 24
        if volume_1h >= hourly_avg * 3:
            spike_x = volume_1h / hourly_avg
            lines.append(
                f"• VOLUME SPIKE PATTERN: 1h vol is {spike_x:.1f}x the 24h hourly average for ${ticker_upper}. "
                f"This mirrors the early breakout pattern of USELESS on LetsBonk — went from obscure to $275M peak "
                f"after a volume surge that most people missed at the time. "
                f"The difference: USELESS holders had no lock upside. ${ticker_upper} POB stakers are earning fees on every txn right now."
            )

    # Pattern 3: new token with buy pressure
    if age_days is not None and age_days < 7 and price_change_24h is not None and price_change_24h > 0:
        mc_str = f"${market_cap / 1e6:.2f}M MC" if market_cap >= 1e6 else f"${market_cap:,.0f} MC"
        lines.append(
            f"• EARLY LAUNCH PATTERN: ${ticker_upper} is {age_days:.1f} days old with {price_change_24h:+.1f}% 24h and {mc_str}. "
            f"Pump.fun's best runners (FWOG $74M, Michi $57M) showed this exact profile in their first week — "
            f"but those had no lock mechanics, so early buyers had zero alignment incentive to hold. "
            f"Printr's creator stake requirement and POB means the early believers are structurally incentivized to stay."
        )

    # Pattern 4: token in the ecosystem, no specific metric triggered — general arc context
    if len(lines) == 1:
        lines.append(
            f"• EVOLUTIONARY ARC: ${ticker_upper} is a Printr ecosystem token in Era 3 of launchpad evolution. "
            f"Era 1 (pump.fun Jan 2024): democratized creation, 98.6% died, no lock mechanics. "
            f"Era 2 (LetsBonk Apr 2025): added 1% creator fees, but holders still had no upside. "
            f"Era 3 (Printr Apr 2026): 100% of fees to POB stakers, creator must stake, verifiable on-chain conviction. "
            f"The pattern: each era solved the previous era's alignment failure."
        )

    return "\n".join(lines)


def get_competitive_edge_context() -> str:
    """
    Returns the cross-launchpad comparison narrative.
    Use for Dune analytics or competitor comparison tweet topics.
    """
    return (
        "LAUNCHPAD EVOLUTION CONTEXT (draw parallels to make this feel like veteran analysis):\n"
        "• Era 1 — pump.fun (Jan 2024): democratized token creation. Runners: FWOG ($74M), Michi ($57M), Daddy Tate ($45M). "
        "But: 1.4% graduation rate, 98.6% rug/die, 11.5M+ tokens created. "
        "Core failure: no lock mechanics — anyone dumps instantly, creators take 0.05% fee and ghost.\n"
        "• Era 2 — LetsBonk (Apr 2025): added 1% creator fee share. "
        "Runners: USELESS ($275M peak), Hosico Cat ($69M). "
        "Flipped pump.fun in daily launches within 24h. "
        "But: creators still dumped after farming fees — holder alignment still missing.\n"
        "• Era 3 — Printr (Apr 2026): 100% of custom fees to POB stakers. "
        "Creator MUST stake alongside community — no code path for launch-and-ghost. "
        "7-180d locks, up to 2.5x multiplier, 8 chains, anti-vamp, 5 fee models. "
        "Before buying any token: verify on-chain how much is staked, who locked, for how long.\n"
        "• The pattern: each era solved the PREVIOUS era's core alignment failure. "
        "pump.fun couldn't solve creator dumps. LetsBonk couldn't solve holder dumps. "
        "Printr makes conviction verifiable and rewards it mathematically.\n"
        "• Receipts: pump.fun graduation rate = 1.4%. "
        "Printr POB stakers earn fees passively. Different survival mechanics entirely."
    )


def get_trading_ux_parallel() -> str:
    """
    Returns the DEX → TG bots → terminals → launchpad infrastructure parallel.
    Use for platform-level tweets about why infrastructure upgrades always win.
    """
    return (
        "TRADING INFRASTRUCTURE EVOLUTION PARALLEL (use to frame Printr as inevitable infrastructure upgrade):\n"
        "• Phase 1 — Manual DEX swaps (Uniswap/Raydium): basic access, slow execution, no edge\n"
        "• Phase 2 — TG bots (Maestro/Unibot): faster sniping, wallet speed, convenience — won by giving traders more edge\n"
        "• Phase 3 — Terminals (Photon/BullX): charts, copy trading, multi-position, better execution — won again by giving more edge\n"
        "• Pattern: every phase won because it gave traders MORE EDGE than the previous tool. "
        "Simple tools always lose share to power tools — not suddenly, but inevitably.\n"
        "• Launchpad parallel runs the exact same arc:\n"
        "  Phase 1 — direct network launches: dev-only, high friction\n"
        "  Phase 2 — legacy launchpads (pump.fun): made it trivial for anyone, abstracted the complexity\n"
        "  Phase 3 — Printr: omnichain, anti-vamp, 5 fee models, POB alignment, AI agent support — "
        "gives builders and holders edges that simply don't exist on legacy platforms\n"
        "• The thesis: simple launchpads lose share to power infrastructure. "
        "We've watched this happen in trading UX twice already. Launchpads are next."
    )


def get_ecosystem_momentum_context(active_tickers: list[str]) -> str:
    """
    Returns pattern context when multiple ecosystem tokens show strength simultaneously.
    Use when 3+ tokens are moving together.
    """
    if len(active_tickers) < 2:
        return ""
    tickers_fmt = ", ".join(f"${t.upper()}" for t in active_tickers)
    return (
        f"ECOSYSTEM MOMENTUM PATTERN: {tickers_fmt} showing simultaneous strength. "
        f"Compare: when BONK OGs all launched on LetsBonk simultaneously in Apr 2025, "
        f"the coordinated momentum drove LetsBonk to flip pump.fun in daily launches within 24h. "
        f"Coordinated ecosystem moves are a signal of community depth, not coincidence. "
        f"On Printr, each of these tokens has independent POB stakers earning fees — "
        f"the ecosystem has aligned economic interests across multiple tokens, not just one."
    )
