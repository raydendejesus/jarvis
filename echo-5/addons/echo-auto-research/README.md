# Jarvis Auto Research (optional addon)

This is a separate, opt-in download from the core Jarvis project. It is **not** installed or wired in by default when you set up core Jarvis from the main repo - you only get this behavior if you deliberately add it, following the steps below.

## What it does

Once wired in and turned on, Jarvis checks in the background, once an hour, whether anything in its memory of you is worth quietly researching and remembering ahead of time - so if you later mention it, it already knows. It never invents topics out of nowhere; it only ever picks something connected to a fact it already has about you, and only if it doesn't already have saved knowledge on that exact thing.

It reuses your existing `jarvis` Ollama model (no second model, no extra VRAM beyond what's already loaded for normal chat) and is capped at a small number of research passes per day (`autonomous_research_daily_limit`, default 3), so the actual added cost is a handful of extra LLM calls and web searches per day at most - not a constantly-running process.

## Why this is a separate download, not part of core

The core project's philosophy is that nothing runs, or spends a token, or reaches the web, unless you actually asked for it in the moment. This addon deliberately breaks that rule in one narrow, capped, and fully opt-in way - it's kept out of the base install so that choice stays explicit and visible instead of being a hidden default. Turn it on if you want a slightly more anticipatory assistant; leave it out entirely if you'd rather it never do anything you didn't just ask for.

## Safety design ("a fence, not a cage")

Autonomous research is deliberately not locked down to a tiny whitelist of "approved" sites - that would make it nearly useless for genuinely varied topics. Instead it uses a **blocklist** approach on two layers:

1. A keyword filter on the topic itself, rejecting anything that even loosely resembles weapons, self-harm, hacking/exploit content, drugs, or other clearly unsafe subject matter, before a single search is ever made.
2. A domain filter on search results (`RESEARCH_BLOCKED_DOMAINS`), which excludes known hacking/cracking forums, exploit-trading sites, piracy indices, and dark-web related domains from what it's willing to learn from.

Both filters apply **only** to this autonomous pipeline. They never touch or restrict your own manual `web_search` / `fetch_webpage` tool calls in normal conversation - those remain completely open, exactly as in core Jarvis.

## Installing it

1. Copy `autoresearch.py` from this folder into your `backend/` folder (next to `server.py`, `tools.py`, etc.).

2. In `backend/config.py`, add these two keys to the `DEFAULTS` dict:

   ```python
   "autonomous_research_enabled": False,
   "autonomous_research_daily_limit": 3,
   ```

3. In `backend/server.py`, add the import near the other local imports:

   ```python
   import autoresearch
   ```

4. In `server.py`'s `lifespan()` function, start and cancel the background loop alongside the existing `guard_task`:

   ```python
   @asynccontextmanager
   async def lifespan(app: FastAPI):
       guard_task = asyncio.create_task(security.guard_loop())
       research_task = asyncio.create_task(autoresearch.research_loop())
       yield
       guard_task.cancel()
       research_task.cancel()
   ```

5. In the `SettingsUpdate` model in `server.py`, add the two optional fields so the toggle and daily limit can be changed at runtime:

   ```python
   autonomous_research_enabled: bool | None = None
   autonomous_research_daily_limit: int | None = None
   ```

6. (Optional but recommended) Add a manual trigger endpoint anywhere among the other `@app.post` routes in `server.py`, useful for testing without waiting up to an hour for the loop to fire on its own:

   ```python
   @app.post("/api/research/run_now")
   async def run_research_now() -> dict:
       try:
           topic = await autoresearch.maybe_research_once()
       except Exception as exc:  # noqa: BLE001 - surface cleanly rather than a raw 500
           return {"researched": None, "detail": f"Research pass failed: {exc}"}
       if topic is None:
           return {"researched": None, "detail": "Nothing to research right now (disabled, limit hit, or nothing new found)."}
       return {"researched": topic}
   ```

7. (Optional) Add a toggle in the dashboard and/or tray icon, the same way every other capability toggle works in this project:
   - `frontend/app.js`: add `"autonomous_research_enabled"` to the `TOGGLE_KEYS` array.
   - `frontend/index.html`: add a status-strip indicator span (`id="ind-autonomous_research_enabled"`) and a Settings-panel checkbox row (`id="toggle-autonomous_research_enabled"`), matching the pattern of the existing toggles.
   - `JARVIS_TRAY.pyw`: add `pystray.MenuItem("Autonomous Research", toggle_flag("autonomous_research_enabled"), checked=flag_checked("autonomous_research_enabled"))` to the tray menu, next to the other toggle items.

8. Rebuild nothing model-wise - this addon doesn't touch the Modelfile or persona. Just restart the backend (and the tray app, if you added the toggle there) so the new code loads.

It stays off (`autonomous_research_enabled: false`) even after installing, until you explicitly turn it on from wherever you wired the toggle, or by editing `backend/config.json` directly.

## Uninstalling

Reverse the steps above: remove the import, the `research_task` lines, the two `SettingsUpdate` fields, the endpoint, any toggle UI you added, and delete `backend/autoresearch.py`. Nothing else in the core project depends on it.
