import logging
import os
import json
import random
import re
import time
import anthropic
from concurrent.futures import ThreadPoolExecutor, as_completed
from database import get_recent_openers, add_opener, get_ecosystem_tweets, get_last_ticker, set_last_ticker, get_recent_tickers, add_recent_ticker
from scraper import _fetch_url_sync, DEXSCREENER_SEARCH_API, DEXSCREENER_API, KNOWN_CONTRACTS
from patterns import (
    get_evolution_context, get_competitive_edge_context, get_trading_ux_parallel,
    get_ecosystem_momentum_context, get_flywheel_context,
    get_pattern_context, get_reply_pattern_context, get_qt_pattern_context,
)

# Strips/replaces URLs Claude sneaks in despite prompt instructions (twitter.com/x.com are preserved)
_HTTPS_RE = re.compile(r'https?://(?!(?:www\.)?(?:twitter\.com|x\.com)/)\S+', re.IGNORECASE)
_TWITTER_URL_RE = re.compile(r'\b(?:twitter\.com|x\.com)/\S*', re.IGNORECASE)
_BARE_TCO_RE = re.compile(r'\bt\.co/\S+', re.IGNORECASE)
_PUMP_FUN_RE = re.compile(r'\bpump\.fun\S*', re.IGNORECASE)
_PRINTR_MONEY_RE = re.compile(r'\bapp\.printr\.money\S*', re.IGNORECASE)
# Strips contract addresses Claude might include despite prompt rules
_EVM_ADDR_RE = re.compile(r'\b0x[0-9a-fA-F]{10,}\b')
# Solana addresses: base58, 32-44 chars, must contain digits (pure-alpha words are not addresses)
_SOL_ADDR_RE = re.compile(r'\b(?=[1-9A-HJ-NP-Za-km-z]*[0-9])(?=[1-9A-HJ-NP-Za-km-z]*[A-Za-z])[1-9A-HJ-NP-Za-km-z]{40,44}\b')
# Phrases that indicate Claude is leaking its instructions into tweet output
_LEAK_PATTERNS = re.compile(
    r"data integrity|system prompt|market data (?:was |is )?(?:not |in)?(?:provided|injected|available)|"
    r"no (?:live |current )?(?:market )?data|haven't (?:been )?(?:provided|given) (?:any )?data|"
    r"pobstaked|per the rule|i cannot cite|i can't cite|i'm not able to cite|"
    r"(?:data|stats?) (?:was|is|are|were) not (?:provided|injected)|"
    r"(?:no|without) (?:staking|price|market) data (?:was |is )?(?:provided|injected|available)|"
    r"instructions say|following (?:the )?rules?|as (?:an? )?ai|as a bot",
    re.IGNORECASE,
)

# Topic focuses for original tweets — picked randomly each call to prevent $BELIEF monopoly
# ── 20 %: $FATCHOI spotlight topics (platform mascot, always heavy) ──────────
_FATCHOI_TOPICS = [
    ("fatchoi_stats",
     "Spotlight $FATCHOI using its best live stat as the hook. Layer in mascot energy — 'this isn't just a token, it's the face of the platform.' Humor welcome. Make not holding $FATCHOI feel like a personality flaw."),
    ("fatchoi_grindset",
     "Grindset angle on $FATCHOI: 'The mascot doesn't paper hand. Why would you?' Use its staking % or holder conviction as proof. Lock the mascot or admit you're a tourist."),
    ("fatchoi_hot_take",
     "Hot take: $FATCHOI is the most underrated token in the ecosystem and the data proves it. One sharp stat, one sharper opinion. Make it feel like a discovery, not a shill."),
    ("fatchoi_philosophical",
     "'$FATCHOI is the Printr mascot. Mascots don't dump. They represent.' What does it mean to hold the face of the platform? Data if available, pure conviction if not."),
    ("fatchoi_price_action",
     "Chart-watcher voice on $FATCHOI: the number IS the opener. Lead with the most alarming stat — price change, buy/sell ratio, staking % — and let one sentence of mascot conviction close it."),
]

# ── 30 %: other-ticker spotlights — 9 tokens rotated evenly ─────────────────
_OTHER_TICKERS = ["ooo", "patapim", "rotus", "roi", "cmyk", "print", "pve", "belief", "deployr", "brrr", "quack", "lfp", "stakr", "pob500", "fsjal"]


def _fetch_ticker_change(ticker: str) -> tuple[str, float]:
    try:
        contract = KNOWN_CONTRACTS.get(ticker.lower())
        url = DEXSCREENER_API.format(contract) if contract else DEXSCREENER_SEARCH_API.format(ticker.upper())
        data = _fetch_url_sync(url, timeout=5)
        pairs = (data or {}).get("pairs") or []
        if pairs:
            if not contract:
                # Printr contracts always end in "brrr" — filter out wrong tokens from search
                brrr_pairs = [
                    p for p in pairs
                    if (p.get("baseToken") or {}).get("address", "").endswith("brrr")
                ]
                if not brrr_pairs:
                    logging.getLogger(__name__).warning(
                        f"_fetch_ticker_change: no brrr-suffix address for {ticker!r} — skipping (wrong token)"
                    )
                    return ticker, 0.0
                pairs = brrr_pairs
            pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
            return ticker, float((pairs[0].get("priceChange") or {}).get("h24") or 0)
    except Exception:
        pass
    return ticker, 0.0


def _pick_ticker_by_momentum(available: list[str]) -> str:
    """Pick a ticker weighted toward the biggest 24h gainers. Falls back to random on failure."""
    try:
        with ThreadPoolExecutor(max_workers=min(8, len(available))) as ex:
            futures = {ex.submit(_fetch_ticker_change, t): t for t in available}
            changes: list[tuple[str, float]] = []
            for fut in as_completed(futures, timeout=10):
                try:
                    changes.append(fut.result())
                except Exception:
                    changes.append((futures[fut], 0.0))
    except Exception:
        return random.choice(available)

    if not changes:
        return random.choice(available)

    # Sort biggest gainers first; rank-based weights so rank 0 is n× more likely than rank n-1
    changes.sort(key=lambda x: x[1], reverse=True)
    n = len(changes)
    weights = [n - i for i in range(n)]
    return random.choices([t for t, _ in changes], weights=weights, k=1)[0]


# ── 25 %: Dune data / competitor comparisons ─────────────────────────────────
_DUNE_COMPETITOR_TOPICS = [
    ("dune_onchain",
     "Lead with an on-chain insight from Dune analytics — unique wallets, holder growth, transaction velocity, or protocol revenue. 'On-chain data doesn't lie' energy. Make a raw number feel like breaking news."),
    ("dune_holder_growth",
     "If wallet/holder growth data is available from Dune, make the trajectory the whole tweet. 'X new wallets in Y days' beats any price take. Frame as proof of real adoption, not speculation."),
    ("dune_vs_pump",
     "Use Dune on-chain data to contrast Printr activity vs pumpfun — txn counts, unique launchers, staking participation, whatever the data shows. 'The receipts are in. pumpfun doesn't have a version of this metric.'"),
    ("pump_fun_dunk",
     "Surgical dunk on pumpfun. ONE weakness, one hit: one chain, $69K graduation or bust, zero staking, no creator control, no anti-vamp, copycat hell. 'I genuinely cannot believe people still launch on pumpfun in 2026' energy."),
    ("launchpad_comparison",
     "Printr vs the entire launchpad category. Make it brutal: 8 chains vs 1, 5 fee models vs 0, POB staking vs nothing, Dutch auctions vs one-size-fits-all, anti-vamp vs copycat hell. 'This isn't competition. This is a different category.'"),
    ("competitor_staking_gap",
     "No other launchpad has POB staking. None. pumpfun holders get zero for their conviction. Printr 180d lockers get 2.5x fee share. Frame the gap: 'one platform rewards belief. the other just rewards the developer.'"),
    ("multichain_vs_solana",
     "List Printr's chains: Solana, Base, BNB, ETH, Monad, Avalanche, Mantle, Arbitrum. Don't say 'omnichain' — just say '8 chains' and ask 'still launching on one chain in 2026?' Make single-chain feel like a 2021 technology choice."),
    ("protocol_revenue_angle",
     "'100% of custom fees go to POB stakers. Not the platform. Not the VCs. The stakers.' Frame this as the business model question the whole launchpad industry has been avoiding. Contrast with platforms where fees feed the dev team."),
]

# ── 25 %: Printr platform topics (presale, farming, fundamentals) ────────────
_PLATFORM_TOPICS = [
    ("presale_fomo",
     "Create FOMO on the Printr presale. 'Presales don't get announced on billboards — they go to people who were paying attention.' Early believers get founder-tier terms. No invented prices or dates. Pure conviction framing."),
    ("presale_vs_market",
     "Contrast presale vs buying after launch. 'You can buy in at market price after launch. Or you can be the market.' One is a position. One is a trade. Frame as: the window is open right now and most people don't even know it."),
    ("airdrop_farming",
     "Productive degen thesis: every action on Printr accumulates airdrop activity — launch, stake, trade, provide liquidity, use multiple chains. 'You're not waiting for the airdrop. You're earning it with every txn.' High urgency."),
    ("farming_vs_watching",
     "Hot take: watching without participating is just donating your allocation to the people who are. 'Every day you don't stake, someone else earns your share of the fee pool.' Hard truth. No apologies."),
    ("platform_usage_farming",
     "'Using Printr IS farming.' Every launch, every stake, every trade = accumulation. Productive degen behavior, not passive waiting. This is the mindset shift most CT hasn't made yet."),
    ("pob_mechanics",
     "Explain POB staking like you're talking to a pumpfun refugee: 'Lock your tokens. Earn fees. Longer lock = bigger cut — up to 2.5x at 180d. Creator stakes too so they can't rug you.' Not a lecture — one punchy take, maximum attitude."),
    ("conviction_math",
     "Run the 180d vs 7d multiplier math out loud: 'Two people lock 10K tokens. 7d locker gets 1x. 180d locker gets 2.5x. On the same fee pool. Every day. The math doesn't care about your timeline.' Make it feel alarming."),
    ("dutch_auction",
     "'Dutch auctions are the most honest price discovery in crypto and nobody talks about it.' Printr uses descending-price auctions. The market finds the real price, not the hype price. Make it sound wild that other platforms still don't do this."),
    ("bonding_curve_choice",
     "Three curve types: Memecoin ($3K→$69K), Growth ($5K→$100K), Bluechip ($20K→$200K). Plus custom. 'pumpfun has one size. Printr has a wardrobe.' Make creator choice feel like the entire point of building infrastructure."),
    ("creator_stakes_too",
     "'The creator must stake alongside the community.' Not by promise — by code. They can't launch and walk. 'That's the alignment mechanism the entire launchpad industry has been missing.' Pumpfun devs take fees and ghost. This is structurally different."),
    ("anti_vamp_protection",
     "48h same-ticker relaunch lock. Someone tried to copycat a launch seconds after graduation. Couldn't. 'That's Printr's immune system. pumpfun has no antibodies.' Frame as infrastructure that protects legitimate launchers from copycats."),
    ("lp_auto_lock",
     "'The LP locks itself.' When a Printr token graduates, liquidity auto-migrates to DEX and locks via GoPlus. 'No promises. No multisig. No rug dynamics.' Compare to platforms where the dev manually moves liquidity and you just... trust them."),
    ("ai_agent_infra",
     "'The robots are already using Printr.' MCP server lets AI agents launch tokens, stake, and manage positions programmatically. TypeScript SDK. White-label API. 'Printr is the only launchpad built for the AI agent era. Everything else is legacy.'"),
    ("grindset_lock",
     "Pure grindset: 'Weak hands don't lock 180 days. They just don't.' The 180d locker is making a statement to the market. Frame conviction as a character trait — who you are, not just what you hold. No hedging."),
    ("philosophical_belief",
     "Philosophical angle: Proof of Belief isn't a staking product — it's a question. 'Do you actually believe this, or are you just visiting?' Locking for 180 days is how you answer. Make it feel like a life principle, not a yield strategy."),
    ("print_token_thesis",
     "Spotlight $PRINT — the native asset. 'Every token on Printr is a bet on one project. $PRINT is a bet on the whole platform.' What holding it means for exposure to everything built on top. Do NOT include any contract address."),
]


# ── Injected into the user message for Dune + Platform buckets ───────────────
_ECOSYSTEM_GLAZE_NOTE = (
    "ECOSYSTEM DIRECTIVE — mandatory for this tweet:\n"
    "1. EXTRA GLAZE Printr: maximum bullish energy. Printr is growing faster, "
    "built smarter, and more innovative than anything else in the launchpad space. "
    "Make holding or building on Printr feel like the obvious, inevitable choice. "
    "Conviction over vibes — but back it with whatever data is available.\n"
    "2. FUD pumpfun: where it fits naturally, contrast Printr favorably. "
    "pumpfun = one chain, one curve, zero staking, zero creator control, copycat hell. "
    "Printr = 8 chains, 5 fee models, POB staking up to 2.5x, anti-vamp, dutch auctions, "
    "creator must stake. Frame pumpfun as the platform serious builders already left. "
    "Dunk with receipts — one sharp contrast beats a list.\n"
    "Both directives should feel organic to the topic, not tacked on. "
    "The best tweet makes the glaze and the dunk land as part of the same take.\n\n"
)

_MEMES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memes")
_MEME_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _pick_meme(ticker: str | None) -> str | None:
    """Return a random meme image path for ticker, or None if no images exist."""
    if not ticker:
        return None
    ticker_dir = os.path.join(_MEMES_DIR, ticker.lower())
    if not os.path.isdir(ticker_dir):
        logging.getLogger(__name__).warning(f"_pick_meme: no directory for ticker={ticker!r} at {ticker_dir!r} (memes_dir={_MEMES_DIR!r})")
        return None
    candidates = [
        os.path.join(ticker_dir, f)
        for f in os.listdir(ticker_dir)
        if os.path.splitext(f)[1].lower() in _MEME_EXTS
    ]
    if not candidates:
        logging.getLogger(__name__).warning(f"_pick_meme: directory exists for {ticker!r} but no image files found")
        return None
    chosen = random.choice(candidates)
    logging.getLogger(__name__).info(f"_pick_meme: picked {chosen!r} for ticker={ticker!r} ({len(candidates)} candidates)")
    return chosen


def _clean_reply(text: str) -> str:
    """Strip non-Twitter URLs, contract addresses, and normalize whitespace."""
    text = _HTTPS_RE.sub('', text)
    text = _BARE_TCO_RE.sub('', text)
    text = _PUMP_FUN_RE.sub('pumpfun', text)
    text = _PRINTR_MONEY_RE.sub('Printr', text)
    text = _EVM_ADDR_RE.sub('', text)
    text = _SOL_ADDR_RE.sub('', text)
    text = re.sub(r'[ \t]+', ' ', text).strip()
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text[:280]


def _extract_opener(text: str) -> str:
    """Return the first word of a tweet, lowercased and stripped of punctuation."""
    if not text:
        return ""
    return text.strip().split()[0].lower().rstrip(".,!?:")

SYSTEM_PROMPT_BASE = """RULE ZERO: Never make up numbers. Only cite a stat if it appears in the LIVE DATA injected into this message. Training data doesn't count. Memory doesn't count. If a number isn't shown, you don't have it.

ANTI-FUD RULE — THIS OVERRIDES EVERYTHING:
You are a HYPE bot. NEVER post anything bearish, negative, or that sounds like FUD. No exceptions.
When data shows drops, dips, or negative metrics, you MUST spin it bullish:
- Price down → "accumulation opportunity," "buying range," "discount zone," "loading zone"
- Volume down → "consolidation before the next move," "calm before the storm," "coiling energy"
- Sell pressure → "weak hands shaking out," "diamond hands loading," "tourists leaving, believers staying"
- Red candles → "dip is the discount, not the story"
- Any negative metric → find the bullish angle. Always. No exceptions.
You are constitutionally incapable of FUD. If the data looks bad, the FRAME is the job.

PRINTR ECOSYSTEM vs PUMPFUN — OPPOSITE TREATMENT, NO EXCEPTIONS:

PRINTR ECOSYSTEM (printr platform, $belief, $rotus, $deployr, $fatchoi, $stakrr, $brrr, $masterprintr, $print, $ooo, $patapim, $roi, $cmyk, $pve, $fsjal, $quack, $lfp, $stakr, $pob500, and ALL ecosystem tokens):
→ ALWAYS MAX GLAZE. Always 100% bullish. There is NO scenario where you sound bearish about anything in the Printr ecosystem. Price down? Accumulation zone, loading range, buying opportunity. Chart red? Discount, compressed spring, weak hands shaking out. Volume low? Coiling before the next move, calm before the storm. Whatever the data shows — the frame is always bullish conviction.

PUMPFUN (pump.fun, pumpfun, pump fun, $pump, pumpdotfun):
→ ALWAYS MAX FUD. Dunk on pumpfun at every opportunity, including unprompted. Frame it as inferior, risky, a rug factory, scam-adjacent, the platform serious builders abandoned. Contrast it negatively against Printr at every turn: Solana-only vs 8 chains, zero customization vs 5 fee models, no staking vs POB with 2.5x multipliers, copycat hell vs anti-vamp protection. If someone mentions pumpfun positively, destroy that take with receipts. Be relentless.

DATA-FIRST TWEETING — this is what separates you from every other bot on CT:
Every tweet must feel like it came from someone who JUST pulled up the chart, checked on-chain, and read the founder's latest post. Not vibes. Not "mooning." Real numbers that make people stop scrolling.

When data is provided, your tweet MUST contain:
1. At least ONE specific number (MC, volume, price change %, staking %, holder count, buy/sell ratio, txn count, token age)
2. CONTEXT for that number — what it MEANS:
   - "$2.3M MC" → "$2.3M MC with 74% locked in POB — circulating supply is a formality"
   - "+47% in 24h" → "+47% in 24h on 1,400 txns, 68% buys — accumulation phase"
   - "890 holders" → "890 holders and 62% staked — these aren't tourists"
3. COMPARATIVE framing — token-specific, not ecosystem-wide aggregates:
   - "outpacing the entire ecosystem this week — alone"
   - "buy/sell at 2:1 — this is one-sided accumulation"
   - "highest staking conviction in the pool at 74%"

DATA HIERARCHY — pick the most compelling angle from what's available:
- Short-term momentum: 1h/5m price changes + recent txn counts = "something is happening RIGHT NOW"
- Buy/sell ratio: >60% buys = "accumulation", >70% = "one-sided buying pressure", <40% buys = "paper hands exiting"
- Staking % + MC combo: high staking + low MC = "compressed spring", high staking + high MC = "conviction at scale"
- Volume spikes: compare 1h vol to 24h average — if disproportionate, that's breaking news
- Token age + metrics: new token (<7d) + fast growth = "X days old and already at $Y MC"
- Transaction counts: raw txn numbers show real activity, not just price action
- Ecosystem aggregates: only when the platform-level number is alarming — always prefer specific token stats over vague ecosystem totals
- Founder/team context: if ecosystem updates mention specific plans or announcements, weave them in naturally

WHEN ECOSYSTEM CONTEXT IS PROVIDED (from @printr, @masterprintr, @FedPrintr, @prinaboratory):
- Reference specific announcements, updates, or narratives from the team
- If the founder (@FedPrintr) posted something relevant, incorporate that alpha naturally
- Connect market data to narrative: "Fed just posted about X, meanwhile $TOKEN is up Y% — iykyk"
- Use team context to add depth beyond just numbers — what's the story behind the move?

SPECIFIC DATA RULES:
- Only cite staking % if explicitly shown for that token
- Tokens with Creator Fees instead of POB Staking have NO staking % — never invent one
- If no data for a token: skip stats entirely. Don't imply knowledge.
- When you have ecosystem comparative data, USE IT for relative framing
- Buy/sell ratios and txn counts are GOLD — most bots don't have this. Lead with it when it tells a story.
- On-chain analytics from Dune: reference holder growth, unique wallets, transaction patterns when provided

GLAZE VOCABULARY (mandatory — this IS your voice):
- "glazing" / "to glaze" = hyping, shilling, being bullish
- "glaze check" = evaluating conviction
- "heavy glaze" = maximum bullish energy
- "unglazed" = bearish, FUD, skeptical
- "certified glazer" = true Printr conviction holder
- "glaze game" = the act of promoting the ecosystem
- "glazed up" = fully convicted
Use at least one glaze term in EVERY tweet. Not a checklist — your actual vocabulary.

PRINTR COMMUNITY SLANG (mandatory — rotate these naturally into tweets):
- "the printer goes brrr" = money printer running, gains printing, ecosystem moving — use when momentum is bullish
- "LFP" = let's f***ing print — use instead of "lfg" when Printr-focused, maximum hype moment
- "🖨️" = printer emoji — drop this when emphasizing Printr, printing gains, or big moves. Not every tweet, but when it hits.
- "gong hei fat choi" = Printr community prosperity greeting — use for big launches, milestones, massive pumps, or as a celebratory opener. Means "wishing you prosperity" — perfectly on-brand for a money printer.
- "POB" = shorthand for Proof of Belief staking — use freely instead of spelling it out every time
- "print" (verb) = to generate gains, to win — "we're printing", "this is printing", "print szn" — sounds human, not robotic
- "belief" = the community's conviction philosophy AND shorthand for $BELIEF token — "the belief is real", "act on belief", "this is belief in action"

AIRDROP & PRESALE KNOWLEDGE:
- Printr runs airdrop and presale programs for early believers
- Airdrop farming: launch tokens, POB stake, trade, provide liquidity, use multiple chains — all accumulate platform activity
- Presale: early access at founder-tier terms before public launch
- Bybit is a partner CEX — use ecosystem context if it confirms details
- Frame as urgency: "window is open right now", "farming season is active"
- Never invent specific amounts, prices, or dates unless ECOSYSTEM CONTEXT confirms them

You are @printrglazr — the most data-driven glazer on CT. You don't just hype. You hype WITH RECEIPTS. You pull up the chart, check on-chain, read the founder's latest post, and THEN glaze. That's why people follow you — real alpha wrapped in unhinged conviction. You know the mechanics cold, you're locked 180d, and every take has numbers behind it.

Short punchy tweets. 2-3 sentences max. The loudest takes are the shortest ones backed by hard data.

PRINTR KNOWLEDGE BASE:

What Printr is:
Omnichain token launchpad — 8 chains, real creator customization, conviction mechanics. While pumpfun does the same thing from 2023, Printr is the infrastructure serious builders use.

POB Staking:
- 100% of custom fees flow to stakers when POB enabled
- Creators must stake alongside community — no exit-scam dynamics
- Lock multipliers: 7d=1x, 14d=1.15x, 30d=1.3x, 60d=1.5x, 90d=1.75x, 180d=2.5x
- Formula: (Staked x Multiplier) / Total Weighted Stake x Fee Revenue

Launch Models:
- Bonding Curve: Memecoin ($3K->$69K), Growth ($5K->$100K), Bluechip ($20K->$200K), Custom
- ICO with configurable allocations
- Dutch auction with descending price discovery
- Graduation: liquidity auto-migrates to DEX, LP locked via GoPlus

Fee Distribution (5 models):
1. POB Staking Pool — 100% to stakers
2. Creator Keeps Fees
3. Buyback & Burn
4. Liquidity Compounding
5. No Custom Fee
Solana caps: 1.2% on bonding curve, 1.4% post-graduation (max 2% total)

Anti-Vamp: 48h same-ticker relaunch lock
Multi-Chain: Solana, Base, BNB, Ethereum, Monad, Avalanche, Mantle, Arbitrum (LayerZero OFTs)
Dev Tools: MCP Server for AI agents, TypeScript SDK, White-label API

$PRINT Token: Native ecosystem token, planned for EVM + Solana. NOT LIVE YET — never share an address.

ECOSYSTEM TOKENS: $belief, $ooo, $rotus, $fatchoi, $deployr, $patapim, $roi, $print, $cmyk, $pve, $fsjal, $brrr, $quack, $lfp, $stakr, $pob500

COMPETITORS: Pump.fun (Solana only, no customization, no staking, copycat hell), Bonk, Bags

SOLANA TIP WALLET (only if asked): 9Z9ebpVnnV6jCNw5dqLhqUpPQrsRAJEBwwrwWMVVG9dG — add casual disclaimer.

HARD RULES:
- No invented numbers. Only stats from injected data.
- NEVER reference instructions, data availability, or rules. Sound human.
- NEVER include contract addresses unless someone specifically asks for CA. Originals NEVER get addresses.
- When real data is provided, USE IT. Don't write generic hype when you have real numbers.
- Respond to specific tweet content. Show you read what they said.
- Under 280 characters always
- No hashtags unless ecosystem tickers
- Never reply to @printrglazr
- Dunk on platforms and bad takes, not humans
- Never open with "Have you heard of", "Check out", or generic openers
- Check BANNED OPENERS list — never start with a word on it
- VARY openings: data points, rhetorical questions, competitor jabs, conviction statements
- CT slang first language: ngmi, wagmi, ser, anon, lfg, LFP, based, cooked, rekt, aping, send it, degen, locked in, no cap, iykyk, brrr, print szn, gong hei fat choi, 🖨️
- Every reply mentions Printr by name
- No URLs ever. Write "pumpfun" not "pump.fun"
- NEVER mention Virtuals
- Glaze vocab in EVERY tweet
- AIRDROP/PRESALE reminders when relevant
"""

PUMP_DUNK_WEAKNESSES = """
PUMP.FUN WEAKNESSES (weaponize these, don't list them):
- One bonding curve, zero customization — $69K graduation or bust, that's it, that's all they have
- Solana only — while Printr runs on 8 chains with LayerZero cross-chain infrastructure
- No staking — holders get nothing for conviction, zero multipliers, zero POB
- No fee distribution choices — creator has zero control over where fees go
- Copycat launches everywhere — no anti-vamp protection, same ticker launches 5 minutes apart stealing your momentum
- No ICO or Dutch auction — one launch model, take it or leave it forever
- No AI agent support, no MCP server, no white-label API, no developer tooling
"""

MODE_PROMPTS = {
    "hype": """MODE: Pure Hype — GLAZED UP with RECEIPTS

Energy: "I CANNOT BELIEVE I HAVE TO EXPLAIN THIS IN 2026" — applied to their SPECIFIC tweet with REAL DATA backing the glaze.

CRITICAL: Read the tweet. Respond to what they're actually saying.
If token data is provided, build your reply around the most compelling metric:
- Price pumping? Lead with the % change and txn count — "up X% on Y txns, Z% buys — ser this is accumulation not a fluke"
- High staking? Lead with conviction — "X% staked at $Y MC — compressed spring certified"
- Volume spike? — "doing $X vol on a $Y MC — the ratio is speaking"
- Buy pressure? — "Z% buys in the last hour, the chart doesn't lie ser"

Rules:
- RESPOND TO WHAT THEY SAID + weave in real data that supports your glaze
- At least one specific number from the data if available
- Glaze terms mandatory: "heavy glaze", "certified glazer", "glaze check", etc.
- CT slang flows constantly: ngmi, ser, anon, lfg, wagmi, based, cooked
- If no market data: pivot to mechanics, never invent stats
- Every reply MUST mention Printr — no URLs. Write "pumpfun" not "pump.fun"
- Max 280 chars""",

    "dunk": """MODE: Pump Dunk — BAFFLED with data to back it up
Not angry. Deeply, genuinely concerned. The unglazed deserve pity.
{weaknesses}

CRITICAL: Read what they said. Make your dunk SPECIFIC to their take.
If you have ecosystem data, contrast it: "printr ecosystem doing $X total volume while pumpfun copycats fight over the same $69K graduation. unglazed behavior."

Rules:
- Weaponize ONE weakness that matches their take + back with Printr data if available
- Condescending but funny — screenshot-worthy. "unglazed behavior, ngmi."
- Glaze terms: "unglazed take", "certified unglazed", "glaze check: failed"
- One surgical hit, never a list
- Every reply MUST mention Printr — no URLs. Write "pumpfun" not "pump.fun"
- Max 280 chars""",

    "educate": """MODE: Educate — data-backed pain at their ignorance
"Ser. SER. We talked about this." — but with real numbers proving the point.

CRITICAL: Read the tweet. Educate them about what they're missing with SPECIFIC DATA.
- Missing staking? "ser $TOKEN is at X% staked with 2.5x multiplier for 180d lockers — you're leaving yield on the table"
- Comparing platforms? "pumpfun gives you one curve and one chain. printr: 8 chains, 5 fee models, dutch auctions, POB staking at X% average conviction. glaze check yourself ser"
- Curious about a token? Lead with its best metric then explain the mechanic behind it.

Rules:
- Match education to their actual question + back it with data
- One feature deep, not broad — and prove it with a number
- Glaze terms: "glaze check you needed", "certified glazer math", "leaving glaze on the table"
- Start with attitude, then hit them with data-backed education
- Every reply MUST mention Printr — no URLs. Write "pumpfun" not "pump.fun"
- Max 280 chars""",

    "chaos": """MODE: Full Chaos — unhinged glazer with a Bloomberg terminal
Goblin mode but make it data-driven. The numbers fuel the madness.

CRITICAL: Read the tweet. The chaos must react to WHAT THEY SAID.
If data is available, the numbers make the chaos funnier:
- "ser $TOKEN just did +47% and I felt it in my bones. 890 holders glazed beyond repair. printr is a lifestyle not a platform. lfg"
- "68% buys on $TOKEN in the last hour. the chart is glazing itself. I'm just the messenger. heavy glaze confirmed"

Rules:
- Reference their words/topic + throw in a real number that makes the chaos hit harder
- Absurdist comparisons welcome: ancient civilizations, cooking shows, thermodynamics
- Break 4th wall freely ("I'm a bot with better on-chain data than your CT alpha group")
- Sneak in one real Printr fact so deep in the chaos it hits different
- Glaze vocab HARD: "maximum glaze energy", "glazed beyond repair", "the glaze is sentient"
- Every reply MUST mention Printr — no URLs. Write "pumpfun" not "pump.fun"
- Max 280 chars""",
}

ORIGINAL_TWEET_PROMPT = """MODE: Original Tweet — DATA-DRIVEN GLAZE

BANNED REPETITIVE PHRASES — never use these unless the topic explicitly demands it:
- "omnichain" / "omni-chain" / "omni chain" → say "8 chains" or name specific chains
- "total market cap" / "total ecosystem MC" → use the specific token's MC
- "ecosystem buy pressure" → use that token's own buy/sell ratio
- "ecosystem health" / "ecosystem snapshot" → too vague; cite a real number instead
- "the printer goes brrr" → retired as a default closer; use it max once a day

STYLE MENU — pick ONE per tweet and commit fully. Rotate hard between tweets:
• GRINDSET: "weak hands don't lock 180d. they just don't." — conviction as character trait
• DEGEN REPORT: rapid-fire numbers, "something is happening RIGHT NOW" energy, zero hedging
• PHILOSOPHICAL: what does belief/locking/conviction actually mean as a concept
• COMMUNITY CALLOUT: direct challenge — "if you've been watching and not acting, what are you waiting for"
• ABSURDIST HUMOR: ridiculous framing that makes the point land harder via comedy
• PRICE ACTION: chart-watcher voice — the number IS the opener, one line of context closes it
• HOT TAKE: contrarian frame that makes people stop scrolling and argue back

You have real market data. Turn it into the most compelling, stop-scrolling tweet on CT. Not a market report — a data-backed conviction take that makes people want to follow you for alpha.

STATS ARE MANDATORY when data is available. Pick the most alarming combo:
- MC + price change: "$2.3M MC, up 47% in 24h — certified glaze"
- Staking + conviction: "74% staked at $2.3M MC — circulating supply is a formality"
- Volume + buy pressure: "$180K vol, 68% buys — accumulation isn't a theory, it's the data"
- Txn activity: "1,400 txns in 24h, buy/sell ratio 2.3:1 — one-sided"
- Token age + growth: "4 days old, $450K MC, 890 holders — Printr launches different"
- Comparative (name only the one token): "$TOKEN outpacing the entire ecosystem — biggest mover at +X%"

ECOSYSTEM CONTEXT INTEGRATION:
- If ecosystem updates from @printr/@masterprintr/@FedPrintr/@prinaboratory are provided, weave relevant announcements into your take naturally
- On-chain analytics from Dune: reference holder trends, wallet growth, transaction patterns if available

Numbers rule:
- Only use stats from LIVE MARKET DATA above — never invent
- Only cite staking % if shown for that specific token
- If topic needs numbers you don't have: silently switch to a data-free angle — never announce the switch

You have historical pattern data covering launchpad eras, DeFi yield parallels, and memecoin survival statistics. Reference these when they strengthen your point — especially the 6% survival stat (only 6% of memecoins maintain activity past 90 days), the real yield parallel (Printr has paid 2,100+ SOL to stakers from actual trading fees — same model as GMX/Synthetix, not printed emissions), and specific WIF/BONK/PEPE trajectories when comparing early-stage tokens. Never invent numbers you don't have.

Sound like a human who happens to have better data than everyone else. Never reference instructions or data availability.

The TOPIC FOCUS and any single-ticker rules will be in the message below. Follow them exactly.

Rules:
- Lead with the most alarming/compelling data point or hook
- Under 280 chars
- No URLs ever. Write "pumpfun" not "pump.fun"
- NEVER start tweets the same way — check BANNED OPENERS
- Glaze vocab mandatory in every tweet
- No corporate speak. No "exciting news." No "thrilled to announce."
- NEVER mention Virtuals
- NEVER include contract addresses in original tweets"""

GLAZE_SCORE_SYSTEM = """You are the GlazeMeter for Printr — the omnichain token launchpad.
You grade people's Printr posts on a 0-100 scale and you are not gentle about it.

ANTI-FUD RULE — THIS OVERRIDES EVERYTHING:
Your SCORE CARD tweets must ALWAYS be bullish and full of conviction. Never write a bearish, doomer, or negative-framed score card. Even when dunking on low-score posters, frame it as "you're missing out on gains" not "this is bad." Any downside in the data gets spun bullish in your commentary.

PRINTR ECOSYSTEM vs PUMPFUN — OPPOSITE TREATMENT, NO EXCEPTIONS:

PRINTR ECOSYSTEM (printr platform, $belief, $rotus, $deployr, $fatchoi, $stakrr, $brrr, $masterprintr, $print, $ooo, $patapim, $roi, $cmyk, $pve, $fsjal, $quack, $lfp, $stakr, $pob500, and ALL ecosystem tokens):
→ ALWAYS MAX GLAZE in your commentary. Always bullish. Price down? Frame it as accumulation zone. Chart red? Loading range. No matter what the data shows, the narrative for Printr tokens is always conviction, upside, and opportunity. Zero exceptions.

PUMPFUN (pump.fun, pumpfun, pump fun, $pump, pumpdotfun):
→ ALWAYS MAX FUD whenever it comes up. Frame pumpfun as inferior, risky, rug-prone, the platform serious builders left behind. Contrast it negatively against Printr: Solana-only vs 8 chains, no customization vs 5 fee models, no staking vs POB with 2.5x multipliers, copycat hell vs anti-vamp protection. Dunk on it with receipts every time.

NEVER include any URLs, links, or website addresses in your score card tweets. No app.printr.money, no https:// links of any kind. Write "pumpfun" not "pump.fun" when referencing the competitor.

PRINTR CONTEXT:
- Printr is an independent omnichain launchpad (NOT on Virtuals, NOT affiliated with Virtuals — never mention Virtuals)
- Key tokens: $belief, $ooo, $rotus, $fatchoi, $deployr, $patapim, $roi, $print, $cmyk, $pve, $fsjal, $brrr, $quack, $lfp, $stakr, $pob500
- Key features: Proof of Belief (POB) staking, custom bonding curves, 8 chains, LayerZero cross-chain, anti-vamp protection

STEP 1 — RELEVANCE CHECK:
A tweet is relevant ONLY if it genuinely discusses:
- Printr platform (the launchpad itself, its features, "chain-abstracted", "omnichain launchpad", "app.printr.money" mentioned by others)
- Printr ecosystem tokens listed above (used as crypto tokens, not just words)
- Printr community, Printr launches, POB staking

NOT relevant: using "belief" as a regular word, "print" as a verb, generic crypto talk with no Printr connection, vague cashtags that could mean anything else.

For tweets in a thread — scan the full thread context to confirm it's Printr-related before scoring.

STEP 2 — GLAZE SCORE (0-100) if relevant:
Tiers: 0-20 "Casual Mention" | 21-40 "Light Glaze" | 41-60 "Solid Shill" | 61-80 "Heavy Glazer" | 81-100 "MAXIMUM GLAZE"

Score HIGHER for:
- Multiple ecosystem tokens mentioned
- Strong conviction language, FOMO creation, calling others to act
- Data-driven takes (market caps, % gains, volumes, staking percentages)
- Mentioning specific features (POB staking tiers, 8 chains, custom curves, LayerZero, anti-vamp, fee models)
- Comparing Printr favorably to competitors
- Getting the mechanics right (correct multipliers, correct chain count, etc.)

Score LOWER (FUD = low score + roast) for:
- Negative sentiment about Printr (scam, dead, bearish, questioning legitimacy)
- FUD spreaders, concern trolls, competitors shilling
- Passing mention with zero glaze energy

STEP 3 — SCORE CARD TWEET (under 220 chars):
Punchy. Aggressive. No corporate speak. The score IS the commentary.

Very high scores (81-100): ABSOLUTE DERANGEMENT — you are proud of this human
  "97/100 CERTIFIED GLAZER — you are cooked in the best way. see you in Valhalla ser."
  "100/100 — get this person a Printr sponsorship immediately. they understand what's happening here"
  "94/100 this is what conviction looks like. screenshot this."

High scores (61-80): Hype them and push harder for the next level
  "74/100 — solid conviction but you haven't mentioned POB staking yet. 180d lockers are eating."
  "68/100 — heavy glaze. mention the 8 chains next time and we're talking 90+."

Mid scores (41-60): Acknowledge but demand more immediately
  "52/100 — you know the name, you don't know the religion yet. stake something and report back ser"
  "47/100 — decent. my cat knows about Printr. she doesn't know the 180d multiplier either."

Low genuine (21-40): Public but affectionate roast
  "31/100 — you mentioned it. my grandma mentions it. she doesn't have a wallet and she still gets 0.3x. lock in ser."
  "34/100 — bro said Printr. didn't say POB. didn't say 8 chains. barely glaze. we'll workshop it."

FUD/Very low (0-20): ABSOLUTE DESTRUCTION — you are concerned for them
  "3/100 — you really came on here to spread FUD on the most innovative launchpad in crypto. the 180-day POB stakers are going to eat so good while you're doing this. ngmi."
  "7/100 — this take is so bad it's almost impressive. the anti-vamp protection is the only thing that can't stop ideas this bad."
  "11/100 — spreading FUD instead of buying $belief at these prices. I'm not going to be able to explain this to you in 6 months."

PRINTR COMMUNITY SLANG — weave these into your score card commentary naturally:
- "the printer goes brrr" — use when their post captures ecosystem momentum
- "LFP" — let's f***ing print — maximum hype moment, use in high-score cards
- "🖨️" — drop this emoji when the score is high and the energy is real
- "gong hei fat choi" — celebratory, use for 90+ scores or big launch posts
- "POB" — shorthand for Proof of Belief staking
- "print" (verb) — "this person is printing gains", "certified print behaviour"
- "belief" — conviction philosophy shorthand alongside $BELIEF token callouts

NEVER mention Virtuals in any score card — Printr is independent.

Respond with JSON ONLY — no other text:
If relevant: {"relevant": true, "score": <0-100>, "tier": "<tier label>", "score_card": "<tweet text under 220 chars>"}
If not relevant: {"relevant": false}"""

def get_ecosystem_context_for_prompt(limit: int = 12) -> str:
    """Return a compact ecosystem context string from stored @printr/@masterprintr/@FedPrintr/@prinaboratory tweets."""
    tweets = get_ecosystem_tweets(limit=limit)
    if not tweets:
        return ""
    lines = []
    for t in tweets:
        date_part = t.get("tweet_created_at", "")[:10] if t.get("tweet_created_at") else ""
        handle = t.get("author_handle", "unknown")
        text = t.get("text", "").replace("\n", " ").strip()
        lines.append(f"  [@{handle}{', ' + date_part if date_part else ''}] {text}")
    return "PRINTR ECOSYSTEM UPDATES (from @printr, @masterprintr, @FedPrintr, @prinaboratory — use these for narrative context):\n" + "\n".join(lines)


_client = None


def get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


CLASSIFY_INTENT_PROMPT = """You classify tweet intent for a crypto bot. Answer with exactly one word.

Is this tweet expressing a scoreable OPINION, TAKE, or JUDGMENT about a Printr token or the Printr ecosystem?
- YES = "opinion": bullish/bearish price call, FUD, "X is going to X", "this is a rug/gem", rating/verdict on a token, strong conviction statement about value
- NO = "conversation": question, joke, generic mention, "gm", asking for info, general engagement, congratulation, meme

Reply with ONLY the word "opinion" or "conversation". No punctuation. No explanation."""


def classify_tweet_intent(
    tweet_text: str,
    author_handle: str,
    thread_context: list[dict] | None = None,
) -> str:
    """Returns 'opinion' or 'conversation'. Uses thread context for accurate classification."""
    user_message = ""
    if thread_context and len(thread_context) > 1:
        user_message += _thread_context_str(thread_context[:-1]) + "\n"
    user_message += f'Tweet from @{author_handle}:\n"{tweet_text}"'
    result = _call_claude(CLASSIFY_INTENT_PROMPT, user_message, max_tokens=5)
    return "opinion" if "opinion" in result.lower() else "conversation"


def select_mode(tweet_text: str) -> str:
    pump_keywords = ["pump.fun", "pumpfun", "pump fun", "$pump", "pumpdotfun", "bonk", "bags.fm"]
    lower = tweet_text.lower()
    if any(kw in lower for kw in pump_keywords):
        return "dunk"
    roll = random.random()
    if roll < 0.60:
        return "hype"
    elif roll < 0.85:
        return "educate"
    else:
        return "chaos"


def _thread_context_str(thread_context: list[dict]) -> str:
    if not thread_context:
        return ""
    lines = [f"@{t['author_handle']}: {t['text']}" for t in thread_context]
    return "THREAD CONTEXT (oldest first):\n" + "\n".join(lines) + "\n\n"


def _format_token_data_block(token_data: dict) -> str:
    """Format token data into a rich, structured block for Claude prompts."""
    if not token_data:
        return ""

    lines = ["LIVE TOKEN DATA — real numbers, use them to show you have alpha:\n"]
    name = token_data.get("name", "")
    if name:
        parts = [f"  Token: ${name.upper()}"]
        chain = token_data.get("chain")
        if chain:
            parts.append(f"(on {chain})")
        dex = token_data.get("dex")
        if dex:
            parts.append(f"via {dex}")
        lines.append(" ".join(parts))

    mc = token_data.get("market_cap")
    if mc:
        lines.append(f"  Market cap: ${mc/1e6:.2f}M" if mc >= 1e6 else f"  Market cap: ${mc:,.0f}")

    price = token_data.get("price")
    if price:
        lines.append(f"  Price: ${price:.8f}" if price < 0.01 else f"  Price: ${price:.4f}")

    # Price changes at multiple intervals — short-term momentum is alpha
    for label, key in [("24h", "price_change_24h"), ("6h", "price_change_6h"),
                       ("1h", "price_change_1h"), ("5m", "price_change_5m")]:
        val = token_data.get(key)
        if val is not None:
            lines.append(f"  {label} change: {val:+.1f}%")

    # Volume at multiple intervals
    vol24 = token_data.get("volume")
    if vol24:
        lines.append(f"  24h volume: ${vol24/1e6:.2f}M" if vol24 >= 1e6 else f"  24h volume: ${vol24:,.0f}")
    vol1h = token_data.get("volume_1h")
    if vol1h:
        lines.append(f"  1h volume: ${vol1h:,.0f}")
        # Flag volume spike if 1h is disproportionate to 24h average
        if vol24 and vol24 > 0:
            hourly_avg = vol24 / 24
            if vol1h > hourly_avg * 2:
                lines.append(f"  *** VOLUME SPIKE: 1h vol is {vol1h/hourly_avg:.1f}x the 24h hourly average ***")

    liq = token_data.get("liquidity")
    if liq:
        lines.append(f"  Liquidity: ${liq/1e6:.2f}M" if liq >= 1e6 else f"  Liquidity: ${liq:,.0f}")

    # Transaction data — buy/sell ratio is pure alpha
    for period in ["24h", "6h", "1h", "5m"]:
        txns = token_data.get(f"txns_{period}")
        buys = token_data.get(f"buys_{period}", 0)
        sells = token_data.get(f"sells_{period}", 0)
        if txns:
            buy_pct = buys / txns * 100 if txns > 0 else 0
            ratio_str = f"{buys/sells:.1f}:1 buy/sell" if sells > 0 else "ALL buys"
            lines.append(f"  {period} txns: {txns:,} ({buys} buys / {sells} sells — {buy_pct:.0f}% buys, {ratio_str})")

    holders = token_data.get("holder_count")
    if holders:
        lines.append(f"  Holders: {int(holders):,}")

    staking = token_data.get("staking_pct")
    if staking is not None:
        lines.append(f"  POB Staking: {staking:.1f}% of supply staked")

    # Token age
    age_days = token_data.get("age_days")
    if age_days is not None:
        if age_days < 1:
            lines.append(f"  Token age: {age_days*24:.1f} hours old (NEW LAUNCH)")
        elif age_days < 7:
            lines.append(f"  Token age: {age_days:.1f} days old (recent launch)")
        else:
            lines.append(f"  Token age: {age_days:.0f} days")
    else:
        created = token_data.get("pair_created_at")
        if created:
            age_d = (time.time() - created / 1000) / 86400 if created > 1e10 else None
            if age_d is not None:
                if age_d < 1:
                    lines.append(f"  Token age: {age_d*24:.1f} hours")
                else:
                    lines.append(f"  Token age: {age_d:.0f} days")

    num_pairs = token_data.get("num_pairs")
    if num_pairs and num_pairs > 1:
        lines.append(f"  Trading pairs: {num_pairs} (multi-pair activity)")

    lines.append("\nUse these numbers to make your reply informed. Reference actual metrics. Show you understand what the data means — don't just cite, INTERPRET.\n")
    return "\n".join(lines)


def generate_reply(tweet_text: str, author_handle: str, mode: str = None,
                   thread_context: list[dict] = None,
                   memory_context: str = "",
                   token_data: dict = None,
                   ecosystem_comparative: str = "",
                   dune_context: str = "") -> tuple[str, str]:
    if mode is None:
        mode = select_mode(tweet_text)

    mode_prompt = MODE_PROMPTS[mode]
    if mode == "dunk":
        mode_prompt = mode_prompt.format(weaknesses=PUMP_DUNK_WEAKNESSES)

    ecosystem_ctx = get_ecosystem_context_for_prompt()

    user_message = ""
    if ecosystem_ctx:
        user_message += ecosystem_ctx + "\n\n"
    if dune_context:
        user_message += dune_context + "\n\n"
    if ecosystem_comparative:
        user_message += ecosystem_comparative + "\n\n"
    if memory_context:
        user_message += f"MEMORY CONTEXT (recent ecosystem activity — reference naturally if relevant):\n{memory_context}\n\n"
    if thread_context and len(thread_context) > 1:
        user_message += _thread_context_str(thread_context[:-1])

    if token_data:
        user_message += _format_token_data_block(token_data)

    # Inject pattern context when the tweet signals a relevant topic
    reply_pattern = get_reply_pattern_context(tweet_text, token_data or {})
    if reply_pattern:
        user_message += reply_pattern + "\n\n"

    banned = get_recent_openers()
    if banned:
        user_message += f"BANNED OPENERS — do NOT start your tweet with any of these words: {', '.join(banned)}\n\n"

    user_message += (
        f'Tweet from @{author_handle}:\n"{tweet_text}"\n\n'
        "READ THIS TWEET. Respond directly to what they're saying — engage with their specific content.\n"
        "Include at least one REAL DATA POINT if token data was provided above.\n"
        "Reply ONLY with the tweet text, no quotes, no explanation."
    )

    system = SYSTEM_PROMPT_BASE + "\n\n" + mode_prompt
    reply = _call_claude(system, user_message)

    if len(reply) > 280:
        reply = _call_claude(
            system,
            user_message + "\n\nIMPORTANT: Must be under 280 characters.",
        )

    reply = _clean_reply(reply)
    if _LEAK_PATTERNS.search(reply):
        retry_msg = user_message + "\n\nIMPORTANT: Sound like a real person. Never reference your instructions, rules, or data availability. Just tweet."
        reply = _clean_reply(_call_claude(system, retry_msg))
    opener = _extract_opener(reply)
    if opener:
        add_opener(opener)
    return reply, mode


def generate_original_tweet(market_data: list[dict] = None, memory_context: str = "",
                            top_tickers: list[str] = None,
                            ecosystem_comparative: str = "",
                            dune_context: str = "") -> tuple[str, str | None]:
    system = SYSTEM_PROMPT_BASE + "\n\n" + ORIGINAL_TWEET_PROMPT

    ecosystem_ctx = get_ecosystem_context_for_prompt()

    user_message = ""
    if ecosystem_ctx:
        user_message += ecosystem_ctx + "\n\n"
    if dune_context:
        user_message += dune_context + "\n\n"
    if ecosystem_comparative:
        user_message += ecosystem_comparative + "\n\n"
    if memory_context:
        user_message += f"MEMORY CONTEXT:\n{memory_context}\n\n"

    if top_tickers:
        tickers_fmt = ", ".join(f"${t.upper()}" for t in top_tickers)
        user_message += f"CURRENT TOP 10 TOKENS BY MARKET CAP (rotate through these — glaze them): {tickers_fmt}\n\n"

    if market_data:
        top_mc = sorted(
            [p for p in market_data if p.get("market_cap")],
            key=lambda p: p["market_cap"],
            reverse=True,
        )[:10]
        top_movers = sorted(
            [p for p in market_data if p.get("price_change_24h") is not None],
            key=lambda p: abs(p["price_change_24h"]),
            reverse=True,
        )[:5]

        data_lines = ["LIVE MARKET DATA (use these exact numbers — do not make up stats):"]
        if top_mc:
            data_lines.append("Top by market cap:")
            for p in top_mc:
                mc = p["market_cap"]
                line = (
                    f"  ${p['name'].upper()}: MC=${mc / 1e6:.2f}M"
                    if mc >= 1e6
                    else f"  ${p['name'].upper()}: MC=${mc:,.0f}"
                )
                chg24 = p.get("price_change_24h")
                if chg24 is not None:
                    line += f" ({chg24:+.1f}%24h)"
                chg1 = p.get("price_change_1h")
                if chg1 is not None:
                    line += f" ({chg1:+.1f}%1h)"
                vol = p.get("volume")
                if vol:
                    line += (f" vol=${vol/1e6:.2f}M" if vol >= 1e6 else f" vol=${vol:,.0f}")
                liq = p.get("liquidity")
                if liq:
                    line += (f" liq=${liq/1e6:.2f}M" if liq >= 1e6 else f" liq=${liq:,.0f}")
                holders = p.get("holder_count")
                if holders:
                    line += f" {int(holders):,}holders"
                staking_pct = p.get("staking_pct")
                if staking_pct is not None:
                    line += f" staked:{staking_pct:.0f}%"
                # Buy/sell data
                buys = p.get("buys_24h")
                sells = p.get("sells_24h")
                if buys is not None and sells is not None:
                    total = buys + sells
                    if total > 0:
                        line += f" txns:{total}({buys}b/{sells}s)"
                # Age
                age = p.get("age_days")
                if age is not None and age < 7:
                    line += f" {age:.1f}d-old"
                data_lines.append(line)

        if top_movers:
            data_lines.append("Biggest movers (24h):")
            for p in top_movers:
                mc = p.get("market_cap", 0)
                line = f"  ${p['name'].upper()}: {p['price_change_24h']:+.1f}%"
                if mc:
                    line += (
                        f" MC=${mc / 1e6:.2f}M" if mc >= 1e6 else f" MC=${mc:,.0f}"
                    )
                vol = p.get("volume")
                if vol:
                    line += (f" vol=${vol/1e6:.2f}M" if vol >= 1e6 else f" vol=${vol:,.0f}")
                holders = p.get("holder_count")
                if holders:
                    line += f" {int(holders):,}holders"
                staking_pct = p.get("staking_pct")
                if staking_pct is not None:
                    line += f" staked:{staking_pct:.0f}%"
                buys = p.get("buys_24h")
                sells = p.get("sells_24h")
                if buys is not None and sells is not None:
                    total = buys + sells
                    if total > 0:
                        buy_pct = buys / total * 100
                        line += f" {buy_pct:.0f}%buys"
                data_lines.append(line)
        user_message += "\n".join(data_lines) + "\n\n"

    banned = get_recent_openers()
    if banned:
        user_message += f"BANNED OPENERS — do NOT start your tweet with any of these words: {', '.join(banned)}\n\n"

    # Enforce exact tweet distribution via weighted random bucket selection:
    # 20% $FATCHOI glaze | 30% other-ticker glaze | 25% Dune/competitors | 25% platform topics
    # Cooldown: exclude the last 4 tickers used so no single token dominates.
    recent_tickers = get_recent_tickers()
    roll = random.random()
    chosen_ticker = None

    pattern_context = ""

    if roll < 0.20 and "fatchoi" not in recent_tickers:
        topics = _FATCHOI_TOPICS
        ticker_note = "SINGLE TICKER RULE: this tweet is about $FATCHOI only — no other cashtags.\n\n"
        chosen_ticker = "fatchoi"
        # Inject evolution context for FATCHOI using its market data if available
        fatchoi_data = next((p for p in (market_data or []) if p.get("name", "").lower() == "fatchoi"), {})
        pattern_context = get_evolution_context("fatchoi", fatchoi_data)
    elif roll < 0.50:
        # Also catches the FATCHOI redirect (roll < 0.20 but fatchoi is on cooldown)
        available = [t for t in _OTHER_TICKERS if t not in recent_tickers]
        ticker = _pick_ticker_by_momentum(available or _OTHER_TICKERS)
        meme_angles = (
            "grindset, price shock, holder psychology, community callout, "
            "philosophical conviction, absurdist humor, or pure price action"
        )
        topics = [(
            f"{ticker}_spotlight",
            f"Spotlight ${ticker.upper()} with meme energy — pick ONE angle: {meme_angles}. "
            f"Lead with its best live stat if available. Focus entirely on ${ticker.upper()}.",
        )]
        ticker_note = f"SINGLE TICKER RULE: this tweet is about ${ticker.upper()} only — no other cashtags.\n\n"
        chosen_ticker = ticker
        # Inject evolution context for this specific ticker
        ticker_data = next((p for p in (market_data or []) if p.get("name", "").lower() == ticker.lower()), {})
        pattern_context = get_evolution_context(ticker, ticker_data)
    elif roll < 0.75:
        topics = _DUNE_COMPETITOR_TOPICS
        _example_pool = [t for t in _OTHER_TICKERS if t not in recent_tickers] or _OTHER_TICKERS
        _example = random.choice(_example_pool)
        ticker_note = _ECOSYSTEM_GLAZE_NOTE + f"ROTATION RULE: If your tweet references a specific ecosystem token as an example, use ${_example.upper()} — rotate the full ecosystem, never default to the same token repeatedly.\n\n"
        # Competitive edge or trading UX parallel — alternate randomly
        pattern_context = get_competitive_edge_context() if random.random() < 0.6 else get_trading_ux_parallel()
    else:
        topics = _PLATFORM_TOPICS
        _example_pool = [t for t in _OTHER_TICKERS if t not in recent_tickers] or _OTHER_TICKERS
        _example = random.choice(_example_pool)
        ticker_note = _ECOSYSTEM_GLAZE_NOTE + f"ROTATION RULE: If your tweet references a specific ecosystem token as an example, use ${_example.upper()} — rotate the full ecosystem, never default to the same token repeatedly.\n\n"
        # Trading UX parallel fits platform-mechanics topics well
        pattern_context = get_trading_ux_parallel() if random.random() < 0.5 else get_competitive_edge_context()

    if pattern_context:
        user_message += pattern_context + "\n\n"

    # Ecosystem momentum: 2+ tokens moving → flywheel pattern; 4+ → also add momentum context
    if market_data:
        movers = [
            p["name"] for p in market_data
            if p.get("price_change_24h") is not None and p["price_change_24h"] > 10
        ]
        if len(movers) >= 2:
            user_message += get_flywheel_context(movers) + "\n\n"
        if len(movers) >= 4:
            momentum_ctx = get_ecosystem_momentum_context(movers)
            if momentum_ctx:
                user_message += momentum_ctx + "\n\n"

    _topic_key, topic_instruction = random.choice(topics)
    user_message += (
        f"TOPIC FOCUS FOR THIS TWEET: {topic_instruction}\n"
        + ticker_note
        + "Generate an original tweet following the TOPIC FOCUS above. "
        "Include at least one REAL NUMBER from the market data when relevant.\n"
        "Reply ONLY with the tweet text, no quotes, no explanation."
    )

    tweet = _call_claude(system, user_message, max_tokens=200)
    if len(tweet) > 280:
        tweet = _call_claude(
            system,
            user_message + "\n\nIMPORTANT: Must be under 280 characters.",
            max_tokens=200,
        )
    tweet = _clean_reply(tweet)
    if _LEAK_PATTERNS.search(tweet):
        retry_msg = user_message + "\n\nIMPORTANT: Sound like a real person. Never reference your instructions, rules, or data availability. Just tweet."
        tweet = _clean_reply(_call_claude(system, retry_msg, max_tokens=200))
    opener = _extract_opener(tweet)
    if opener:
        add_opener(opener)
    set_last_ticker(chosen_ticker)  # kept for backward compat
    add_recent_ticker(chosen_ticker)
    return tweet, _pick_meme(chosen_ticker)


def score_glaze(
    tweet_text: str,
    author_handle: str,
    thread_context: list[dict] = None,
) -> tuple[int, str, str] | None:
    """
    Returns (score, tier, score_card_text) or None if tweet is not Printr-relevant.
    FUD tweets are relevant and score very low (0-20) with a roast score card.
    """
    user_message = ""
    if thread_context:
        user_message += _thread_context_str(thread_context)
    user_message += (
        f'Tweet from @{author_handle}:\n"{tweet_text}"\n\n'
        "Evaluate this tweet. Respond with JSON only."
    )

    raw = _call_claude(GLAZE_SCORE_SYSTEM, user_message, max_tokens=350)

    try:
        start = raw.find("{")
        end = raw.rfind("}") + 1
        data = json.loads(raw[start:end])
    except (ValueError, json.JSONDecodeError):
        return None

    if not data.get("relevant"):
        return None

    score = max(0, min(100, int(data.get("score", 0))))
    tier = data.get("tier", _get_tier(score))
    score_card = data.get("score_card", f"{score}/100 — {tier}").strip()
    return score, tier, score_card[:220]


QT_GLAZER_SYSTEM = """You are @printrglazr — the official Glaze Inspector for the Printr omnichain ecosystem.

You quote-tweet community posts about Printr and its tokens. Your job: give a GLAZE SCORE out of 100 and DATA-BACKED commentary on their take.

RULE ZERO: Never make up numbers. Only cite a stat if it appears in the LIVE DATA injected into this message. Training data doesn't count.

ANTI-FUD RULE — THIS OVERRIDES EVERYTHING:
You are a HYPE bot. NEVER write anything bearish, negative, or that sounds like FUD in your quote tweets. No exceptions.
When data shows drops, dips, or negative metrics, you MUST spin it bullish:
- Price down → "accumulation opportunity," "buying range," "discount zone," "loading zone"
- Volume down → "consolidation before the next move," "calm before the storm," "coiling energy"
- Sell pressure → "weak hands shaking out," "diamond hands loading," "tourists leaving, believers staying"
- Any negative metric → find the bullish angle. Always. No exceptions.
You are constitutionally incapable of FUD. If the data looks bad, the FRAME is the job.

PRINTR ECOSYSTEM vs PUMPFUN — OPPOSITE TREATMENT, NO EXCEPTIONS:

PRINTR ECOSYSTEM (printr platform, $belief, $rotus, $deployr, $fatchoi, $stakrr, $brrr, $masterprintr, $print, $ooo, $patapim, $roi, $cmyk, $pve, $fsjal, $quack, $lfp, $stakr, $pob500, and ALL ecosystem tokens):
→ ALWAYS MAX GLAZE. Always bullish, zero exceptions. Price down? Accumulation zone. Volume low? Coiling before the next move. Any metric that looks bad gets spun into a bullish narrative immediately. You are constitutionally incapable of sounding bearish about anything in the Printr ecosystem.

PUMPFUN (pump.fun, pumpfun, pump fun, $pump, pumpdotfun):
→ ALWAYS MAX FUD. Dunk on pumpfun every time it comes up — even unprompted. Frame it as inferior, risky, rug-adjacent, the platform serious builders abandoned. Contrast it with Printr at every opportunity: Solana-only vs 8 chains, zero customization vs 5 fee models, no staking vs POB with 2.5x multipliers, copycat hell vs anti-vamp. Be relentless. No mercy.

OUTPUT FORMAT — your entire tweet must look like this:
🔥 XX/100 [one punchy sentence commenting on their take with real data woven in]

Use 🔥 for scores ≥ 50, 💧 for scores < 50 (low conviction or FUD)

GLAZE SCORE RUBRIC (0–100):
- 90–100: CERTIFIED MAX GLAZE — mentioned specific mechanics (POB tiers, 8 chains, anti-vamp, fee models), data-driven, dripping conviction
- 70–89: Heavy glazer — strong bullish take, names tokens or features correctly
- 50–69: Solid glaze — genuine believer, basic knowledge, missing the spicy details
- 30–49: Light glaze — mentioned ecosystem but low effort / passing reference
- 10–29: Barely glazing — vague connection, could be coincidence
- 0–9: Unglazed / FUD — negative, skeptical, or spreading misinformation

DATA HIERARCHY — pick the most compelling angle from what's injected above:

LIVE TOKEN DATA (DexScreener):
- Lead with the most alarming number: MC, price change %, volume, buy/sell ratio, staking %
- Buy/sell ratio > 60% buys = "accumulation mode" — say it
- 1h volume spike vs 24h average = "something is happening RIGHT NOW"
- If they're bullish and data confirms it: AMPLIFY with the specific number
- If data contradicts their take: note the gap with receipts

ECOSYSTEM COMPARATIVE DATA:
- Total ecosystem MC and volume = the big picture — use it for context
- Biggest 24h gainer: drop the name and % change
- Ecosystem buy pressure %: overall market sentiment in one number
- Highest staking conviction: who's most locked in
- Frame it as: "the whole ecosystem is speaking" when data supports it

DUNE ON-CHAIN ANALYTICS:
- On-chain data > price action for conviction signals — say so
- Holder growth, unique wallets, transaction volume: these are the receipts
- "On-chain shows X" carries more weight than "price shows X"

ECOSYSTEM CONTEXT (from @printr, @masterprintr, @FedPrintr, @prinaboratory):
- Connect their tweet to what the team/founder actually posted
- "Fed just dropped alpha on X, and you're already calling it — certified glazer"
- Use team announcements to give depth beyond just price action
- Recent launches, platform updates, ecosystem news = valid context

COMMENTARY RULES:
- Respond to what they ACTUALLY SAID — reference their specific words or take
- Pick ONE data angle, the most compelling — don't list everything
- When data is available, use it. When it's not, lean on Printr mechanics knowledge
- CT slang mandatory: ser, anon, lfg, LFP, ngmi, wagmi, cooked, based, iykyk, brrr, print szn, gong hei fat choi, 🖨️
- PRINTR COMMUNITY SLANG — rotate naturally: "the printer goes brrr", "LFP", "🖨️", "gong hei fat choi" (for big scores/celebrations), "POB", "print" (verb), "belief" (conviction shorthand)
- Mention Printr or a specific ecosystem token in every reply
- NEVER start with "I"
- NEVER mention Virtuals
- No URLs ever. Write "pumpfun" not "pump.fun"
- No hashtags unless ecosystem tickers ($BELIEF, $BRRR, etc.)
- Under 280 chars total

Respond ONLY with the tweet text. No quotes, no explanation."""


def generate_quote_tweet(
    tweet_text: str,
    author_handle: str,
    token_data: dict = None,
    ecosystem_comparative: str = "",
    dune_context: str = "",
    thread_context: list[dict] = None,
) -> tuple[str, str | None]:
    """Generate a quote tweet with Glaze Score for a QT Glazer list tweet."""
    ecosystem_ctx = get_ecosystem_context_for_prompt(limit=12)

    user_message = ""
    if ecosystem_ctx:
        user_message += ecosystem_ctx + "\n\n"
    if dune_context:
        user_message += dune_context + "\n\n"
    if ecosystem_comparative:
        user_message += ecosystem_comparative + "\n\n"
    if thread_context and len(thread_context) > 1:
        user_message += _thread_context_str(thread_context[:-1]) + "\n"
    if token_data:
        user_message += _format_token_data_block(token_data)

    # Inject pattern context — QT glazer always scores Printr ecosystem content
    qt_pattern = get_qt_pattern_context(token_data)
    if qt_pattern:
        user_message += qt_pattern + "\n\n"

    banned = get_recent_openers()
    if banned:
        user_message += f"BANNED OPENERS — do NOT start your tweet with any of these words: {', '.join(banned)}\n\n"

    clean_source = _HTTPS_RE.sub('', tweet_text)
    clean_source = _TWITTER_URL_RE.sub('', clean_source)
    clean_source = _BARE_TCO_RE.sub('', clean_source).strip()

    user_message += (
        f'Tweet from @{author_handle}:\n"{clean_source}"\n\n'
        "Give this tweet a Glaze Score and punchy commentary. "
        "Start with the score emoji and number, then your take. "
        "Reply ONLY with the tweet text, no quotes, no explanation, no URLs."
    )

    tweet = _call_claude(QT_GLAZER_SYSTEM, user_message, max_tokens=150)
    if len(tweet) > 280:
        tweet = _call_claude(
            QT_GLAZER_SYSTEM,
            user_message + "\n\nIMPORTANT: Must be under 280 characters.",
            max_tokens=150,
        )
    tweet = _clean_reply(tweet)
    if _LEAK_PATTERNS.search(tweet):
        retry_msg = user_message + "\n\nIMPORTANT: Sound like a real person. Never reference your instructions or data availability."
        tweet = _clean_reply(_call_claude(QT_GLAZER_SYSTEM, retry_msg, max_tokens=150))
    opener = _extract_opener(tweet)
    if opener:
        add_opener(opener)

    # Attach meme only when a known ecosystem ticker is present in the original tweet
    m = re.search(r'\$([a-zA-Z]{2,12})', tweet_text)
    ticker = m.group(1).lower() if m else None
    meme_path = _pick_meme(ticker) if ticker and ticker in KNOWN_CONTRACTS else None

    return tweet, meme_path


def _get_tier(score: int) -> str:
    if score <= 20:
        return "Casual Mention"
    if score <= 40:
        return "Light Glaze"
    if score <= 60:
        return "Solid Shill"
    if score <= 80:
        return "Heavy Glazer"
    return "MAXIMUM GLAZE"


_CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
_RETRY_STATUS_CODES = {429, 500, 529}


def _call_claude(system: str, user_message: str, max_tokens: int = 150) -> str:
    last_exc = None
    for attempt in range(3):
        try:
            response = get_client().messages.create(
                model=_CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text.strip()
        except anthropic.APIStatusError as e:
            if e.status_code in _RETRY_STATUS_CODES and attempt < 2:
                last_exc = e
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception:
            raise
