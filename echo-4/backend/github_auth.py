"""
GitHub OAuth connection - same shape as google_auth.py (a real "Connect
GitHub" redirect-and-approve flow), for the github plugin's read-only repo
tools. Requires a GitHub OAuth App sir creates himself - see
github_oauth_config.example.json and the README for exact steps.
"""
import json
from pathlib import Path
from urllib.parse import urlencode

import httpx

CONFIG_FILE = Path(__file__).resolve().parent / "github_oauth_config.json"
TOKEN_FILE = Path(__file__).resolve().parent / "connections" / "github_token.json"

REDIRECT_URI = "http://127.0.0.1:8765/api/connections/github/callback"

# GitHub's classic OAuth App scopes don't split read vs write for private
# repos - "repo" grants both. The tools this plugin exposes are read-only in
# behavior regardless of what the token technically permits; see the README
# for this caveat spelled out plainly rather than overclaiming read-only.
SCOPES = ["repo", "read:user"]

AUTH_ENDPOINT = "https://github.com/login/oauth/authorize"
TOKEN_ENDPOINT = "https://github.com/login/oauth/access_token"


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


def _load_token() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8")).get("access_token")
    except (json.JSONDecodeError, OSError):
        return None


def is_connected() -> bool:
    return _load_token() is not None


def disconnect() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def build_auth_url() -> str | None:
    client = load_client_config()
    if client is None:
        return None
    params = {
        "client_id": client["client_id"],
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(SCOPES),
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


async def exchange_code(code: str) -> None:
    client = load_client_config()
    if client is None:
        raise RuntimeError("GitHub OAuth isn't configured (github_oauth_config.json missing).")

    async with httpx.AsyncClient(timeout=20) as http_client:
        resp = await http_client.post(
            TOKEN_ENDPOINT,
            data={
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()

    if "access_token" not in data:
        raise RuntimeError(f"GitHub didn't return an access token: {data}")

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"access_token": data["access_token"]}, indent=2), encoding="utf-8")


def get_token() -> str | None:
    return _load_token()
