# Jarvis

A local, standalone personal AI assistant. Hermes 3 (via Ollama) is the brain, Edge TTS gives it a British voice, and you can talk to it either through a browser dashboard or a native background listener that works with no browser open at all. It researches the web and remembers what it learns, remembers things about you across restarts, knows your location, looks at photos/video/your screen/your camera, places and receives real phone calls, and can act inside a browser tab through its own on-screen cursor when you ask it to. Optionally it guards your desk with face recognition. No cloud LLM, no subscription. Runs entirely on this machine.

## System requirements & resource cost

**VRAM usage (NVIDIA GPU):**

| Component | VRAM | When it's loaded |
|---|---|---|
| Brain (`hermes3` 8B, the `jarvis` persona) | ~5-6 GB | Always, while the assistant is on |
| Native listener (`faster-whisper`, "small.en", GPU) | ~1 GB | Always, while the assistant is on |
| Vision (`qwen2.5vl:7b`) | ~6 GB | Only while Screen Access, Camera Access, or the browser's pixel-control fallback is on *and* actively used |
| Desk Guard face matching (`insightface`, ONNX) | <1 GB | Only while Desk Guard is enabled |
| Voice output (`edge-tts`) | 0 (not a local model - a network call to Microsoft's TTS service) | N/A |
| Browser Control (normal path) | 0 (no model at all - structured text through the brain that's already loaded) | Only while actively acting in a tab |

Typical steady-state (just talking to it) is **~6-7 GB VRAM**. A moment of looking at your screen, camera, or an unreadable webpage can spike that to **~12-13 GB** while that one request is in flight, then it settles back down.

**Bare minimum:** an 8 GB GPU (e.g. RTX 3060 Ti / RTX 2070) - covers the brain and native listener comfortably; vision requests will be noticeably slower since the vision model may need to swap in rather than sit resident alongside the brain.

**Recommended:** 12 GB+ VRAM (e.g. RTX 3060 12GB, RTX 4070) so the brain and vision model can both stay resident without swapping.

**Built and tested on:** an RTX 3090 (24 GB) - comfortable headroom for everything at once, including Desk Guard running continuously.

**System RAM:** 16 GB minimum, 32 GB recommended. **Disk:** ~15-20 GB free for the Ollama models, Python dependencies, and Whisper/insightface's model downloads (grows slowly afterward - memory and knowledge files are small plain text).

**No GPU?** It'll still run - `faster-whisper` falls back to CPU automatically if the GPU path fails - but the LLM itself will be noticeably slower on CPU. Not designed or tuned for a CPU-only setup.

## Running it

Double-click `JARVIS.bat` once. On first run it will:

1. Create a Python virtual environment in `venv/` (isolated from every other Python project on this machine).
2. Install all backend dependencies.
3. Make sure Ollama is running, pull `hermes3` (~4.7GB) and `qwen2.5vl:7b` (~6GB) if missing, and build the `jarvis` persona model.
4. Register Jarvis to start automatically at every Windows login (a shortcut in your Startup folder, no admin rights needed).
5. Start the tray app, which starts the backend, the native background listener, and opens the dashboard.

After that, **Jarvis starts on its own every time you log into Windows** unless you've turned it off (see Toggles below) — you generally never need to run `JARVIS.bat` again. It's kept around for re-running setup (e.g. after editing the Modelfile) or as a manual fallback if you ever quit the tray app and want to relaunch by hand.

## Using it

**Two ways to talk to it, both landing on the same conversation:**

- **Browser dashboard** — its own microphone is **off by default** (the native listener below already covers this; having both on at once caused it to occasionally hear the same thing twice, slightly differently, and answer both). Turn on "Also listen via this tab's mic" in Settings if you want it anyway. You can always attach a photo or video via the "Attach" button regardless.
- **Native background listener** — runs automatically once the tray app is up, independent of any browser tab. It transcribes what it hears locally (`faster-whisper`, GPU-accelerated) and responds the moment it catches "Jarvis" anywhere in what you said. This is what lets you talk to it from across the room with nothing open on screen.

## Toggles

Everything sensitive is off by default and controlled from either the tray icon (right-click it) or the dashboard's Settings panel — both read/write the same `backend/config.json`, so they always agree:

- **Jarvis Enabled** — master switch. Off means the assistant won't respond (but the tray/server keep running quietly so you can flip it back on).
- **Screen Access** — lets it take a screenshot and describe it, only when you ask it to.
- **Camera Access** — lets it grab a single webcam frame and describe it, only when you ask it to.
- **Desk Guard** — see below. Requires enrolling your face first.
- **Calling** — lets it place outbound phone calls. See Phone Calling below.
- **Call Notifications** — tells you (by calling you back) about unanswered outbound calls.
- **Location** — a three-state cycle button, not a simple on/off: click through OFF → PC → PHONE → OFF. Whichever device matches the current mode is the one that shares its position; the other stays quiet even if its dashboard is open.
- **Browser Control** (+ its own pixel-fallback toggle) — see below.
- **Code canvas** — see below.
- *(each installed plugin)* — its own toggle appears automatically in the Plugins panel and tray menu.

## Desk Guard (webcam presence lock)

Click **"Enroll my face"** on the dashboard first (look at the camera for a few seconds while it captures reference photos), then turn on **Desk Guard**. While enabled, roughly every 15 seconds it checks the webcam:

- No one there → does nothing.
- It's you → does nothing.
- Someone else → **locks the PC using Windows' own lock screen** (the same as pressing Win+L).

Important: **voice cannot unlock Windows.** Getting back in always requires your real Windows password/PIN/Hello, on purpose — that's real security, not something a local AI should ever be able to bypass. Jarvis keeps running and listening in the background the whole time regardless. Every time the workstation is unlocked for real, it automatically captures a fresh reference photo of you, so it keeps adapting to new lighting, glasses, outfits, etc. over time.

False locks are possible (bad lighting, an odd angle) — that's inherent to any face-matching system, not a bug. If Desk Guard is ever misbehaving, turn it off from the tray icon instantly, or just quit the tray app entirely; it's a normal background process, not a driver or service, and Windows always reserves Ctrl+Alt+Del regardless of anything Jarvis does.

## Phone calling

Already configured and working, via `backend/telephony_config.json` (Telnyx + an ngrok tunnel — the tray app keeps ngrok running and self-heals the tunnel URL if it ever changes). Jarvis can place outbound calls (phone book by name, or a raw number given in conversation) and receive inbound calls, having a real spoken conversation over the line using the same brain and voice as everywhere else. Say "Jarvis, call `<name>`" or "Jarvis, hang up" mid-call. When you call the number configured as `owner_number`, it treats you as "sir" the same as always; calling anyone else, it introduces itself as an assistant calling on your behalf and refers to you by nickname to that third party rather than sharing your name outright.

## Knowledge base (what Jarvis learns on its own)

Separate from memory — memory is what you tell it directly, the knowledge base is what it learns by researching. Before answering a factual question via `web_search`, it checks `check_knowledge` first; if it doesn't already know, it searches, answers, then saves what it learned via `save_knowledge` so the same question is instant next time. Saved knowledge lives as plain `.md` files under `backend/knowledge/`, auto-condensed once the total saved text passes 100,000 words so it doesn't grow forever.

There's no separate always-on autonomous research process — it only ever learns something new in response to an actual question you asked, never on a background timer.

## Location awareness

The dashboard can share the browser's location (works from a phone's browser too) to the `get_location` tool, gated by the three-state Location toggle described above. Nothing is written to disk — it's held in memory only, and is overwritten the next time either device reports in.

## Browser Control (Jarvis's own on-screen cursor)

Lets Jarvis click and type inside one browser tab you've explicitly handed it, through his own blue on-screen cursor labeled "Jarvis" — never your real mouse, and only when you ask him to do something there in the moment.

**Installing the extension:** open `chrome://extensions`, turn on **Developer mode** (top right), then just **drag and drop the `browser_extension` folder straight onto that page** — this loads it directly and sidesteps a file-picker quirk where Chrome's own "Load unpacked" dialog can fail to show a folder that's actually there. If you ever need to reload it after an edit, drag it in again or use the refresh icon on its card.

Once installed, turn on **Browser Control** from the tray icon or dashboard Settings (off by default), then just ask - e.g. "Jarvis, click the search box and type in cat videos." No per-tab activation step is needed; it acts on whichever tab is currently active, even while you're looking at something else entirely (that's the normal way to use it).

A second toggle, **pixel-control fallback**, only appears once Browser Control is on, and only matters for pages Jarvis can't read normally (canvas-drawn apps). Every single use of it shows a full-page on-screen warning that must be clicked "Allow" first — no exceptions. The extension's own popup also has an independent pause switch that overrides everything else instantly, regardless of any other toggle.

## Plugin system

Drop a `.py` file into `backend/plugins/` and it's fully installed — its tool schemas, handler(s), and optional config toggle are auto-discovered at startup, with the dashboard's **Plugins** panel and the tray menu both growing a toggle for it automatically. No core file needs editing. `plugins/billing_tracker.py` is the reference example (and genuinely usable — "log $12 for lunch," "how much did I spend last month"). Plugins are dashboard/native-listener only, by construction — phone calls use a small fixed tool list that never touches them.

## Code canvas

"Jarvis, build me a website for X" now gets you a real, live, working preview — not a description in words. Turn on **Code canvas** in Settings (off by default), and a panel appears on the right side of the dashboard: a genuine white preview pane, rendered from the complete HTML/CSS/JS Jarvis writes in one shot each time (there's no partial-update mode - every call passes the whole page, even for a small tweak).

The preview renders in a sandboxed `<iframe sandbox="allow-scripts">` - scripts run so interactive demos actually work, but the generated page has no access to this dashboard, your cookies, or anything else in your real browser; it's a fully isolated, disposable document. The last thing built persists across a dashboard reload (and a backend restart) so it doesn't just vanish, but it's view state, not something saved to memory the way a fact or a knowledge-base entry is.

Widened the model's context window (4096 → 8192 tokens) specifically so a full page fits comfortably in one tool call - there's ample VRAM headroom for this on any GPU this project already recommends.

## Connecting external accounts (Google, GitHub, Discord, Notion)

Four connected-account plugins ship so far, all **read-only by design** — searching/reading, never sending, creating, or writing. Each is its own toggle in the dashboard's Plugins panel and its own row in the **Connections** panel. Not every service uses the same connection mechanism — building each one honestly to how that service actually works, rather than forcing a fake uniform flow, matters more than a tidy story.

### Google (Gmail search, Calendar lookup, Drive search, Slides creation)

A real "Connect Google Account" flow — click a button, sign in, grant access. One-time setup in your own Google Cloud account first:

1. [console.cloud.google.com](https://console.cloud.google.com) → new project → **APIs & Services → Library** → enable Gmail API, Google Calendar API, Google Drive API, and **Google Slides API**.
2. **APIs & Services → OAuth consent screen** → External → fill in an app name → leave it in **Testing** status → add your own Google account under **Test users**.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID** → **Desktop app** (not Web application - this type auto-trusts any `localhost`/`127.0.0.1` address, so there's no redirect URI to type in and get wrong) → name it anything → copy the Client ID and Client secret.
4. Copy `backend/google_oauth_config.example.json` to `backend/google_oauth_config.json`, paste in those two values, restart the backend.
5. Turn on the **Google Workspace** plugin toggle, then click **Connect Google** in the Connections panel and sign in.

Gmail/Calendar/Drive stay read-only. **Slides is the one deliberate exception** — "Jarvis, make me a slideshow about X" genuinely creates a real, editable Google Slides presentation and hands back a real link. Creating a presentation is low-consequence (nothing sent to anyone, trivially edited or deleted afterward), which is why this one gets write access while the others don't. If you connected Google before this was added, you'll need to click **Connect Google** again — the new scope requires a fresh grant.

### GitHub (list issues, pull requests, recent commits)

Same OAuth-click shape as Google:

1. [github.com/settings/developers](https://github.com/settings/developers) → **OAuth Apps → New OAuth App**. Application name: anything. Homepage URL: `http://127.0.0.1:8765`. Authorization callback URL: `http://127.0.0.1:8765/api/connections/github/callback`.
2. Register it, then **Generate a new client secret**. Copy the Client ID and the secret.
3. Copy `backend/github_oauth_config.example.json` to `backend/github_oauth_config.json`, paste in those two values, restart the backend.
4. Turn on the **GitHub** plugin toggle, then click **Connect GitHub** in the Connections panel and authorize.

Worth knowing: GitHub's classic OAuth App scopes don't cleanly split read from write for private repos — the `repo` scope this requests technically permits more than reading. Jarvis's own tools only ever read (list issues/PRs/commits, nothing that creates or modifies), regardless of what the underlying token could do.

### Discord (list servers, list channels, read recent messages)

Different mechanism, since reading/posting in servers you're in requires a bot, not a personal OAuth login:

1. [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application** → name it anything.
2. **Bot** tab → **Add Bot** (or **Reset Token**) → copy the bot token.
3. Still on that page, note the **Application ID** from the **General Information** tab.
4. In the dashboard's Connections panel, paste the bot token into the Discord field (and its Application ID, if the field asks) and click **Save**.
5. Click the **"Invite the bot to a server"** link that appears - pick a server you own/manage, authorize it. Repeat this step for each additional server you want Jarvis to see.
6. Turn on the **Discord** plugin toggle.

The bot is invited with read-only permissions (view channels, read message history) - no send/manage permissions requested.

### Notion (search shared pages/databases)

Notion's own recommended approach for a personal tool - an "internal integration" token, not OAuth:

1. [notion.so/my-integrations](https://www.notion.so/my-integrations) → **New integration** → name it anything, pick your workspace.
2. Copy the **Internal Integration Secret** it shows you.
3. In Notion itself, open each page or database you want Jarvis to be able to search, click **"..." → Connections**, and add your integration - Notion only ever shares what you explicitly connect this way, nothing else in your workspace.
4. In the dashboard's Connections panel, paste the secret into the Notion field and click **Save**.
5. Turn on the **Notion** plugin toggle.

## Changing the personality

Edit the `SYSTEM` block in `backend/Modelfile`, then rebuild:

```
ollama create jarvis -f backend\Modelfile
```

## Changing the voice

`VOICE` in `backend/server.py` (default `en-GB-RyanNeural`). Other British options: `en-GB-ThomasNeural` (male), `en-GB-SoniaNeural` / `en-GB-LibbyNeural` (female).

## Persistent memory

Facts Jarvis learns about you (via the `remember` tool, or by you asking it to remember something) are saved to `backend/memory/facts.json` — plain, human-readable/editable JSON. It's loaded into every conversation automatically. Delete entries any time by editing the file directly.

## Security notes

- The server only ever binds to `127.0.0.1` (this machine only) — nothing on your network or the internet can reach it.
- Cross-origin requests to the backend are rejected (blocks the classic "a malicious website open in another tab silently pokes your local AI" attack).
- Content pulled from the web (search results, fetched pages) is explicitly tagged as untrusted in the model's context, and the `remember` tool caps how much text can be stored per fact — both reduce (not eliminate) the risk of a malicious webpage trying to plant instructions via prompt injection.
- Jarvis has no tool that executes arbitrary code or writes arbitrary files. The worst a compromised or hallucinating response can do is say something wrong, mis-lock the desk, or write a bogus memory entry (visible and editable in `facts.json`) — it cannot take over the machine or act outside these specific tools.
- Desk Guard's lock action only ever locks; it has no unlock capability, so it can't be turned into a way to keep you out.
- **Browser Control is the one deliberate exception to "read-only by default"**: once you've turned it on and activated it on a specific tab, it can genuinely click and type there — submit forms, follow links, anything a real click could do. That's why it needs both a dedicated toggle and a fresh per-tab activation click before it can touch anything, rather than being always-available like the other tools.

## Known limitations - what it can't do, and what it might get wrong

Worth reading before you rely on it for something that matters, based on things actually observed while building and testing this:

- **Browser Control can't touch Chrome's own pages.** The New Tab page, `chrome://` pages, and the Chrome Web Store are hard-blocked from any extension acting on them - not a bug, not fixable, a Chrome security boundary. If it says it can't reach the page, check what tab is actually focused first.
- **Canvas-drawn pages have no readable structure.** `browser_scan_page` finds nothing on pages built entirely on `<canvas>` (some games, some design tools) - the pixel fallback exists for exactly this, but it's slower and needs its own on-screen approval each time.
- **Speech recognition isn't perfect.** The native listener occasionally mishears a word, especially with quiet or fast speech - this can make it act on a slightly wrong transcript. If something seems off, just say it again more clearly rather than assuming it's broken.
- **Tool-calling reliability with a local model is good, not perfect.** Several real bugs were found and fixed during testing - the model skipping a step it was told to always do, describing a browser action as done when it wasn't, needing a tool's own error message spelled out for it instead of acting on it directly. The persona has explicit rules against these now, and they hold up in testing, but a local 8B model won't have the same consistency as a much larger cloud model on every possible phrasing.
- **Don't treat a spoken confirmation as proof for anything that actually matters.** For something consequential - a call you need to be certain went through, a form submission you need to be certain happened - verify directly (check your phone, look at the page yourself) rather than taking "done, sir" as the last word, the same way you'd double-check anything else that's important enough to matter.
- **Multi-window Chrome setups are less tested.** Browser Control tracks whichever tab you last switched to; with several separate Chrome windows open at once, which one it considers "current" hasn't been rigorously exercised.
- **Desk Guard's face matching isn't a security system.** Lighting, glasses, and camera angle can all cause a false lock, or rarely, a false match - see the Desk Guard section above.

## Not included (possible future add-ons)

- A premium TTS voice (e.g. ElevenLabs), free tier capped around 10k characters/month, in place of `edge-tts`.
- Multi-user awareness — memory, the phone book, and conversation history are all global state, not per-person.