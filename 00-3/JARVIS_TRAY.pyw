import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_DIR / "backend"
sys.path.insert(0, str(BACKEND_DIR))

# Redirect this process's own output here directly, rather than relying on
# whatever launched it (the Startup shortcut, JARVIS.bat, or a manual
# restart) to redirect it - none of those do, so print()/listener.py's
# logging previously went nowhere unless launched with the right shell
# redirection by hand, making tray_out.log silently go stale after any
# restart that didn't happen to include it.
sys.stdout = open(PROJECT_DIR / "tray_out.log", "a", encoding="utf-8", buffering=1)
sys.stderr = open(PROJECT_DIR / "tray_err.log", "a", encoding="utf-8", buffering=1)

import httpx
import pystray
from PIL import Image, ImageDraw

import config as config_module  # noqa: E402
import listener  # noqa: E402
import plugin_loader  # noqa: E402
import security  # noqa: E402
import telephony  # noqa: E402

SERVER_URL = "http://127.0.0.1:8765"
PYTHON_EXE = PROJECT_DIR / "venv" / "Scripts" / "python.exe"
NGROK_EXE = Path(r"C:\Users\Rayden\AppData\Local\ngrok-bin\ngrok.exe")
NGROK_API = "http://127.0.0.1:4040/api/tunnels"

server_process = None
ngrok_process = None


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
    # Without explicit redirection here, a relaunch by this watchdog inherits
    # pythonw.exe's own (nonexistent) console streams and the server's output
    # simply vanishes - server_out.log/server_err.log would silently stop
    # updating after the first restart, long before anyone notices.
    out_log = open(PROJECT_DIR / "server_out.log", "a", encoding="utf-8")
    err_log = open(PROJECT_DIR / "server_err.log", "a", encoding="utf-8")
    server_process = subprocess.Popen(
        [str(PYTHON_EXE), str(BACKEND_DIR / "server.py")],
        cwd=str(PROJECT_DIR),
        stdout=out_log,
        stderr=err_log,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    for _ in range(30):
        if is_server_running():
            break
        time.sleep(0.5)


def get_ngrok_url() -> str | None:
    try:
        tunnels = httpx.get(NGROK_API, timeout=3).json().get("tunnels", [])
        for t in tunnels:
            if t.get("proto") == "https":
                return t.get("public_url")
    except Exception:
        pass
    return None


def update_telnyx_voice_url(cfg: dict, new_url: str) -> None:
    try:
        httpx.patch(
            f"https://api.telnyx.com/v2/texml_applications/{cfg['application_sid']}",
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            json={"voice_url": f"{new_url}/api/telephony/voice"},
            timeout=20,
        )
        print(f"[tray] updated Telnyx voice_url to {new_url}/api/telephony/voice")
    except Exception as exc:
        print(f"[tray] failed to update Telnyx voice_url: {exc}")


def start_ngrok_if_needed() -> None:
    """Phone calling depends on Telnyx being able to reach this machine, which only
    works while the ngrok tunnel is alive. This isn't started by Windows on its own -
    without this, the tunnel silently dies on any reboot/crash and calls start failing
    with no obvious cause. If ngrok ever hands out a different URL than last time
    (expected on the free tier without a claimed static domain), this keeps
    telephony_config.json and the Telnyx application in sync automatically."""
    global ngrok_process
    if not telephony.is_configured():
        return

    if get_ngrok_url() is None:
        if not NGROK_EXE.exists():
            print(f"[tray] ngrok not found at {NGROK_EXE}, skipping tunnel start")
            return
        ngrok_process = subprocess.Popen(
            [str(NGROK_EXE), "http", "8765", "--log=stdout"],
            cwd=str(PROJECT_DIR),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for _ in range(20):
            if get_ngrok_url() is not None:
                break
            time.sleep(0.5)

    url = get_ngrok_url()
    cfg = telephony.load_config()
    if url and cfg and cfg.get("public_base_url") != url:
        print(f"[tray] ngrok URL is now {url}, syncing config and Telnyx")
        cfg["public_base_url"] = url
        telephony.save_config(cfg)
        update_telnyx_voice_url(cfg, url)


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
        if not new_value:
            # This tray menu bypasses server.py's /api/plugins toggle endpoint
            # entirely (writes config.json directly), so it needs its own call
            # into the same resource-cleanup hook - otherwise a plugin holding
            # a real GPU process only gets shut down when toggled off from the
            # dashboard, not from here.
            meta = next((m for m in plugin_loader.plugin_metadata() if m["config_key"] == key), None)
            if meta:
                plugin_loader.call_on_disable(meta["name"])

    return _toggle


def flag_checked(key: str):
    def _checked(item):
        return config_module.load_config().get(key, False)

    return _checked


LOCATION_MODE_CYCLE = ["off", "pc", "phone"]


def cycle_location_mode(icon, item) -> None:
    cfg = config_module.load_config()
    current = cfg.get("location_mode", "off")
    next_index = (LOCATION_MODE_CYCLE.index(current) + 1) % len(LOCATION_MODE_CYCLE) if current in LOCATION_MODE_CYCLE else 0
    cfg["location_mode"] = LOCATION_MODE_CYCLE[next_index]
    config_module.save_config(cfg)


def location_mode_label(item) -> str:
    mode = config_module.load_config().get("location_mode", "off")
    return f"Location: {mode.upper()} (click to change)"


def quit_app(icon, item) -> None:
    icon.stop()
    if server_process is not None:
        server_process.terminate()
    if ngrok_process is not None:
        ngrok_process.terminate()


def start_listener_thread() -> None:
    thread = threading.Thread(target=listener.run, daemon=True)
    thread.start()


WATCHDOG_INTERVAL_SECONDS = 20


def watchdog_loop() -> None:
    """start_server_if_needed/start_ngrok_if_needed only ran once, at tray startup -
    if either process died later (crash, killed manually, anything), nothing ever
    noticed or restarted it, so Jarvis could go silently unreachable indefinitely.
    This keeps checking for the life of the tray, not just at launch."""
    while True:
        time.sleep(WATCHDOG_INTERVAL_SECONDS)
        try:
            if not is_server_running():
                print("[tray] backend server is down, restarting it", flush=True)
                start_server_if_needed()
            start_ngrok_if_needed()
        except Exception as exc:  # noqa: BLE001 - the watchdog itself must never die
            print(f"[tray] watchdog error: {exc}", flush=True)


def start_watchdog_thread() -> None:
    thread = threading.Thread(target=watchdog_loop, daemon=True)
    thread.start()


def main() -> None:
    start_server_if_needed()
    start_ngrok_if_needed()
    start_listener_thread()
    start_watchdog_thread()

    cfg = config_module.load_config()
    if cfg.get("ai_enabled"):
        threading.Timer(1.0, open_dashboard).start()

    menu_items = [
        pystray.MenuItem("Open Dashboard", open_dashboard),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Jarvis Enabled", toggle_flag("ai_enabled"), checked=flag_checked("ai_enabled")),
        pystray.MenuItem("Screen Access", toggle_flag("screen_access"), checked=flag_checked("screen_access")),
        pystray.MenuItem("Camera Access", toggle_flag("camera_access"), checked=flag_checked("camera_access")),
        pystray.MenuItem("Desk Guard", toggle_flag("desk_guard_enabled"), checked=flag_checked("desk_guard_enabled")),
        pystray.MenuItem("Calling", toggle_flag("calling_enabled"), checked=flag_checked("calling_enabled")),
        pystray.MenuItem("Call Notifications", toggle_flag("call_notifications_enabled"), checked=flag_checked("call_notifications_enabled")),
        pystray.MenuItem(location_mode_label, cycle_location_mode),
        pystray.MenuItem("Browser Control", toggle_flag("browser_control_enabled"), checked=flag_checked("browser_control_enabled")),
        pystray.MenuItem("  -> allow pixel-control fallback", toggle_flag("browser_pixel_fallback_enabled"), checked=flag_checked("browser_pixel_fallback_enabled")),
    ]

    plugin_toggles = [m for m in plugin_loader.plugin_metadata() if not m["always_on"]]
    if plugin_toggles:
        menu_items.append(pystray.Menu.SEPARATOR)
        for meta in plugin_toggles:
            menu_items.append(
                pystray.MenuItem(meta["label"], toggle_flag(meta["config_key"]), checked=flag_checked(meta["config_key"]))
            )

    menu_items.append(pystray.Menu.SEPARATOR)
    menu_items.append(pystray.MenuItem("Quit", quit_app))

    icon = pystray.Icon("jarvis", make_icon_image(), "Jarvis", menu=pystray.Menu(*menu_items))
    icon.run()


if __name__ == "__main__":
    main()