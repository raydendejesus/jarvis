"""
Google OAuth connection - lets Jarvis's Gmail/Calendar/Drive plugin (see
plugins/google_workspace.py) act on sir's own Google account, via a normal
"Connect Google Account" flow through the dashboard (never over the phone,
never headless - it requires a real click and a real Google sign-in).

Setup requires a Google Cloud OAuth client sir creates himself - see
google_oauth_config.example.json and the README for exact steps. This file
never runs without that config existing; is_configured() gates everything.
"""
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

CONFIG_FILE = Path(__file__).resolve().parent / "google_oauth_config.json"
TOKENS_FILE = Path(__file__).resolve().parent / "connections" / "google_tokens.json"

REDIRECT_URI = "http://127.0.0.1:8765/api/connections/google/callback"

# Gmail/Calendar/Drive stay read-only, deliberately - sending email, creating
# calendar events, and writing arbitrary Drive files are real-world actions
# with real consequences, left for a follow-up once the connection itself is
# proven solid. Slides is the one deliberate exception: creating a
# presentation is genuinely low-consequence (nothing sent to anyone, trivial
# to edit or delete afterward) and "making a slideshow" was the actual ask,
# so it gets write access while the rest stays read-only.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/presentations",
]

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def load_client_config() -> dict | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not data.get("client_id") or not data.get("client_secret"):
        return None
    return data


def is_configured() -> bool:
    return load_client_config() is not None


def _load_tokens() -> dict | None:
    if not TOKENS_FILE.exists():
        return None
    try:
        return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_tokens(tokens: dict) -> None:
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def is_connected() -> bool:
    tokens = _load_tokens()
    return bool(tokens and tokens.get("refresh_token"))


def disconnect() -> None:
    if TOKENS_FILE.exists():
        TOKENS_FILE.unlink()


def build_auth_url() -> str | None:
    client = load_client_config()
    if client is None:
        return None
    params = {
        "client_id": client["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


async def exchange_code(code: str) -> None:
    client = load_client_config()
    if client is None:
        raise RuntimeError("Google OAuth isn't configured (google_oauth_config.json missing).")

    async with httpx.AsyncClient(timeout=20) as http_client:
        resp = await http_client.post(TOKEN_ENDPOINT, data={
            "code": code,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        })
        resp.raise_for_status()
        data = resp.json()

    _save_tokens({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + data.get("expires_in", 3600),
    })


async def _refresh_access_token(tokens: dict) -> str:
    client = load_client_config()
    async with httpx.AsyncClient(timeout=20) as http_client:
        resp = await http_client.post(TOKEN_ENDPOINT, data={
            "refresh_token": tokens["refresh_token"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "grant_type": "refresh_token",
        })
        resp.raise_for_status()
        data = resp.json()

    tokens["access_token"] = data["access_token"]
    tokens["expires_at"] = time.time() + data.get("expires_in", 3600)
    _save_tokens(tokens)
    return tokens["access_token"]


async def get_valid_access_token() -> str | None:
    tokens = _load_tokens()
    if tokens is None or not tokens.get("refresh_token"):
        return None
    if time.time() < tokens.get("expires_at", 0) - 60:
        return tokens["access_token"]
    return await _refresh_access_token(tokens)
