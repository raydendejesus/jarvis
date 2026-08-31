"""
Notion connection - a personal tool uses Notion's "internal integration"
model, not the redirect-OAuth flow meant for multi-workspace SaaS apps: sir
generates one secret token in Notion's settings, pastes it in, then shares
specific pages/databases with the integration inside Notion itself.
"""
import json
from pathlib import Path

TOKEN_FILE = Path(__file__).resolve().parent / "connections" / "notion_token.json"


def _load() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_connected() -> bool:
    data = _load()
    return bool(data and data.get("integration_secret"))


def get_token() -> str | None:
    data = _load()
    return data.get("integration_secret") if data else None


def save_token(integration_secret: str) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"integration_secret": integration_secret}, indent=2), encoding="utf-8")


def disconnect() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
