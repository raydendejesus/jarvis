import base64
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

OLLAMA_URL = "http://localhost:11434/api/chat"
VISION_MODEL = "qwen2.5vl:7b"
TEXT_MODEL = "jarvis"
FRAME_COUNT = 6

FFMPEG_FALLBACK = Path(r"O:\ffmpeg-8.0.1-essentials_build\bin\ffmpeg.exe")


def _ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    if FFMPEG_FALLBACK.exists():
        return str(FFMPEG_FALLBACK)
    raise RuntimeError("ffmpeg not found on PATH or at the known fallback location")


async def describe_image(image_bytes: bytes, question: str = "Describe what you see in detail.") -> str:
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            OLLAMA_URL,
            json={
                "model": VISION_MODEL,
                "messages": [{"role": "user", "content": question, "images": [image_b64]}],
                "stream": False,
                "keep_alive": -1,
            },
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


async def locate_point(image_bytes: bytes, description: str) -> tuple[float, float] | None:
    """Asks the vision model to pinpoint one described element in a screenshot,
    returning normalized (x_fraction, y_fraction) coordinates (0-1, origin
    top-left) for the browser-control pixel fallback. Returns None if the model
    couldn't confidently identify a location."""
    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    prompt = (
        f"This is a screenshot of a browser tab. Find this element: \"{description}\". "
        "Reply with ONLY two numbers separated by a comma - the fraction of the way "
        "across (0.0 = left edge, 1.0 = right edge) and the fraction of the way down "
        "(0.0 = top edge, 1.0 = bottom edge) to its center, e.g. \"0.42,0.17\". "
        "If you cannot confidently find it, reply with exactly NONE."
    )
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            OLLAMA_URL,
            json={
                "model": VISION_MODEL,
                "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
                "stream": False,
                "keep_alive": -1,
            },
        )
        resp.raise_for_status()
        answer = resp.json()["message"]["content"].strip()

    if answer.upper().startswith("NONE"):
        return None
    try:
        x_str, y_str = answer.split(",", 1)
        x, y = float(x_str.strip()), float(y_str.strip())
    except ValueError:
        return None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None
    return x, y


def _extract_frames(video_path: str, out_dir: Path, count: int = FRAME_COUNT) -> list[Path]:
    probe = subprocess.run(
        [_ffmpeg_path(), "-i", video_path],
        capture_output=True, text=True,
    )
    duration = 10.0
    for line in probe.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            hms = line.split(",")[0].replace("Duration:", "").strip()
            h, m, s = hms.split(":")
            duration = int(h) * 3600 + int(m) * 60 + float(s)
            break

    frame_paths = []
    for i in range(count):
        timestamp = max(0.0, duration * (i + 1) / (count + 1))
        frame_path = out_dir / f"frame_{i}.jpg"
        subprocess.run(
            [_ffmpeg_path(), "-y", "-ss", str(timestamp), "-i", video_path,
             "-frames:v", "1", "-q:v", "2", str(frame_path)],
            capture_output=True,
        )
        if frame_path.exists():
            frame_paths.append(frame_path)
    return frame_paths


async def summarize_video(video_path: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        frames = _extract_frames(video_path, Path(tmp))
        if not frames:
            return "I wasn't able to extract any frames from that video, sir."

        descriptions = []
        for frame_path in frames:
            desc = await describe_image(frame_path.read_bytes(), "Describe this video frame briefly.")
            descriptions.append(desc)

        frame_summaries = "\n".join(f"Frame {i + 1}: {d}" for i, d in enumerate(descriptions))
        prompt = (
            "Here are descriptions of frames sampled evenly through a video, in order. "
            "Summarize what the video overall appears to show, in a few natural sentences:\n\n"
            f"{frame_summaries}"
        )

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={"model": TEXT_MODEL, "messages": [{"role": "user", "content": prompt}], "stream": False, "keep_alive": -1},
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]