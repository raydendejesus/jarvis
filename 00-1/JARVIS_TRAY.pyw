import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import httpx
import pystray
from PIL import Image, ImageDraw

PROJECT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import config as config_module  # noqa: E402
import listener  # noqa: E402
import security  # noqa: E402

SERVER_URL = "http://127.0.0.1:8765"
PYTHON_EXE = PROJECT_DIR / "venv" / "Scripts" / "python.exe"

server_process = None


def is_server_running() -> bool:
    try:
        httpx.get(f"{SERVER_URL}/api/settings", timeout=1.5)
        return True
    except httpx.HTTPError:
        return False


def start_server_if_needed() -> None:
    global server_process
    if is_server_running():
        return
    server_process = subprocess.Popen(
        [str(PYTHON_EXE), str(BACKEND_DIR / "server.py")],
        cwd=str(PROJECT_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for _ in range(30):
        if is_server_running():
            break
        time.sleep(0.5)


def make_icon_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((8, 8, 56, 56), fill=(15, 20, 25, 255), outline=(77, 227, 255, 255), width=3)
    draw.ellipse((24, 24, 40, 40), fill=(77, 227, 255, 255))
    return img


def open_dashboard(icon=None, item=None) -> None:
    webbrowser.open(SERVER_URL)


def toggle_flag(key: str):
    def _toggle(icon, item):
        cfg = config_module.load_config()
        new_value = not cfg.get(key, False)
        if key == "desk_guard_enabled" and new_value and security.known_face_count() == 0:
            return
        cfg[key] = new_value
        config_module.save_config(cfg)

    return _toggle


def flag_checked(key: str):
    def _checked(item):
        return config_module.load_config().get(key, False)

    return _checked


def quit_app(icon, item) -> None:
    icon.stop()
    if server_process is not None:
        server_process.terminate()


def start_listener_thread() -> None:
    thread = threading.Thread(target=listener.run, daemon=True)
    thread.start()


def main() -> None:
    start_server_if_needed()
    start_listener_thread()

    cfg = config_module.load_config()
    if cfg.get("ai_enabled"):
        threading.Timer(1.0, open_dashboard).start()

    icon = pystray.Icon(
        "jarvis",
        make_icon_image(),
        "Jarvis",
        menu=pystray.Menu(
            pystray.MenuItem("Open Dashboard", open_dashboard),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Jarvis Enabled", toggle_flag("ai_enabled"), checked=flag_checked("ai_enabled")),
            pystray.MenuItem("Screen Access", toggle_flag("screen_access"), checked=flag_checked("screen_access")),
            pystray.MenuItem("Camera Access", toggle_flag("camera_access"), checked=flag_checked("camera_access")),
            pystray.MenuItem("Desk Guard", toggle_flag("desk_guard_enabled"), checked=flag_checked("desk_guard_enabled")),
            pystray.MenuItem("Calling", toggle_flag("calling_enabled"), checked=flag_checked("calling_enabled")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", quit_app),
        ),
    )
    icon.run()


if __name__ == "__main__":
    main()