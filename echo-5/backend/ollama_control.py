"""
Switches Ollama between "one request at a time" and "two at once" from the dashboard.

Ollama reads OLLAMA_NUM_PARALLEL only when it starts, so changing it means
restarting Ollama. With one slot, a call's reply that is still being worked out holds
everything behind it - the next caller, your own voice command - in line; with two,
the second request starts straight away (each runs a little slower while both are
active, and the second slot costs about 1 GB of VRAM).

The setting is also written to the user's environment so Ollama keeps it the next
time it starts at login, and removed again (or put back to whatever the user had) when
the switch goes off. Only processes running from Ollama's own install folder are
ever stopped.
"""
import asyncio
import ctypes
import os
import re
import shutil
import subprocess
import winreg
from ctypes import wintypes as wt
from pathlib import Path

import httpx

ENV_NAME = "OLLAMA_NUM_PARALLEL"
SLOTS_WHEN_ON = 2
LOG_FILE = Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "server.log"
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
RESTART_WAIT_SECONDS = 60

_NO_WINDOW = 0x08000000
_DETACHED = 0x00000008
_NEW_GROUP = 0x00000200

status = {"restarting": False, "error": ""}


def current_slots() -> int | None:
    """What the running Ollama actually started with, read from its own log (the newest
    "server config" line) - the truth, not just what this app last asked for."""
    found = None
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if 'msg="server config"' in line:
                    match = re.search(r"OLLAMA_NUM_PARALLEL:(\d+)", line)
                    found = int(match.group(1)) if match else None
    except OSError:
        return None
    return found


def _env_get() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, ENV_NAME)[0])
    except OSError:
        return None


def _env_set(value: str | None) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, ENV_NAME)
            except FileNotFoundError:
                pass
        else:
            winreg.SetValueEx(key, ENV_NAME, 0, winreg.REG_SZ, value)
    # Let running programs (Explorer, so anything started from it) pick the change up.
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPCWSTR, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_ulong)]
    user32.SendMessageTimeoutW(wt.HWND(0xFFFF), 0x001A, 0, "Environment", 0x2, 5000, ctypes.byref(ctypes.c_ulong()))


def persist_user_env(enabled: bool, previous: str | None) -> str | None:
    """Writes (or removes) the setting in the user's environment. Returns the value the
    user had before, to be remembered while the switch is on and restored when it goes off."""
    if enabled:
        existing = _env_get()
        remember = existing if existing not in (None, str(SLOTS_WHEN_ON)) else previous
        _env_set(str(SLOTS_WHEN_ON))
        return remember
    _env_set(previous)
    return None


def spawn_env(config: dict) -> dict:
    """The environment to start Ollama with, per the saved switch."""
    env = os.environ.copy()
    if config.get("ollama_parallel_2_enabled"):
        env[ENV_NAME] = str(SLOTS_WHEN_ON)
    else:
        saved = _env_get()
        if saved is None:
            env.pop(ENV_NAME, None)
        else:
            env[ENV_NAME] = saved
    return env


def _install_dir() -> Path | None:
    exe = shutil.which("ollama")
    return Path(exe).parent if exe else None


def _find_app() -> Path | None:
    install = _install_dir()
    candidates = [install / "ollama app.exe"] if install else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Programs" / "Ollama" / "ollama app.exe")
    return next((c for c in candidates if c.exists()), None)


def _stop_ollama() -> None:
    """Stops everything running from Ollama's own install folder (the tray app, the server
    and the model runner) and nothing else."""
    install = _install_dir()
    if install is None:
        return
    folder = str(install).rstrip("\\").replace("'", "''") + "\\"
    script = (
        f"$d = '{folder}'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d, [StringComparison]::OrdinalIgnoreCase) } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, timeout=30, creationflags=_NO_WINDOW,
    )


def _ollama_alive() -> bool:
    for image in ("ollama.exe", "ollama app.exe"):
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"],
            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW,
        ).stdout
        if image.lower() in out.lower():
            return True
    return False


async def _api_up() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            await client.get(OLLAMA_TAGS_URL)
        return True
    except httpx.HTTPError:
        return False


async def restart(slots: int, config: dict, warm=None) -> None:
    """Restarts Ollama so it serves `slots` requests at once, and waits until it's back.
    Progress and any problem are reported through `status`."""
    status.update(restarting=True, error="")
    try:
        await asyncio.to_thread(_stop_ollama)
        for _ in range(20):
            if not await asyncio.to_thread(_ollama_alive):
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Couldn't stop the running Ollama, so nothing was changed.")
        await asyncio.sleep(1.0)

        env = spawn_env(config)
        app, cli = _find_app(), shutil.which("ollama")
        flags = _NO_WINDOW | _DETACHED | _NEW_GROUP
        if app is not None:
            subprocess.Popen([str(app)], env=env, creationflags=flags, close_fds=True)
        elif cli is not None:
            subprocess.Popen([cli, "serve"], env=env, creationflags=flags, close_fds=True)
        else:
            raise RuntimeError("Couldn't find Ollama to restart it.")

        for waited in range(RESTART_WAIT_SECONDS):
            await asyncio.sleep(1)
            if await _api_up():
                break
            if waited == 25 and app is not None and cli is not None:
                # The tray app didn't bring the server up - start the server directly.
                subprocess.Popen([cli, "serve"], env=env, creationflags=flags, close_fds=True)
        else:
            raise RuntimeError(f"Ollama didn't come back within {RESTART_WAIT_SECONDS} seconds.")

        if warm is not None:
            await warm()
        for _ in range(10):
            if current_slots() == slots:
                return
            await asyncio.sleep(0.5)
        status["error"] = f"Ollama restarted but reports {current_slots()} at once, not {slots}."
    except Exception as exc:  # noqa: BLE001 - reported to the dashboard instead of raised
        status["error"] = str(exc)
    finally:
        status["restarting"] = False
