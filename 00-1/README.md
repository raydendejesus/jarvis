# Jarvis

A local, self-hosted AI assistant: a real LLM as the brain (running on your own GPU via Ollama), a natural British voice, wake-word listening that works whether or not a browser is open, vision (photos/video/screen/camera), persistent memory, web research, and real phone calling (Jarvis can call you, and you can call it).

Nothing about the core assistant depends on a cloud AI provider or a subscription. The only external services involved are optional: a phone number provider (for calling) and a tunnel service (to expose your PC to that phone provider).

## What it can do

- Hold a spoken conversation, wake-word activated ("Jarvis, ...") or fully hands-free after one activation
- Search the web and read pages for anything outside its own knowledge
- Remember facts about you across restarts
- Look at a photo or video you upload, or your screen/webcam on request
- Watch your desk via webcam and lock Windows if it sees a face that isn't yours (opt-in)
- Keep a phone book and place/receive real phone calls, having an actual spoken conversation over the line
- Run persistently: starts at Windows login, controllable via a system tray icon or the web dashboard

## Architecture - how it works

```
                    ┌─────────────────────────────┐
Voice in ────────►  │   FastAPI backend (server.py) │ ────► Voice out (edge-tts)
(two paths,             │                             │
 see below)             │  conversation history        │
                        │  + memory facts               │
                        │  + phone book                 │
                        │  + current toggle state        │
                        └──────────────┬───────────────┘
                                       │ POST /api/chat
                                       ▼
                        Ollama (qwen2.5:14b, "jarvis" persona)
                                       │
                          tool_calls?  │  final answer
                          ┌────────────┴────────────┐
                          ▼                          ▼
                  web_search / fetch_webpage     spoken reply
                  remember / view_screen
                  view_camera / call_phone_number
```

**Two independent voice-input paths**, both landing on the same `/api/chat` endpoint:

1. **Browser dashboard** (`frontend/`) - uses the browser's built-in `SpeechRecognition` API. Only listens while the dashboard tab is open; sends audio to Google's/Microsoft's cloud for transcription (free, no API key, but not private).
2. **Native background listener** (`backend/listener.py`) - runs continuously via the system tray app, independent of any browser. It watches the microphone for speech (a simple energy-threshold voice-activity detector), and when someone talks, transcribes the utterance locally with a small Whisper model (`faster-whisper`, GPU-accelerated). If the transcript contains "jarvis" anywhere, everything after that word is sent to Jarvis as a command. This is what lets you say "hey Jarvis" or just "Jarvis" from anywhere in the room with nothing open on screen.

Both paths implement a lightweight "awake" window: once you've triggered Jarvis once, you can keep talking to it for a while without repeating the wake word (say "night Jarvis" to end it early).

**The brain**: Qwen2.5 14B, running locally through [Ollama](https://ollama.com), with a custom persona baked in via `backend/Modelfile` (a British-butler personality, told explicitly what tools it has and how to use them).

**Tool use**: Ollama supports OpenAI-style function calling. Each request tells the model which tools are currently available (`backend/tools.py` builds this list based on your toggle settings), and if the model decides to use one, the backend executes it and feeds the result back before the model gives its final spoken answer. None of the tools can run arbitrary code or write arbitrary files - each does exactly one narrow thing (search the web, read a specific page, save a memory, grab one frame from the screen/camera, place a call).

**Memory**: `backend/memory.py` keeps a flat JSON file of facts the model has been told to remember. Every conversation turn gets that list injected as context, so it "already knows" things you've told it before.

**Vision**: photos, video frames, screenshots, and webcam captures are all sent to a second model, `qwen2.5vl:7b` (Qwen's vision-language variant), which describes what it sees in plain text - that description then gets folded back into the normal conversation.

**Desk Guard**: uses `insightface` to compute a face embedding from a webcam frame and compares it against a small set of your own reference photos (captured via an "enroll" step). If a present face doesn't match closely enough, it calls Windows' own `LockWorkStation()` API - the same as pressing Win+L. It never has any way to unlock Windows; getting back in always requires your real password/PIN/Hello. After every real unlock, it grabs a fresh reference photo automatically, so its accuracy improves over time.

**Phone calling**: uses [Telnyx](https://telnyx.com)'s TeXML product (a TwiML-compatible call-control language). An incoming call hits a webhook on your machine; the response tells Telnyx to play a greeting and gather the caller's spoken reply; Telnyx transcribes that speech itself and posts the text back to another webhook, which runs it through the exact same Jarvis conversation pipeline used everywhere else, generates a spoken reply with edge-tts, and loops. Outbound calls work the same way in reverse - Jarvis's `call_phone_number` tool tells Telnyx to dial a number and fetch call instructions from your server once it connects. Because your PC isn't normally reachable from the internet, [ngrok](https://ngrok.com) creates a public HTTPS tunnel to your local server just for Telnyx's webhooks to reach.

**Toggles**: `backend/config.json` (auto-created with safe defaults - everything except the assistant itself starts OFF) gates screen access, camera access, calling, and Desk Guard. The system tray icon and the dashboard's Settings panel both read/write this same file.

## Why it's built this way

- **Local-first**: the LLM, TTS, vision, memory, and face recognition all run on your own machine. The only things that ever leave it are: (a) browser-tab speech recognition, if you use that path, and (b) phone call audio, which is inherent to how phone calls work at all.
- **Two voice paths instead of one**: browser-based recognition is easy and needs zero extra setup, but only works while a tab is open. The native listener has no such limitation, at the cost of one extra local dependency (Whisper) - keeping both means you get convenience *and* an always-on option.
- **Substring wake-word matching over a trained wake-word model**: an off-the-shelf model like `openWakeWord`'s "hey jarvis" is trained on that exact phrase and won't reliably catch a bare "Jarvis." Rather than training a custom model, this project just transcribes short utterances locally and checks for the word anywhere in the text - which naturally handles "Jarvis," "hey Jarvis," "yo Jarvis," or any other phrasing, at the cost of needing to run Whisper on every detected utterance instead of a cheaper always-on keyword spotter.
- **A resettable conversation history tied to toggle changes**: early testing found the model would sometimes keep insisting a capability was "off" even seconds after being turned on, because it was anchoring on what it said earlier in the same conversation. The fix wasn't a bigger prompt - flipping a screen/camera/calling toggle now clears the conversation history, since stale context about the old capability state was actively misleading the model.
- **TeXML over raw Call Control**: Telnyx offers a lower-level, fully custom call-handling API (Call Control), and a higher-level Twilio-compatible one (TeXML). This project uses TeXML because its request/response shape (a webhook that returns simple XML instructions) is dramatically simpler to build against correctly than hand-rolling call-state management, at a small cost in flexibility this project doesn't need.
- **No tool that can execute code, write arbitrary files, or reach arbitrary network destinations**: the worst outcome of a bad or manipulated response is a wrong answer, a mis-triggered lock, or a bogus memory entry (visible and editable in plain JSON) - never control over the machine itself.

## Setup

### Prerequisites

- Windows 10/11
- [Ollama](https://ollama.com) installed
- Python 3.12 available via the `py` launcher
- An NVIDIA GPU is strongly recommended (this was built and tested against an RTX 3090) - it'll run on CPU, just much slower
- [ffmpeg](https://ffmpeg.org/download.html) installed and on your PATH (only needed for the video-summary feature)
- A webcam, if you want Desk Guard or the "look through the camera" feature

### 1. First run

Just double-click `JARVIS.bat`. On first run it will:

1. Create a Python virtual environment in `venv/`
2. Install all dependencies from `backend/requirements.txt`
3. Pull `qwen2.5:14b` (~9GB) and `qwen2.5vl:7b` (~6GB) via Ollama, and build the `jarvis` persona from `backend/Modelfile`
4. Register itself to start automatically at Windows login (a shortcut in your Startup folder - no admin rights needed)
5. Start the tray app, which starts the backend and opens the dashboard at `http://127.0.0.1:8765`

After that, it starts on its own at every login. Open Chrome's `chrome://settings/onStartup`, choose "Open a specific page or set of pages," and add `http://127.0.0.1:8765/` if you also want it to reopen automatically any time you launch Chrome fresh.

### 2. Using it

Open the dashboard in **Chrome or Edge** (required for the browser's speech recognition) and allow microphone access. Say "Jarvis" followed by anything. The native background listener also runs automatically once the tray app is up - you can talk to Jarvis with no browser open at all.

If you have more than one microphone (or any virtual audio device like a voice-changer, a VR headset, or a streaming app), check `PREFERRED_DEVICE_NAMES` near the top of `backend/listener.py` and add your actual mic's name - Windows can silently make a virtual device the system default, which will make Jarvis (and the browser tab) unable to hear you at all.

### 3. Turning on capabilities

Everything beyond basic conversation is off until you turn it on, from the dashboard's Settings panel or the tray icon menu:

| Toggle | What it does |
|---|---|
| Jarvis Enabled | Master switch for the whole assistant |
| Screen Access | Lets Jarvis take a screenshot and describe it, on request |
| Camera Access | Lets Jarvis grab a webcam frame and describe it, on request |
| Desk Guard | Webcam presence lock (see below) - requires enrolling your face first |
| Calling | Lets Jarvis place outbound calls - requires phone setup below |

**Desk Guard**: click "Enroll my face" on the dashboard a few times (it captures reference photos over a few seconds each time), then turn the toggle on.

### 4. Optional: phone calling setup

This is the most involved part, since it wires together two external accounts. Take it slowly.

**A. Get a Telnyx account and phone number**

1. Sign up at [telnyx.com](https://telnyx.com) - self-service, no invite needed
2. Go to **Numbers → Phone Numbers → Buy Numbers**, search by your area code, and buy a **Local** number with **Voice** and **SMS** capability
3. If told to enable SMS on the number, note that US carriers require a quick one-time registration step (A2P 10DLC for local numbers) before texting works reliably - calling works immediately regardless

**B. Get your API key**

Go to your account menu (top-right) → **API Keys** → **Create API Key**. Copy it.

**C. Get your Account SID**

Either check `https://portal.telnyx.com/#/account/general`, or fetch it via the API (this is the reliable method - it's the `organization_id` field):

```bash
curl -s -H "Authorization: Bearer YOUR_API_KEY" https://api.telnyx.com/v2/api_keys
```

**D. Create an Outbound Voice Profile**

Go to **Voice → Outbound Voice Profiles → Create Profile**. Restrict allowed destinations to your own country, and set a low daily spend limit and channel limit (1-2) as a safety net. Note the profile's ID from the URL or the list page.

**E. Set up ngrok** (exposes your local server to Telnyx's webhooks)

1. Sign up free at [ngrok.com](https://ngrok.com), copy your authtoken from the dashboard
2. `ngrok config add-authtoken YOUR_TOKEN`
3. `ngrok http 8765` - copy the `https://....ngrok-free.app` (or `.dev`) URL it prints

Note: the free tier gives you a new random URL every time you restart the tunnel, unless you claim the one free static domain ngrok's dashboard offers. Also note: some antivirus software (including Windows Defender) has been known to falsely flag ngrok's executable as a threat - if that happens, add a Defender exclusion for the folder you extracted it into (Settings → Virus & threat protection → Manage settings → Exclusions).

**F. Create the TeXML application**

Important: Telnyx's portal has a "Voice API Applications" wizard that actually creates a **Call Control Application**, not a TeXML one, even though the flow looks the same either way - this project needs the TeXML kind specifically. The reliable way to get one is via the API directly:

```bash
curl -X POST -H "Authorization: Bearer YOUR_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "friendly_name": "jarvis-texml",
    "voice_url": "https://YOUR_NGROK_URL/api/telephony/voice",
    "voice_method": "POST",
    "active": true
  }' \
  https://api.telnyx.com/v2/texml_applications
```

Note the `"id"` field in the response - that's your Application SID. Attach the outbound voice profile from step D:

```bash
curl -X PATCH -H "Authorization: Bearer YOUR_API_KEY" -H "Content-Type: application/json" \
  -d '{"outbound":{"outbound_voice_profile_id":"YOUR_OVP_ID"}}' \
  https://api.telnyx.com/v2/texml_applications/YOUR_APPLICATION_ID
```

**G. Point your phone number at the new application**

Find your number's resource ID:

```bash
curl -s -H "Authorization: Bearer YOUR_API_KEY" https://api.telnyx.com/v2/phone_numbers
```

Then assign it:

```bash
curl -X PATCH -H "Authorization: Bearer YOUR_API_KEY" -H "Content-Type: application/json" \
  -d '{"connection_id":"YOUR_APPLICATION_ID"}' \
  https://api.telnyx.com/v2/phone_numbers/YOUR_PHONE_NUMBER_RESOURCE_ID
```

**H. Fill in the config**

Copy `backend/telephony_config.example.json` to `backend/telephony_config.json` and fill in the five values you now have: `api_key`, `telnyx_number` (in `+1XXXXXXXXXX` format), `public_base_url` (your ngrok URL, no trailing slash), `account_sid`, `application_sid`.

**I. Turn on the Calling toggle** and restart `JARVIS_TRAY.pyw`.

Whenever your ngrok URL changes (every restart, unless you have a static domain), update both `telephony_config.json`'s `public_base_url` and the TeXML application's `voice_url` (via a PATCH request like step F) to match.

### 5. Phone book

Add contacts from the dashboard's Phone Book panel (name + number). Say "Jarvis, call `<name>`" to have it place a call - it will only ever call a number that's either in the phone book or spoken explicitly in the conversation.

## Changing the personality or voice

Edit the `SYSTEM` block in `backend/Modelfile`, then rebuild: `ollama create jarvis -f backend\Modelfile`.

Voice: change `VOICE` in `backend/server.py` (default `en-GB-RyanNeural`). Run `edge-tts --list-voices` (inside the venv) to see every option.

## Security notes

- The server only binds to `127.0.0.1` - nothing on your network or the internet can reach it directly (phone calls reach it only via the ngrok tunnel you explicitly start).
- Cross-origin requests to the backend are rejected, closing off the common "malicious website open in another tab pokes your local AI" attack.
- Content pulled from the web is explicitly tagged as untrusted in the model's context, and the `remember` tool caps how much text one call can store, both reducing (not eliminating) prompt-injection risk from a malicious page.
- No tool exists that executes arbitrary code, writes arbitrary files, or reaches arbitrary network destinations beyond read-only search/fetch and the phone system.
- Desk Guard's lock action only ever locks; it has no unlock capability.

## License

No license file is included by default - add one (MIT is a common permissive choice) if you want to make clear what others can do with this code.
