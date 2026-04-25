import logging
import re
from database import (
    store_tweet_summary, get_recent_tweet_summaries,
    store_project_data, get_latest_project_data,
    store_narrative, get_recent_narratives,
)

logger = logging.getLogger(__name__)

ECOSYSTEM_TOKENS = [
    "belief", "ooo", "rotus", "fatchoi", "deployr", "patapim",
    "roi", "noob", "print", "cmyk", "pve", "ket", "fsjal", "marmot",
]

NARRATIVE_PATTERNS = [
    (r"\$\w+\s+(?:is\s+)?(?:pumping|mooning|ripping|flying|running|up)", "pump momentum"),
    (r"(?:new\s+ath|all[- ]time\s+high)", "ATH vibes"),
    (r"(?:printr|launchpad|chain.abstract)", "printr ecosystem buzz"),
    (r"pump\.?fun", "pumpfun competition"),
    (r"(?:staking|stake|proof.?of.?belief)", "staking narrative"),
    (r"(?:multi.?chain|cross.?chain)", "multichain narrative"),
    (r"(?:degen|aping\s+in|ape\s+in)", "degen sentiment"),
    (r"(?:scam|rug|dead|fud|bearish)", "fud/bearish sentiment"),
]


def remember_tweet(tweet_id: str, author_handle: str, text: str):
    store_tweet_summary(tweet_id, author_handle, text[:300])
    _extract_narratives(text)


def _extract_narratives(text: str):
    lower = text.lower()
    for pattern, narrative in NARRATIVE_PATTERNS:
        if re.search(pattern, lower):
            store_narrative(narrative, strength=1)
    for token in ECOSYSTEM_TOKENS:
        if f"${token}" in lower:
            store_narrative(f"${token} trending", strength=1)


def remember_project_data(project_name: str, contract_address: str, staking_pct: float = None, **kwargs):
    store_project_data(project_name, contract_address, staking_pct=staking_pct, **kwargs)


def get_memory_context(include_tweets: bool = True, include_projects: bool = True,
                       include_narratives: bool = True) -> str:
    parts = []

    if include_tweets:
        summaries = get_recent_tweet_summaries(limit=15)
        if summaries:
            lines = [f"@{s['author_handle']}: {s['summary'][:120]}" for s in summaries]
            parts.append("RECENTLY SEEN TWEETS:\n" + "\n".join(lines))

    if include_projects:
        projects = get_latest_project_data(limit=15)
        if projects:
            proj_lines = []
            for p in projects:
                mc = p.get("market_cap") or 0
                chg = p.get("price_change_24h")
                vol = p.get("volume") or 0
                staking_pct = p.get("staking_pct")
                line = f"${p['project_name'].upper()}"
                if mc:
                    line += f" MC=${mc / 1_000_000:.2f}M" if mc >= 1_000_000 else f" MC=${mc:,.0f}"
                if chg is not None:
                    line += f" ({chg:+.1f}%24h)"
                if vol:
                    line += f" Vol=${vol / 1000:.0f}k" if vol >= 1000 else f" Vol=${vol:.0f}"
                if staking_pct is not None:
                    line += f" POBstaked={staking_pct:.0f}%"
                proj_lines.append(line)
            parts.append("LATEST MARKET DATA:\n" + "\n".join(proj_lines))

    if include_narratives:
        narratives = get_recent_narratives(limit=8)
        if narratives:
            narr_lines = [f"{n['narrative']} (x{n['total_strength']})" for n in narratives]
            parts.append("TRENDING NARRATIVES (24h): " + ", ".join(narr_lines))

    return "\n\n".join(parts)
