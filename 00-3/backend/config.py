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
    "location_mode": "off",
    "browser_control_enabled": False,
    "browser_pixel_fallback_enabled": False,
}
# A plugin's toggle gets a default the moment its file is dropped into
# backend/plugins/ - no manual edit here needed.
DEFAULTS.update(plugin_loader.config_defaults())


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        save_config(DEFAULTS)
        return dict(DEFAULTS)
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}
    return {**DEFAULTS, **data}


def save_config(config: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")