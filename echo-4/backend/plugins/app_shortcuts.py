"""
Saved shortcuts to things that aren't installed Windows apps - mainly websites
like Netflix, YouTube or Twitch - opened by name in the default browser.

Adding one is a spoken request ("add a shortcut called Crunchyroll for
crunchyroll.com") or a line in plugins_data/app_shortcuts.json. The add tool
only accepts web addresses; pointing a shortcut at a local program is done by
editing that file by hand, so a manipulated request can never register one.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import launch_utils as lu

PLUGIN_NAME = "app_shortcuts"
TOGGLE_LABEL = "Custom Shortcuts (Netflix, websites)"
CONFIG_KEY = "app_shortcuts_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"

DATA_FILE = Path(__file__).resolve().parent.parent / "plugins_data" / "app_shortcuts.json"

DEFAULT_SHORTCUTS = {
    "Netflix": "https://www.netflix.com",
    "YouTube": "https://www.youtube.com",
    "Twitch": "https://www.twitch.tv",
    "Disney Plus": "https://www.disneyplus.com",
    "Hulu": "https://www.hulu.com",
    "Prime Video": "https://www.primevideo.com",
}


def _load() -> dict[str, str]:
    if not DATA_FILE.exists():
        _save(DEFAULT_SHORTCUTS)
        return dict(DEFAULT_SHORTCUTS)
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SHORTCUTS)
    return {str(k): str(v) for k, v in data.items()}


def _save(shortcuts: dict[str, str]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(shortcuts, indent=2, ensure_ascii=False), encoding="utf-8")


def _clean_web_address(raw: str) -> str | None:
    raw = raw.strip()
    if not raw or "\\" in raw or " " in raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if parsed.scheme not in ("http", "https") or "." not in host:
        return None
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        return None
    return raw


def _as_items(shortcuts: dict[str, str]) -> list[dict]:
    return [{"name": name, "target": target} for name, target in shortcuts.items()]


async def open_shortcut(args: dict) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "Which shortcut should I open?"
    shortcuts = _load()
    ranked = lu.rank(name, _as_items(shortcuts))
    if not ranked or ranked[0][0] < lu.MATCH_THRESHOLD:
        near = lu.suggestions(ranked) if ranked else "none saved that are close"
        return f"I don't have a shortcut called '{name}'. Closest: {near}. I can add it if you give me the web address."
    best = ranked[0][1]
    target = best["target"]
    if not target.lower().startswith(("http://", "https://")) and not os.path.exists(target):
        return f"The shortcut '{best['name']}' points at {target}, which doesn't exist any more."
    os.startfile(target)
    return f"Opening {best['name']}."


async def add_shortcut(args: dict) -> str:
    name = (args.get("name") or "").strip()
    address = _clean_web_address(args.get("address") or "")
    if not name:
        return "What should the shortcut be called?"
    if address is None:
        return "I need a valid web address for that shortcut, like netflix.com."
    shortcuts = _load()
    shortcuts[name] = address
    _save(shortcuts)
    return f"Saved a shortcut called {name} for {address}."


async def remove_shortcut(args: dict) -> str:
    name = (args.get("name") or "").strip()
    shortcuts = _load()
    ranked = lu.rank(name, _as_items(shortcuts))
    if not name or not ranked or ranked[0][0] < 0.8:
        return f"I don't have a shortcut that clearly matches '{name}'."
    removed = ranked[0][1]["name"]
    del shortcuts[removed]
    _save(shortcuts)
    return f"Removed the {removed} shortcut."


async def list_shortcuts(args: dict) -> str:
    shortcuts = _load()
    if not shortcuts:
        return "No shortcuts saved."
    return "Saved shortcuts: " + ", ".join(shortcuts) + "."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "open_shortcut",
            "description": (
                "Opens a saved shortcut by name - a website or service such as Netflix, YouTube, Twitch, "
                "Hulu - in the default browser. Use this for streaming sites and anything that is a website "
                "rather than an installed app."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_shortcut",
            "description": "Saves a new shortcut: a name sir will say, and the website address it opens.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "What sir will call it, e.g. Crunchyroll."},
                    "address": {"type": "string", "description": "The website address, e.g. crunchyroll.com."},
                },
                "required": ["name", "address"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_shortcut",
            "description": "Deletes a saved shortcut by name.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_shortcuts",
            "description": "Lists the saved shortcut names.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

DISPATCH = {
    "open_shortcut": open_shortcut,
    "add_shortcut": add_shortcut,
    "remove_shortcut": remove_shortcut,
    "list_shortcuts": list_shortcuts,
}
