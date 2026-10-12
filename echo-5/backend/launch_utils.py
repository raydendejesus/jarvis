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
