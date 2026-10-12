"""
Discord connection - unlike Google/GitHub, reading/posting in servers you're
in requires a bot (created once in Discord's developer portal), not a user
OAuth login. Sir pastes the bot's token in from the dashboard; separately,
he uses a one-click "invite this bot to a server" link (built from the same
config) per server he wants it in - see the README for exact steps.
"""
import json
from pathlib import Path
from urllib.parse import urlencode

TOKEN_FILE = Path(__file__).resolve().parent / "connections" / "discord_bot_token.json"

# Read Messages/View Channels + Read Message History - deliberately no send/
# manage permissions, matching the read-only-for-now approach used elsewhere.
INVITE_PERMISSIONS = 66560


def _load() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_connected() -> bool:
    data = _load()
    return bool(data and data.get("bot_token"))


def get_token() -> str | None:
    data = _load()
    return data.get("bot_token") if data else None


def save_token(bot_token: str, client_id: str | None = None) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"bot_token": bot_token, "client_id": client_id}, indent=2), encoding="utf-8")


def disconnect() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()


def build_invite_url() -> str | None:
    data = _load()
    if not data or not data.get("client_id"):
        return None
    params = {"client_id": data["client_id"], "permissions": INVITE_PERMISSIONS, "scope": "bot"}
    return f"https://discord.com/api/oauth2/authorize?{urlencode(params)}"
