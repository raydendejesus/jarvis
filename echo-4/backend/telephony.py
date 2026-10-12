import json
import time
import uuid
import xml.sax.saxutils as xml_sax
from collections import OrderedDict
from pathlib import Path

import httpx

CONFIG_FILE = Path(__file__).resolve().parent / "telephony_config.json"
TELNYX_API_BASE = "https://api.telnyx.com/v2"
REQUIRED_KEYS = ("api_key", "telnyx_number", "public_base_url", "account_sid", "application_sid")
MAX_AUDIO_CLIPS = 50

# Per-call state, keyed by Telnyx's CallSid. Personal-scale call volume, so no
# eviction beyond the natural fact that a phone number only has so many calls.
call_histories: dict[str, list[dict]] = {}
call_context: dict[str, dict] = {}
call_last_response_at: dict[str, float] = {}
MAX_SILENCE_SECONDS = 5 * 60

# Lets a later "hang up on <name>" command find the right in-progress call.
active_outbound_calls: dict[str, str] = {}  # lowercased contact key -> CallSid

# Generated TTS clips Telnyx's <Play> fetches by URL. Not popped on read since
# Telnyx may retry the GET; capped in size instead so it can't grow unbounded.
audio_clips: "OrderedDict[str, bytes]" = OrderedDict()


# --- hang-ups ------------------------------------------------------------------------
# A caller who hangs up mid-reply must not keep the brain, the voice engine - and the
# next caller's turn - busy generating something nobody will ever hear.
TERMINAL_CALL_STATUSES = {"completed", "busy", "no-answer", "failed", "canceled", "cancelled"}
MAX_ENDED_CALLS_REMEMBERED = 200
ended_calls: "OrderedDict[str, float]" = OrderedDict()
inflight_work: dict[str, set] = {}  # CallSid -> asyncio tasks currently generating for that call


def mark_call_ended(call_sid: str) -> None:
    """The caller is gone: remember it, and stop anything still being generated for them."""
    if not call_sid:
        return
    ended_calls[call_sid] = time.time()
    while len(ended_calls) > MAX_ENDED_CALLS_REMEMBERED:
        ended_calls.popitem(last=False)
    for task in list(inflight_work.get(call_sid, ())):
        task.cancel()


def call_has_ended(call_sid: str) -> bool:
    return call_sid in ended_calls


def forget_call(call_sid: str) -> None:
    for store in (call_context, call_last_response_at, call_histories):
        store.pop(call_sid, None)
    inflight_work.pop(call_sid, None)
    for key in [k for k, v in active_outbound_calls.items() if v == call_sid]:
        del active_outbound_calls[key]


# Telnyx rate-limits its call-status endpoint hard, and the same API key places calls and
# rings sir - so confirming a hang-up this way is deliberately slow, shared across every
# call, and backs off completely the moment Telnyx pushes back.
CALL_STATUS_MIN_INTERVAL_SECONDS = 3.0
CALL_STATUS_BACKOFF_SECONDS = 20.0
_last_status_fetch = 0.0
_status_backoff_until = 0.0


async def fetch_call_status(cfg: dict, call_sid: str, force: bool = False) -> str | None:
    """Telnyx's own view of a call ("in-progress", "completed", ...), or None when unknown
    or when asking would be too soon."""
    global _last_status_fetch, _status_backoff_until
    now = time.monotonic()
    if now < _status_backoff_until:
        return None
    if not force and now - _last_status_fetch < CALL_STATUS_MIN_INTERVAL_SECONDS:
        return None
    _last_status_fetch = now
    url = f"{TELNYX_API_BASE}/texml/Accounts/{cfg['account_sid']}/Calls/{call_sid}"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {cfg['api_key']}"})
    except httpx.HTTPError:
        return None
    if resp.status_code == 429:
        _status_backoff_until = now + CALL_STATUS_BACKOFF_SECONDS
        return None
    if resp.status_code != 200:
        return None
    try:
        return str(resp.json().get("status", "")).strip().lower() or None
    except ValueError:
        return None


def load_config() -> dict | None:
    if not CONFIG_FILE.exists():
        return None
    try:
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if any(not cfg.get(k) or str(cfg.get(k)).startswith("PASTE_") for k in REQUIRED_KEYS):
        return None
    return cfg


def is_configured() -> bool:
    return load_config() is not None


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def register_audio_clip(audio_bytes: bytes) -> str:
    clip_id = uuid.uuid4().hex
    audio_clips[clip_id] = audio_bytes
    while len(audio_clips) > MAX_AUDIO_CLIPS:
        audio_clips.popitem(last=False)
    return clip_id


def get_audio_clip(clip_id: str) -> bytes | None:
    return audio_clips.get(clip_id)


def _escape(text: str) -> str:
    return xml_sax.escape(text)


def gather_texml(cfg: dict, audio_clip_id: str) -> str:
    """Play is deliberately a SIBLING before Gather, not nested inside it. Nesting
    a Play inside Gather enables barge-in (listening starts *while* it's still
    talking), which was causing Echo to cut people off mid-sentence, or even
    pick up its own voice bleed and interrupt itself. This way it fully finishes
    speaking before it starts listening."""
    audio_url = f"{cfg['public_base_url']}/api/telephony/audio/{audio_clip_id}"
    action_url = f"{cfg['public_base_url']}/api/telephony/gather"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{_escape(audio_url)}</Play>
    <Gather input="speech" action="{_escape(action_url)}" method="POST" timeout="6" speechTimeout="auto" transcriptionEngine="Deepgram" model="deepgram/nova-3" />
    <Say>I didn't hear anything, goodbye for now.</Say>
</Response>"""


def say_and_hangup_texml(cfg: dict, audio_clip_id: str) -> str:
    audio_url = f"{cfg['public_base_url']}/api/telephony/audio/{audio_clip_id}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{_escape(audio_url)}</Play>
    <Hangup/>
</Response>"""


PHONE_PERSONA_INBOUND = (
    "You are on a live phone call right now, not the usual browser dashboard. The person "
    "calling is sir himself, reaching you remotely. Keep replies short and natural to say "
    "aloud in a real-time conversation - this is spoken back-and-forth, not a long-form chat. "
    "If the conversation reaches a natural end, say a clear goodbye."
)


OWNER_NICKNAME_TO_OTHERS = "Dr. Mystario"

# Allowed contacts can call as often and as long as they like; this is only a
# ceiling so a call that was left open doesn't run forever.
GUEST_MAX_CALL_SECONDS = 2 * 60 * 60


def phone_persona_guest(caller_name: str) -> str:
    return (
        f"You are on a live phone call. The caller is {caller_name}, a contact that "
        f"{OWNER_NICKNAME_TO_OTHERS} has given permission to talk to you - they are NOT "
        f"{OWNER_NICKNAME_TO_OTHERS}, so never call them 'sir'. You are Echo, "
        f"{OWNER_NICKNAME_TO_OTHERS}'s AI assistant. You can chat and help with general questions "
        f"and look things up on the web. You have no access to {OWNER_NICKNAME_TO_OTHERS}'s private "
        "information, accounts, files, messages, contacts, memories or devices, and you must never "
        f"share anything personal about {OWNER_NICKNAME_TO_OTHERS} or act on his behalf (placing "
        "calls, controlling the computer, sending messages); if asked, politely say that isn't "
        "something you can do for them. Keep replies short and natural to say aloud in a real-time "
        "conversation, and say a clear goodbye when the conversation naturally concludes."
    )


def phone_persona_outbound(contact_name: str | None, opening_message: str) -> str:
    who = contact_name or "the person you are calling"
    return (
        f"You are on a live phone call you placed on {OWNER_NICKNAME_TO_OTHERS}'s behalf, speaking "
        f"with {who} - NOT with {OWNER_NICKNAME_TO_OTHERS} himself. Do not call this person 'sir' - "
        f"that's reserved for when you're speaking directly with {OWNER_NICKNAME_TO_OTHERS}. Refer to "
        f"him by his name, {OWNER_NICKNAME_TO_OTHERS}, when talking about him to this person. You've "
        "already introduced yourself as Echo at the very start of this call - do not introduce "
        "yourself again. "
        f"The reason for this call: {opening_message or f'{OWNER_NICKNAME_TO_OTHERS} asked you to call and see how they are doing'}. "
        "Keep replies short and natural to say aloud in real-time conversation. Be polite and "
        f"clear, deliver {OWNER_NICKNAME_TO_OTHERS}'s message, answer reasonable follow-up questions "
        "on his behalf, and say a clear goodbye when the conversation naturally concludes."
    )


def outbound_opening(contact_name: str, opening_message: str, greeting: str, to_owner: bool) -> str:
    """The first thing said when an outbound call is answered. One definition, so the
    audio rendered before dialing is always exactly what is spoken on answer."""
    if to_owner:
        return f"Hello, sir. {opening_message}" if opening_message else "Hello, sir - it's Echo."
    if greeting:
        return greeting
    hello = f"Hi {contact_name}, this is Echo" if contact_name else "Hello, this is Echo"
    if opening_message:
        return f"{hello}, calling on behalf of {OWNER_NICKNAME_TO_OTHERS}. {opening_message}"
    return f"{hello}, an AI assistant calling on behalf of {OWNER_NICKNAME_TO_OTHERS}."


async def place_outbound_call(
    cfg: dict, to_number: str, contact_name: str, opening_message: str,
    greeting: str = "", clip_id: str = "",
) -> dict:
    from urllib.parse import urlencode

    query = urlencode({
        "contact": contact_name or "", "msg": opening_message or "",
        "greeting": greeting or "", "clip": clip_id or "",
    })
    url = f"{TELNYX_API_BASE}/texml/Accounts/{cfg['account_sid']}/Calls"
    async with httpx.AsyncClient(timeout=40) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            data={
                "To": to_number,
                "From": cfg["telnyx_number"],
                "ApplicationSid": cfg["application_sid"],
                "Url": f"{cfg['public_base_url']}/api/telephony/outbound_voice?{query}",
                "StatusCallback": f"{cfg['public_base_url']}/api/telephony/status?{query}",
                "StatusCallbackEvent": "initiated ringing answered completed",
                "MachineDetection": "Enable",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    call_sid = data.get("sid")
    if call_sid:
        for key in (contact_name, to_number):
            if key:
                active_outbound_calls[key.strip().lower()] = call_sid
    return data


def find_active_call_sid(name_or_number: str) -> str | None:
    return active_outbound_calls.get(name_or_number.strip().lower())


async def end_call(cfg: dict, call_sid: str) -> None:
    url = f"{TELNYX_API_BASE}/texml/Accounts/{cfg['account_sid']}/Calls/{call_sid}"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            data={"Status": "completed"},
        )
        resp.raise_for_status()


async def notify_owner(cfg: dict, message: str) -> None:
    """A one-way call to tell sir something (e.g. an outbound call went unanswered) -
    no StatusCallback attached, deliberately, so a missed notification call can't
    chain into notifying about itself."""
    owner_number = cfg.get("owner_number")
    if not owner_number:
        return

    from urllib.parse import urlencode

    query = urlencode({"msg": message})
    url = f"{TELNYX_API_BASE}/texml/Accounts/{cfg['account_sid']}/Calls"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            data={
                "To": owner_number,
                "From": cfg["telnyx_number"],
                "ApplicationSid": cfg["application_sid"],
                "Url": f"{cfg['public_base_url']}/api/telephony/notify_voice?{query}",
            },
        )
        resp.raise_for_status()
