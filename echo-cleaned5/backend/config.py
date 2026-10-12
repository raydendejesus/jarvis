import json
from pathlib import Path

import plugin_loader

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"

DEFAULTS = {
    "ai_enabled": True,
    "screen_access": False,
    "camera_access": False,
    "desk_guard_enabled": False,
    "desk_guard_threshold": 0.65,
    "calling_enabled": False,
    "call_notifications_enabled": False,
    "sir_page_enabled": False,
    "location_mode": "off",
    "browser_control_enabled": False,
    "browser_pixel_fallback_enabled": False,
    "code_canvas_enabled": False,
    "vr_mode_enabled": False,
}
# A plugin's toggle gets a default the moment its file is dropped into
# backend/plugins/ - no manual edit here needed.
DEFAULTS.update(plugin_loader.config_defaults())


# The listener asks for the config roughly ten times a second; re-parse only when
# the file has actually changed (any process's write changes its mtime/size).
_cache: tuple[tuple[int, int], dict] | None = None


def load_config() -> dict:
    global _cache
    if not CONFIG_FILE.exists():
        save_config(DEFAULTS)
        return dict(DEFAULTS)
    try:
        stat = CONFIG_FILE.stat()
        key = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    if key is not None and _cache is not None and _cache[0] == key:
        return dict(_cache[1])
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        parsed_ok = True
    except (json.JSONDecodeError, OSError):
        data = {}
        parsed_ok = False
    merged = {**DEFAULTS, **data}
    if parsed_ok and key is not None:
        _cache = (key, merged)
    return dict(merged)


def save_config(config: dict) -> None:
    global _cache
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")
    _cache = None