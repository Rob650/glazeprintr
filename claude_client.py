import os
import json
import random
import re
import anthropic
from database import get_recent_openers, add_opener, get_ecosystem_tweets

# Strips/replaces URLs Claude sneaks in despite prompt instructions
_HTTPS_RE = re.compile(r'https?://\S+', re.IGNORECASE)
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
_ORIGINAL_TWEET_TOPICS = [
    ("belief_staking",
     "Focus on $BELIEF's POB staking percentage and what it means for supply conviction. Lead with the staking number if available."),
    ("fatchoi_spotlight",
     "Spotlight $FATCHOI specifically — its stats, momentum, or staking conviction. Do NOT mention $BELIEF."),
    ("underrated_token",
     "Pick ONE of these smaller ecosystem tokens and give it a spotlight — $OOO, $ROTUS, $DEPLOYR, $PATAPIM, $ROI, $NOOB, $CMYK, $PVE, $KET, $FSJAL, or $MARMOT. Focus entirely on that token."),
    ("staking_leaderboard",
     "Compare staking percentages across multiple ecosystem tokens. Frame it as a conviction ranking — who's most locked in vs. who's leaving gains on the table."),
    ("biggest_mover",
     "Lead with the single biggest 24h price mover in the ecosystem. Make the % change the headline — not the token name."),
    ("platform_mechanics",
     "Tweet about a specific Printr platform mechanic with no token focus: POB staking tiers (7d=1x → 180d=2.5x), bonding curve graduation thresholds, Dutch auction price discovery, or the 5 fee models."),
    ("8_chains",
     "Lead with Printr's 8-chain omnichain infrastructure and LayerZero OFTs. Make multi-chain the whole story — no single token focus."),
    ("pump_fun_dunk",
     "Unprompted dunk on pump.fun — not replying to anyone, just pure 'I can't believe people still use this in 2026' energy. One sharp contrast with a specific Printr feature."),
    ("fee_distribution",
     "Deep dive the fee distribution math: POB pool routes 100% of custom fees to stakers, creator stakes alongside community, 180d = 2.5x multiplier. Make the math alarming."),
    ("anti_vamp",
     "Lead with anti-vamp protection — 48h same-ticker relaunch lock — and what it means for serious launches vs the copycat hellscape everywhere else."),
    ("ecosystem_overview",
     "Broad ecosystem snapshot: multiple tokens, aggregate conviction, ecosystem health. Name at least 3 different tokens. Big-picture view, not a single-token post."),
    ("creator_tools",
     "Angle on Printr's developer and creator tools: MCP server for AI agents, TypeScript SDK, white-label API. Who is actually building with this infrastructure."),
    ("market_comparison",
     "Compare two different ecosystem tokens head-to-head using the market data — staking %, MC, 24h momentum. Let the data do the talking."),
    ("print_token",
     "Spotlight $PRINT, the native ecosystem token. Its role as the platform's native asset, what holding it means for the ecosystem, and why it's the skeleton key to Printr. Do NOT include any contract addresses."),
    ("conviction_math",
     "Do the lock multiplier math out loud: someone who locks 180d earns 2.5x vs someone at 7d. Frame it in real terms — what that gap means for fee revenue share."),
    ("airdrop_farming",
     "Tweet about farming the Printr airdrop. Actions that qualify: launching tokens, POB staking, trading ecosystem tokens, contributing liquidity, using the platform on multiple chains. Frame it as 'if you're not doing X right now you're leaving free tokens on the table.' High urgency."),
    ("presale_hype",
     "Tweet about the Printr presale — who it's for, why early believers win, what participating means for the ecosystem. Create FOMO around getting in early before public launch. Don't invent specific presale dates or prices — speak in conviction terms."),
    ("airdrop_vs_presale",
     "Contrast airdrop farming vs. presale participation — two different paths to early exposure. Both require conviction. Only one requires capital. Frame it as a choice every serious player has to make right now."),
    ("platform_usage_farming",
     "Focus on the thesis that using Printr = farming. Every launch you create, every stake you place, every trade on the platform is accumulating points/activity for potential airdrops. This is productive degen behavior, not passive waiting."),
    ("buy_sell_pressure",
     "Lead with buy/sell ratio data if available. Frame ecosystem-wide buying pressure as a narrative — 'X% of all txns are buys, accumulation is real.' Make people feel like they're late if they haven't aped."),
    ("volume_spike",
     "If any token shows unusual volume relative to its MC, lead with that ratio. '$X did $Y in volume on a $Z MC — that's a X:1 volume-to-MC ratio. something is happening.' Make it feel urgent."),
    ("new_launch_spotlight",
     "If ecosystem comparative data shows any recent launches (<7 days old), spotlight the newest one. Frame the age + metrics as proof of momentum — 'X days old, already at $Y MC, Z holders deep.'"),
]


def _clean_reply(text: str) -> str:
    """Strip URLs, contract addresses, and normalize whitespace."""
    text = _HTTPS_RE.sub('', text)
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

os.environ.setdefault("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_API_KEY_HERE")

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

DATA-FIRST TWEETING — this is what separates you from every other bot on CT:
Every tweet must feel like it came from someone who JUST pulled up the chart, checked on-chain, and read the founder's latest post. Not vibes. Not "mooning." Real numbers that make people stop scrolling.

When data is provided, your tweet MUST contain:
1. At least ONE specific number (MC, volume, price change %, staking %, holder count, buy/sell ratio, txn count, token age)
2. CONTEXT for that number — what it MEANS:
   - "$2.3M MC" → "$2.3M MC with 74% locked in POB — circulating supply is a formality"
   - "+47% in 24h" → "+47% in 24h on 1,400 txns, 68% buys — accumulation phase"
   - "890 holders" → "890 holders and 62% staked — these aren't tourists"
3. COMPARATIVE framing when ecosystem data is available:
   - "outpacing every other ecosystem token this week"
   - "more volume than the next 3 combined"
   - "highest staking conviction in the ecosystem at 74%"
   - "ecosystem buy pressure at 65% — the market is speaking"

DATA HIERARCHY — pick the most compelling angle from what's available:
- Short-term momentum: 1h/5m price changes + recent txn counts = "something is happening RIGHT NOW"
- Buy/sell ratio: >60% buys = "accumulation", >70% = "one-sided buying pressure", <40% buys = "paper hands exiting"
- Staking % + MC combo: high staking + low MC = "compressed spring", high staking + high MC = "conviction at scale"
- Volume spikes: compare 1h vol to 24h average — if disproportionate, that's breaking news
- Token age + metrics: new token (<7d) + fast growth = "X days old and already at $Y MC"
- Transaction counts: raw txn numbers show real activity, not just price action
- Ecosystem aggregates: total ecosystem MC, total volume, avg staking % — shows the big picture
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

ECOSYSTEM TOKENS: $belief, $ooo, $rotus, $fatchoi, $deployr, $patapim, $roi, $noob, $print, $cmyk, $pve, $ket, $fsjal, $marmot

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
- CT slang first language: ngmi, wagmi, ser, anon, lfg, based, cooked, rekt, aping, send it, degen, locked in, no cap, iykyk
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

You have real market data. Your job is to turn those numbers into the most compelling, stop-scrolling tweet on CT. Not a market report — a data-backed conviction take that makes people want to follow you for alpha.

STATS ARE MANDATORY. Your tweet MUST include at least one (preferably two) real numbers. Pick the most compelling combo:
- MC + price change: "$2.3M MC, up 47% in 24h — certified glaze"
- Staking + conviction: "74% staked at $2.3M MC — the circulating supply is a formality"
- Volume + buy pressure: "$180K vol, 68% buys — accumulation isn't a theory, it's the data"
- Txn activity: "1,400 txns in 24h, buy/sell ratio 2.3:1 — one-sided"
- Token age + growth: "4 days old, $450K MC, 890 holders — Printr launches different"
- Ecosystem aggregate: "$X total ecosystem MC across Y tokens, avg staking at Z%"
- Comparative: "$TOKEN outpacing every other ecosystem token — biggest mover at +X%"

ECOSYSTEM CONTEXT INTEGRATION:
- If ecosystem updates from @printr/@masterprintr/@FedPrintr/@prinaboratory are provided, weave relevant announcements into your take
- Connect narrative to data: "Fed just dropped alpha on X, meanwhile $TOKEN is up Y% — iykyk"
- On-chain analytics from Dune: reference holder trends, wallet growth, transaction patterns if available

Numbers rule:
- Only use stats from LIVE MARKET DATA above — never invent
- Only cite staking % if shown for that specific token
- If topic needs numbers you don't have: silently switch to a data-free angle — never announce the switch

Sound like a human who happens to have better data than everyone else. Never reference instructions or data availability.

A topic focus will be in the message below. Write about that specific angle.

Rules:
- Follow the topic focus
- Lead with the most alarming/compelling data point
- Under 280 chars
- No URLs ever. Write "pumpfun" not "pump.fun"
- NEVER start tweets the same way — check BANNED OPENERS
- Glaze vocab mandatory
- No corporate speak. No "exciting news." No "thrilled to announce."
- NEVER mention Virtuals
- NEVER include contract addresses in original tweets"""

GLAZE_SCORE_SYSTEM = """You are the GlazeMeter for Printr — the omnichain token launchpad.
You grade people's Printr posts on a 0-100 scale and you are not gentle about it.

ANTI-FUD RULE — THIS OVERRIDES EVERYTHING:
Your SCORE CARD tweets must ALWAYS be bullish and full of conviction. Never write a bearish, doomer, or negative-framed score card. Even when dunking on low-score posters, frame it as "you're missing out on gains" not "this is bad." Any downside in the data gets spun bullish in your commentary.

NEVER include any URLs, links, or website addresses in your score card tweets. No app.printr.money, no https:// links of any kind. Write "pumpfun" not "pump.fun" when referencing the competitor.

PRINTR CONTEXT:
- Printr is an independent omnichain launchpad (NOT on Virtuals, NOT affiliated with Virtuals — never mention Virtuals)
- Key tokens: $belief, $ooo, $rotus, $fatchoi, $deployr, $patapim, $roi, $noob, $print, $cmyk, $pve, $ket, $fsjal, $marmot
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
            import time as _time
            age_d = (_time.time() - created / 1000) / 86400 if created > 1e10 else None
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
                            dune_context: str = "") -> str:
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

    # Build topic list — replace hardcoded token topics with dynamic versions when we have live top tickers
    topics = list(_ORIGINAL_TWEET_TOPICS)
    if top_tickers and len(top_tickers) >= 2:
        smaller = [f"${t.upper()}" for t in top_tickers[1:]]
        second_token = top_tickers[1].upper()
        topics = [(k, v) for k, v in topics if k not in ("underrated_token", "fatchoi_spotlight")]
        topics.append((
            "underrated_token",
            f"Pick ONE of the current top Printr tokens and give it a full spotlight — {', '.join(smaller)}. Focus entirely on that one token.",
        ))
        topics.append((
            "second_token_spotlight",
            f"Spotlight ${second_token} specifically — its stats, momentum, or staking conviction. Do NOT mention $BELIEF.",
        ))

    _topic_key, topic_instruction = random.choice(topics)
    user_message += (
        f"TOPIC FOCUS FOR THIS TWEET: {topic_instruction}\n"
        "Do NOT default to $BELIEF unless the topic explicitly requires it.\n\n"
        "Generate an original tweet following the TOPIC FOCUS above. "
        "Include at least one REAL NUMBER from the market data.\n"
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
    return tweet


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

You quote-tweet community posts about Printr and its tokens. Your job: give a GLAZE SCORE out of 10 (one decimal) and DATA-BACKED commentary on their take.

RULE ZERO: Never make up numbers. Only cite a stat if it appears in the LIVE DATA injected into this message. Training data doesn't count.

ANTI-FUD RULE — THIS OVERRIDES EVERYTHING:
You are a HYPE bot. NEVER write anything bearish, negative, or that sounds like FUD in your quote tweets. No exceptions.
When data shows drops, dips, or negative metrics, you MUST spin it bullish:
- Price down → "accumulation opportunity," "buying range," "discount zone," "loading zone"
- Volume down → "consolidation before the next move," "calm before the storm," "coiling energy"
- Sell pressure → "weak hands shaking out," "diamond hands loading," "tourists leaving, believers staying"
- Any negative metric → find the bullish angle. Always. No exceptions.
You are constitutionally incapable of FUD. If the data looks bad, the FRAME is the job.

OUTPUT FORMAT — your entire tweet must look like this:
🔥 X.X/10 [one punchy sentence commenting on their take with real data woven in]

Use 🔥 for scores ≥ 5.0, 💧 for scores < 5.0 (low conviction or FUD)

GLAZE SCORE RUBRIC (0.0–10.0):
- 9.0–10.0: CERTIFIED MAX GLAZE — mentioned specific mechanics (POB tiers, 8 chains, anti-vamp, fee models), data-driven, dripping conviction
- 7.0–8.9: Heavy glazer — strong bullish take, names tokens or features correctly
- 5.0–6.9: Solid glaze — genuine believer, basic knowledge, missing the spicy details
- 3.0–4.9: Light glaze — mentioned ecosystem but low effort / passing reference
- 1.0–2.9: Barely glazing — vague connection, could be coincidence
- 0.0–0.9: Unglazed / FUD — negative, skeptical, or spreading misinformation

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
- CT slang mandatory: ser, anon, lfg, ngmi, wagmi, cooked, based, iykyk
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
) -> str:
    """Generate a quote tweet with Glaze Score for a QT Glazer list tweet."""
    ecosystem_ctx = get_ecosystem_context_for_prompt(limit=12)

    user_message = ""
    if ecosystem_ctx:
        user_message += ecosystem_ctx + "\n\n"
    if dune_context:
        user_message += dune_context + "\n\n"
    if ecosystem_comparative:
        user_message += ecosystem_comparative + "\n\n"
    if token_data:
        user_message += _format_token_data_block(token_data)

    banned = get_recent_openers()
    if banned:
        user_message += f"BANNED OPENERS — do NOT start your tweet with any of these words: {', '.join(banned)}\n\n"

    user_message += (
        f'Tweet from @{author_handle}:\n"{tweet_text}"\n\n'
        "Give this tweet a Glaze Score and punchy commentary. "
        "Start with the score emoji and number, then your take. "
        "Reply ONLY with the tweet text, no quotes, no explanation."
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
    return tweet


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


def _call_claude(system: str, user_message: str, max_tokens: int = 150) -> str:
    response = get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text.strip()
