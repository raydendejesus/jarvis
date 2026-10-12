"""
"Sir Page": phones the owner and says what happened when something he'd want to
know about occurs - an unknown caller turned away, an allowed contact using
Echo, Desk Guard locking the computer.

Everything funnels through page_owner(), which is gated by the Sir Page toggle
and rate-limited, so nothing outside can use it to make Echo ring his phone
over and over (a stranger dialing repeatedly, for instance).
"""
import re
import time

import config as config_module
import telephony

COOLDOWN_SECONDS = 600
MAX_PAGES_PER_HOUR = 6

_last_by_kind: dict[str, float] = {}
_recent_pages: list[float] = []


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


async def page_owner(kind: str, message: str) -> bool:
    """Calls the owner's phone and speaks `message`. `kind` keys the cooldown, so
    the same event repeating doesn't ring again within COOLDOWN_SECONDS.
    Returns whether a call was actually placed. Never raises."""
    if not config_module.load_config().get("sir_page_enabled"):
        return False

    cfg = telephony.load_config()
    if cfg is None or not cfg.get("owner_number"):
        print("[pager] Sir Page is on but calling isn't configured, so it can't ring your phone.", flush=True)
        return False

    now = time.time()
    if now - _last_by_kind.get(kind, 0.0) < COOLDOWN_SECONDS:
        return False
    _recent_pages[:] = [t for t in _recent_pages if now - t < 3600]
    if len(_recent_pages) >= MAX_PAGES_PER_HOUR:
        print(f"[pager] hourly page limit reached - not paging for {kind}", flush=True)
        return False

    _last_by_kind[kind] = now
    _recent_pages.append(now)
    try:
        await telephony.notify_owner(cfg, message)
    except Exception as exc:  # noqa: BLE001 - a failed page must never break the thing that triggered it
        print(f"[pager] couldn't place the page for {kind}: {exc!r}", flush=True)
        return False
    print(f"[pager] paged sir: {message}", flush=True)
    return True
