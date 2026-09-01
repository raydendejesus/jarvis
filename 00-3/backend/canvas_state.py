"""
Holds the code canvas's current content - the live HTML/CSS/JS page Jarvis
is showing on the dashboard, next to the conversation. Persisted to disk so
a page reload doesn't lose it, but this is view state, not anything sir
asked to be remembered - a fresh write_canvas_code call just replaces it.
"""
import json
import time
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "canvas_state.json"

_state = {"html": "", "title": "", "updated_at": 0.0}


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


def as_prompt_block() -> str:
    """conversation_history is in-memory only and gets wiped on every backend
    restart, so relying on it to know what's currently on the canvas silently
    breaks any 'change what you just built' request the moment the process
    restarts - this reads the actual persisted ground truth instead, every turn."""
    if not _state.get("html"):
        return "Canvas: currently empty - nothing built yet."
    title = _state.get("title") or "(untitled)"
    return (
        f'Canvas: currently showing "{title}". This is the exact current HTML - if sir asks you '
        f"to change, fix, or build on what's showing, call write_canvas_code with this content "
        f"plus your changes (never guess at what's already there, and never just describe the "
        f"change in chat instead of calling the tool):\n{_state['html']}"
    )


def set_content(html: str, title: str = "") -> None:
    global _state
    _state = {"html": html, "title": title, "updated_at": time.time()}
    STATE_FILE.write_text(json.dumps(_state), encoding="utf-8")
