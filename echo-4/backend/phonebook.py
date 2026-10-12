import json
import re
from pathlib import Path

PHONEBOOK_FILE = Path(__file__).resolve().parent / "phonebook.json"


def _ensure_file() -> None:
    if not PHONEBOOK_FILE.exists():
        PHONEBOOK_FILE.write_text("[]", encoding="utf-8")


def load_entries() -> list[dict]:
    _ensure_file()
    try:
        return json.loads(PHONEBOOK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_entries(entries: list[dict]) -> None:
    PHONEBOOK_FILE.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def _normalize_number(raw: str) -> str | None:
    """Telnyx's API requires strict E.164 (+1XXXXXXXXXX) - reject anything that
    doesn't look like a real phone number rather than passing junk through."""
    if not re.fullmatch(r"\+?[0-9()\-.\s]{7,}", raw):
        return None
    digits = re.sub(r"[^0-9+]", "", raw)
    if not digits.startswith("+"):
        digits = "+1" + digits.lstrip("1") if len(digits) == 10 else "+" + digits
    return digits


def add_entry(name: str, number: str) -> list[dict]:
    normalized = _normalize_number(number) or number
    entries = load_entries()
    entries = [e for e in entries if e["name"].lower() != name.lower()]
    entries.append({"name": name, "number": normalized})
    save_entries(entries)
    return entries


def delete_entry(name: str) -> list[dict]:
    entries = load_entries()
    entries = [e for e in entries if e["name"].lower() != name.lower()]
    save_entries(entries)
    return entries


def find_number(name_or_number: str) -> str | None:
    """Resolve a phonebook name to a number, or pass through anything that already looks like a number."""
    direct = _normalize_number(name_or_number)
    if direct:
        return direct

    target = name_or_number.strip().lower()
    for entry in load_entries():
        if entry["name"].strip().lower() == target:
            return _normalize_number(entry["number"]) or entry["number"]
    for entry in load_entries():
        if target in entry["name"].strip().lower():
            return _normalize_number(entry["number"]) or entry["number"]
    return None


def as_prompt_block() -> str:
    entries = load_entries()
    if not entries:
        return "Phone book: empty - no saved contacts yet."
    lines = "\n".join(f"- {e['name']}: {e['number']}" for e in entries)
    return f"Phone book (names sir can ask you to call):\n{lines}"
