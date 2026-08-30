import asyncio
from datetime import date

import httpx

import config as config_module
import knowledge
import memory
import tools

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "jarvis"
CHECK_INTERVAL_SECONDS = 3600

# Defense-in-depth keyword filter, checked before ever researching a topic. Not
# a substitute for the model's own judgment, but a cheap first layer that can't
# be argued with.
BLOCKLIST_KEYWORDS = [
    "bomb", "explosive", "weapon", "gun", "kill", "suicide", "self-harm", "self harm",
    "drug synthesis", "meth", "heroin", "cocaine", "child", "csam", "hack into",
    "exploit", "malware", "virus", "ddos", "credit card number", "social security number",
]


def _is_safe_topic(topic: str) -> bool:
    lower = topic.lower()
    return not any(word in lower for word in BLOCKLIST_KEYWORDS)


def _todays_count(cfg: dict) -> int:
    today = date.today().isoformat()
    if cfg.get("autonomous_research_date") != today:
        return 0
    return cfg.get("autonomous_research_count", 0)


def _record_research(cfg: dict) -> None:
    today = date.today().isoformat()
    count = _todays_count(cfg)
    cfg["autonomous_research_date"] = today
    cfg["autonomous_research_count"] = count + 1
    config_module.save_config(cfg)


async def _ask_ollama(prompt: str) -> str:
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            OLLAMA_URL,
            json={"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "stream": False, "keep_alive": -1},
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()


async def pick_topic() -> str | None:
    """Only ever picks from things already connected to known facts - it never
    invents a topic out of nowhere. Facts with nothing worth researching yield
    NONE."""
    facts = memory.load_facts()
    if not facts:
        return None

    facts_text = "\n".join(f"- {f['text']}" for f in facts[-30:])
    prompt = (
        "Here are facts known about the user:\n" + facts_text + "\n\n"
        "Pick ONE specific, narrow topic connected to these facts that would be "
        "genuinely useful to research and remember for later - an interest, hobby, "
        "or something they've mentioned wanting to know about. Reply with just the "
        "topic in a few words, or reply exactly NONE if nothing here warrants research."
    )
    topic = await _ask_ollama(prompt)
    if not topic or topic.upper() == "NONE":
        return None
    return topic


# The fence, not a cage: autonomous research can still reach the whole open web,
# this just excludes known-dangerous neighborhoods (hacking/cracking forums,
# exploit trading, piracy, dark-web indices) from what it's willing to learn
# from on its own. The user's own web_search/fetch_webpage tool calls are
# completely unaffected - this filter only ever applies to this self-directed
# research pipeline.
RESEARCH_BLOCKED_DOMAINS = [
    ".onion", "hackforums", "breachforums", "raidforums", "nulled.to", "cracked.to",
    "cracked.io", "leakbase", "exploit.in", "xss.is", "sinister.ly", "thepiratebay",
    "1337x", "yts.mx", "fmovies", "putlocker", "rarbg", "darkweb", "deepweb",
]


def _filter_safe_results(results: list[dict]) -> list[dict]:
    safe = []
    for r in results:
        href = (r.get("href") or "").lower()
        if any(bad in href for bad in RESEARCH_BLOCKED_DOMAINS):
            print(f"[autoresearch] filtered out blocked domain: {href}", flush=True)
            continue
        safe.append(r)
    return safe


async def research_and_save(topic: str) -> None:
    raw_results = await tools.web_search_raw(topic, max_results=8)
    safe_results = _filter_safe_results(raw_results)
    if not safe_results:
        print(f"[autoresearch] no safe results left for topic, skipping: {topic}", flush=True)
        return

    lines = [f"- {r.get('title')}: {r.get('body')} ({r.get('href')})" for r in safe_results]
    search_results = "\n".join(lines)
    prompt = f"Summarize what's useful to know and remember about '{topic}', based on this:\n\n{search_results}"
    summary = await _ask_ollama(prompt)
    knowledge.save_topic(topic, summary)
    print(f"[autoresearch] researched and saved: {topic}", flush=True)


async def maybe_research_once() -> str | None:
    """Runs the full pick -> safety-check -> research -> save pipeline once, if
    the daily limit hasn't been hit. Returns the topic researched, or None if it
    skipped (disabled, limit hit, nothing to research, or unsafe topic)."""
    cfg = config_module.load_config()
    if not cfg.get("autonomous_research_enabled") or not cfg.get("ai_enabled"):
        return None

    limit = cfg.get("autonomous_research_daily_limit", 3)
    if _todays_count(cfg) >= limit:
        return None

    topic = await pick_topic()
    if topic is None:
        return None
    if knowledge.find_topic(topic) is not None:
        return None
    if not _is_safe_topic(topic):
        print(f"[autoresearch] skipped unsafe topic: {topic}", flush=True)
        return None

    await research_and_save(topic)
    _record_research(cfg)
    return topic


async def research_loop() -> None:
    while True:
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        try:
            await maybe_research_once()
        except Exception as exc:  # noqa: BLE001 - never let this loop die
            print(f"[autoresearch] error: {exc}", flush=True)
