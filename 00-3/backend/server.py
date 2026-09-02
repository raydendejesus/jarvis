import asyncio
import base64
import os
import re
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import browser_control
import canvas_state
import config as config_module
import discord_auth
import github_auth
import google_auth
import location
import memory
import notion_auth
import phonebook
import plugin_loader
import security
import telephony
import tools
import vision

MODEL_NAME = "jarvis"
VOICE = "en-GB-RyanNeural"
OLLAMA_URL = "http://localhost:11434/api/chat"
MAX_HISTORY_MESSAGES = 30
MAX_TOOL_ITERATIONS = 8
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

conversation_history: list[dict] = []
chat_turn_busy = False

# The native background listener (listener.py) runs in a separate process
# (inside JARVIS_TRAY.pyw, not this server) and talks to the dashboard's own
# browser tab only through /api/chat - the dashboard has no way to know a
# native-listener conversation is even happening, so its "thinking"/"speaking"
# indicator and transcript log went dead the moment the dashboard's own mic
# became opt-in/off-by-default. listener.py reports its own phase changes
# here; the dashboard polls it to stay in sync with conversations it isn't
# actually driving itself.
listener_status = {"seq": 0, "phase": "idle", "text": ""}


@asynccontextmanager
async def lifespan(app: FastAPI):
    guard_task = asyncio.create_task(security.guard_loop())
    yield
    guard_task.cancel()


app = FastAPI(lifespan=lifespan)

ALLOWED_ORIGINS = {"http://127.0.0.1:8765", "http://localhost:8765"}


def _is_extension_origin(origin: str | None) -> bool:
    # A chrome-extension:// origin can only ever be sent by an actual installed
    # browser extension - a malicious webpage cannot forge this scheme - so it's
    # safe to trust for the Jarvis browser-control extension's own requests.
    return bool(origin) and origin.startswith("chrome-extension://")


@app.middleware("http")
async def browser_extension_cors(request, call_next):
    """The browser extension's requests come from a chrome-extension:// origin
    targeting 127.0.0.1, which Chrome treats as a cross-origin request into a
    'private' address needing an explicit Private Network Access approval on
    top of ordinary CORS - without answering both, Chrome holds the request
    open indefinitely rather than ever actually sending it or erroring, which
    otherwise looks like a silent, unexplained hang with nothing in any log."""
    origin = request.headers.get("origin")
    if not _is_extension_origin(origin):
        return await call_next(request)

    if request.method == "OPTIONS":
        from fastapi.responses import Response as PlainResponse
        return PlainResponse(status_code=204, headers={
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Private-Network": "true",
        })

    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.middleware("http")
async def block_cross_origin_writes(request, call_next):
    """Blocks the classic 'malicious webpage open in your browser silently POSTs to
    localhost' CSRF attack. A cross-origin request always carries an Origin header
    (per the Fetch spec) that won't match ours; a same-origin request from our own
    frontend, or a direct local tool with no Origin concept at all, is let through."""
    origin = request.headers.get("origin")
    if request.method in ("POST", "PUT", "DELETE") and origin and origin not in ALLOWED_ORIGINS and not _is_extension_origin(origin):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": "Cross-origin request blocked."})
    return await call_next(request)


@app.middleware("http")
async def no_cache_frontend_files(request, call_next):
    """This project's frontend files get edited constantly during development
    and the browser's default caching repeatedly served a stale copy after a
    normal refresh, making it look like a change hadn't taken effect when it
    actually had - force every non-API GET to always be refetched fresh."""
    response = await call_next(request)
    if request.method == "GET" and not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    audio: str
    mime: str = "audio/mpeg"
    canvas_html: str | None = None
    canvas_title: str | None = None


class SettingsUpdate(BaseModel):
    ai_enabled: bool | None = None
    screen_access: bool | None = None
    camera_access: bool | None = None
    desk_guard_enabled: bool | None = None
    desk_guard_threshold: float | None = None
    calling_enabled: bool | None = None
    call_notifications_enabled: bool | None = None
    location_mode: str | None = None
    browser_control_enabled: bool | None = None
    browser_pixel_fallback_enabled: bool | None = None
    code_canvas_enabled: bool | None = None


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


async def unload_model(model_name: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(OLLAMA_URL, json={"model": model_name, "messages": [], "keep_alive": 0})
    except Exception as exc:  # noqa: BLE001 - best-effort, never block a toggle on this
        print(f"[server] failed to unload {model_name}: {exc}")


async def unload_ollama_models() -> None:
    """Models are kept loaded indefinitely (keep_alive=-1) for fast replies while
    Jarvis is on, which means they'd otherwise sit in VRAM forever even switched
    off. Explicitly unload both the text and vision models when turned off."""
    for model_name in (MODEL_NAME, vision.VISION_MODEL):
        await unload_model(model_name)


def _access_status_block(cfg: dict) -> str:
    def state(flag: bool) -> str:
        return "ON - the tool is available, use it" if flag else "OFF - do not claim you looked, say access is off"

    if not telephony.is_configured():
        calling_state = "OFF - calling isn't set up yet, say so if asked"
    elif cfg.get("calling_enabled"):
        calling_state = "ON - call_phone_number tool is available, use it"
    else:
        calling_state = "OFF - sir has calling turned off right now, say so if asked"
    browser_connected = browser_control.is_connected()
    if not cfg.get("browser_control_enabled"):
        browser_state = "OFF - do not claim you can act in a browser, say the capability is off"
    elif not browser_connected:
        browser_state = "ON but no browser tab is currently connected - say so if asked to do something there"
    else:
        browser_state = "ON and connected - browser_scan_page/browser_click/browser_type/browser_scroll are available"

    return (
        "Current access status, authoritative for this message (ignore anything said in "
        "earlier turns about this - permissions can change between messages):\n"
        f"- Screen access: {state(cfg.get('screen_access', False))}\n"
        f"- Camera access: {state(cfg.get('camera_access', False))}\n"
        f"- Calling: {calling_state}\n"
        f"- Browser control: {browser_state}"
    )


_HTML_FENCE_RE = re.compile(r"```(?:html)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_HTML_DOC_RE = re.compile(r"(<!DOCTYPE html.*?</html>|<html[\s>].*?</html>)", re.DOTALL | re.IGNORECASE)


def _extract_embedded_html(text: str) -> str | None:
    """Even with explicit instructions and the tool available, the model sometimes
    describes/dumps a full HTML document as chat text instead of actually calling
    write_canvas_code - when that happens the canvas silently never updates, and
    worse, the raw markup would get read aloud verbatim by text-to-speech. This is
    a safety net, not the primary path: salvage the HTML into the canvas anyway."""
    for fence_match in _HTML_FENCE_RE.finditer(text):
        candidate = fence_match.group(1).strip()
        if re.search(r"<html[\s>]", candidate, re.IGNORECASE):
            return candidate
    doc_match = _HTML_DOC_RE.search(text)
    if doc_match:
        return doc_match.group(1).strip()
    return None


async def run_chat_turn(user_message: str, cfg: dict) -> str:
    conversation_history.append({"role": "user", "content": user_message})

    messages = list(conversation_history)
    status_msg = {"role": "system", "content": _access_status_block(cfg)}
    messages.insert(len(messages) - 1, status_msg)

    context_blocks = [memory.facts_as_prompt_block(), phonebook.as_prompt_block()]
    if cfg.get("code_canvas_enabled"):
        context_blocks.append(canvas_state.as_prompt_block())
    context_blocks = [b for b in context_blocks if b]
    if context_blocks:
        messages = [{"role": "system", "content": "\n\n".join(context_blocks)}] + messages

    schemas = tools.available_schemas(cfg)

    for _ in range(MAX_TOOL_ITERATIONS):
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={"model": MODEL_NAME, "messages": messages, "tools": schemas, "stream": False, "keep_alive": -1},
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
        # Properly silent, not a spoken "I'm off" - an off switch that still
        # visibly reacts to you doesn't feel like it's actually off.
        return ChatResponse(reply="", audio="")

    global chat_turn_busy
    if chat_turn_busy:
        # The browser dashboard's own speech recognition and the native
        # background listener can both pick up the same spoken utterance at
        # once, each firing its own independent /api/chat call - without this,
        # that produced two full, separately-executed replies (two overlapping
        # voices, and for browser-control requests, duplicated real actions).
        # Only ever run one turn at a time; silently drop whichever second
        # copy of the same utterance shows up while the first is in progress.
        return ChatResponse(reply="", audio="")

    chat_turn_busy = True
    try:
        canvas_before = canvas_state.get_updated_at()
        reply_text = await run_chat_turn(req.message, cfg)

        if cfg.get("code_canvas_enabled") and canvas_state.get_updated_at() == canvas_before:
            embedded_html = _extract_embedded_html(reply_text)
            if embedded_html:
                canvas_state.set_content(embedded_html, canvas_state.get().get("title") or "")
                reply_text = "I've updated the canvas - take a look."

        response = await _speak(reply_text)
        if canvas_state.get_updated_at() != canvas_before:
            canvas = canvas_state.get()
            response.canvas_html = canvas["html"]
            response.canvas_title = canvas.get("title") or None
        return response
    finally:
        chat_turn_busy = False


@app.get("/api/canvas")
async def get_canvas() -> dict:
    return canvas_state.get()


class ListenerEvent(BaseModel):
    phase: str
    text: str = ""


@app.post("/api/listener/event")
async def report_listener_event(ev: ListenerEvent) -> dict:
    global listener_status
    listener_status = {"seq": listener_status["seq"] + 1, "phase": ev.phase, "text": ev.text}
    return {"ok": True}


@app.get("/api/listener/event")
async def get_listener_event() -> dict:
    return listener_status


PHONE_MAX_HISTORY = 20


async def run_phone_turn(call_sid: str, user_text: str, system_prompt: str, phone_tools: list[dict]) -> str:
    history = telephony.call_histories.setdefault(call_sid, [])
    history.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": system_prompt}] + history

    for _ in range(MAX_TOOL_ITERATIONS):
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                OLLAMA_URL,
                json={"model": MODEL_NAME, "messages": messages, "tools": phone_tools, "stream": False, "keep_alive": -1},
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
    telephony.call_last_response_at[call_sid] = time.time()
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
    telephony.call_last_response_at[call_sid] = time.time()

    dialed_number = form.get("To", "")
    calling_owner = cfg and dialed_number and dialed_number == cfg.get("owner_number")

    if calling_owner:
        opening = f"Hello, sir. {opening_message}" if opening_message else "Hello, sir - it's Jarvis."
    else:
        opening = (
            f"Hello, this is Jarvis, calling on behalf of {telephony.OWNER_NICKNAME_TO_OTHERS}. {opening_message}"
            if opening_message
            else f"Hello, this is Jarvis, an AI assistant calling on behalf of {telephony.OWNER_NICKNAME_TO_OTHERS}."
        )
    # Recorded as something Jarvis already said, so the model doesn't re-introduce
    # itself again on the first real reply - it otherwise has no way of knowing
    # this opening line already happened.
    telephony.call_histories.setdefault(call_sid, []).append({"role": "assistant", "content": opening})

    clip_id = await _speak_clip(opening)
    return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")


@app.post("/api/telephony/gather")
async def telephony_gather(request: Request) -> Response:
    cfg = telephony.load_config()
    form = await request.form()
    call_sid = form.get("CallSid", "")
    speech_text = form.get("SpeechResult", "")

    context = telephony.call_context.get(call_sid, {"direction": "inbound"})
    dialed_number = form.get("To", "")
    is_owner = cfg and dialed_number and dialed_number == cfg.get("owner_number")

    if context["direction"] == "outbound" and not is_owner:
        system_prompt = telephony.phone_persona_outbound(
            context.get("contact_name"), context.get("opening_message", "")
        )
        phone_tools = tools.PHONE_TOOLS_OUTBOUND
    else:
        system_prompt = telephony.PHONE_PERSONA_INBOUND
        phone_tools = tools.PHONE_TOOLS_INBOUND

    last_response = telephony.call_last_response_at.get(call_sid, time.time())
    silent_for = time.time() - last_response

    if not speech_text:
        if silent_for > telephony.MAX_SILENCE_SECONDS:
            clip_id = await _speak_clip("I haven't heard anything in a while, so I'll let you go. Goodbye.")
            return Response(content=telephony.say_and_hangup_texml(cfg, clip_id), media_type="application/xml")
        clip_id = await _speak_clip("Sorry, I didn't catch that. Could you say it again?")
        return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")

    telephony.call_last_response_at[call_sid] = time.time()
    reply_text = await run_phone_turn(call_sid, speech_text, system_prompt, phone_tools)
    clip_id = await _speak_clip(reply_text)
    return Response(content=telephony.gather_texml(cfg, clip_id), media_type="application/xml")


UNANSWERED_STATUSES = {"busy", "no-answer", "failed", "canceled"}
NOT_HUMAN_ANSWERED_BY = {"machine", "fax", "unknown"}
notified_call_sids: set[str] = set()


@app.post("/api/telephony/status")
async def telephony_status(request: Request) -> Response:
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    answered_by = form.get("AnsweredBy", "")
    contact = request.query_params.get("contact", "")

    if call_status in ("completed", *UNANSWERED_STATUSES):
        stale_keys = [k for k, v in telephony.active_outbound_calls.items() if v == call_sid]
        for k in stale_keys:
            del telephony.active_outbound_calls[k]

    unanswered = call_status in UNANSWERED_STATUSES or answered_by in NOT_HUMAN_ANSWERED_BY
    notifications_on = config_module.load_config().get("call_notifications_enabled", False)

    if unanswered and notifications_on and call_sid and call_sid not in notified_call_sids:
        notified_call_sids.add(call_sid)
        who = contact or form.get("To", "the number")
        reason = "went to voicemail" if answered_by in NOT_HUMAN_ANSWERED_BY else f"wasn't answered ({call_status})"
        note = f"Tried calling {who} on sir's behalf, but it {reason}."
        memory.add_fact(note)

        cfg = telephony.load_config()
        if cfg:
            message = f"Sir, I tried calling {who} for you, but {reason.replace('sir', 'you')}."
            try:
                await telephony.notify_owner(cfg, message)
            except Exception as exc:  # noqa: BLE001 - never let a notification failure break the webhook
                print(f"[status] failed to notify owner: {exc}")

    return Response(content="", status_code=200)


@app.post("/api/telephony/notify_voice")
async def telephony_notify_voice(request: Request) -> Response:
    cfg = telephony.load_config()
    message = request.query_params.get("msg", "I have an update for you, sir.")
    clip_id = await _speak_clip(message)
    return Response(content=telephony.say_and_hangup_texml(cfg, clip_id), media_type="application/xml")


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


class LocationUpdate(BaseModel):
    latitude: float
    longitude: float
    label: str | None = None


@app.post("/api/location")
async def report_location(update: LocationUpdate) -> dict:
    cfg = config_module.load_config()
    if cfg.get("location_mode", "off") == "off":
        raise HTTPException(status_code=403, detail="Location access is currently off.")
    location.set_location(update.latitude, update.longitude, update.label)
    return {"ok": True}


class BrowserResultReport(BaseModel):
    ok: bool
    data: dict | None = None
    error: str | None = None


@app.get("/api/browser/poll")
async def browser_poll() -> dict:
    """Long-polled continuously by the Jarvis browser extension's background
    script. Blocks until a tool call queues an action, or returns empty after
    a while so the extension can simply poll again."""
    command = await browser_control.wait_for_command()
    return {"command": command}


@app.post("/api/browser/report")
async def browser_report(report: BrowserResultReport) -> dict:
    """Called by the extension once it's finished (or failed) executing the
    action it was just given."""
    browser_control.submit_result(report.model_dump())
    return {"ok": True}


@app.get("/api/settings")
async def get_settings() -> dict:
    return config_module.load_config()


ACCESS_KEYS_AFFECTING_HISTORY = ("screen_access", "camera_access", "calling_enabled", "browser_control_enabled", "code_canvas_enabled")
VALID_LOCATION_MODES = {"off", "pc", "phone"}


@app.post("/api/settings")
async def set_settings(update: SettingsUpdate) -> dict:
    cfg = config_module.load_config()
    changes = update.model_dump(exclude_none=True)

    if changes.get("desk_guard_enabled") and security.known_face_count() == 0:
        raise HTTPException(status_code=400, detail="Enroll at least one face before enabling Desk Guard.")

    if "location_mode" in changes and changes["location_mode"] not in VALID_LOCATION_MODES:
        raise HTTPException(status_code=400, detail="location_mode must be one of: off, pc, phone.")

    if changes.get("browser_pixel_fallback_enabled") and not cfg.get("browser_control_enabled") and not changes.get("browser_control_enabled"):
        raise HTTPException(status_code=400, detail="Turn on browser control before enabling its pixel fallback.")

    access_changed = any(cfg.get(k) != changes[k] for k in ACCESS_KEYS_AFFECTING_HISTORY if k in changes)
    turning_off = changes.get("ai_enabled") is False and cfg.get("ai_enabled") is not False

    cfg.update(changes)
    config_module.save_config(cfg)

    if access_changed:
        # Old turns discussing "access is off" would otherwise bias the model into
        # contradicting the new state - a fresh state deserves a fresh conversation.
        conversation_history.clear()

    if turning_off:
        await unload_ollama_models()
    elif not cfg.get("screen_access") and not cfg.get("camera_access"):
        # The vision model is shared by both screen and camera tools and was
        # kept loaded indefinitely for speed - only safe to drop it once
        # *neither* toggle needs it anymore, otherwise the other one breaks.
        await unload_model(vision.VISION_MODEL)

    return cfg


@app.get("/api/plugins")
async def list_plugins() -> list[dict]:
    cfg = config_module.load_config()
    result = []
    for meta in plugin_loader.plugin_metadata():
        enabled = True if meta["always_on"] else bool(cfg.get(meta["config_key"]))
        result.append({**meta, "enabled": enabled})
    return result


class PluginToggle(BaseModel):
    enabled: bool


@app.post("/api/plugins/{plugin_name}/toggle")
async def toggle_plugin(plugin_name: str, update: PluginToggle) -> dict:
    meta = next((m for m in plugin_loader.plugin_metadata() if m["name"] == plugin_name), None)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"No plugin named '{plugin_name}'.")
    if meta["always_on"]:
        raise HTTPException(status_code=400, detail=f"'{plugin_name}' has no toggle - it's always on.")

    cfg = config_module.load_config()
    cfg[meta["config_key"]] = update.enabled
    config_module.save_config(cfg)
    if not update.enabled:
        plugin_loader.call_on_disable(plugin_name)
    # Same reasoning as ACCESS_KEYS_AFFECTING_HISTORY above - stale context
    # about a plugin's old on/off state shouldn't bias the next reply.
    conversation_history.clear()
    return {"ok": True, "name": plugin_name, "enabled": update.enabled}


@app.get("/api/connections")
async def list_connections() -> list[dict]:
    return [
        {
            "name": "google",
            "label": "Google (Gmail / Calendar / Drive)",
            "auth_style": "oauth",
            "configured": google_auth.is_configured(),
            "connected": google_auth.is_connected(),
        },
        {
            "name": "github",
            "label": "GitHub",
            "auth_style": "oauth",
            "configured": github_auth.is_configured(),
            "connected": github_auth.is_connected(),
        },
        {
            "name": "discord",
            "label": "Discord",
            "auth_style": "token",
            "configured": True,
            "connected": discord_auth.is_connected(),
            "invite_url": discord_auth.build_invite_url(),
        },
        {
            "name": "notion",
            "label": "Notion",
            "auth_style": "token",
            "configured": True,
            "connected": notion_auth.is_connected(),
        },
    ]


class OAuthClientCredentials(BaseModel):
    client_id: str
    client_secret: str


@app.post("/api/connections/google/credentials")
async def google_save_credentials(update: OAuthClientCredentials) -> dict:
    if not update.client_id.strip() or not update.client_secret.strip():
        raise HTTPException(status_code=400, detail="Both the Client ID and Client secret are required.")
    google_auth.save_client_config(update.client_id.strip(), update.client_secret.strip())
    return {"ok": True}


@app.post("/api/connections/github/credentials")
async def github_save_credentials(update: OAuthClientCredentials) -> dict:
    if not update.client_id.strip() or not update.client_secret.strip():
        raise HTTPException(status_code=400, detail="Both the Client ID and Client secret are required.")
    github_auth.save_client_config(update.client_id.strip(), update.client_secret.strip())
    return {"ok": True}


@app.get("/api/connections/google/start")
async def google_connect_start() -> Response:
    from fastapi.responses import RedirectResponse
    url = google_auth.build_auth_url()
    if url is None:
        raise HTTPException(
            status_code=400,
            detail="Google OAuth isn't configured yet - see backend/google_oauth_config.example.json.",
        )
    return RedirectResponse(url)


@app.get("/api/connections/google/callback")
async def google_connect_callback(request: Request) -> Response:
    from fastapi.responses import RedirectResponse
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"/?google_connect=error&detail={error}")
    if not code:
        return RedirectResponse("/?google_connect=error&detail=no_code")
    try:
        await google_auth.exchange_code(code)
    except Exception as exc:  # noqa: BLE001 - surface to the dashboard, not a raw 500
        return RedirectResponse(f"/?google_connect=error&detail={exc}")
    return RedirectResponse("/?google_connect=success")


@app.post("/api/connections/google/disconnect")
async def google_disconnect() -> dict:
    google_auth.disconnect()
    return {"ok": True}


@app.get("/api/connections/github/start")
async def github_connect_start() -> Response:
    from fastapi.responses import RedirectResponse
    url = github_auth.build_auth_url()
    if url is None:
        raise HTTPException(
            status_code=400,
            detail="GitHub OAuth isn't configured yet - see backend/github_oauth_config.example.json.",
        )
    return RedirectResponse(url)


@app.get("/api/connections/github/callback")
async def github_connect_callback(request: Request) -> Response:
    from fastapi.responses import RedirectResponse
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"/?github_connect=error&detail={error}")
    if not code:
        return RedirectResponse("/?github_connect=error&detail=no_code")
    try:
        await github_auth.exchange_code(code)
    except Exception as exc:  # noqa: BLE001 - surface to the dashboard, not a raw 500
        return RedirectResponse(f"/?github_connect=error&detail={exc}")
    return RedirectResponse("/?github_connect=success")


@app.post("/api/connections/github/disconnect")
async def github_disconnect() -> dict:
    github_auth.disconnect()
    return {"ok": True}


class DiscordTokenSubmit(BaseModel):
    bot_token: str
    client_id: str | None = None


@app.post("/api/connections/discord/token")
async def discord_save_token(update: DiscordTokenSubmit) -> dict:
    if not update.bot_token.strip():
        raise HTTPException(status_code=400, detail="Bot token can't be empty.")
    discord_auth.save_token(update.bot_token.strip(), update.client_id.strip() if update.client_id else None)
    return {"ok": True}


@app.post("/api/connections/discord/disconnect")
async def discord_disconnect() -> dict:
    discord_auth.disconnect()
    return {"ok": True}


class NotionTokenSubmit(BaseModel):
    integration_secret: str


@app.post("/api/connections/notion/token")
async def notion_save_token(update: NotionTokenSubmit) -> dict:
    if not update.integration_secret.strip():
        raise HTTPException(status_code=400, detail="Integration secret can't be empty.")
    notion_auth.save_token(update.integration_secret.strip())
    return {"ok": True}


@app.post("/api/connections/notion/disconnect")
async def notion_disconnect() -> dict:
    notion_auth.disconnect()
    return {"ok": True}


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)