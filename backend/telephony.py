import json
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

# Generated TTS clips Telnyx's <Play> fetches by URL. Not popped on read since
# Telnyx may retry the GET; capped in size instead so it can't grow unbounded.
audio_clips: "OrderedDict[str, bytes]" = OrderedDict()


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
    audio_url = f"{cfg['public_base_url']}/api/telephony/audio/{audio_clip_id}"
    action_url = f"{cfg['public_base_url']}/api/telephony/gather"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Gather input="speech" action="{_escape(action_url)}" method="POST" timeout="6" speechTimeout="auto" transcriptionEngine="Deepgram" model="deepgram/nova-3">
        <Play>{_escape(audio_url)}</Play>
    </Gather>
    <Say>I didn't hear anything, goodbye for now.</Say>
</Response>"""


PHONE_PERSONA_INBOUND = (
    "You are on a live phone call right now, not the usual browser dashboard. The person "
    "calling is sir himself, reaching you remotely. Keep replies short and natural to say "
    "aloud in a real-time conversation - this is spoken back-and-forth, not a long-form chat. "
    "If the conversation reaches a natural end, say a clear goodbye."
)


def phone_persona_outbound(contact_name: str | None, opening_message: str) -> str:
    who = contact_name or "the person you are calling"
    return (
        f"You are on a live phone call you placed on sir's behalf, speaking with {who} - "
        "NOT with sir himself. Do not call this person 'sir'. Introduce yourself as Jarvis, "
        "sir's AI assistant, if you haven't already. "
        f"The reason for this call: {opening_message or 'sir asked you to call and see how they are doing'}. "
        "Keep replies short and natural to say aloud in real-time conversation. Be polite and "
        "clear, deliver sir's message, answer reasonable follow-up questions on his behalf, and "
        "say a clear goodbye when the conversation naturally concludes."
    )


async def place_outbound_call(cfg: dict, to_number: str, contact_name: str, opening_message: str) -> dict:
    from urllib.parse import urlencode

    query = urlencode({"contact": contact_name or "", "msg": opening_message or ""})
    url = f"{TELNYX_API_BASE}/texml/Accounts/{cfg['account_sid']}/Calls"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            data={
                "To": to_number,
                "From": cfg["telnyx_number"],
                "ApplicationSid": cfg["application_sid"],
                "Url": f"{cfg['public_base_url']}/api/telephony/outbound_voice?{query}",
            },
        )
        resp.raise_for_status()
        return resp.json()
