import asyncio
import base64
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config as config_module
import memory
import phonebook
import security
import telephony
import tools
import vision

MODEL_NAME = "jarvis"
VOICE = "en-GB-RyanNeural"
OLLAMA_URL = "http://localhost:11434/api/chat"
MAX_HISTORY_MESSAGES = 30
MAX_TOOL_ITERATIONS = 5
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

conversation_history: list[dict] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    guard_task = asyncio.create_task(security.guard_loop())
    yield
    guard_task.cancel()


app = FastAPI(lifespan=lifespan)

ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765"}


@app.middleware("http")
async def block_cross_origin_writes(request, call_next):
    """Blocks the classic 'malicious webpage open in your browser silently POSTs to
    localhost' CSRF attack. A cross-origin request always carries an Origin header
    (per the Fetch spec) that won't match ours; a same-origin request from our own
    frontend, or a direct local tool with no Origin concept at all, is let through."""
    origin = request.headers.get("origin")
    if request.method in ("POST", "PUT", "DELETE") and origin and origin not in ALLOWED_ORIGINS:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked."})
    return await call_next(request)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    audio: str
    mime: str = "audio/mpeg"


class SettingsUpdate(BaseModel):
    ai_enabled: bool | None = None
    screen_access: bool | None = None
    camera_access: bool | None = None
    desk_guard_enabled: bool | None = None
    desk_guard_threshold: float | None = None
    calling_enabled: bool | None = None


async def synthesize(text: str) -> bytes:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=VOICE)
    audio_chunks = bytearray()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.extend(chunk["data"])
    return bytes(audio_chunks)


async def _speak(text: str) -> ChatResponse:
    audio_bytes = await synthesize(text)
    return ChatResponse(reply=text, audio=base64.b64encode(audio_bytes).decode("ascii"))


def _access_status_block(cfg: dict) -> str:
    def state(flag: bool) -> str:
        return "ON - the tool is available, use it" if flag else "OFF - do not claim you looked, say access is off"

    if not telephony.is_configured():
        calling_state = "OFF - calling isn't set up yet, say so if asked"
    elif cfg.get("calling_enabled"):
        calling_state = "ON - call_phone_number tool is available, use it"
    else:
        calling_state = "OFF - sir has calling turned off right now, say so if asked"
    return (
        "Current access status, authoritative for this message (ignore anything said in "
        "earlier turns about this - permissions can change between messages):\n"
        f"- Screen access: {state(cfg.get('screen_access', False))}\n"
        f"- Camera access: {state(cfg.get('camera_access', False))}\n"
        f"- Calling: {calling_state}"
    )


async def run_chat_turn(user_message: str, cfg: dict) -> str:
    conversation_history.append({"role": "user", "content": user_message})

    messages = list(conversation_history)
    status_msg = {"role": "system", "content": _access_status_block(cfg)}
    messages.insert(len(messages) - 1, status_msg)

    context_blocks = [memory.facts_as_prompt_block(), phonebook.as_prompt_block()]
    context_blocks = [b for b in context_blocks if b]
    if context_blocks:
        messages = [{"role": "system", "content": "\n\n".join(context_blocks)}] + messages

    schemas = tools.available_schemas(cfg)

    for _ in range(MAX_TOOL_ITERATIONS):
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={"model": MODEL_NAME, "messages": messages, "tools": schemas, "stream": False},
            )
            resp.raise_for_status()
            data = resp.json()

        message = data["message"]
        messages.append(message)
        conversation_history.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            del conversation_history[:-MAX_HISTORY_MESSAGES]
            return message.get("content", "")

        for call in tool_calls:
            fn = call["function"]
            name = fn["name"]
            args = fn.get("arguments") or {}
            result = await tools.execute_tool(name, args)
            tool_msg = {"role": "tool", "content": result, "name": name}
            messages.append(tool_msg)
            conversation_history.append(tool_msg)

    del conversation_history[:-MAX_HISTORY_MESSAGES]
    return "I've gone through several steps but couldn't reach a final answer just now, sir."


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    cfg = config_module.load_config()
    if not cfg.get("ai_enabled"):
        return await _speak("I'm currently switched off, sir.")

    reply_text = await run_chat_turn(req.message, cfg)
    return await _speak(reply_text)


PHONE_MAX_HISTORY = 20


async def run_phone_turn(call_sid: str, user_text: str, system_prompt: str) -> str:
    history = telephony.call_histories.setdefault(call_sid, [])
    history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": system_prompt}] + history

    for _ in range(MAX_TOOL_ITERATIONS):
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={"model": MODEL_NAME, "messages": messages, "tools": tools.ALWAYS_ON_SCHEMAS, "stream": False},
            )
            resp.raise_for_status()
            data = resp.json()

        message = data["message"]
        messages.append(message)
        history.append(message)

        tool_calls = message.get("tool_calls")
        if not tool_calls:
            del history[:-PHONE_MAX_HISTORY]
            return message.get("content", "")

        for call in tool_calls:
            fn = call["function"]
            name = fn["name"]
            args = fn.get("arguments") or {}
            result = await tools.execute_tool(name, args)
            tool_msg = {"role": "tool", "content": result, "name": name}
            messages.append(tool_msg)
            history.append(tool_msg)

    del history[:-PHONE_MAX_HISTORY]
    return "I'm having some trouble completing that thought. Let's pick this up another time, goodbye."


async def _speak_clip(text: str) -> str:
    audio_bytes = await synthesize(text)
    return telephony.register_audio_clip(audio_bytes)


@app.post("/api/telephony/voice")
async def telephony_voice(request: Request) -> Response:
    cfg = telephony.load_config()
    form = await request.form()
    call_sid = form.get("CallSid", "")

    if cfg is None:
        return Response(
            content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Say>Jarvis calling is not fully configured yet. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )

    if not config_module.load_config().get("calling_enabled"):
        return Response(
            content="<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response><Say>Calling is currently turned off. Goodbye.</Say><Hangup/></Response>",
            media_type="application/xml",
        )

    telephony.call_context[call_sid] = {"direction": "inbound"}
    clip_id = await _speak_clip("Hello, this is Jarvis. How can I help you?")
    return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")


@app.post("/api/telephony/outbound_voice")
async def telephony_outbound_voice(request: Request) -> Response:
    cfg = telephony.load_config()
    form = await request.form()
    call_sid = form.get("CallSid", "")
    contact_name = request.query_params.get("contact", "")
    opening_message = request.query_params.get("msg", "")

    telephony.call_context[call_sid] = {
        "direction": "outbound",
        "contact_name": contact_name,
        "opening_message": opening_message,
    }

    opening = (
        f"Hello, this is Jarvis, calling on behalf of my user. {opening_message}"
        if opening_message
        else "Hello, this is Jarvis, an AI assistant calling on behalf of my user."
    )
    clip_id = await _speak_clip(opening)
    return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")


@app.post("/api/telephony/gather")
async def telephony_gather(request: Request) -> Response:
    cfg = telephony.load_config()
    form = await request.form()
    call_sid = form.get("CallSid", "")
    speech_text = form.get("SpeechResult", "")

    context = telephony.call_context.get(call_sid, {"direction": "inbound"})
    if context["direction"] == "outbound":
        system_prompt = telephony.phone_persona_outbound(
            context.get("contact_name"), context.get("opening_message", "")
        )
    else:
        system_prompt = telephony.PHONE_PERSONA_INBOUND

    if not speech_text:
        clip_id = await _speak_clip("Sorry, I didn't catch that. Could you say it again?")
        return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")

    reply_text = await run_phone_turn(call_sid, speech_text, system_prompt)
    clip_id = await _speak_clip(reply_text)
    return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")


@app.get("/api/telephony/audio/{clip_id}")
async def telephony_audio(clip_id: str) -> Response:
    audio_bytes = telephony.get_audio_clip(clip_id)
    if audio_bytes is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    return Response(content=audio_bytes, media_type="audio/mpeg")


class PhonebookEntry(BaseModel):
    name: str
    number: str


@app.get("/api/phonebook")
async def get_phonebook() -> list[dict]:
    return phonebook.load_entries()


@app.post("/api/phonebook")
async def add_phonebook_entry(entry: PhonebookEntry) -> list[dict]:
    return phonebook.add_entry(entry.name, entry.number)


@app.delete("/api/phonebook/{name}")
async def delete_phonebook_entry(name: str) -> list[dict]:
    return phonebook.delete_entry(name)


@app.post("/api/upload", response_model=ChatResponse)
async def upload(file: UploadFile = File(...)) -> ChatResponse:
    data = await file.read()
    content_type = file.content_type or ""

    if content_type.startswith("image/"):
        description = await vision.describe_image(data)
    elif content_type.startswith("video/"):
        suffix = Path(file.filename or "upload.mp4").suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            description = await vision.summarize_video(tmp_path)
        finally:
            os.unlink(tmp_path)
    else:
        return await _speak("I can only look at images or videos, sir.")

    conversation_history.append({"role": "user", "content": f"[Uploaded a {content_type} file for you to look at.]"})
    conversation_history.append({"role": "assistant", "content": description})
    del conversation_history[:-MAX_HISTORY_MESSAGES]

    return await _speak(description)


@app.post("/api/security/enroll")
async def enroll_face() -> dict:
    return await asyncio.to_thread(security.enroll)


@app.get("/api/security/status")
async def security_status() -> dict:
    return {"known_faces": security.known_face_count()}


@app.get("/api/settings")
async def get_settings() -> dict:
    return config_module.load_config()


ACCESS_KEYS_AFFECTING_HISTORY = ("screen_access", "camera_access", "calling_enabled")


@app.post("/api/settings")
async def set_settings(update: SettingsUpdate) -> dict:
    cfg = config_module.load_config()
    changes = update.model_dump(exclude_none=True)

    if changes.get("desk_guard_enabled") and security.known_face_count() == 0:
        raise HTTPException(status_code=400, detail="Enroll at least one face before enabling Desk Guard.")

    access_changed = any(cfg.get(k) != changes[k] for k in ACCESS_KEYS_AFFECTING_HISTORY if k in changes)

    cfg.update(changes)
    config_module.save_config(cfg)

    if access_changed:
        # Old turns discussing "access is off" would otherwise bias the model into
        # contradicting the new state - a fresh state deserves a fresh conversation.
        conversation_history.clear()

    return cfg


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)