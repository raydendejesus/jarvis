"""
On-demand image generation via the local ComfyUI install (O:\\ComfyUI). This
never keeps ComfyUI running in the background - it's a real GPU process that
holds VRAM the whole time it's up, so it's launched lazily the moment this
tool is actually called, and killed outright the instant this plugin's
toggle is switched off (see on_disable() below) rather than being left idle.

Two things sir specifically asked for beyond "just call ComfyUI":
1. Before generating, write out the actual detailed prompt used to a .txt
   file alongside the generated image - generate_image's parameter is meant
   to be a genuinely detailed description Jarvis composes itself, not sir's
   short request verbatim, and this keeps a real record of what was used.
2. Never "reference" a specific real thing (a particular character, object,
   brand, person, style) into ComfyUI from a guess alone - if the request
   names something concrete like that, search for a real reference photo of
   it first and feed ComfyUI that image (img2img) alongside the prompt,
   rather than generating purely from a text description of something it's
   never actually seen.
"""
import asyncio
import base64
import random
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

import canvas_state

PLUGIN_NAME = "image_generator"
TOGGLE_LABEL = "Image Generation (ComfyUI)"
CONFIG_KEY = "image_generator_enabled"
ENABLED_BY_DEFAULT = False
VRAM_COST = "~4-6 GB while actively generating (Stable Diffusion 1.5) - ComfyUI is not kept running when off"

COMFYUI_DIR = Path(r"O:\ComfyUI\ComfyUI_windows_portable")
COMFYUI_LAUNCH_BAT = COMFYUI_DIR / "run_nvidia_gpu.bat"
COMFYUI_PORT = 8188
COMFYUI_URL = f"http://127.0.0.1:{COMFYUI_PORT}"
CHECKPOINT_NAME = "v1-5-pruned-emaonly.safetensors"

INPUT_DIR = COMFYUI_DIR / "ComfyUI" / "input"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "plugins_data" / "generated_images"

STARTUP_TIMEOUT_SECONDS = 90
GENERATION_TIMEOUT_SECONDS = 180

NEGATIVE_PROMPT = "blurry, low quality, distorted, deformed, extra limbs, watermark, text, signature"


async def _is_comfyui_running() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"{COMFYUI_URL}/system_stats")
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


async def _ensure_comfyui_running() -> bool:
    if await _is_comfyui_running():
        return True
    if not COMFYUI_LAUNCH_BAT.exists():
        return False
    subprocess.Popen(
        ["cmd", "/c", str(COMFYUI_LAUNCH_BAT)],
        cwd=str(COMFYUI_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        if await _is_comfyui_running():
            return True
    return False


async def _fetch_reference_image(query: str) -> str | None:
    """Downloads the first usable image result straight into ComfyUI's own
    input/ folder (where its LoadImage node reads by filename) - honestly
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
    return None


def _build_workflow(prompt: str, reference_filename: str | None) -> dict:
    workflow = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CHECKPOINT_NAME}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE_PROMPT, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "jarvis", "images": ["8", 0]}},
    }
    if reference_filename:
        # Plain img2img - denoise <1 keeps the result visually grounded in the
        # reference's shapes/composition instead of generating blind. This
        # install has no IPAdapter/ControlNet custom nodes for stronger
        # likeness-preserving conditioning; img2img is what's actually available.
        workflow["10"] = {"class_type": "LoadImage", "inputs": {"image": reference_filename}}
        workflow["5"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["10", 0], "vae": ["4", 2]}}
        denoise = 0.65
    else:
        workflow["5"] = {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}}
        denoise = 1.0

    workflow["3"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": random.randint(0, 2**31 - 1),
            "steps": 20,
            "cfg": 7.0,
            "sampler_name": "euler",
            "scheduler": "normal",
            "denoise": denoise,
            "model": ["4", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    }
    return workflow


async def _run_generation(workflow: dict) -> bytes | None:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex})
        resp.raise_for_status()
        prompt_id = resp.json()["prompt_id"]

    deadline = time.monotonic() + GENERATION_TIMEOUT_SECONDS
    async with httpx.AsyncClient(timeout=15) as client:
        while time.monotonic() < deadline:
            hist_resp = await client.get(f"{COMFYUI_URL}/history/{prompt_id}")
            history = hist_resp.json()
            if prompt_id in history:
                for node_output in history[prompt_id].get("outputs", {}).values():
                    for img in node_output.get("images", []):
                        img_resp = await client.get(
                            f"{COMFYUI_URL}/view",
                            params={
                                "filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output"),
                            },
                        )
                        img_resp.raise_for_status()
                        return img_resp.content
                return None
            await asyncio.sleep(2)
    return None


async def generate_image(args: dict) -> str:
    detailed_prompt = (args.get("detailed_prompt") or "").strip()
    if not detailed_prompt:
        return "I need an actual detailed description to generate from, not just a short request."
    reference_query = (args.get("reference_search_query") or "").strip()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (OUTPUT_DIR / f"{stamp}_prompt.txt").write_text(detailed_prompt, encoding="utf-8")

    reference_filename = None
    if reference_query:
        reference_filename = await _fetch_reference_image(reference_query)

    if not await _ensure_comfyui_running():
        return "ComfyUI isn't running and I couldn't start it in time - it may need to be launched manually this once."

    image_bytes = await _run_generation(_build_workflow(detailed_prompt, reference_filename))
    if image_bytes is None:
        return "ComfyUI didn't return an image in time - the generation may still be running, or it failed."

    (OUTPUT_DIR / f"{stamp}.png").write_bytes(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    canvas_state.set_content(
        f'<body style="margin:0;height:100vh;display:flex;align-items:center;justify-content:center;'
        f'background:#111;"><img src="data:image/png;base64,{b64}" style="max-width:100%;max-height:100%;"></body>',
        "Generated Image",
    )

    ref_note = " using a reference photo I found first" if reference_filename else ""
    if reference_query and not reference_filename:
        ref_note = " (I couldn't find a usable reference photo, so this was generated from the description alone)"
    return f"Image generated{ref_note} and shown on the canvas."


def on_disable() -> None:
    """'Off' has to mean off - ComfyUI is a real GPU process, not just a tool
    Jarvis stops calling. Kills whatever's listening on ComfyUI's port,
    regardless of whether this process or a manual launch started it."""
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


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generates a real image with a local Stable Diffusion install and shows it on the dashboard "
                "canvas. detailed_prompt must be a rich, specific description YOU write - expand whatever sir "
                "asked for into real detail (subject, composition, setting, lighting, style, colors), never pass "
                "his short request through as-is. If what he asked for names something concrete and real - a "
                "specific character, object, brand, person, or visual style, not just a generic description - "
                "set reference_search_query to a short web image search for it first, so generation is grounded "
                "in a real photo instead of a guess."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "detailed_prompt": {
                        "type": "string",
                        "description": "A rich, detailed image description written by you, not sir's raw request.",
                    },
                    "reference_search_query": {
                        "type": "string",
                        "description": "Optional web image search query to ground generation in a real reference photo of something specific and real.",
                    },
                },
                "required": ["detailed_prompt"],
            },
        },
    },
]

DISPATCH = {
    "generate_image": generate_image,
}
