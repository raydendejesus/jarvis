"""
Shared lazy-launch/poll/shutdown logic for local ComfyUI (O:\\ComfyUI), used by
every plugin that generates something through it (image_generator.py,
model_3d_generator.py). ComfyUI is a real GPU process that holds VRAM the
whole time it's up, so it's only ever launched on demand and torn down
completely once no plugin still needs it - never kept running in the
background just in case.
"""
import asyncio
import subprocess
import time
import uuid
from pathlib import Path

import httpx

COMFYUI_DIR = Path(r"O:\ComfyUI\ComfyUI_windows_portable")
COMFYUI_LAUNCH_BAT = COMFYUI_DIR / "run_nvidia_gpu.bat"
COMFYUI_PORT = 8188
COMFYUI_URL = f"http://127.0.0.1:{COMFYUI_PORT}"

INPUT_DIR = COMFYUI_DIR / "ComfyUI" / "input"

STARTUP_TIMEOUT_SECONDS = 180
GENERATION_TIMEOUT_SECONDS = 180


async def is_running() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{COMFYUI_URL}/system_stats")
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def ensure_running(log_prefix: str) -> bool:
    if await is_running():
        print(f"[{log_prefix}] ComfyUI already running", flush=True)
        return True
    if not COMFYUI_LAUNCH_BAT.exists():
        print(f"[{log_prefix}] launch script not found at {COMFYUI_LAUNCH_BAT}", flush=True)
        return False
    print(f"[{log_prefix}] ComfyUI not running - launching it now, this can take a while cold", flush=True)
    subprocess.Popen(
        ["cmd", "/c", str(COMFYUI_LAUNCH_BAT)],
        cwd=str(COMFYUI_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        if await is_running():
            print(f"[{log_prefix}] ComfyUI is up", flush=True)
            return True
    print(f"[{log_prefix}] ComfyUI still not reachable after {STARTUP_TIMEOUT_SECONDS}s, giving up", flush=True)
    return False


async def fetch_reference_image(query: str, log_prefix: str) -> str | None:
    """Downloads the first usable image result straight into ComfyUI's own
    input/ folder (where a LoadImage node reads by filename) - honestly
    returns None on any failure rather than pretending a reference was used."""
    try:
        from ddgs import DDGS
        results = await asyncio.to_thread(lambda: list(DDGS().images(query, max_results=5)))
    except Exception:  # noqa: BLE001
        return None

    for result in results:
        url = result.get("image")
        if not url:
            continue
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
            if not resp.headers.get("content-type", "").startswith("image/"):
                continue
            filename = f"ref_{uuid.uuid4().hex}.jpg"
            INPUT_DIR.mkdir(parents=True, exist_ok=True)
            (INPUT_DIR / filename).write_bytes(resp.content)
            return filename
        except Exception:  # noqa: BLE001
            continue
    print(f"[{log_prefix}] no usable reference photo found for {query!r}", flush=True)
    return None


async def submit_workflow(workflow: dict) -> str:
    """Submits a workflow, returns the prompt_id to poll history for."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex})
        resp.raise_for_status()
        return resp.json()["prompt_id"]


async def wait_for_history(prompt_id: str) -> dict | None:
    """Polls /history until the given prompt_id's outputs are ready, returning
    its full `outputs` dict (node_id -> node's UI-result dict), or None on timeout."""
    deadline = time.monotonic() + GENERATION_TIMEOUT_SECONDS
    async with httpx.AsyncClient(timeout=15) as client:
        while time.monotonic() < deadline:
            hist_resp = await client.get(f"{COMFYUI_URL}/history/{prompt_id}")
            history = hist_resp.json()
            if prompt_id in history:
                return history[prompt_id].get("outputs", {})
            await asyncio.sleep(2)
    return None


async def fetch_output_file(filename: str, subfolder: str, file_type: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{COMFYUI_URL}/view",
            params={"filename": filename, "subfolder": subfolder, "type": file_type},
        )
        resp.raise_for_status()
        return resp.content


def shutdown_if_unneeded(my_config_key: str, sibling_config_keys: list[str]) -> None:
    """'Off' has to mean off - ComfyUI is a real GPU process, not just a tool
    Jarvis stops calling. Only actually kills it if no OTHER ComfyUI-dependent
    plugin is still enabled, since two plugins can share the same instance.

    Imports config locally rather than at module level - config.py imports
    plugin_loader, which imports every plugin (including this module's
    callers) at plugin-discovery time, so a top-level import here would be
    circular depending on which module happens to get imported first."""
    import config as config_module
    config = config_module.load_config()

    if any(config.get(key) for key in sibling_config_keys):
        print(f"[comfyui_shared] {my_config_key} disabled, but another ComfyUI-dependent plugin is still on - leaving it running", flush=True)
        return

    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        return

    pids = set()
    for line in result.stdout.splitlines():
        if f":{COMFYUI_PORT} " in line and "LISTENING" in line:
            pids.add(line.split()[-1])

    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", pid], capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:  # noqa: BLE001
            pass
