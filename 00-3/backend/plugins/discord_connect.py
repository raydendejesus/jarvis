"""
Read-only Discord lookups (list servers the bot is in, list channels, read
recent messages), once sir has pasted his bot's token in from the
dashboard's Connections panel and invited it to at least one server. See
discord_auth.py for the connection itself.
"""
import httpx

import discord_auth

PLUGIN_NAME = "discord_connect"
TOGGLE_LABEL = "Discord"
CONFIG_KEY = "discord_connect_enabled"
ENABLED_BY_DEFAULT = False

API = "https://discord.com/api/v10"

NOT_CONNECTED_MSG = (
    "Discord isn't connected yet - go to the dashboard's Connections panel, paste in a bot "
    "token, and invite the bot to a server, then try again."
)


def _headers() -> dict | None:
    token = discord_auth.get_token()
    if token is None:
        return None
    return {"Authorization": f"Bot {token}"}


async def list_discord_servers(args: dict) -> str:
    headers = _headers()
    if headers is None:
        return NOT_CONNECTED_MSG

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{API}/users/@me/guilds", headers=headers)
        if resp.status_code != 200:
            return f"Couldn't list Discord servers: {resp.status_code} {resp.text[:200]}"
        guilds = resp.json()

    if not guilds:
        return "This bot hasn't been invited to any servers yet."
    return "Servers the bot is in:\n" + "\n".join(f"- {g['name']}" for g in guilds)


async def _find_guild_id(client: httpx.AsyncClient, headers: dict, server_name: str) -> str | None:
    resp = await client.get(f"{API}/users/@me/guilds", headers=headers)
    if resp.status_code != 200:
        return None
    for g in resp.json():
        if g["name"].lower() == server_name.lower():
            return g["id"]
    return None


async def list_discord_channels(args: dict) -> str:
    headers = _headers()
    if headers is None:
        return NOT_CONNECTED_MSG
    server_name = (args.get("server_name") or "").strip()
    if not server_name:
        return "I need a server name to list its channels."

    async with httpx.AsyncClient(timeout=20) as client:
        guild_id = await _find_guild_id(client, headers, server_name)
        if guild_id is None:
            return f"I don't see a server named '{server_name}' that the bot is in."
        resp = await client.get(f"{API}/guilds/{guild_id}/channels", headers=headers)
        if resp.status_code != 200:
            return f"Couldn't list channels for {server_name}: {resp.status_code} {resp.text[:200]}"
        channels = [c for c in resp.json() if c.get("type") == 0]  # text channels only

    if not channels:
        return f"No text channels found in {server_name}."
    return f"Text channels in {server_name}:\n" + "\n".join(f"- #{c['name']}" for c in channels)


async def read_discord_channel(args: dict) -> str:
    headers = _headers()
    if headers is None:
        return NOT_CONNECTED_MSG
    server_name = (args.get("server_name") or "").strip()
    channel_name = (args.get("channel_name") or "").strip().lstrip("#")
    count = min(int(args.get("count", 10) or 10), 20)
    if not server_name or not channel_name:
        return "I need both a server name and a channel name."

    async with httpx.AsyncClient(timeout=20) as client:
        guild_id = await _find_guild_id(client, headers, server_name)
        if guild_id is None:
            return f"I don't see a server named '{server_name}' that the bot is in."

        chans_resp = await client.get(f"{API}/guilds/{guild_id}/channels", headers=headers)
        if chans_resp.status_code != 200:
            return f"Couldn't list channels for {server_name}: {chans_resp.status_code} {chans_resp.text[:200]}"
        channel = next((c for c in chans_resp.json() if c.get("name", "").lower() == channel_name.lower()), None)
        if channel is None:
            return f"I don't see a channel named '{channel_name}' in {server_name}."

        msgs_resp = await client.get(
            f"{API}/channels/{channel['id']}/messages", headers=headers, params={"limit": count},
        )
        if msgs_resp.status_code != 200:
            return f"Couldn't read #{channel_name}: {msgs_resp.status_code} {msgs_resp.text[:200]}"
        messages = msgs_resp.json()

    if not messages:
        return f"No recent messages in #{channel_name}."
    lines = [f"- {m['author']['username']}: {m['content']}" for m in reversed(messages) if m.get("content")]
    return f"Recent messages in #{channel_name} ({server_name}):\n" + "\n".join(lines)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_discord_servers",
            "description": "List the Discord servers sir's bot has been invited to.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_discord_channels",
            "description": "List the text channels in a Discord server sir's bot is in.",
            "parameters": {
                "type": "object",
                "properties": {"server_name": {"type": "string"}},
                "required": ["server_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_discord_channel",
            "description": "Read the most recent messages in a specific Discord channel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "server_name": {"type": "string"},
                    "channel_name": {"type": "string"},
                    "count": {"type": "integer", "description": "How many recent messages, max 20"},
                },
                "required": ["server_name", "channel_name"],
            },
        },
    },
]

DISPATCH = {
    "list_discord_servers": list_discord_servers,
    "list_discord_channels": list_discord_channels,
    "read_discord_channel": read_discord_channel,
}
