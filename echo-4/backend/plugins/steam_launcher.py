"""
Launches installed Steam games by name. Reads Steam's own installed-game
manifests across every library folder, and - because a steam:// launch fired
at a Steam that isn't running (or hasn't finished logging in) is silently
dropped - starts Steam and waits until it's actually signed in first.
"""
import asyncio
import os
import re
import subprocess
import winreg
from pathlib import Path

import launch_utils as lu

PLUGIN_NAME = "steam_launcher"
TOGGLE_LABEL = "Launch Steam Games"
CONFIG_KEY = "steam_launcher_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"

STEAM_START_TIMEOUT_SECONDS = 90
_NOT_A_GAME = re.compile(r"steamworks|redistributable|proton|steam linux runtime|steamvr", re.I)


def _steam_key():
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")


def _steam_path() -> Path | None:
    try:
        with _steam_key() as key:
            return Path(winreg.QueryValueEx(key, "SteamPath")[0])
    except OSError:
        return None


def _library_roots() -> list[Path]:
    steam_path = _steam_path()
    if steam_path is None:
        return []
    roots = [steam_path]
    vdf = steam_path / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        text = vdf.read_text(encoding="utf-8", errors="replace")
        for raw in re.findall(r'"path"\s+"([^"]+)"', text):
            path = Path(raw.replace("\\\\", "\\"))
            if path not in roots:
                roots.append(path)
    return [r for r in roots if (r / "steamapps").is_dir()]


def _list_games() -> list[dict]:
    games = []
    for root in _library_roots():
        for manifest in (root / "steamapps").glob("appmanifest_*.acf"):
            text = manifest.read_text(encoding="utf-8", errors="replace")
            appid = re.search(r'"appid"\s+"(\d+)"', text)
            name = re.search(r'"name"\s+"([^"]*)"', text)
            if appid and name and not _NOT_A_GAME.search(name.group(1)):
                games.append({"name": name.group(1), "appid": appid.group(1)})
    return games


def _steam_ready() -> bool:
    """Running AND signed in - the client ignores launch requests before that."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess") as key:
            signed_in = bool(winreg.QueryValueEx(key, "ActiveUser")[0])
    except OSError:
        return False
    return signed_in and lu.is_process_running("steam.exe")


async def _ensure_steam_running() -> bool:
    if await asyncio.to_thread(_steam_ready):
        return True
    try:
        with _steam_key() as key:
            steam_exe = winreg.QueryValueEx(key, "SteamExe")[0]
    except OSError:
        return False
    if not await asyncio.to_thread(lu.is_process_running, "steam.exe"):
        subprocess.Popen([steam_exe], creationflags=lu.CREATE_NO_WINDOW)
    return await lu.wait_until(_steam_ready, STEAM_START_TIMEOUT_SECONDS, interval=2.0)


async def launch_steam_game(args: dict) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "Which game should I launch?"
    games = await asyncio.to_thread(_list_games)
    if not games:
        return "I couldn't find any installed Steam games."
    ranked = lu.rank(name, games)
    if not ranked or ranked[0][0] < lu.MATCH_THRESHOLD:
        near = lu.suggestions(ranked) if ranked else "nothing close"
        return f"I couldn't find an installed Steam game called '{name}'. Closest: {near}."
    best = ranked[0][1]
    blocked = lu.launch_blocked_reason("steam:" + best["appid"])
    if blocked:
        return f"{best['name']}: {blocked}."

    if not await _ensure_steam_running():
        return (
            f"Steam isn't running and didn't finish starting within {STEAM_START_TIMEOUT_SECONDS} seconds, "
            f"so I haven't launched {best['name']}. It may be waiting on a login or an update."
        )
    os.startfile(f"steam://rungameid/{best['appid']}")
    lu.note_launch("steam:" + best["appid"])
    return f"Launching {best['name']} through Steam."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "launch_steam_game",
            "description": (
                "Launches an installed Steam game by name. Starts Steam first and waits for it if it isn't "
                "already running."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "The game's name as sir said it."}},
                "required": ["name"],
            },
        },
    },
]

DISPATCH = {"launch_steam_game": lu.spoken(launch_steam_game)}
