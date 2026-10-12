"""
Spotify connection - same shape as github_auth.py (a real "Connect Spotify"
redirect-and-approve flow), for the spotify_control plugin. Requires a free
Spotify developer app sir creates himself - see
spotify_oauth_config.example.json and the README for exact steps. Starting
playback through the Web API needs Spotify Premium.

Unlike GitHub's non-expiring token, Spotify access tokens last an hour, so
get_valid_token() refreshes them automatically using the refresh token.
"""
import base64
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

CONFIG_FILE = Path(__file__).resolve().parent / "spotify_oauth_config.json"
TOKEN_FILE = Path(__file__).resolve().parent / "connections" / "spotify_token.json"

REDIRECT_URI = "http://127.0.0.1:8765/api/connections/spotify/callback"

SCOPES = [
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-library-read",
]

AUTH_ENDPOINT = "https://accounts.spotify.com/authorize"
TOKEN_ENDPOINT = "https://accounts.spotify.com/api/token"


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


def save_client_config(client_id: str, client_secret: str) -> None:
    CONFIG_FILE.write_text(
        json.dumps({"client_id": client_id, "client_secret": client_secret}, indent=2), encoding="utf-8"
    )


def _load_token_data() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if data.get("refresh_token") or data.get("access_token") else None


def _save_token_data(data: dict) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def is_connected() -> bool:
    return _load_token_data() is not None


def disconnect() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def build_auth_url() -> str | None:
    client = load_client_config()
    if client is None:
        return None
    params = {
        "client_id": client["client_id"],
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


def _basic_auth_header(client: dict) -> dict:
    raw = f"{client['client_id']}:{client['client_secret']}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


async def exchange_code(code: str) -> None:
    client = load_client_config()
    if client is None:
        raise RuntimeError("Spotify OAuth isn't configured (spotify_oauth_config.json missing).")

    async with httpx.AsyncClient(timeout=20) as http_client:
        resp = await http_client.post(
            TOKEN_ENDPOINT,
            data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
            headers=_basic_auth_header(client),
        )
        resp.raise_for_status()
        data = resp.json()

    if "access_token" not in data:
        raise RuntimeError(f"Spotify didn't return an access token: {data}")

    _save_token_data({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_at": time.time() + int(data.get("expires_in", 3600)),
    })


async def get_valid_token() -> str | None:
    """The current access token, refreshed first if it's about to expire.
    None if not connected, or if the refresh was rejected (re-connect needed)."""
    data = _load_token_data()
    if data is None:
        return None
    if data.get("access_token") and data.get("expires_at", 0) - 60 > time.time():
        return data["access_token"]

    client = load_client_config()
    refresh_token = data.get("refresh_token")
    if client is None or not refresh_token:
        return None

    async with httpx.AsyncClient(timeout=20) as http_client:
        resp = await http_client.post(
            TOKEN_ENDPOINT,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers=_basic_auth_header(client),
        )
    if resp.status_code != 200:
        return None
    fresh = resp.json()
    _save_token_data({
        "access_token": fresh["access_token"],
        "refresh_token": fresh.get("refresh_token", refresh_token),
        "expires_at": time.time() + int(fresh.get("expires_in", 3600)),
    })
    return fresh["access_token"]
