"""
"Sir Page": phones the owner and says what happened when something he'd want to
know about occurs - an unknown caller turned away, an allowed contact using
Echo, Desk Guard locking the computer.

This only ever limits how often ECHO RINGS SIR with a status report. It never
limits anyone calling Echo - allowed contacts can call as often and as long as
they like.

The limits are per kind of event, so one noisy kind can't use up the budget
of another: a run of unknown numbers can never stop a Desk Guard alert from
getting through.
"""
import re
import time

import config as config_module
import telephony

# The same event repeating (same number, same contact) isn't re-announced within
# this window. Desk Guard events are the ones that matter most, so they repeat sooner.
COOLDOWN_SECONDS = {"deskguard": 300}
DEFAULT_COOLDOWN_SECONDS = 600

# Only the kinds that strangers can trigger are capped per hour, so nobody can
# make Echo ring sir over and over. Anything left out of this table (Desk Guard
# alerts) is never held back. Calls beyond the cap aren't lost: they are counted
# and mentioned at the start of the next call, and listed on the dashboard.
HOURLY_CAP = {"blocked": 6, "guest": 12}

_last_by_kind: dict[str, float] = {}
_recent_by_category: dict[str, list[float]] = {}
_held_back: dict[str, int] = {}


def spoken_number(number: str) -> str:
    """'+13305551234' -> '3 3 0, 5 5 5, 1 2 3 4' so it's read out digit by digit."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        groups = [digits[:3], digits[3:6], digits[6:]]
    else:
        groups = [digits]
    return ", ".join(" ".join(g) for g in groups) or "a hidden number"


def _held_back_note() -> str:
    total = sum(_held_back.values())
    if not total:
        return ""
    return f" Also, {total} other call{'s' if total != 1 else ''} came in that I didn't ring you about."


async def page_owner(kind: str, message: str) -> bool:
    """Calls the owner's phone and speaks `message`. `kind` is "category:detail"
    (e.g. "blocked:+1330...") - the detail keys the cooldown, the category keys
    the hourly cap. Returns whether a call was actually placed. Never raises."""
    if not config_module.load_config().get("sir_page_enabled"):
        return False

    cfg = telephony.load_config()
    if cfg is None or not cfg.get("owner_number"):
        print("[pager] Sir Page is on but calling isn't configured, so it can't ring your phone.", flush=True)
        return False

    category = kind.split(":", 1)[0]
    now = time.time()
    if now - _last_by_kind.get(kind, 0.0) < COOLDOWN_SECONDS.get(category, DEFAULT_COOLDOWN_SECONDS):
        return False

    cap = HOURLY_CAP.get(category)
    if cap is not None:
        window = _recent_by_category.setdefault(category, [])
        window[:] = [t for t in window if now - t < 3600]
        if len(window) >= cap:
            _held_back[category] = _held_back.get(category, 0) + 1
            print(f"[pager] {category} page limit reached for this hour - holding back {kind}", flush=True)
            return False
        window.append(now)

    _last_by_kind[kind] = now
    full_message = message + _held_back_note()
    try:
        await telephony.notify_owner(cfg, full_message)
    except Exception as exc:  # noqa: BLE001 - a failed page must never break the thing that triggered it
        print(f"[pager] couldn't place the page for {kind}: {exc!r}", flush=True)
        return False
    _held_back.clear()
    print(f"[pager] paged sir: {full_message}", flush=True)
    return True
