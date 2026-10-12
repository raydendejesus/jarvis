"""
Holds the 3D canvas's current content - metadata about the most recently
generated 3D model (a .glb file living in plugins_data/generated_3d_models/),
mirroring canvas_state.py's pattern exactly. The actual model bytes are served
as a static file by filename (see server.py); this just tracks which one is
current and when it was made, so the dashboard knows when to reload it.

No as_prompt_block() here, unlike canvas_state.py - a 3D model can't be
meaningfully "edited" via text the way HTML can, so each request generates a
fresh model from scratch rather than modifying the current one.
"""
import json
import time
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "model3d_state.json"

_state = {"filename": "", "title": "", "updated_at": 0.0}


def _load() -> None:
    global _state
    if STATE_FILE.exists():
        try:
            _state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass


_load()


def get() -> dict:
    return dict(_state)


def get_updated_at() -> float:
    return _state.get("updated_at", 0.0)


def set_content(filename: str, title: str = "") -> None:
    global _state
    _state = {"filename": filename, "title": title, "updated_at": time.time()}
    STATE_FILE.write_text(json.dumps(_state), encoding="utf-8")
