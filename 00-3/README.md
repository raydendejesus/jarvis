# Jarvis 00-3

A local, self-hosted AI assistant: a real LLM as the brain (running on your own GPU via Ollama), a natural British voice, wake-word listening that works whether or not a browser is open, vision (photos/video/screen/camera), a persistent memory and reactive knowledge base, location awareness, web research, real phone calling (Jarvis can call you, and you can call it), and its own on-screen cursor that can act inside whatever browser tab you're viewing when you ask it to.

Nothing about the core assistant depends on a cloud AI provider or a subscription. The only external services involved are optional: a phone number provider (for calling) and a tunnel service (to expose your PC to that phone provider).

## System requirements & resource cost

This runs a real local LLM plus a separate vision model plus local speech transcription, so it is meaningfully heavier than a typical hobby script. Numbers below are for the default models this project pulls.

**VRAM usage (NVIDIA GPU):**

| Component | VRAM | When it's loaded |
|---|---|---|
| Brain (`hermes3` 8B, the `jarvis` persona) | ~5-6 GB | Always, while the assistant is on |
| Native listener (`faster-whisper`, "small.en", GPU) | ~1 GB | Always, while the assistant is on |
| Vision (`qwen2.5vl:7b`) | ~6 GB | Only while Screen Access, Camera Access, or the browser's pixel-control fallback is on *and* actively used |
| Desk Guard face matching (`insightface`, ONNX) | <1 GB | Only while Desk Guard is enabled |
| Voice output (`edge-tts`) | 0 (not a local model - a network call to Microsoft's TTS service) | N/A |
| Browser Control (the DOM-based path) | 0 (no model at all - it's structured text through the brain that's already loaded) | Only while you're actively asking it to act in a tab |

Typical steady-state use (just talking to it) is **~6-7 GB VRAM**. A moment of looking at your screen, camera, or an unreadable webpage can spike that to **~12-13 GB** while that one request is in flight, then it settles back down. Browser Control's normal path adds no VRAM at all; only its rare pixel-fallback path touches the vision model.

**Bare minimum:** an 8 GB GPU (e.g. RTX 3060 Ti / RTX 2070) - covers the brain and native listener comfortably; vision requests will be noticeably slower since the vision model may need to swap in rather than sit resident alongside the brain.

**Recommended:** 12 GB+ VRAM (e.g. RTX 3060 12GB, RTX 4070) so the brain and vision model can both stay resident without swapping.

**Built and tested on:** an RTX 3090 (24 GB) - comfortable headroom for everything at once, including Desk Guard running continuously.

**System RAM:** 16 GB minimum, 32 GB recommended. **Disk:** ~15-20 GB free for the Ollama models, Python dependencies, and Whisper/insightface's model downloads (this grows slowly afterward - memory and knowledge files are small plain text).

**No GPU?** It will still run - `faster-whisper` falls back to CPU automatically if the GPU path fails - but the LLM itself will be noticeably slower on CPU. This project was not designed or tuned for a CPU-only setup.

### Pros

- Fully local for the core loop: the LLM, vision, memory, knowledge base, and face matching all run on your own GPU - no subscription, no per-message API cost, no cloud AI account required for any of it.
- Real, working phone calling (inbound and outbound) - unusual for a self-hosted hobby assistant.
- Its own browser-acting cursor, without the heavier pixel-and-vision-model approach most "AI browses for you" tools use - most pages are read structurally, near-instantly, with no extra VRAM.
- Every resource-heavy or privacy-sensitive capability (camera, screen, calling, Desk Guard, browser control) is an explicit, saved toggle - nothing turns itself on.
- No tool can execute arbitrary code or reach an arbitrary network destination, and the one tool that can act on your behalf (Browser Control) needs a dedicated toggle plus fresh confirmation before its riskiest fallback path ever runs.
- A self-improving reactive knowledge base: once it looks something up, it remembers the answer in a plain, human-readable text file you can read, edit, or delete yourself.
- Open and inspectable: it's just Python and JavaScript files, nothing obfuscated, no telemetry added by this project.

### Cons

- GPU-hungry - not a good fit for a machine without a dedicated NVIDIA GPU.
- Windows-only right now: Desk Guard's lock action, Startup-folder autostart, and the tray icon are all Windows-specific.
- Not 100% local by default: the browser dashboard's speech recognition (if you turn it on) sends audio to Google's/Microsoft's cloud, and voice output (`edge-tts`) is a network call to Microsoft's TTS service, not an on-device model.
- Phone calling setup is genuinely involved: two external accounts (Telnyx + ngrok), several manual API calls, and it's one of the more fragile parts of the system.
- Browser Control's structural reading approach doesn't work on canvas-drawn pages (some games, some design tools) - its pixel-based fallback handles those, at the cost of the extra permission and warning overhead described below.
- Desk Guard is a convenience feature, not a security system - lighting, glasses, hats, and camera angle can all cause a false lock (or, less often, a false match).
- Single-user by design: conversation history, memory, and the phone book are global state, not per-user.
- No authentication on the local dashboard beyond binding to `127.0.0.1` - anyone with access to that port on your machine can use it as you.

## What it can do

- Hold a spoken conversation, wake-word activated ("Jarvis, ...") or fully hands-free after one activation
- Search the web and read pages for anything outside its own knowledge, and keep what it learns in a growing local knowledge base so it doesn't have to search again
- Remember facts about you across restarts
- Know your current location (PC or phone) if asked
- Look at a photo or video you upload, or your screen/webcam on request
- Read or act inside whatever browser tab you're viewing - click, type, scroll, or describe what's on the page - through its own on-screen cursor, only when you ask it to
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
                        Ollama (hermes3 8B, "jarvis" persona)
                                       │
                          tool_calls?  │  final answer
                          ┌────────────┴────────────┐
                          ▼                          ▼
                  web_search / fetch_webpage     spoken reply
                  remember / check_knowledge
                  save_knowledge / get_location
                  view_screen / view_camera
                  call_phone_number / hang_up_call
                  browser_scan_page / browser_read_page
                  browser_click / browser_type / browser_scroll
                                       │
                                       ▼ (long-poll)
                        Browser extension (background.js)
                                       │
                                       ▼
                        Content script in your active tab
                        (element scanner + blue "Jarvis" cursor)
```

**Two independent voice-input paths**, both landing on the same `/api/chat` endpoint:

1. **Browser dashboard** (`frontend/`) - uses the browser's built-in `SpeechRecognition` API. Off by default (see Toggles) since the native listener below already covers this; turn it on per-browser-tab in Settings if you want it too.
2. **Native background listener** (`backend/listener.py`) - runs continuously via the system tray app, independent of any browser. It watches the microphone for speech (a simple energy-threshold voice-activity detector), and when someone talks, transcribes the utterance locally with a small Whisper model (`faster-whisper`, GPU-accelerated). If the transcript contains "jarvis" anywhere, everything after that word is sent to Jarvis as a command. This is what lets you say "hey Jarvis" or just "Jarvis" from anywhere in the room with nothing open on screen, and it's the default/primary path.

Both paths implement a lightweight "awake" window: once you've triggered Jarvis once, you can keep talking to it for a while without repeating the wake word (say "night Jarvis" to end it early).

**The brain**: Hermes 3 (8B), running locally through [Ollama](https://ollama.com), with a custom persona baked in via `backend/Modelfile` (a British-butler personality, told explicitly what tools it has and how to use them, and given an explicit rule to never claim a tool succeeded when it actually failed - to avoid it fabricating a plausible-sounding answer instead of admitting it can't currently do something).

**Tool use**: Ollama supports OpenAI-style function calling. Each request tells the model which tools are currently available (`backend/tools.py` builds this list based on your toggle settings), and if the model decides to use one, the backend executes it and feeds the result back before the model gives its final spoken answer. None of the tools can run arbitrary code or write arbitrary files - each does exactly one narrow thing (search the web, read a specific page, save a memory, check or save learned knowledge, get the last known location, grab one frame from the screen/camera, place a call, or act in a browser tab).

**Memory**: `backend/memory.py` keeps a flat JSON file of facts the model has been told to remember. Every conversation turn gets that list injected as context, so it "already knows" things you've told it before.

**Knowledge base**: `backend/knowledge.py` is separate from memory - it's what the model learns by researching, not what you tell it directly. Before searching the web on a factual question, it calls `check_knowledge` first; if nothing's saved yet, it searches, answers, and calls `save_knowledge` so the same question is instant next time. Saved knowledge lives as plain `.md` files under `backend/knowledge/`, automatically condensed once the total saved text passes 100,000 words.

**Location**: the dashboard can share the browser's geolocation (works on a PC or a phone browser) via a three-state button in Settings - one click cycles OFF → PC → PHONE → OFF. Whichever mode is selected, only the matching device (a PC browser or a phone browser) actually shares its position; the other stays quiet even if its dashboard tab happens to be open. Nothing is written to disk - `backend/location.py` holds only the last-known position in memory.

**Vision**: photos, video frames, screenshots, and webcam captures are all sent to a second model, `qwen2.5vl:7b` (Qwen's vision-language variant), which describes what it sees in plain text - that description then gets folded back into the normal conversation. Browser Control's pixel fallback (below) reuses this same model rather than adding another one.

**Browser Control**: covered in its own section below, since it's involved enough to need one.

**Desk Guard**: uses `insightface` to compute a face embedding from a webcam frame and compares it against a small set of your own reference photos (captured via an "enroll" step). If a present face doesn't match closely enough, it calls Windows' own `LockWorkStation()` API - the same as pressing Win+L. It never has any way to unlock Windows; getting back in always requires your real password/PIN/Hello. After every real unlock, it grabs a fresh reference photo automatically, so its accuracy improves over time.

**Phone calling**: uses [Telnyx](https://telnyx.com)'s TeXML product (a TwiML-compatible call-control language). An incoming call hits a webhook on your machine; the response tells Telnyx to play a greeting and gather the caller's spoken reply; Telnyx transcribes that speech itself and posts the text back to another webhook, which runs it through the exact same Jarvis conversation pipeline used everywhere else, generates a spoken reply with edge-tts, and loops. Outbound calls work the same way in reverse - Jarvis's `call_phone_number` tool tells Telnyx to dial a number and fetch call instructions from your server once it connects. Because your PC isn't normally reachable from the internet, [ngrok](https://ngrok.com) creates a public HTTPS tunnel to your local server just for Telnyx's webhooks to reach. If you configure `owner_number` in `telephony_config.json`, calling that number specifically uses the normal "sir" persona instead of the third-party-caller persona it uses with everyone else.

**Toggles**: `backend/config.json` (auto-created with safe defaults - everything except the assistant itself starts OFF) gates screen access, camera access, calling, call notifications, location, Desk Guard, and Browser Control (plus its pixel-fallback sub-toggle). The system tray icon and the dashboard's Settings panel both read/write this same file.

## Browser Control (Jarvis's own on-screen cursor)

This is the newest capability in this version, so it gets a full walkthrough rather than a summary.

### What it is

Lets Jarvis click, type, scroll, and read content inside whatever browser tab you're currently viewing - through his own separate on-screen cursor (a small blue pointer labeled "Jarvis"), never your real mouse. It works by reading the page's actual structure (buttons, links, form fields - the same information a screen reader uses), not by taking screenshots and guessing pixel coordinates, which is why it needs no extra VRAM and reacts almost instantly for ordinary webpages. It only ever acts because you asked it to do something in the moment - never on its own initiative, and never chained into an unrelated request.

### Installing the extension

1. Open `chrome://extensions` and turn on **Developer mode** (top-right toggle).
2. **Drag and drop the `browser_extension` folder straight onto that page.** This is the reliable method - Chrome's own "Load unpacked" file picker has a known quirk where it can fail to show a folder that's actually there; dragging it in sidesteps that entirely.
3. You should see a card appear titled "Jarvis Browser Control" with no error badge. If Chrome asks you to confirm the permissions it's requesting, accept - it needs broad tab access specifically so it can act on whatever tab you're currently looking at without a separate click every time you switch tabs.

To reload it after any future update, use the refresh icon on that same card (or drag the folder in again).

### Turning it on and using it

1. Turn on **Browser Control** in the dashboard Settings or tray menu (off by default).
2. Just ask, e.g. "Jarvis, scan this page" or "Jarvis, click the search box and type in cat videos" or "Jarvis, what does this page say?" - no per-tab setup step is needed; it acts on whichever tab is currently active, even if you're not looking at Chrome right now (talking to it from another window is the normal way to use it).
3. Watch for the blue "Jarvis" cursor gliding to each element before it acts - that's your visual confirmation it's genuinely doing something, not just replying with text.

Two different tools cover two different questions: **"what does this page say"** uses `browser_read_page` (the actual rendered text, respecting anything JavaScript put there and whatever you're logged into); **"click/type something"** uses `browser_scan_page` first to find the right element, then `browser_click`/`browser_type`.

### The pixel-control fallback, and its warning

Most pages are read structurally as above. A minority - canvas-drawn games, some design tools - have no readable structure at all. For those, a second toggle, **only visible once Browser Control itself is on**, lets Jarvis fall back to taking a screenshot, having the vision model locate what you described, and clicking that exact pixel via Chrome's own debugger protocol. Every single use of this fallback shows a full-page on-screen warning that must be clicked "Allow" first - no exceptions, no "don't ask again," and Chrome's own "this tab is being debugged" bar is also visible the whole time it's active, on top of that warning. This path is deliberately more effort to reach than the normal one.

### Toggle layers (this genuinely has more than one)

1. The dashboard/tray **Browser Control** toggle - whether the tool exists for the model to call at all.
2. The separate **pixel-fallback** toggle - whether the riskier fallback path exists, only reachable once (1) is on.
3. The extension's own popup has an independent **Pause** switch that blocks every browser action instantly, regardless of either toggle above.
4. The pixel fallback's on-screen warning, required fresh every single time it's used.

### A note on the broader permission

Because Jarvis acts on whatever tab you're currently viewing without a separate click each time, the extension requests access to any site (`<all_urls>`) rather than only one tab at a time. What that permission does *not* change is whether it's ever allowed to act: the dashboard toggle, "only when you ask in the moment," and the pixel-fallback's warning are exactly as strict either way - the broader permission only affects *which tab* it's technically capable of reaching into, not *whether* it will do anything there.

## Optional addon: Jarvis Auto Research

This repo also includes a separate, **not installed by default**, opt-in addon under [`addons/jarvis-auto-research/`](addons/jarvis-auto-research/) that lets Jarvis quietly research topics connected to what it already knows about you, in the background, on a strict daily cap - so if you later ask about it, it already knows. It ships as its own download with its own install/uninstall instructions and its own explanation of the safety guardrails (a keyword filter plus a domain blocklist - "a fence, not a cage" - applied only to this background pipeline, never to your own manual searches). See that folder's README before deciding whether to add it - the short version is in the box below.

> **Quick summary of the Auto Research DLC**: once installed and turned on, Jarvis spends at most a few extra LLM calls a day (capped, default 3/day) quietly researching things connected to facts it already has about you, so a later question about it is answered instantly. It reuses the same brain model already running - no extra VRAM. Full install steps (which files to copy, exact code to paste into `server.py`/`config.py`, and how to add the toggle) are in [`addons/jarvis-auto-research/README.md`](addons/jarvis-auto-research/README.md).

## Why it's built this way

- **Local-first**: the LLM, vision, memory, knowledge base, and face recognition all run on your own machine. The only things that ever leave it are: (a) browser-tab speech recognition, if you turn that on, (b) `edge-tts`'s network call to synthesize voice output, and (c) phone call audio, which is inherent to how phone calls work at all.
- **The dashboard's own mic is opt-in, not automatic**: earlier testing found that with both the dashboard's browser-based recognition and the native listener active at once, the same spoken utterance could be transcribed slightly differently by each, producing two separate replies. Rather than trying to perfectly de-duplicate two independent speech engines, the simpler and more reliable fix was to make the redundant one opt-in.
- **Browser Control reads structure before pixels**: the DOM/accessibility-style approach (reading buttons, links, and labels directly) is what the best-performing open-source browser-automation tools actually use today, not the heavier screenshot-plus-vision-model loop popularized by some past commercial browser agents - it's faster, needs no extra model, and works with an ordinary extension permission instead of Chrome's more sensitive debugger permission. The pixel fallback exists specifically for the pages where that approach has nothing to read.
- **An explicit anti-fabrication rule in the persona**: early testing surfaced the model occasionally describing a browser action as having succeeded when the underlying tool had actually failed or wasn't connected - a fabricated success is worse than an honest failure, so the persona now has a direct, blanket instruction never to do this, for every tool, not just browser control.
- **A resettable conversation history tied to toggle changes**: early testing found the model would sometimes keep insisting a capability was "off" even seconds after being turned on, because it was anchoring on what it said earlier in the same conversation. The fix wasn't a bigger prompt - flipping a toggle now clears the conversation history, since stale context about the old capability state was actively misleading the model.
- **TeXML over raw Call Control**: Telnyx offers a lower-level, fully custom call-handling API (Call Control), and a higher-level Twilio-compatible one (TeXML). This project uses TeXML because its request/response shape (a webhook that returns simple XML instructions) is dramatically simpler to build against correctly than hand-rolling call-state management, at a small cost in flexibility this project doesn't need.
- **Autonomous research kept as a separate addon, not core**: proactive background LLM calls conflict with the core project's rule that nothing runs or spends a token unless you asked for it in the moment - so that trade-off is opt-in and clearly labeled rather than a hidden default.
- **No tool that can execute code or reach arbitrary network destinations, and only one that can act on your behalf**: the worst outcome of a bad or manipulated response is a wrong answer, a mis-triggered lock, a bogus memory entry, or an unwanted click/type in a browser tab you explicitly enabled it for - never control over the machine itself, and never something it can do without you having turned the relevant toggle on first.

## Setup

### Prerequisites

- Windows 10/11
- [Ollama](https://ollama.com) installed
- Python 3.12 available via the `py` launcher
- An NVIDIA GPU is strongly recommended (this was built and tested against an RTX 3090, see the resource-cost section above) - it'll run on CPU, just much slower
- [ffmpeg](https://ffmpeg.org/download.html) installed and on your PATH (only needed for the video-summary feature)
- A webcam, if you want Desk Guard or the "look through the camera" feature
- Chrome, if you want Browser Control (it's a Chrome extension specifically)

### 1. First run

Just double-click `JARVIS.bat`. On first run it will:

1. Create a Python virtual environment in `venv/`
2. Install all dependencies from `backend/requirements.txt`
3. Pull `hermes3` (~4.7GB) and `qwen2.5vl:7b` (~6GB) via Ollama, and build the `jarvis` persona from `backend/Modelfile`
4. Register itself to start automatically at Windows login (a shortcut in your Startup folder - no admin rights needed)
5. Start the tray app, which starts the backend, the native background listener, and opens the dashboard at `http://127.0.0.1:8765`

After that, it starts on its own at every login.

### 2. Using it

Say "Jarvis" followed by anything - the native background listener runs automatically once the tray app is up, with no browser needed at all. If you also want the dashboard's own tab to listen (normally unnecessary), open it in **Chrome or Edge** and turn on "Also listen via this tab's mic" in Settings.

If you have more than one microphone (or any virtual audio device like a voice-changer, a VR headset, or a streaming app), check `PREFERRED_DEVICE_NAMES` near the top of `backend/listener.py` and add your actual mic's name - Windows can silently make a virtual device the system default, which will make Jarvis unable to hear you at all.

### 3. Turning on capabilities

Everything beyond basic conversation is off until you turn it on, from the dashboard's Settings panel or the tray icon menu:

| Toggle | What it does |
|---|---|
| Jarvis Enabled | Master switch for the whole assistant |
| Also listen via this tab's mic | Off by default - lets this specific browser tab also listen, alongside the native listener |
| Screen Access | Lets Jarvis take a screenshot and describe it, on request |
| Camera Access | Lets Jarvis grab a webcam frame and describe it, on request |
| Desk Guard | Webcam presence lock (see below) - requires enrolling your face first |
| Calling | Lets Jarvis place outbound calls - requires phone setup below |
| Call Notifications | Tells you about unanswered outbound calls |
| Location | Three-state cycle: OFF → PC → PHONE → OFF |
| Browser Control | Lets Jarvis act in your current browser tab - see the dedicated section above |
| &nbsp;&nbsp;→ pixel-control fallback | Only visible once Browser Control is on - see above |

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

Copy `backend/telephony_config.example.json` to `backend/telephony_config.json` and fill in the six values you now have: `api_key`, `telnyx_number` (in `+1XXXXXXXXXX` format), `public_base_url` (your ngrok URL, no trailing slash), `account_sid`, `application_sid`, and `owner_number` (your own phone number - calling this one specifically gets Jarvis's normal persona instead of its third-party-caller persona).

**I. Turn on the Calling toggle** and restart `JARVIS_TRAY.pyw`.

Whenever your ngrok URL changes (every restart, unless you have a static domain), update both `telephony_config.json`'s `public_base_url` and the TeXML application's `voice_url` (via a PATCH request like step F) to match.

### 5. Phone book

Add contacts from the dashboard's Phone Book panel (name + number). Say "Jarvis, call `<name>`" to have it place a call - it will only ever call a number that's either in the phone book or spoken explicitly in the conversation.

## Changing the personality or voice

Edit the `SYSTEM` block in `backend/Modelfile`, then rebuild: `ollama create jarvis -f backend\Modelfile`.

Voice: change `VOICE` in `backend/server.py` (default `en-GB-RyanNeural`). Run `edge-tts --list-voices` (inside the venv) to see every option.

## Security notes

- The server only binds to `127.0.0.1` - nothing on your network or the internet can reach it directly (phone calls reach it only via the ngrok tunnel you explicitly start).
- Cross-origin requests to the backend are rejected by default; the one deliberate exception is the browser extension's own `chrome-extension://` origin, which cannot be forged by an ordinary malicious webpage.
- Content pulled from the web is explicitly tagged as untrusted in the model's context, and the `remember` tool caps how much text one call can store, both reducing (not eliminating) prompt-injection risk from a malicious page.
- No tool exists that executes arbitrary code or reaches arbitrary network destinations beyond read-only search/fetch, the phone system, and Browser Control's explicitly-scoped tab actions.
- **Browser Control is the one deliberate exception to "read-only by default"**: once turned on, it can genuinely click and type in your current tab - submit forms, follow links, anything a real click could do. That's why it needs its own toggle, and why its riskiest fallback path needs a fresh on-screen "Allow" every single time, rather than being always-available like the other tools.
- Desk Guard's lock action only ever locks; it has no unlock capability.

## License

This project is released under a custom, plain-language **non-commercial** license - see [`LICENSE.md`](LICENSE.md) for the full text. In short: free to use, modify, and share forever; it may never be sold by anyone; and if it's used as the foundation of another project, or makes up 51% or more of one, that project is bound by the same no-selling rule too.
