"""
On-demand 3D model generation via ComfyUI's native Trellis.2/Pixal3D support
(merged into ComfyUI core 2026-08-22 - no custom nodes, no hand-compiled CUDA
extensions). Shares comfyui_shared.py's lazy-launch/shutdown machinery with
image_generator.py, since both depend on the same ComfyUI process.

Confirmed by directly inspecting ComfyUI's own shipped example workflow
(see plugins_data/trellis2_workflow_notes.md) and validating the extracted
graph against ComfyUI's own execution.validate_prompt(): this pipeline is
fundamentally IMAGE-to-3D. There is no text-prompt input anywhere in it - so
detailed_prompt drives generation one of two ways:
1. If reference_search_query finds a real photo, that photo is what actually
   goes into the 3D pipeline - detailed_prompt is saved for the record only.
2. Otherwise, detailed_prompt is used to synthesize a starting image first
   (image_generator.py's own SD1.5 workflow, via generate_plain_image), which
   is then fed into the 3D pipeline as its input image.
"""
import json
import random
import uuid
from datetime import datetime
from pathlib import Path

import httpx

import comfyui_shared
import model3d_state
from plugins.image_generator import generate_plain_image

PLUGIN_NAME = "model_3d_generator"
TOGGLE_LABEL = "3D Model Generation (ComfyUI)"
CONFIG_KEY = "model_3d_generator_enabled"
ENABLED_BY_DEFAULT = False
VRAM_COST = "~8-16 GB while actively generating (Trellis.2/Pixal3D) - ComfyUI is not kept running when off"

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "plugins_data" / "generated_3d_models"
WORKFLOW_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "plugins_data" / "trellis2_api_workflow_reference.json"

# The seed inputs on these nodes (structure/shape-512/shape-1536/texture
# KSamplers) are fixed values in the template - randomize per-request so
# repeated generations of the same image don't come back identical.
SEED_NODE_IDS = ("3", "12", "18", "23")


def _load_workflow_template() -> dict:
    return json.loads(WORKFLOW_TEMPLATE_PATH.read_text(encoding="utf-8"))


async def _upload_image_to_comfyui(image_bytes: bytes, filename: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{comfyui_shared.COMFYUI_URL}/upload/image",
            files={"image": (filename, image_bytes, "image/png")},
        )
        resp.raise_for_status()
        return resp.json()["name"]


def _build_workflow(image_filename: str) -> dict:
    workflow = _load_workflow_template()
    workflow["122"]["inputs"]["image"] = image_filename
    for node_id in SEED_NODE_IDS:
        workflow[node_id]["inputs"]["seed"] = random.randint(0, 2**31 - 1)
    return workflow


async def _run_generation(workflow: dict) -> bytes | None:
    prompt_id = await comfyui_shared.submit_workflow(workflow)
    outputs = await comfyui_shared.wait_for_history(prompt_id)
    if outputs is None:
        return None
    for node_output in outputs.values():
        for item in node_output.get("3d", []):
            return await comfyui_shared.fetch_output_file(
                item["filename"], item.get("subfolder", ""), item.get("type", "output")
            )
    return None


async def generate_3d_model(args: dict) -> str:
    detailed_prompt = (args.get("detailed_prompt") or "").strip()
    if not detailed_prompt:
        return "I need an actual detailed description to generate from, not just a short request."
    reference_query = (args.get("reference_search_query") or "").strip()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (OUTPUT_DIR / f"{stamp}_prompt.txt").write_text(detailed_prompt, encoding="utf-8")

    if not await comfyui_shared.ensure_running("model_3d_generator"):
        return "ComfyUI isn't running and I couldn't start it in time - it may need to be launched manually this once."

    reference_filename = None
    if reference_query:
        reference_filename = await comfyui_shared.fetch_reference_image(reference_query, "model_3d_generator")
        print(f"[model_3d_generator] reference photo: {reference_filename or 'none found'}", flush=True)

    if reference_filename:
        input_image_filename = reference_filename
        used_reference = True
    else:
        print(f"[model_3d_generator] no reference photo - synthesizing a starting image from: {detailed_prompt!r}", flush=True)
        image_bytes = await generate_plain_image(detailed_prompt)
        if image_bytes is None:
            print("[model_3d_generator] failed to synthesize a starting image", flush=True)
            return "I couldn't generate a starting image to build the 3D model from."
        synth_filename = f"synth_{uuid.uuid4().hex}.png"
        input_image_filename = await _upload_image_to_comfyui(image_bytes, synth_filename)
        used_reference = False

    print(f"[model_3d_generator] generating 3D model from image {input_image_filename!r}", flush=True)
    model_bytes = await _run_generation(_build_workflow(input_image_filename))
    if model_bytes is None:
        print("[model_3d_generator] no 3D model came back before the timeout, or the pipeline failed silently", flush=True)
        return "ComfyUI didn't return a 3D model in time - the generation may still be running, or it failed."

    print("[model_3d_generator] 3D model generated successfully", flush=True)
    glb_filename = f"{stamp}.glb"
    (OUTPUT_DIR / glb_filename).write_bytes(model_bytes)
    model3d_state.set_content(glb_filename, "Generated 3D Model")

    ref_note = " using a reference photo I found first" if used_reference else " (built from a starting image I generated from the description)"
    return f"3D model generated{ref_note} and shown on the 3D canvas."


def on_disable() -> None:
    comfyui_shared.shutdown_if_unneeded(CONFIG_KEY, ["image_generator_enabled"])


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "generate_3d_model",
            "description": (
                "Generates a real, textured 3D model (a .glb file) and shows it on the dashboard's 3D canvas, "
                "orbit/zoom viewable. This pipeline is genuinely image-to-3D, not text-to-3D - there is no direct "
                "text-prompt input. detailed_prompt must still be a rich, specific description YOU write (shape, "
                "material, color, distinguishing features) - it's used as the record of what was asked, and as "
                "the basis for a starting image if no reference photo is found. If what sir asked for names "
                "something concrete and real - a specific character, object, animal, brand, or thing with a known "
                "real appearance - set reference_search_query to a short web image search for it first, so the "
                "model is built from an actual photo instead of a synthesized guess."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "detailed_prompt": {
                        "type": "string",
                        "description": "A rich, detailed description written by you, not sir's raw request.",
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
    "generate_3d_model": generate_3d_model,
}
