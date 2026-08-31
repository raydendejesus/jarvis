"""
Read-only GitHub repo lookups (issues, pull requests, recent commits), once
sir has connected his GitHub account from the dashboard's Connections panel.
See github_auth.py for the connection itself.
"""
import httpx

import github_auth

PLUGIN_NAME = "github_connect"
TOGGLE_LABEL = "GitHub"
CONFIG_KEY = "github_connect_enabled"
ENABLED_BY_DEFAULT = False

NOT_CONNECTED_MSG = (
    "GitHub isn't connected yet - go to the dashboard's Connections panel and click "
    "'Connect GitHub,' sign in, and authorize access, then try again."
)


def _headers() -> dict | None:
    token = github_auth.get_token()
    if token is None:
        return None
    return {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}


async def list_repo_issues(args: dict) -> str:
    headers = _headers()
    if headers is None:
        return NOT_CONNECTED_MSG
    repo = (args.get("repo") or "").strip()
    if "/" not in repo:
        return "I need a repo in 'owner/name' form, e.g. 'raydendejesus/jarvis'."

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/issues",
            headers=headers, params={"state": "open", "per_page": 8},
        )
        if resp.status_code != 200:
            return f"Couldn't look up issues for {repo}: {resp.status_code} {resp.text[:200]}"
        issues = [i for i in resp.json() if "pull_request" not in i]

    if not issues:
        return f"No open issues on {repo}."
    lines = [f"- #{i['number']}: {i['title']} (opened by {i['user']['login']})" for i in issues]
    return f"Open issues on {repo}:\n" + "\n".join(lines)


async def list_repo_pull_requests(args: dict) -> str:
    headers = _headers()
    if headers is None:
        return NOT_CONNECTED_MSG
    repo = (args.get("repo") or "").strip()
    if "/" not in repo:
        return "I need a repo in 'owner/name' form, e.g. 'raydendejesus/jarvis'."

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/pulls",
            headers=headers, params={"state": "open", "per_page": 8},
        )
        if resp.status_code != 200:
            return f"Couldn't look up pull requests for {repo}: {resp.status_code} {resp.text[:200]}"
        prs = resp.json()

    if not prs:
        return f"No open pull requests on {repo}."
    lines = [f"- #{pr['number']}: {pr['title']} (by {pr['user']['login']})" for pr in prs]
    return f"Open pull requests on {repo}:\n" + "\n".join(lines)


async def get_repo_activity(args: dict) -> str:
    headers = _headers()
    if headers is None:
        return NOT_CONNECTED_MSG
    repo = (args.get("repo") or "").strip()
    if "/" not in repo:
        return "I need a repo in 'owner/name' form, e.g. 'raydendejesus/jarvis'."

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(
            f"https://api.github.com/repos/{repo}/commits",
            headers=headers, params={"per_page": 5},
        )
        if resp.status_code != 200:
            return f"Couldn't look up commits for {repo}: {resp.status_code} {resp.text[:200]}"
        commits = resp.json()

    if not commits:
        return f"No recent commits found on {repo}."
    lines = []
    for c in commits:
        msg = c["commit"]["message"].splitlines()[0]
        author = c["commit"]["author"]["name"]
        lines.append(f"- {msg} ({author})")
    return f"Recent commits on {repo}:\n" + "\n".join(lines)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_repo_issues",
            "description": "List open issues on a GitHub repo sir has access to.",
            "parameters": {
                "type": "object",
                "properties": {"repo": {"type": "string", "description": "Repo in 'owner/name' form"}},
                "required": ["repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_repo_pull_requests",
            "description": "List open pull requests on a GitHub repo sir has access to.",
            "parameters": {
                "type": "object",
                "properties": {"repo": {"type": "string", "description": "Repo in 'owner/name' form"}},
                "required": ["repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_repo_activity",
            "description": "Get the most recent commits on a GitHub repo sir has access to.",
            "parameters": {
                "type": "object",
                "properties": {"repo": {"type": "string", "description": "Repo in 'owner/name' form"}},
                "required": ["repo"],
            },
        },
    },
]

DISPATCH = {
    "list_repo_issues": list_repo_issues,
    "list_repo_pull_requests": list_repo_pull_requests,
    "get_repo_activity": get_repo_activity,
}
