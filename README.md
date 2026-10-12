# Jarvis

A local, self-hosted AI assistant - a real LLM as the brain (running on your own GPU via Ollama), a natural voice, wake-word listening, memory, and increasingly, real-world capability, all without a cloud AI subscription.

This repo holds five successive versions of the project side by side, each in its own folder, so the progression is visible rather than hidden behind force-pushed history. Each version is a complete, independently-runnable copy with its own README - pick the one you actually want to run (almost certainly the newest), or read through all five to see how it evolved.

## [`00-1/`](00-1/) - the original

Qwen2.5 14B as the brain. Voice, vision (photos/video/screen/camera), persistent memory, web research, and real phone calling. The foundation everything else was built on top of.

## [`00-2/`](00-2/) - Hermes 3, a reactive knowledge base, and location

Swapped the brain to Hermes 3 (less restrictive for a personal assistant's everyday use), added a knowledge base that's separate from memory (things it learns by researching, not things you told it), location awareness, and a fixed tool-calling reliability issue that could otherwise exhaust the model's turn budget on a single request. Also introduces this project's first optional addon - **Jarvis Auto Research** - background self-directed research on a strict daily cap, deliberately kept out of the core install.

## [`00-3/`](00-3/) - Browser Control and a plugin system

Everything from 00-2, plus the ability to act inside your browser: a Chrome extension gives Jarvis his own on-screen cursor that can click, type, scroll, and read whatever page you're currently viewing, only when you ask him to. Reads a page's real structure rather than taking screenshots, so it needs no extra VRAM for the normal case, with a pixel-based fallback (behind its own toggle and an on-screen warning) for pages that don't have any readable structure at all. Also introduces a real plugin system - drop a `.py` file into `backend/plugins/` and it's auto-discovered, no core file editing required, toggle and all - with a working expense tracker (`billing_tracker.py`) as the reference example, plus a real OAuth "Connect Google Account" flow (Gmail/Calendar/Drive, read-only) as a second example of a plugin that needs an external account rather than just a local file. Plugins, browser control, and screen/camera access are all dashboard-only by construction; phone calls use a small fixed tool list that never touches any of them. Also adds persona-level rules against fabricating tool results or invented tool *inputs* - both were caught happening during testing and explicitly fixed.

## [`echo-4/`](echo-4/) - Echo-4 edition to the Jarvis series

The project is renamed from **Jarvis** to **Echo** - this is the Echo-4 edition of the Jarvis series. Everything from 00-3, with a neutral-and-green dashboard, "Echo" as the wake word ("Jarvis" still works), opening apps / Steam games / Spotify / shortcuts by voice, per-contact call permissions, greetings built from the call's subject, a "Sir Page" toggle that phones you when something happens, and a compact toggle panel. Launcher files are `ECHO.bat` / `ECHO_TRAY.pyw` and the persona model is `echo`.

## [`echo-5/`](echo-5/) - Echo-5, cleaned up

The clean-up release. It was meant to run smoother, faster and better - and to be less bloated than every version before it - while keeping all of their abilities and the new ones. Same model, same toggles, same behavior as echo-4, but the server loads in about 57 MB instead of 76 MB and uses about 24% less memory running, loads and replies faster, and no reply can hold everyone else up. Its README has the measured comparison with every earlier version.

## Which one should I run?

`echo-4`, unless you have a specific reason to want an earlier version's exact behavior. Each version's own README has full setup instructions - go to [`echo-4/README.md`](echo-4/README.md) and start there.

## License

All five versions share the same license - see [`00-3/LICENSE.md`](00-3/LICENSE.md) (00-1 uses no formal license file; treat 00-2 and 00-3's terms as the project's intent throughout). In short: free to use, modify, and share forever; it may never be sold by anyone; and if it's used as the foundation of another project, or makes up 51% or more of one, that project is bound by the same no-selling rule too.
