"""
Read-only Gmail search, Calendar lookup, and Drive search, once sir has
connected his Google account from the dashboard's Connections panel (a real
OAuth "Sign in with Google" flow - see google_auth.py). This plugin only
ever reads; sending email, creating events, and writing to Drive are
deliberately not included yet.
"""
from datetime import datetime, timedelta

import httpx

import google_auth

PLUGIN_NAME = "google_workspace"
TOGGLE_LABEL = "Google Workspace (Gmail / Calendar / Drive)"
CONFIG_KEY = "google_workspace_enabled"
ENABLED_BY_DEFAULT = False

NOT_CONNECTED_MSG = (
    "Google isn't connected yet - go to the dashboard's Connections panel and click "
    "'Connect Google Account,' sign in, and grant access, then try again."
)


async def _auth_headers() -> dict | None:
    token = await google_auth.get_valid_access_token()
    if token is None:
        return None
    return {"Authorization": f"Bearer {token}"}


async def search_gmail(args: dict) -> str:
    headers = await _auth_headers()
    if headers is None:
        return NOT_CONNECTED_MSG

    query = (args.get("query") or "").strip()
    if not query:
        return "I need something to search for in Gmail."

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=headers, params={"q": query, "maxResults": 5},
        )
        if resp.status_code != 200:
            return f"Gmail search failed: {resp.status_code} {resp.text[:200]}"
        ids = [m["id"] for m in resp.json().get("messages", [])]

        if not ids:
            return f"No emails found matching '{query}'."

        lines = []
        for msg_id in ids:
            meta = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                headers=headers,
                params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
            )
            if meta.status_code != 200:
                continue
            headers_list = meta.json().get("payload", {}).get("headers", [])
            by_name = {h["name"]: h["value"] for h in headers_list}
            lines.append(f"- From {by_name.get('From', '?')}, subject \"{by_name.get('Subject', '(no subject)')}\", {by_name.get('Date', '?')}")

    if not lines:
        return f"Found matching emails but couldn't read their details for '{query}'."
    return f"Found {len(lines)} email(s) matching '{query}':\n" + "\n".join(lines)


PERIOD_CHOICES = ["today", "this_week", "upcoming", "this_month"]


def _period_window(period: str) -> tuple[datetime, datetime] | None:
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    if period == "today":
        return today_start, today_start + timedelta(days=1)
    if period == "this_week":
        start = today_start - timedelta(days=today_start.weekday())
        return start, start + timedelta(days=7)
    if period == "upcoming":
        return now, now + timedelta(days=7)
    if period == "this_month":
        start = datetime(now.year, now.month, 1)
        next_month = datetime(now.year + (now.month == 12), (now.month % 12) + 1, 1)
        return start, next_month
    return None


async def list_calendar_events(args: dict) -> str:
    headers = await _auth_headers()
    if headers is None:
        return NOT_CONNECTED_MSG

    period = (args.get("period") or "upcoming").strip().lower()
    window = _period_window(period)
    if window is None:
        return f"Unknown period '{period}' - use one of: {', '.join(PERIOD_CHOICES)}."
    start, end = window

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            headers=headers,
            params={
                "timeMin": start.isoformat() + "Z",
                "timeMax": end.isoformat() + "Z",
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 10,
            },
        )
        if resp.status_code != 200:
            return f"Calendar lookup failed: {resp.status_code} {resp.text[:200]}"
        events = resp.json().get("items", [])

    if not events:
        return f"No calendar events found for {period.replace('_', ' ')}."

    lines = []
    for event in events:
        when = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date", "?")
        lines.append(f"- {event.get('summary', '(untitled)')} at {when}")
    return f"Calendar events for {period.replace('_', ' ')}:\n" + "\n".join(lines)


async def search_drive_files(args: dict) -> str:
    headers = await _auth_headers()
    if headers is None:
        return NOT_CONNECTED_MSG

    query = (args.get("query") or "").strip()
    if not query:
        return "I need something to search for in Drive."
    escaped = query.replace("'", "\\'")

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            headers=headers,
            params={"q": f"name contains '{escaped}'", "pageSize": 10, "fields": "files(name,mimeType,modifiedTime,webViewLink)"},
        )
        if resp.status_code != 200:
            return f"Drive search failed: {resp.status_code} {resp.text[:200]}"
        files = resp.json().get("files", [])

    if not files:
        return f"No Drive files found matching '{query}'."
    lines = [f"- {f['name']} (modified {f.get('modifiedTime', '?')})" for f in files]
    return f"Found {len(files)} Drive file(s) matching '{query}':\n" + "\n".join(lines)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_gmail",
            "description": "Search sir's Gmail (read-only) and return matching messages' sender, subject, and date.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Gmail search terms, e.g. 'from:amazon' or 'invoice'"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_calendar_events",
            "description": "List sir's Google Calendar events (read-only) for a time period.",
            "parameters": {
                "type": "object",
                "properties": {"period": {"type": "string", "enum": PERIOD_CHOICES, "description": "Which time period to check"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_drive_files",
            "description": "Search sir's Google Drive (read-only) by filename.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Text to search for in file names"}},
                "required": ["query"],
            },
        },
    },
]

DISPATCH = {
    "search_gmail": search_gmail,
    "list_calendar_events": list_calendar_events,
    "search_drive_files": search_drive_files,
}
