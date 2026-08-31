"""
Read-only Notion search, once sir has pasted his integration secret in from
the dashboard's Connections panel and shared specific pages/databases with
that integration inside Notion. See notion_auth.py for the connection itself.
"""
import httpx

import notion_auth

PLUGIN_NAME = "notion_connect"
TOGGLE_LABEL = "Notion"
CONFIG_KEY = "notion_connect_enabled"
ENABLED_BY_DEFAULT = False

NOT_CONNECTED_MSG = (
    "Notion isn't connected yet - go to the dashboard's Connections panel and paste in an "
    "integration secret, then try again. Also make sure you've shared the pages you want "
    "Jarvis to see with that integration inside Notion itself."
)


def _headers() -> dict | None:
    token = notion_auth.get_token()
    if token is None:
        return None
    return {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}


async def search_notion(args: dict) -> str:
    headers = _headers()
    if headers is None:
        return NOT_CONNECTED_MSG
    query = (args.get("query") or "").strip()
    if not query:
        return "I need something to search for in Notion."

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            "https://api.notion.com/v1/search",
            headers=headers, json={"query": query, "page_size": 8},
        )
        if resp.status_code != 200:
            return f"Notion search failed: {resp.status_code} {resp.text[:200]}"
        results = resp.json().get("results", [])

    if not results:
        return f"No Notion pages/databases found matching '{query}' - remember it can only see what's been shared with the integration."

    lines = []
    for r in results:
        title = "(untitled)"
        props = r.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title" and prop.get("title"):
                title = "".join(t.get("plain_text", "") for t in prop["title"])
                break
        lines.append(f"- {title} ({r.get('object', 'item')}, last edited {r.get('last_edited_time', '?')})")
    return f"Found {len(results)} Notion result(s) for '{query}':\n" + "\n".join(lines)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_notion",
            "description": "Search Notion pages and databases sir has shared with Jarvis's integration.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

DISPATCH = {
    "search_notion": search_notion,
}
