import base64
import os
import queue
import re
import site
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
import sounddevice as sd

import config as config_module

# faster-whisper's CUDA path needs cuBLAS/cuDNN, which the pip-installed
# nvidia-cublas-cu12 / nvidia-cudnn-cu12 packages provide but don't put on
# Windows' DLL search path automatically - without this, GPU transcription
# loads fine but crashes on the first real inference.
for _pkg in ("cublas", "cudnn", "cuda_nvrtc"):
    for _site_dir in site.getsitepackages():
        _dll_dir = Path(_site_dir) / "nvidia" / _pkg / "bin"
        if _dll_dir.is_dir():
            os.add_dll_directory(str(_dll_dir))

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
LISTENER_EVENT_URL = "http://127.0.0.1:8765/api/listener/event"

_whisper_model = None
_audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()
_awake_until = 0.0

# Prefer a real physical mic over whatever Windows currently has set as the
# default recording device - that default has a way of silently becoming a
# virtual device (Voicemod, a VR headset, etc.) instead of the mic you're
# actually speaking into.
PREFERRED_DEVICE_NAMES = ["Insta360 Link", "HyperX Cloud", "Headset Microphone"]

# Devices that silently capture something other than a real physical mic
# (game-audio bridges, VR audio loopbacks, voice-changer virtual devices) -
# if Windows' actual default input is one of these, it's worth overriding.
# Otherwise, trust the real default rather than a hardcoded preference list -
# a fixed priority order previously picked a webcam's mic over the user's
# actual headset just because it came first in PREFERRED_DEVICE_NAMES, even
# though the headset was already correctly set as the Windows default.
BLOCKED_DEVICE_NAME_FRAGMENTS = [
    "voicemod", "virtual desktop audio", "oculus virtual", "vdvad",
    "steelseries sonar", "stereo mix", "wave out mix",
]


def _pick_input_device() -> int | None:
    try:
        devices = sd.query_devices()
        default_input = sd.query_devices(kind="input")
    except Exception as exc:  # noqa: BLE001
        print(f"[listener] could not query audio devices: {exc}", flush=True)
        return None

    default_name = (default_input.get("name") or "").lower()
    if not any(bad in default_name for bad in BLOCKED_DEVICE_NAME_FRAGMENTS):
        print(f"[listener] using Windows' default input device: {default_input['name']}", flush=True)
        return None  # None tells sounddevice to use the system default itself

    print(f"[listener] Windows' default input ('{default_input['name']}') looks like a virtual/loopback device - looking for a real mic instead", flush=True)
    for preferred in PREFERRED_DEVICE_NAMES:
        for i, d in enumerate(devices):
            if d["max_input_channels"] > 0 and preferred.lower() in d["name"].lower():
                print(f"[listener] using fallback input device: {d['name']}", flush=True)
                return i

    print("[listener] no known-good mic found either - falling back to system default anyway", flush=True)
    return None


def _load_whisper_cpu():
    from faster_whisper import WhisperModel
    print("[listener] loading Whisper on CPU (int8)", flush=True)
    return WhisperModel("small.en", device="cpu", compute_type="int8")


def _get_whisper():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        try:
            _whisper_model = WhisperModel("small.en", device="cuda", compute_type="float16")
            print("[listener] Whisper loaded on GPU (cuda/float16)", flush=True)
        except Exception as exc:
            print(f"[listener] GPU load failed ({exc}), falling back to CPU", flush=True)
            _whisper_model = _load_whisper_cpu()
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


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = curr
    return prev[-1]


# Whisper regularly mishears "Jarvis" as something close-but-not-exact
# ("Jervis", "Jovis", "Jarvys"...) - requiring an exact substring match meant
# a real, clearly-directed-at-Jarvis utterance would just silently get
# dropped as if nothing was said at all. Fuzzy-matching each word against
# "jarvis" (allowing a small edit distance) catches those near-misses while
# a minimum word length keeps short unrelated words from matching by chance.
_WAKE_WORD_RE = re.compile(r"[a-z']+")


def _find_wake_word(lower: str) -> tuple[int, int] | None:
    for match in _WAKE_WORD_RE.finditer(lower):
        word = match.group()
        if len(word) < 4:
            continue
        if _levenshtein(word, WAKE_WORD) <= 2:
            return match.start(), match.end()
    return None


def _is_sleep_command(lower: str) -> bool:
    # A minor transcription slip (one wrong or missing word) is common enough
    # that requiring the exact phrase "night jarvis" made this fail silently
    # whenever the mic misheard even slightly - checking for both words
    # appearing anywhere, rather than as one exact substring, is far more
    # forgiving while still being clearly intentional rather than accidental.
    return "night" in lower and _find_wake_word(lower) is not None


def _extract_command(transcript: str) -> str | None:
    lower = transcript.lower()
    span = _find_wake_word(lower)
    if span is None:
        return None
    return transcript[span[1]:].strip(" ,.:!-")


def _transcribe(audio: np.ndarray) -> str:
    global _whisper_model
    model = _get_whisper()
    audio_f32 = audio.astype(np.float32) / 32768.0
    try:
        segments, _ = model.transcribe(audio_f32, language="en", beam_size=1)
        return " ".join(seg.text for seg in segments).strip()
    except RuntimeError as exc:
        # The GPU model can load successfully but still fail on the first real
        # inference (e.g. a missing CUDA library) - self-heal to CPU rather than
        # fail this same way on every single utterance from now on.
        print(f"[listener] GPU transcription failed ({exc}), switching to CPU for future requests", flush=True)
        _whisper_model = _load_whisper_cpu()
        segments, _ = _whisper_model.transcribe(audio_f32, language="en", beam_size=1)
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


def _report_status(phase: str, text: str = "") -> None:
    """Best-effort only - the dashboard's live status indicator is a nice-to-have,
    never worth risking the actual listener over if the backend happens to be
    slow or briefly unreachable."""
    try:
        httpx.post(LISTENER_EVENT_URL, json={"phase": phase, "text": text}, timeout=2)
    except Exception:  # noqa: BLE001
        pass


def _send_to_jarvis(text: str) -> None:
    try:
        resp = httpx.post(CHAT_URL, json={"message": text}, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 - keep the listener alive on any request failure
        print(f"[listener] chat request failed: {exc}", flush=True)
        _report_status("idle")
        return

    if not data.get("reply") or not data.get("audio"):
        print("[listener] Jarvis is switched off, staying silent", flush=True)
        _report_status("idle")
        return
    _report_status("speaking", data["reply"])
    _play_audio(data["audio"])
    _report_status("idle")


def _handle_utterance(audio: np.ndarray) -> None:
    """Runs synchronously on the capture loop's thread, deliberately - this pauses
    listening for the duration, so Jarvis speaking its own reply out loud never gets
    picked back up by the mic as a new utterance."""
    try:
        _handle_utterance_inner(audio)
    except Exception as exc:  # noqa: BLE001 - nothing in here may ever be allowed to kill the listener thread
        print(f"[listener] unexpected error handling utterance, skipping it: {exc}", flush=True)


def _handle_utterance_inner(audio: np.ndarray) -> None:
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
        if _is_sleep_command(lower):
            _awake_until = 0.0
            print("[listener] back to sleep", flush=True)
            return
        command = transcript.strip()
    elif _is_sleep_command(lower):
        # Already asleep - "night jarvis" said again is a harmless no-op,
        # not a command to pass along.
        return
    else:
        command = _extract_command(transcript)
        if command is None:
            return

    if not command:
        _awake_until = now + AWAKE_WINDOW_SECONDS
        return

    print(f"[listener] command: {command!r}", flush=True)
    _awake_until = now + AWAKE_WINDOW_SECONDS
    _report_status("thinking", command)
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
