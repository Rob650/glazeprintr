"""
Launchpad evolution knowledge base for GlazePrintr.

Imported by claude_client.py and injected into Claude prompts so tweets
feel like veteran trader analysis — recognising which chapter of the story
we're in and drawing parallels across launchpad generations.
"""

from __future__ import annotations
import random

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
        "sol_paid_to_stakers": "2,100+",
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

# ── New pattern knowledge base ─────────────────────────────────────────────────

_REAL_YIELD_PARALLEL = {
    "name": "Real Yield Parallel",
    "defi_precedent": "GMX (GLP stakers earn real ETH/AVAX from trading fees) and Synthetix (SNX stakers earn sUSD from synth fees) proved DeFi could pay real yield from actual protocol revenue — not printed token rewards.",
    "traditional_parallel": "Traditional stocks pay dividends from revenue. GMX proved crypto could do the same.",
    "printr_application": (
        "Printr applied the same model to memecoins. POB stakers earn real SOL from actual trading fees — "
        "not inflationary token emissions, not VC-funded buybacks. "
        "Printr has already paid out over 2,100 SOL to stakers from real trading activity."
    ),
    "contrast": "Most memecoin 'staking' is printed rewards that inflate supply. Printr's is protocol revenue sharing.",
    "use_when": "Token has staking enabled, discussing Printr fee model, or comparing staking mechanics across platforms",
}

_HOLDER_DISTRIBUTION = {
    "name": "Holder Distribution 20% Rule",
    "research_finding": "Tokens where top 10 wallets hold >20% of supply are structurally fragile — amplifies manipulation risk and whale dump exposure.",
    "printr_mechanic": (
        "POB staking naturally distributes power: locked tokens can't be used to dump during their stake period. "
        "No single wallet can exit-dump while staked. "
        "More distributed effective supply correlates with reduced volatility and greater long-term resilience."
    ),
    "pump_contrast": "pump.fun had no lock mechanics — whale concentration + instant dump = the 98.6% death rate.",
    "use_when": "Discussing holder counts, staking %, or comparing to pump tokens that got whale-dumped",
}

_ECOSYSTEM_FLYWHEEL = {
    "name": "Ecosystem Flywheel",
    "mechanic": (
        "Multiple tokens on the same platform create a flywheel: "
        "$FATCHOI success brings attention to Printr → attention finds $ROTUS → "
        "more launchers → more volume → more staking fees → stakers earn more → "
        "more conviction → repeat."
    ),
    "pump_contrast": "pump.fun tokens competed for the same attention pool (zero-sum). Printr tokens amplify each other — rising tide.",
    "cross_pollination": (
        "Each token's trading volume generates fees that flow to POB stakers across the ecosystem. "
        "A bull run on one Printr token isn't isolated — it funds conviction across all stakers."
    ),
    "use_when": "Multiple Printr tokens pumping simultaneously, comparing ecosystem dynamics",
}

_ANTI_SNIPE_EVOLUTION = {
    "name": "Anti-Snipe Evolution",
    "era_0": "pump.fun: zero bot protection — bots extracted an estimated 30-40% of launch liquidity in the first minutes of popular launches.",
    "era_1_5": "Flaunch (intermediate): added captchas + 30-min fixed-price windows + max 0.25% buy per wallet to limit bot extraction.",
    "era_2": (
        "Printr: anti-vamp mechanic prevents same-ticker migration sniping — "
        "48h relaunch lock means copycats can't steal your launch momentum the second you graduate. "
        "Your entry isn't already underwater from bots before humans even see it."
    ),
    "pattern": "Each launchpad generation patched the exploit that killed the previous gen's tokens in the first 5 minutes.",
    "use_when": "Fair launch quality, bot protection, why Printr launches differ in first-hour mechanics",
}

_SIX_PERCENT_SURVIVAL = {
    "name": "Six Percent Survival Rule",
    "stat_1": "Only 6% of memecoins maintain trading activity past 90 days.",
    "stat_2": "82.89% of high-performing memecoins show signs of market manipulation.",
    "printr_mechanism": (
        "POB staking creates mechanical survival pressure: you can't abandon a token your SOL is locked in for 180 days. "
        "Locked holders continue trading, providing liquidity, and earning fees — which sustains volume. "
        "The stake itself is a survival mechanism for the token."
    ),
    "contrast": "pump.fun had no friction to exit. 98.6% died. Staking creates skin in the game that the survival stat doesn't account for.",
    "use_when": "Token longevity, why most memes die, Printr's structural advantage over time",
}

_MEMECOIN_TRAJECTORIES = {
    "name": "Major Memecoin Trajectories",
    "wif": {
        "ticker": "WIF",
        "launch": "Nov 2023",
        "entry_price": "$0.00012",
        "peak_price": "$4.85",
        "peak_mc": "~$4.85B",
        "timeframe": "4 months to peak",
        "multiplier": "~40,000x",
        "notes": "No staking, pure meme momentum on Solana. No lock mechanics, no fee distribution.",
    },
    "bonk": {
        "ticker": "BONK",
        "launch": "Dec 25, 2022",
        "first_week_spike": "4,438%",
        "best_year": "2023 by return %",
        "mechanic": "Community airdrop to Solana devs = instant distributed holder base",
        "notes": "Distribution strategy (not staking) drove the momentum. No lock mechanics.",
    },
    "pepe": {
        "ticker": "PEPE",
        "launch": "Apr 2023",
        "peak_mc": "$1.6B",
        "timeframe": "under 3 weeks to peak",
        "notes": "Zero utility, pure cultural moment. Created numerous millionaires among early investors.",
    },
    "key_comparison": (
        "None of WIF, BONK, or PEPE had lock mechanics, fee revenue, or anti-dump protection. "
        "Pure speculation — fast moon, fast rug for most holders. "
        "Printr tokens have structural advantages these didn't — "
        "potentially slower moon but the stakers are actually earning SOL while waiting."
    ),
    "use_when": (
        "Comparing a Printr token's early metrics to what WIF/BONK/PEPE looked like at similar stages, "
        "BUT always emphasising the structural difference."
    ),
}

_SIMPLE_TO_POWER_TOOL = {
    "name": "Simple to Power Tool Meta",
    "trading_arc": [
        ("Phase 1", "Manual DEX swaps (Uniswap/Raydium)", "basic access, everyone equally slow"),
        ("Phase 2", "TG bots (Maestro/Unibot)", "faster sniping, wallet speed — edge over manual traders"),
        ("Phase 3", "Terminals (Photon/BullX)", "charts, copy trading, multi-position, better execution"),
    ],
    "launchpad_arc": [
        ("Phase 1", "Direct network launches", "dev-only, high friction"),
        ("Phase 2", "pump.fun", "simplified launch, made it trivial for anyone"),
        ("Phase 3", "LetsBonk", "added creator revenue share, community alignment"),
        ("Phase 4", "Printr", "edge/omnichain/POB/anti-vamp/5 fee models/AI agent support"),
    ],
    "thesis": "Simple tools ALWAYS lose share to power tools. The question isn't IF — it's when the migration happens.",
    "pattern": "Every phase won because it gave traders/creators more edge than the previous tool.",
    "use_when": "Platform comparison tweets, discussing why Printr is next, responding to pump.fun/bonk defenders",
}

# ── Master pattern registry ────────────────────────────────────────────────────

_PATTERNS = {
    "staking":       _REAL_YIELD_PARALLEL,
    "yield":         _REAL_YIELD_PARALLEL,
    "holders":       _HOLDER_DISTRIBUTION,
    "distribution":  _HOLDER_DISTRIBUTION,
    "flywheel":      _ECOSYSTEM_FLYWHEEL,
    "ecosystem":     _ECOSYSTEM_FLYWHEEL,
    "fairlaunch":    _ANTI_SNIPE_EVOLUTION,
    "sniping":       _ANTI_SNIPE_EVOLUTION,
    "survival":      _SIX_PERCENT_SURVIVAL,
    "longevity":     _SIX_PERCENT_SURVIVAL,
    "early_stage":   _MEMECOIN_TRAJECTORIES,
    "wif":           _MEMECOIN_TRAJECTORIES,
    "bonk":          _MEMECOIN_TRAJECTORIES,
    "pepe":          _MEMECOIN_TRAJECTORIES,
    "platform":      _SIMPLE_TO_POWER_TOOL,
    "power_tool":    _SIMPLE_TO_POWER_TOOL,
}


# ── Pattern-matching functions ─────────────────────────────────────────────────

def get_pattern_context(signal_type: str, token_data: dict = None) -> str:
    """
    Returns a formatted pattern context string for the given signal type.
    signal_type maps to one of the pattern dicts above.
    """
    token_data = token_data or {}
    p = _PATTERNS.get(signal_type.lower())
    if p is None:
        return ""

    if p is _REAL_YIELD_PARALLEL:
        staking_pct = token_data.get("staking_pct")
        staking_line = (
            f" ${token_data['name'].upper()} has {staking_pct:.0f}% staked right now."
            if staking_pct is not None and token_data.get("name")
            else ""
        )
        return (
            "REAL YIELD PARALLEL (use to frame Printr staking as the DeFi real-yield model applied to memecoins):\n"
            f"• GMX/Synthetix proved DeFi could pay real yield from actual protocol revenue — not printed token rewards.\n"
            f"• Printr applied the same model to memecoins: POB stakers earn real SOL from actual trading fees.{staking_line}\n"
            f"• Printr has already paid out 2,100+ SOL to stakers from real trading activity — not emissions.\n"
            f"• Contrast: most memecoin 'staking' inflates supply with printed rewards. "
            f"Printr's is protocol revenue sharing — same category as GMX, not the same category as APY farms."
        )

    if p is _HOLDER_DISTRIBUTION:
        staking_pct = token_data.get("staking_pct")
        staking_line = (
            f" With {staking_pct:.0f}% staked, the effective float is compressed — "
            f"that's the anti-fragility mechanic in action."
            if staking_pct is not None
            else ""
        )
        return (
            "HOLDER DISTRIBUTION PATTERN (use when discussing staking concentration or comparing supply mechanics):\n"
            f"• Research: tokens where top 10 wallets hold >20% of supply are structurally fragile — "
            f"amplifies manipulation risk.\n"
            f"• Printr's POB staking naturally distributes power: locked tokens can't dump during their stake period.{staking_line}\n"
            f"• pump.fun had zero lock mechanics — whale concentration + instant dump exit = the 98.6% death rate.\n"
            f"• More distributed effective supply = reduced volatility and greater resilience. Staking IS the distribution mechanic."
        )

    if p is _ECOSYSTEM_FLYWHEEL:
        return (
            "ECOSYSTEM FLYWHEEL PATTERN (use when multiple Printr tokens are moving or comparing ecosystem dynamics):\n"
            "• Flywheel: one token's success brings attention to Printr → finds other tokens → more launchers → "
            "more volume → more staking fees → more conviction → repeat.\n"
            "• pump.fun tokens competed for the same attention pool (zero-sum). "
            "Printr tokens amplify each other — rising tide.\n"
            "• Cross-pollination: each token's trading volume generates fees that flow to POB stakers across the whole ecosystem. "
            "A bull run on one Printr token literally funds conviction across all stakers simultaneously."
        )

    if p is _ANTI_SNIPE_EVOLUTION:
        return (
            "ANTI-SNIPE EVOLUTION PATTERN (use to explain why Printr launches hit differently in the first hour):\n"
            "• pump.fun: zero bot protection — bots extracted ~30-40% of launch liquidity in the first minutes.\n"
            "• Flaunch: captchas + 30-min fixed-price + max 0.25% buy per wallet — patched the bot extraction.\n"
            "• Printr: anti-vamp 48h same-ticker relaunch lock + GoPlus LP auto-lock — "
            "copycats can't steal your launch, bots can't front-run graduation. "
            "Your entry isn't already underwater before humans even see it.\n"
            "• Pattern: each launchpad generation patched the exploit that killed the previous gen's tokens in the first 5 minutes."
        )

    if p is _SIX_PERCENT_SURVIVAL:
        return (
            "SIX PERCENT SURVIVAL RULE (use when discussing token longevity or Printr's structural advantage over time):\n"
            "• Only 6% of memecoins maintain trading activity past 90 days.\n"
            "• 82.89% of high-performing memecoins show signs of market manipulation.\n"
            "• Printr's POB staking creates mechanical survival pressure: you can't abandon a token your SOL is locked "
            "in for 180 days — locked holders keep trading, providing liquidity, earning fees, sustaining volume.\n"
            "• The stake itself is a survival mechanism the 6% stat doesn't account for. "
            "pump.fun had zero exit friction. 98.6% died. Different game."
        )

    if p is _MEMECOIN_TRAJECTORIES:
        name = token_data.get("name", "")
        age_days = token_data.get("age_days")
        mc = token_data.get("market_cap") or 0
        mc_str = f"${mc/1e6:.2f}M MC" if mc >= 1e6 else (f"${mc:,.0f} MC" if mc else "")
        age_str = (
            f" At {age_days:.1f} days old{' with ' + mc_str if mc_str else ''}, "
            f"compare to where WIF/BONK/PEPE were at this stage."
            if age_days is not None and age_days < 30
            else ""
        )
        return (
            "MAJOR MEMECOIN TRAJECTORY DATA (use for historical comparisons — emphasise structural difference):\n"
            "• $WIF (Nov 2023): $0.00012 launch → $4.85 peak in 4 months (~40,000x). Pure meme momentum, no staking.\n"
            "• $BONK (Dec 2022): 4,438% spike in first week. Community airdrop to Solana devs = distributed holder base. "
            "Best performer of 2023 by return %.\n"
            "• $PEPE (Apr 2023): $0 → $1.6B MC in under 3 weeks. Zero utility, pure cultural moment.\n"
            f"• None had lock mechanics, fee revenue, or anti-dump protection. Pure speculation.{age_str}\n"
            "• Printr tokens have structural advantages these didn't — stakers earn real SOL while waiting. "
            "The comparison: same early energy, different survival architecture."
        )

    if p is _SIMPLE_TO_POWER_TOOL:
        return (
            "SIMPLE → POWER TOOL PATTERN (use to frame Printr as the inevitable infrastructure upgrade):\n"
            "• Trading arc: Manual DEX → TG bots (Maestro/Unibot) → Terminals (Photon/BullX). "
            "Each phase won by giving traders more edge than the previous tool.\n"
            "• Launchpad arc: direct launches → pump.fun (anyone can launch) → LetsBonk (creator fees) → "
            "Printr (omnichain, POB, anti-vamp, 5 fee models, AI agent support).\n"
            "• Thesis: simple tools ALWAYS lose share to power tools. Not if — when. "
            "We've watched this happen in trading UX twice. Launchpads are next.\n"
            "• pump.fun is the Uniswap manual-swap of launchpads. Printr is the terminal."
        )

    return ""


def get_evolution_context(ticker: str, token_data: dict) -> str:
    """
    Returns a pattern-recognition string based on live token metrics.
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

    # Pattern 1: high staking conviction → real yield + conviction parallel
    if staking_pct is not None and staking_pct >= 25:
        lines.append(
            f"• CONVICTION / REAL YIELD: ${ticker_upper} has {staking_pct:.0f}% supply staked. "
            f"Compare: pump.fun had ZERO lock mechanics — 98.6% of tokens died because anyone could dump instantly. "
            f"The 180d POB lockers here get up to 2.5x fee share on real SOL from trading — "
            f"same model GMX uses (GLP stakers earn real ETH). "
            f"Printr has paid 2,100+ SOL to stakers already. "
            f"This is a structurally different animal from the pump.fun casino."
        )

    # Pattern 2: volume spike → early runner parallel
    if volume_1h > 0 and volume_24h > 0:
        hourly_avg = volume_24h / 24
        if volume_1h >= hourly_avg * 3:
            spike_x = volume_1h / hourly_avg
            lines.append(
                f"• VOLUME SPIKE: 1h vol is {spike_x:.1f}x the 24h hourly average for ${ticker_upper}. "
                f"Mirrors the early breakout pattern of USELESS on LetsBonk (hit $275M peak) "
                f"and WIF's first-week surge. The difference: those holders had no lock upside. "
                f"${ticker_upper} POB stakers are earning real SOL on every txn right now."
            )

    # Pattern 3: new token with buy pressure → early launch + survival parallel
    if age_days is not None and age_days < 7 and price_change_24h is not None and price_change_24h > 0:
        mc_str = f"${market_cap / 1e6:.2f}M MC" if market_cap >= 1e6 else f"${market_cap:,.0f} MC"
        lines.append(
            f"• EARLY LAUNCH: ${ticker_upper} is {age_days:.1f} days old, {price_change_24h:+.1f}% 24h, {mc_str}. "
            f"WIF/BONK/PEPE all showed strong early metrics — but only 6% of memecoins survive past 90 days. "
            f"pump.fun's best runners had this same early profile but zero lock mechanics. "
            f"Printr's creator stake + POB structurally incentivises early believers to hold. "
            f"Different game."
        )
    elif age_days is not None and age_days < 30:
        # ANTI_SNIPE context for young tokens even without 24h spike
        lines.append(
            f"• FAIR LAUNCH EDGE: ${ticker_upper} is {age_days:.0f} days old. "
            f"pump.fun launches had bots extracting ~30-40% of liquidity in the first minutes. "
            f"Printr's anti-vamp + GoPlus LP auto-lock means the token's early price action "
            f"reflects real human demand — not just bot front-running."
        )

    # Pattern 4: no specific signal — general arc context + survival stat
    if len(lines) == 1:
        lines.append(
            f"• EVOLUTIONARY ARC: ${ticker_upper} is in Era 3 of launchpad evolution. "
            f"Era 1 (pump.fun Jan 2024): 98.6% died, no lock mechanics. "
            f"Era 2 (LetsBonk Apr 2025): creator fees, but holders unprotected. "
            f"Era 3 (Printr Apr 2026): 100% of fees to POB stakers, creator must stake, 2,100+ SOL paid out. "
            f"Only 6% of memecoins survive 90 days — Printr's staking creates the mechanical reason to be in the 6%."
        )

    return "\n".join(lines)


def get_competitive_edge_context() -> str:
    """
    Returns the cross-launchpad comparison narrative with real yield and power tool parallels.
    Use for Dune analytics or competitor comparison tweet topics.
    """
    return (
        "LAUNCHPAD EVOLUTION CONTEXT (veteran analysis — draw from these parallels):\n"
        "• Era 1 — pump.fun (Jan 2024): 1.4% graduation, 98.6% die, bots extracted ~30-40% of launch liq. "
        "No staking, no fee share, no lock mechanics. Runners: FWOG ($74M), Michi ($57M). "
        "Core failure: zero alignment — creators dumped, holders had no upside.\n"
        "• Era 2 — LetsBonk (Apr 2025): 1% creator fee share, flipped pump in daily launches in 24h. "
        "Runners: USELESS ($275M), Hosico Cat ($69M). Better, but creators still dumped after farming fees.\n"
        "• Era 3 — Printr (Apr 2026): 100% custom fees to POB stakers. Creator must stake — no code path to ghost. "
        "2,100+ SOL already paid to stakers from REAL trading revenue (same model as GMX). "
        "7-180d locks, 2.5x multiplier, 8 chains, anti-vamp, 5 fee models.\n"
        "• REAL YIELD PARALLEL: Printr POB = what GMX/Synthetix proved in DeFi — "
        "protocol revenue to stakers, not printed emissions. First time this model hit memecoins.\n"
        "• POWER TOOL THESIS: every tool category runs Manual → Convenience → Edge. "
        "Trading: DEX → TG bots → Terminals. Launchpads: direct → pump.fun → Printr. "
        "Simple tools always lose share to power tools. pump.fun is the manual DEX of launchpads.\n"
        "• Receipts: 1.4% graduation vs POB stakers earning real SOL passively. Different survival mechanics entirely."
    )


def get_trading_ux_parallel() -> str:
    """
    Returns the DEX → TG bots → terminals → launchpad infrastructure parallel.
    Use for platform-level tweets about why infrastructure upgrades always win.
    """
    return (
        "TRADING INFRASTRUCTURE EVOLUTION PARALLEL (use to frame Printr as inevitable upgrade):\n"
        "• Phase 1 — Manual DEX swaps (Uniswap/Raydium): basic access, slow execution, no edge\n"
        "• Phase 2 — TG bots (Maestro/Unibot): faster sniping, wallet speed — won by giving more edge\n"
        "• Phase 3 — Terminals (Photon/BullX): charts, copy trading, multi-position — won again by giving more edge\n"
        "• Thesis: every phase won because it gave traders MORE EDGE. Simple tools always lose to power tools — not if, when.\n"
        "• Launchpad parallel:\n"
        "  Phase 1 — direct launches: dev-only\n"
        "  Phase 2 — pump.fun: anyone can launch\n"
        "  Phase 3 — LetsBonk: creator revenue alignment\n"
        "  Phase 4 — Printr: omnichain, POB staking (2,100+ SOL paid out), anti-vamp, 5 fee models, AI agent support\n"
        "• pump.fun is the Uniswap manual-swap of launchpads. Printr is the terminal. "
        "We've watched this migration happen twice in trading UX. Launchpads are next."
    )


def get_flywheel_context(active_tokens: list[str]) -> str:
    """
    Returns ecosystem flywheel pattern context when 2+ tokens are showing strength.
    """
    if len(active_tokens) < 2:
        return ""
    tickers_fmt = ", ".join(f"${t.upper()}" for t in active_tokens)
    return (
        f"ECOSYSTEM FLYWHEEL PATTERN: {tickers_fmt} showing simultaneous strength.\n"
        f"• Flywheel mechanic: success on one Printr token brings attention to the platform → "
        f"finds other ecosystem tokens → more volume → more staking fees → more conviction → repeat.\n"
        f"• pump.fun tokens competed for the same attention pool (zero-sum). "
        f"Printr tokens amplify each other — rising tide model.\n"
        f"• Cross-pollination: each token's volume generates fees for POB stakers across the whole ecosystem. "
        f"Multiple tokens pumping simultaneously = compounding fee revenue for stakers.\n"
        f"• Compare: when BONK OGs all launched on LetsBonk in Apr 2025, coordinated momentum "
        f"flipped pump.fun in daily launches within 24h. Ecosystem alignment isn't coincidence — it's the mechanic."
    )


def get_ecosystem_momentum_context(active_tickers: list[str]) -> str:
    """
    Returns pattern context when 2+ ecosystem tokens show strength simultaneously.
    Focuses on the coordinated launch parallel (LetsBonk BONK OGs).
    """
    if len(active_tickers) < 2:
        return ""
    tickers_fmt = ", ".join(f"${t.upper()}" for t in active_tickers)
    return (
        f"ECOSYSTEM MOMENTUM PATTERN: {tickers_fmt} showing simultaneous strength. "
        f"When BONK OGs all launched on LetsBonk simultaneously in Apr 2025, "
        f"coordinated momentum drove LetsBonk to flip pump.fun in daily launches within 24h. "
        f"On Printr, each of these tokens has independent POB stakers earning fees — "
        f"aligned economic interests across multiple tokens, not just one."
    )


def get_reply_pattern_context(tweet_text: str, token_data: dict = None) -> str:
    """
    Selects the most relevant pattern based on keywords in the tweet being replied to.
    Returns an empty string if no strong signal found.
    """
    token_data = token_data or {}
    lower = tweet_text.lower()

    # Priority order: most specific signal wins
    if any(kw in lower for kw in ["stake", "staking", "pob", "yield", "lock", "fee", "earn"]):
        return get_pattern_context("staking", token_data)

    if any(kw in lower for kw in ["holder", "wallet", "supply", "distribution", "whale", "dump"]):
        return get_pattern_context("holders", token_data)

    if any(kw in lower for kw in ["snipe", "bot", "front", "fair launch", "launch", "rug"]):
        return get_pattern_context("fairlaunch", token_data)

    if any(kw in lower for kw in ["survive", "dead", "die", "rug", "90 day", "longevity", "sustain"]):
        return get_pattern_context("survival", token_data)

    if any(kw in lower for kw in ["wif", "bonk", "pepe", "doge", "shib", "early", "1000x", "100x"]):
        return get_pattern_context("early_stage", token_data)

    if any(kw in lower for kw in ["pump.fun", "pumpfun", "letsbonk", "bonk.fun", "compare", " vs ", "better than", "switch"]):
        return get_pattern_context("platform", token_data)

    return ""


def get_qt_pattern_context(token_data: dict = None) -> str:
    """
    Returns a relevant pattern for QT glazer scoring — rotates through a curated pool.
    Always returns something since QT is always Printr-ecosystem content.
    """
    token_data = token_data or {}
    staking_pct = token_data.get("staking_pct")

    # If we have staking data, lead with the real yield parallel
    if staking_pct is not None:
        return get_pattern_context("staking", token_data)

    # Otherwise rotate through the most impactful patterns for ecosystem scoring
    choices = ["staking", "survival", "platform", "early_stage"]
    return get_pattern_context(random.choice(choices), token_data)
