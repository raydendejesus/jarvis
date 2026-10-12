"""
Opens installed apps by name.

Deliberately NOT a general "run a command" tool: apps are only ever launched
from what Windows itself lists as installed (Get-StartApps), so a misheard or
manipulated request can't turn into arbitrary program execution. Like every
plugin, it is offered only on the dashboard/native-listener path, never on
phone calls. Steam games, media keys and Spotify have their own toggles
(steam_launcher, media_keys, spotify_control).
"""
import asyncio
import json
import re
import subprocess
import time

import launch_utils as lu

PLUGIN_NAME = "app_launcher"
TOGGLE_LABEL = "Open Apps"
CONFIG_KEY = "app_launcher_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"

APPS_CACHE_SECONDS = 300
_NOT_AN_APP = re.compile(r"uninstall|readme|release notes|license|documentation|\bhelp\b|website", re.I)

_apps_cache: tuple[float, list[dict]] = (0.0, [])


def _list_start_apps() -> list[dict]:
    global _apps_cache
    cached_at, cached = _apps_cache
    if cached and time.time() - cached_at < APPS_CACHE_SECONDS:
        return cached
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=30, creationflags=lu.CREATE_NO_WINDOW,
    )
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return cached
    if isinstance(data, dict):
        data = [data]
    apps = [
        {"name": item["Name"], "app_id": item["AppID"]}
        for item in data
        if item.get("Name") and item.get("AppID") and not _NOT_AN_APP.search(item["Name"])
    ]
    _apps_cache = (time.time(), apps)
    return apps


async def open_app(args: dict) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "Which app should I open?"
    apps = await asyncio.to_thread(_list_start_apps)
    if not apps:
        return "I couldn't read the list of installed apps from Windows."
    ranked = lu.rank(name, apps)
    if not ranked or ranked[0][0] < lu.MATCH_THRESHOLD:
        near = lu.suggestions(ranked) if ranked else "nothing close"
        return (
            f"I couldn't find an installed app called '{name}'. Closest: {near}. If it's a website or a "
            "streaming service, try open_shortcut instead."
        )
    best = ranked[0][1]
    blocked = lu.launch_blocked_reason(best["app_id"])
    if blocked:
        return lu.say(f"{best['name']}: {blocked}.")
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{best['app_id']}"], creationflags=lu.CREATE_NO_WINDOW)
    lu.note_launch(best["app_id"])
    return lu.say(f"Opening {best['name']}.")


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": (
                "Opens an installed Windows application by name (browser, Discord, Notepad, a launcher, etc.). "
                "Only apps Windows lists as installed can be opened. Not for Steam games (use launch_steam_game) "
                "and not for starting a song or playlist (use the Spotify tools)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "The app's name as sir said it."}},
                "required": ["name"],
            },
        },
    },
]

DISPATCH = {"open_app": open_app}
