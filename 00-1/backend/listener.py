import base64
import queue
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd

import config as config_module

SAMPLE_RATE = 16000
BLOCK_DURATION = 0.1
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DURATION)
SILENCE_RMS_THRESHOLD = 500
SPEECH_START_BLOCKS = 2
SILENCE_END_BLOCKS = 9
MAX_UTTERANCE_SECONDS = 12
MIN_UTTERANCE_SECONDS = 0.4
AWAKE_WINDOW_SECONDS = 45
WAKE_WORD = "jarvis"
CHAT_URL = "http://127.0.0.1:8765/api/chat"

_whisper_model = None
_audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
_awake_until = 0.0

# Prefer a real physical mic over whatever Windows currently has set as the
# default recording device - that default has a way of silently becoming a
# virtual device (Voicemod, a VR headset, etc.) instead of the mic you're
# actually speaking into.
PREFERRED_DEVICE_NAMES = ["Insta360 Link", "HyperX Cloud", "Headset Microphone"]


def _pick_input_device() -> int | None:
    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        print(f"[listener] could not query audio devices: {exc}", flush=True)
        return None

    for preferred in PREFERRED_DEVICE_NAMES:
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and preferred.lower() in d["name"].lower():
                print(f"[listener] using input device: {d['name']}", flush=True)
                return i

    print("[listener] no preferred mic found, falling back to system default input", flush=True)
    return None


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        try:
            _whisper_model = WhisperModel("small.en", device="cuda", compute_type="float16")
        except Exception:
            _whisper_model = WhisperModel("small.en", device="cpu", compute_type="int8")
    return _whisper_model


def _rms(block: np.ndarray) -> float:
    return float(np.sqrt(np.mean(block.astype(np.float64) ** 2)))


def _audio_callback(indata, frames, time_info, status) -> None:
    _audio_queue.put(indata.copy())


def _drain_queue() -> None:
    while not _audio_queue.empty():
        try:
            _audio_queue.get_nowait()
        except queue.Empty:
            break


def _extract_command(transcript: str) -> str | None:
    lower = transcript.lower()
    idx = lower.find(WAKE_WORD)
    if idx == -1:
        return None
    return transcript[idx + len(WAKE_WORD):].strip(" ,.:!-")


def _transcribe(audio: np.ndarray) -> str:
    model = _get_whisper()
    audio_f32 = audio.astype(np.float32) / 32768.0
    segments, _ = model.transcribe(audio_f32, language="en", beam_size=1)
    return " ".join(seg.text for seg in segments).strip()


def _play_audio(audio_b64: str) -> None:
    import pygame
    if not pygame.mixer.get_init():
        pygame.mixer.init()

    audio_bytes = base64.b64decode(audio_b64)
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name
    try:
        pygame.mixer.music.load(tmp_path)
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.1)
    finally:
        pygame.mixer.music.unload()
        Path(tmp_path).unlink(missing_ok=True)


def _send_to_jarvis(text: str) -> None:
    try:
        resp = httpx.post(CHAT_URL, json={"message": text}, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - keep the listener alive on any request failure
        print(f"[listener] chat request failed: {exc}", flush=True)
        return
    _play_audio(data["audio"])


def _handle_utterance(audio: np.ndarray) -> None:
    """Runs synchronously on the capture loop's thread, deliberately - this pauses
    listening for the duration, so Jarvis speaking its own reply out loud never gets
    picked back up by the mic as a new utterance."""
    global _awake_until

    duration = len(audio) / SAMPLE_RATE
    if duration < MIN_UTTERANCE_SECONDS:
        return

    transcript = _transcribe(audio)
    if not transcript:
        return
    print(f"[listener] heard: {transcript!r}", flush=True)

    now = time.time()
    lower = transcript.lower()

    if now < _awake_until:
        if "night jarvis" in lower:
            _awake_until = 0.0
            print("[listener] back to sleep", flush=True)
            return
        command = transcript.strip()
    else:
        command = _extract_command(transcript)
        if command is None:
            return

    if not command:
        _awake_until = now + AWAKE_WINDOW_SECONDS
        return

    print(f"[listener] command: {command!r}", flush=True)
    _awake_until = now + AWAKE_WINDOW_SECONDS
    _send_to_jarvis(command)
    _drain_queue()


def run() -> None:
    print("[listener] background voice listener starting", flush=True)
    buffer: list[np.ndarray] = []
    speech_blocks = 0
    silence_blocks = 0
    in_speech = False

    device = _pick_input_device()

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16",
        blocksize=BLOCK_SIZE, callback=_audio_callback, device=device,
    ):
        while True:
            if not config_module.load_config().get("ai_enabled", True):
                _drain_queue()
                buffer, speech_blocks, silence_blocks, in_speech = [], 0, 0, False
                time.sleep(1.0)
                continue

            try:
                block = _audio_queue.get(timeout=1.0).flatten()
            except queue.Empty:
                continue

            level = _rms(block)

            if level > SILENCE_RMS_THRESHOLD:
                speech_blocks += 1
                silence_blocks = 0
                buffer.append(block)
                in_speech = in_speech or speech_blocks >= SPEECH_START_BLOCKS
            elif in_speech:
                silence_blocks += 1
                buffer.append(block)
            else:
                speech_blocks = 0
                buffer = []

            utterance_too_long = in_speech and len(buffer) * BLOCK_DURATION > MAX_UTTERANCE_SECONDS
            utterance_done = in_speech and silence_blocks >= SILENCE_END_BLOCKS

            if utterance_done or utterance_too_long:
                audio = np.concatenate(buffer) if buffer else np.array([], dtype=np.int16)
                buffer, speech_blocks, silence_blocks, in_speech = [], 0, 0, False
                if len(audio) > 0:
                    _handle_utterance(audio)
