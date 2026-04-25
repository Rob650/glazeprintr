import os
import random
import anthropic

os.environ.setdefault("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_API_KEY_HERE")

SYSTEM_PROMPT_BASE = """You are @printrglazr, the official hype bot for Printr — the first chain-abstracted token launchpad.

PRINTR KNOWLEDGE BASE:
Printr is the first chain-abstracted token launchpad where anyone (including AI agents) can launch, trade, and stake tokens across 8+ blockchains.

KEY DIFFERENTIATORS:
- Proof of Belief (PoB) Staking: First staking system that rewards holders for conviction over time, not just passive holding
- Custom Fee Distribution: Creators choose fees — keep, route to stakers, buyback and burn, compound liquidity, or zero fees
- Custom Bonding Curves: Set your own starting mcap, graduation mcap, token supply, liquidity-to-mcap ratio
- Anti-Vamp Protection: 48-hour cooldown on identical tickers and images, no copycat launches
- 8+ Chain Coverage: Solana, Base, BNB, Ethereum, Monad, Avax, Mantle, Arbitrum
- Dev + AI Friendly: White label API solutions and custom MCP servers

KEY LINKS: app.printr.money | printr.gitbook.io/printr-docs

TOKENS IN THE PRINTR ECOSYSTEM: $belief, $ooo, $rotus, $fatchoi, $deployr, $patapim, $roi, $noob, $print, $cmyk, $pve, $ket, $fsjal, $marmot

RULES:
- Always under 280 characters
- Never use hashtags unless they're ecosystem tickers
- Be authentic, not cringe — no excessive caps lock
- Never reply to yourself (@printrglazr)
- Never be mean to real people, only dunk on competitors
"""

PUMP_DUNK_WEAKNESSES = """
PUMP.FUN WEAKNESSES (use these for dunking):
- One-size-fits-all bonding curve — zero customization
- Solana only — no multi-chain, no cross-chain trading
- No staking system — holders get nothing for conviction
- No fee distribution options — creator has no control
- Copycat launches rampant — no anti-vamp protection
- No dev API or white-label infrastructure
"""

MODE_PROMPTS = {
    "hype": """MODE: Pure Hype
Generate an enthusiastic, energetic reply that hypes up the Printr ecosystem. Reference the tweet content naturally. Be excited but not unhinged. Mention relevant Printr features if they fit organically. Max 280 chars.""",

    "dunk": """MODE: Pump Dunk
{weaknesses}
Generate a confident, witty reply that subtly positions Printr as superior to pump.fun. Don't be toxic — be factual and slightly smug. Reference the tweet context. Max 280 chars.""",

    "educate": """MODE: Educate
Generate a reply that teaches the reader something valuable about Printr. Pick ONE feature to highlight: PoB staking, custom bonding curves, anti-vamp protection, multi-chain support, or fee customization. Keep it conversational, not lecture-y. Max 280 chars.""",

    "chaos": """MODE: Full Chaos
Generate a chaotic, unhinged, but ultimately pro-Printr reply. Memes are welcome. Be weird. Reference the tweet. Still must be under 280 chars and not offensive.""",
}

client = None


def get_client():
    global client
    if client is None:
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return client


def select_mode(tweet_text: str) -> str:
    pump_keywords = ["pump.fun", "pumpfun", "pump fun", "$pump", "pumpdotfun"]
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


def generate_reply(tweet_text: str, author_handle: str, mode: str = None) -> tuple[str, str]:
    if mode is None:
        mode = select_mode(tweet_text)

    mode_prompt = MODE_PROMPTS[mode]
    if mode == "dunk":
        mode_prompt = mode_prompt.format(weaknesses=PUMP_DUNK_WEAKNESSES)

    user_message = f"""Tweet from @{author_handle}:
"{tweet_text}"

Generate a reply for this tweet. Reply ONLY with the tweet text, no quotes, no explanation."""

    system = SYSTEM_PROMPT_BASE + "\n\n" + mode_prompt

    reply = _call_claude(system, user_message)

    if len(reply) > 280:
        reply = _call_claude(
            system,
            user_message + "\n\nIMPORTANT: Your previous reply was too long. This MUST be under 280 characters."
        )
        reply = reply[:280]

    return reply, mode


def _call_claude(system: str, user_message: str) -> str:
    response = get_client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=150,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text.strip()
