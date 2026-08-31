"""
Example plugin - also a genuinely useful one. Logs expenses sir mentions and
answers spending-summary questions ("how much did we spend last month").

This file is the reference example for writing a new plugin: see
plugin_loader.py's module docstring for the full interface. The short
version - PLUGIN_NAME, CONFIG_KEY, ENABLED_BY_DEFAULT, SCHEMAS, DISPATCH -
is all that's required; nothing outside this file needs to change.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

PLUGIN_NAME = "billing_tracker"
TOGGLE_LABEL = "Billing Tracker"
CONFIG_KEY = "billing_tracker_enabled"
ENABLED_BY_DEFAULT = False

DATA_FILE = Path(__file__).resolve().parent.parent / "plugins_data" / "billing.json"


def _load() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save(entries: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


async def log_expense(args: dict) -> str:
    amount = args.get("amount")
    if not isinstance(amount, (int, float)):
        return "I need a numeric amount to log an expense."
    category = (args.get("category") or "uncategorized").strip().lower()
    description = (args.get("description") or "").strip()

    entries = _load()
    entries.append({
        "amount": round(float(amount), 2),
        "category": category,
        "description": description,
        "timestamp": datetime.now().isoformat(),
    })
    _save(entries)
    return f"Logged ${amount:.2f} under '{category}'" + (f" - {description}" if description else "") + "."


PERIOD_CHOICES = ["today", "this_week", "this_month", "last_month", "all_time"]


def _period_range(period: str) -> tuple[datetime, datetime] | None:
    now = datetime.now()
    today_start = datetime(now.year, now.month, now.day)

    if period == "today":
        return today_start, now
    if period == "this_week":
        return today_start - timedelta(days=today_start.weekday()), now
    if period == "this_month":
        return datetime(now.year, now.month, 1), now
    if period == "last_month":
        first_of_this_month = datetime(now.year, now.month, 1)
        last_month_end = first_of_this_month - timedelta(seconds=1)
        return datetime(last_month_end.year, last_month_end.month, 1), first_of_this_month
    if period == "all_time":
        return datetime.min, now
    return None


async def get_spending_summary(args: dict) -> str:
    period = (args.get("period") or "this_month").strip().lower()
    date_range = _period_range(period)
    if date_range is None:
        return f"Unknown period '{period}' - use one of: {', '.join(PERIOD_CHOICES)}."
    start, end = date_range

    matching = []
    for entry in _load():
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
        except (KeyError, ValueError):
            continue
        if start <= ts <= end:
            matching.append(entry)

    if not matching:
        return f"No expenses logged for {period.replace('_', ' ')}."

    total = sum(entry["amount"] for entry in matching)
    by_category: dict[str, float] = {}
    for entry in matching:
        by_category[entry["category"]] = by_category.get(entry["category"], 0.0) + entry["amount"]
    breakdown = "; ".join(f"{cat}: ${amt:.2f}" for cat, amt in sorted(by_category.items(), key=lambda kv: -kv[1]))

    return f"Total spent {period.replace('_', ' ')}: ${total:.2f} ({len(matching)} entries). By category: {breakdown}."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "log_expense",
            "description": "Log an expense sir tells you about, for later spending summaries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "The dollar amount spent"},
                    "category": {"type": "string", "description": "A short category, e.g. 'groceries', 'gas', 'subscriptions'"},
                    "description": {"type": "string", "description": "Optional short note about what it was for"},
                },
                "required": ["amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spending_summary",
            "description": "Get a total and category breakdown of logged expenses for a time period.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "enum": PERIOD_CHOICES, "description": "Which time period to summarize"},
                },
                "required": [],
            },
        },
    },
]

DISPATCH = {
    "log_expense": log_expense,
    "get_spending_summary": get_spending_summary,
}
