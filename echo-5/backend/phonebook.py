import json
import re
import time
from pathlib import Path

PHONEBOOK_FILE = Path(__file__).resolve().parent / "phonebook.json"
ATTEMPTS_FILE = Path(__file__).resolve().parent / "call_attempts.json"
MAX_ATTEMPTS_KEPT = 50


def _ensure_file() -> None:
    if not PHONEBOOK_FILE.exists():
        PHONEBOOK_FILE.write_text("[]", encoding="utf-8")


def load_entries() -> list[dict]:
    _ensure_file()
    try:
        entries = json.loads(PHONEBOOK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    # Contacts saved before the call-access toggle existed come back as "not
    # allowed to call Echo" - nobody is let in just because they predate it.
    for entry in entries:
        entry["can_call_echo"] = entry.get("can_call_echo") is True
    return entries


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


def _same_number(a: str, b: str) -> bool:
    """Compare on digits, tolerant of a missing/extra country-code prefix."""
    da, db = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    if not da or not db:
        return False
    return da == db or (len(da) >= 10 and len(db) >= 10 and da[-10:] == db[-10:])


def add_entry(name: str, number: str) -> list[dict]:
    normalized = _normalize_number(number) or number
    entries = load_entries()
    previous = next((e for e in entries if e["name"].lower() == name.lower()), None)
    entries = [e for e in entries if e["name"].lower() != name.lower()]
    entries.append({
        "name": name,
        "number": normalized,
        "can_call_echo": previous["can_call_echo"] if previous else False,
    })
    save_entries(entries)
    return entries


def delete_entry(name: str) -> list[dict]:
    entries = load_entries()
    entries = [e for e in entries if e["name"].lower() != name.lower()]
    save_entries(entries)
    return entries


def set_call_access(name: str, allowed: bool) -> list[dict] | None:
    entries = load_entries()
    for entry in entries:
        if entry["name"].lower() == name.lower():
            entry["can_call_echo"] = bool(allowed)
            save_entries(entries)
            return entries
    return None


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


def name_for_number(number: str) -> str | None:
    for entry in load_entries():
        if _same_number(number, entry["number"]):
            return entry["name"]
    return None


def caller_policy(from_number: str, owner_number: str | None) -> dict:
    """Who is this inbound caller, and are they allowed in?

    role "owner"   - the owner's own number: treated as sir, as before.
    role "guest"   - a phone book contact whose call-access toggle is ON.
    role "blocked" - everyone else: a contact with the toggle OFF, or a number
                     that isn't in the phone book at all.
    Caller ID can be spoofed by a determined caller; this stops casual use and
    anyone who was handed the number, not someone impersonating a number."""
    if owner_number and _same_number(from_number, owner_number):
        return {"role": "owner", "name": None, "reason": "owner"}
    for entry in load_entries():
        if _same_number(from_number, entry["number"]):
            if entry["can_call_echo"]:
                return {"role": "guest", "name": entry["name"], "reason": "allowed contact"}
            return {"role": "blocked", "name": entry["name"], "reason": "call access is off for this contact"}
    return {"role": "blocked", "name": None, "reason": "number isn't in the phone book"}


def record_attempt(number: str, name: str | None, reason: str) -> None:
    try:
        attempts = json.loads(ATTEMPTS_FILE.read_text(encoding="utf-8")) if ATTEMPTS_FILE.exists() else []
    except (json.JSONDecodeError, OSError):
        attempts = []
    attempts.append({"time": time.time(), "number": number, "name": name, "reason": reason})
    ATTEMPTS_FILE.write_text(json.dumps(attempts[-MAX_ATTEMPTS_KEPT:], indent=2), encoding="utf-8")


def recent_attempts(limit: int = 10) -> list[dict]:
    try:
        attempts = json.loads(ATTEMPTS_FILE.read_text(encoding="utf-8")) if ATTEMPTS_FILE.exists() else []
    except (json.JSONDecodeError, OSError):
        return []
    return list(reversed(attempts[-limit:]))


def as_prompt_block() -> str:
    entries = load_entries()
    if not entries:
        return "Phone book: empty - no saved contacts yet."
    lines = "\n".join(f"- {e['name']}: {e['number']}" for e in entries)
    return f"Phone book (names sir can ask you to call):\n{lines}"
