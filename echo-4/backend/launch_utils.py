"""
Shared helpers for the launcher-style plugins (apps, Steam, media keys, Spotify):
fuzzy name matching and "is this program actually running yet" checks, so a
tool can start what it needs and wait for it instead of firing a command at
something that isn't there.
"""
import asyncio
import re
import subprocess
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Callable

CREATE_NO_WINDOW = 0x08000000
MATCH_THRESHOLD = 0.6


def norm(text: str) -> str:
    text = re.sub(r"[™®©]", "", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def score(query: str, candidate: str) -> float:
    q, c = norm(query), norm(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if len(q) >= 3 and (q in c or c in q):
        return 0.8 + 0.15 * (min(len(q), len(c)) / max(len(q), len(c)))
    q_words, c_words = set(q.split()), set(c.split())
    if q_words and q_words <= c_words:
        return 0.85
    return SequenceMatcher(None, q, c).ratio()


def rank(query: str, items: list[dict], key: str = "name") -> list[tuple[float, dict]]:
    scored = [(score(query, item[key]), item) for item in items]
    return sorted((s for s in scored if s[0] > 0), key=lambda s: s[0], reverse=True)


def suggestions(ranked: list[tuple[float, dict]], key: str = "name") -> str:
    return ", ".join(item[key] for _, item in ranked[:3])


# --- launch limiter ----------------------------------------------------------------
# A looping model (or a repeated voice command) must never be able to open the same
# thing dozens of times: the same target isn't opened again within a short window,
# and only a handful of launches are allowed per minute overall.
LAUNCH_COOLDOWN_SECONDS = 20
MAX_LAUNCHES_PER_MINUTE = 6
_recent_launches: dict[str, float] = {}


def launch_blocked_reason(key: str) -> str | None:
    """None when it's fine to launch; otherwise what to say instead of launching."""
    now = time.monotonic()
    last = _recent_launches.get(key)
    if last is not None and now - last < LAUNCH_COOLDOWN_SECONDS:
        return "that was opened just a moment ago, so I haven't opened another one"
    if sum(1 for t in _recent_launches.values() if now - t < 60) >= MAX_LAUNCHES_PER_MINUTE:
        return "I've already opened several things in the last minute, so I'm holding off on opening more"
    return None


def note_launch(key: str) -> None:
    now = time.monotonic()
    _recent_launches[key] = now
    for stale in [k for k, t in _recent_launches.items() if now - t > 300]:
        del _recent_launches[stale]


class SpokenResult(str):
    """A tool result that is already a complete, speakable answer for sir. When every tool
    in a round returns one, the server speaks it as the reply instead of asking the model
    to put it into words again - which is faster, and the model can't misread it (a
    "not connected" message was being taken as an instruction to Echo itself)."""


def say(text: str) -> SpokenResult:
    return SpokenResult(text)


def spoken(handler):
    """Wraps a tool handler so everything it returns is spoken as-is."""

    async def wrapped(args: dict):
        result = await handler(args)
        return result if isinstance(result, SpokenResult) else SpokenResult(result)

    wrapped.__name__ = getattr(handler, "__name__", "wrapped")
    return wrapped


def is_process_running(image_name: str) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
        capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW,
    ).stdout
    return image_name.lower() in out.lower()


async def wait_until(check: Callable[[], bool], timeout: float, interval: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await asyncio.to_thread(check):
            return True
        await asyncio.sleep(interval)
    return False
