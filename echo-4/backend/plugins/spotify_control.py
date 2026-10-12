"""
Plays songs, albums, artists and playlists on this PC's Spotify app by name,
and controls playback. Uses Spotify's Web API through the Connections tab's
"Spotify" connection (the desktop app can't be told to start a specific song
any other way - opening a spotify: link only navigates to it). Starting
playback requires Spotify Premium.

If sir's own playlist matches the requested name it wins over a catalog search,
so "play my gym playlist" plays his, not a stranger's playlist with that name.
"""
import asyncio
import os
import re
import socket
import time
from difflib import SequenceMatcher

import httpx

import launch_utils as lu
import spotify_auth

PLUGIN_NAME = "spotify_control"
TOGGLE_LABEL = "Spotify Playback"
CONFIG_KEY = "spotify_control_enabled"
ENABLED_BY_DEFAULT = True
VRAM_COST = "no local model"
RELATED_CONNECTION = "spotify"

API = "https://api.spotify.com/v1"
KINDS = ("track", "playlist", "album", "artist", "liked")
OWN_PLAYLIST_THRESHOLD = 0.75
_LIKED_RE = re.compile(r"\b(liked|saved|favou?rite)\b.*\b(songs?|tracks?|music)\b|^liked$|\bmy likes\b", re.I)
_GENERIC_PLAYLIST_RE = re.compile(r"^(my|a|the|some|any)?\s*(own\s+)?playlists?$", re.I)
NOT_CONNECTED = (
    "Spotify isn't connected to me yet, so I can't do that. Sir needs to connect it once in the Connections tab "
    "on the dashboard - the setup steps are right there - and then I can play songs, playlists and liked songs."
)


class _NotConnected(Exception):
    pass


async def _headers() -> dict:
    token = await spotify_auth.get_valid_token()
    if token is None:
        raise _NotConnected
    return {"Authorization": f"Bearer {token}"}


def _open_spotify_uri(uri: str) -> None:
    os.startfile(uri)


SPOTIFY_START_TIMEOUT_SECONDS = 25


async def _spotify_running() -> bool:
    return await asyncio.to_thread(lu.is_process_running, "Spotify.exe")


async def _ensure_spotify_running() -> bool:
    """Starts the Spotify app if it isn't up and waits for it, so a command is
    never fired at a player that isn't there."""
    if await _spotify_running():
        return True
    await asyncio.to_thread(_open_spotify_uri, "spotify:")
    return await lu.wait_until(lambda: lu.is_process_running("Spotify.exe"), SPOTIFY_START_TIMEOUT_SECONDS)


async def _devices(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await client.get(f"{API}/me/player/devices", headers=headers)
    resp.raise_for_status()
    return resp.json().get("devices", [])


def _pick_device(devices: list[dict]) -> dict | None:
    if not devices:
        return None
    for device in devices:
        if device.get("is_active"):
            return device
    host = socket.gethostname().lower()
    computers = [d for d in devices if d.get("type") == "Computer"]
    for device in computers:
        if host in (device.get("name") or "").lower():
            return device
    return (computers or devices)[0]


async def _ensure_device(client: httpx.AsyncClient, headers: dict) -> dict | None:
    device = _pick_device(await _devices(client, headers))
    if device:
        return device
    # Spotify isn't running (or hasn't registered yet) - start it and wait for it to show up.
    _open_spotify_uri("spotify:")
    for _ in range(15):
        await asyncio.sleep(1)
        device = _pick_device(await _devices(client, headers))
        if device:
            return device
    return None


def _similar(a: str, b: str) -> float:
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 1.0
    if len(a) >= 3 and (a in b or b in a):
        return 0.85
    return SequenceMatcher(None, a, b).ratio()


async def _all_playlists(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    """Every playlist sir owns or follows (up to 200), as {uri, name}."""
    playlists = []
    url = f"{API}/me/playlists"
    params = {"limit": 50}
    for _ in range(4):
        resp = await client.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            break
        data = resp.json()
        for item in data.get("items", []):
            if item and item.get("uri") and item.get("name"):
                playlists.append({"uri": item["uri"], "name": item["name"]})
        if not data.get("next"):
            break
        url, params = data["next"], None
    return playlists


async def _find_own_playlist(client: httpx.AsyncClient, headers: dict, query: str) -> tuple[str, str, None] | None:
    best: tuple[float, str, str] | None = None
    for playlist in await _all_playlists(client, headers):
        score = _similar(query, playlist["name"])
        if best is None or score > best[0]:
            best = (score, playlist["uri"], playlist["name"])
    if best and best[0] >= OWN_PLAYLIST_THRESHOLD:
        return best[1], best[2], None
    return None


async def _set_shuffle(client: httpx.AsyncClient, headers: dict, device_id: str, state: bool) -> None:
    await client.put(
        f"{API}/me/player/shuffle", headers=headers,
        params={"state": "true" if state else "false", "device_id": device_id},
    )


async def _play_liked(client: httpx.AsyncClient, headers: dict, device: dict) -> str:
    """Liked Songs isn't a playlist. Its context URI is spotify:user:<id>:collection;
    if Spotify rejects that, fall back to queueing the most recent 50 liked tracks."""
    me = await client.get(f"{API}/me", headers=headers)
    if me.status_code != 200:
        return f"Spotify wouldn't tell me who you are (error {me.status_code}) - try reconnecting it."
    user_id = me.json()["id"]
    params = {"device_id": device["id"]}

    resp = await client.put(
        f"{API}/me/player/play", headers=headers, params=params,
        json={"context_uri": f"spotify:user:{user_id}:collection"},
    )
    if resp.status_code in (200, 202, 204):
        return "Playing your Liked Songs"

    saved = await client.get(f"{API}/me/tracks", headers=headers, params={"limit": 50})
    if saved.status_code == 403:
        return "I don't have permission to read your liked songs - reconnect Spotify from the Connections tab."
    uris = [i["track"]["uri"] for i in saved.json().get("items", []) if i.get("track")] if saved.status_code == 200 else []
    if not uris:
        return "You don't have any liked songs yet."
    resp = await client.put(f"{API}/me/player/play", headers=headers, params=params, json={"uris": uris})
    if resp.status_code in (200, 202, 204):
        return "Playing your most recent Liked Songs"
    return f"Spotify couldn't start your liked songs (error {resp.status_code})."


_NOT_THE_ORIGINAL = (
    "remix", "slowed", "sped up", "karaoke", "instrumental", "cover", "tribute", "8-bit", "8 bit",
    "lullaby", "nightcore", "reverb", "live", "version",
)


def _pick_track(items: list[dict], query: str) -> dict:
    """The best match for what was asked, preferring the original recording: remixes, covers
    and so on only win if sir actually asked for one."""
    wanted = query.lower()
    best_score, best = -1.0, items[0]
    for item in items:
        artist = item["artists"][0]["name"] if item.get("artists") else ""
        score = lu.score(query, f"{item['name']} {artist}")
        name = item["name"].lower()
        if any(word in name and word not in wanted for word in _NOT_THE_ORIGINAL):
            score -= 0.25
        score += (item.get("popularity") or 0) / 1000
        if score > best_score:
            best_score, best = score, item
    return best


async def _search(client: httpx.AsyncClient, headers: dict, query: str, kind: str) -> tuple[str, str, str | None] | None:
    """(uri, spoken label, the track's album uri when it's a track)."""
    resp = await client.get(
        f"{API}/search", headers=headers, params={"q": query, "type": kind, "limit": 10 if kind == "track" else 5}
    )
    resp.raise_for_status()
    items = [i for i in resp.json().get(f"{kind}s", {}).get("items", []) if i]
    if not items:
        return None
    item = _pick_track(items, query) if kind == "track" else items[0]
    label = item["name"]
    artists = item.get("artists")
    if artists:
        label += " by " + artists[0]["name"]
    album_uri = (item.get("album") or {}).get("uri") if kind == "track" else None
    return item["uri"], label, album_uri


async def _confirm_playing(client: httpx.AsyncClient, headers: dict, seconds: float = 5.0) -> bool:
    """Spotify can accept a play command (HTTP 204) and then never start - so look."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        resp = await client.get(f"{API}/me/player", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("is_playing") and data.get("item"):
                return True
        await asyncio.sleep(0.5)
    return False


async def _start_playback(
    client: httpx.AsyncClient, headers: dict, device_id: str, kind: str, uri: str, album_uri: str | None
) -> tuple[bool, int]:
    """Starts playback and confirms it. For a single song the desktop player ignores a bare
    track list, so the song is played inside its album (which it honors); the bare form is
    the fallback. Returns (really playing, last HTTP status)."""
    if kind == "track":
        bodies = []
        if album_uri:
            bodies.append({"context_uri": album_uri, "offset": {"uri": uri}})
        bodies.append({"uris": [uri]})
    else:
        bodies = [{"context_uri": uri}]
    status = 0
    for body in bodies:
        resp = await client.put(
            f"{API}/me/player/play", headers=headers, params={"device_id": device_id}, json=body
        )
        status = resp.status_code
        if status not in (200, 202, 204):
            return False, status
        if await _confirm_playing(client, headers):
            return True, status
    return False, status


async def spotify_play(args: dict) -> str:
    query = (args.get("query") or "").strip()
    kind = (args.get("kind") or "track").strip().lower()
    shuffle = args.get("shuffle") if isinstance(args.get("shuffle"), bool) else None
    if kind not in KINDS:
        kind = "track"
    if kind != "liked" and _LIKED_RE.search(query):
        kind = "liked"
    if not query and kind != "liked":
        return "What should I play?"

    try:
        headers = await _headers()
    except _NotConnected:
        search_for = query or "liked songs"
        await asyncio.to_thread(_open_spotify_uri, "spotify:search:" + search_for.replace(" ", "%20"))
        return (
            f"I've opened a Spotify search for {search_for}, but I can't start it myself yet because Spotify isn't "
            "connected to me. Sir needs to connect it once in the Connections tab on the dashboard - the setup steps are "
            "right there - and then I can play songs, playlists and liked songs."
        )

    if not await _ensure_spotify_running():
        return "Spotify isn't running and didn't start - I couldn't open it."

    async with httpx.AsyncClient(timeout=20) as client:
        if kind == "playlist" and _GENERIC_PLAYLIST_RE.match(query):
            names = [p["name"] for p in await _all_playlists(client, headers)]
            if not names:
                return "You don't have any playlists on Spotify yet. I can play your Liked Songs, though."
            return "Which playlist? You have: " + ", ".join(names[:25]) + ("..." if len(names) > 25 else ".")

        device = await _ensure_device(client, headers)
        if device is None:
            return "I couldn't find a Spotify player to play on - the app didn't come up. Try opening Spotify first."

        if kind == "liked":
            result = await _play_liked(client, headers, device)
            if result.startswith("Playing"):
                if shuffle is not None:
                    await _set_shuffle(client, headers, device["id"], shuffle)
                return f"{result} on {device.get('name', 'Spotify')}."
            return result

        found = None
        source = "search"
        if kind == "playlist":
            found = await _find_own_playlist(client, headers, query)
            if found:
                source = "your playlists"
        if found is None:
            found = await _search(client, headers, query, kind)
        if found is None:
            return f"I couldn't find a {kind} matching '{query}' on Spotify."
        uri, label, album_uri = found

        started, status = await _start_playback(client, headers, device["id"], kind, uri, album_uri)
        if started and shuffle is not None:
            await _set_shuffle(client, headers, device["id"], shuffle)

    device_name = device.get("name", "Spotify")
    if started:
        where = " from your playlists" if source == "your playlists" else ""
        return f"Playing {label}{where} on {device_name}."
    if status in (200, 202, 204):
        return (
            f"I asked Spotify to play {label} on {device_name} and it accepted the request, but it never started. "
            "Try pressing play once in the Spotify app, or ask me again in a moment."
        )
    resp_status = status
    if resp_status == 403:
        return "Spotify refused to start playback - this needs a Spotify Premium account."
    if resp_status == 401:
        return "Spotify rejected the connection. Reconnect it from the Connections tab."
    return f"Spotify couldn't start playback (error {resp_status})."


_VK_PLAY_PAUSE, _VK_NEXT, _VK_PREVIOUS = 0xB3, 0xB0, 0xB1


def _press_media_key(vk: int) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)


def _spotify_window_title() -> str:
    import subprocess

    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process -Name Spotify -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle } "
         "| Select-Object -First 1).MainWindowTitle"],
        capture_output=True, text=True, timeout=15, creationflags=0x08000000,
    )
    return out.stdout.strip()


async def _media_key_fallback(action: str) -> str:
    """Playback control that works before Spotify is connected: Spotify listens
    to the system media keys. Play/pause is a toggle, so the window title (a
    track name while playing, plain 'Spotify ...' while paused) decides whether
    a press is needed - otherwise "pause" on an already-paused player would
    start it instead."""
    if action not in ("pause", "resume", "next", "previous"):
        return (
            "Skipping to a time, restarting, shuffle, repeat and volume need Spotify connected to me, and it isn't yet. "
            "Sir can connect it once in the Connections tab on the dashboard."
        )
    if action == "next":
        _press_media_key(_VK_NEXT)
        return "Skipped to the next track."
    if action == "previous":
        _press_media_key(_VK_PREVIOUS)
        return "Back to the previous track."

    title = await asyncio.to_thread(_spotify_window_title)
    if not title:
        _press_media_key(_VK_PLAY_PAUSE)
        return "Sent play/pause to Spotify (I couldn't see its window, so I can't confirm the state)."
    is_paused = title.lower().startswith("spotify") and " - " not in title
    if action == "pause":
        if is_paused:
            return "Spotify is already paused."
        _press_media_key(_VK_PLAY_PAUSE)
        return "Paused."
    if not is_paused:
        return "Spotify is already playing."
    _press_media_key(_VK_PLAY_PAUSE)
    return "Resumed."


async def spotify_playback(args: dict) -> str:
    action = (args.get("action") or "").strip().lower()
    volume = args.get("volume_percent")

    if not await _spotify_running():
        if action != "resume":
            return "Spotify isn't running, so nothing is playing."
        if not await _ensure_spotify_running():
            return "Spotify isn't running and didn't start - I couldn't open it."
        await asyncio.sleep(4)  # let it finish loading before the play command lands

    try:
        headers = await _headers()
    except _NotConnected:
        return await _media_key_fallback(action)

    routes = {
        "pause": ("PUT", "/me/player/pause"),
        "resume": ("PUT", "/me/player/play"),
        "next": ("POST", "/me/player/next"),
        "previous": ("POST", "/me/player/previous"),
    }
    seconds = args.get("seconds")
    relative = args.get("relative") is True
    state_arg = str(args.get("state") or "").strip().lower()

    async def _progress() -> tuple[int, int] | None:
        """(progress_ms, duration_ms) of what's playing, or None if nothing is."""
        resp = await client.get(f"{API}/me/player", headers=headers)
        if resp.status_code != 200:
            return None
        data = resp.json()
        item = data.get("item") or {}
        return int(data.get("progress_ms") or 0), int(item.get("duration_ms") or 0)

    def _clock(ms: int) -> str:
        total = max(0, ms) // 1000
        return f"{total // 60}:{total % 60:02d}"

    async with httpx.AsyncClient(timeout=20) as client:
        if action == "volume":
            if not isinstance(volume, (int, float)):
                return "What volume percent should I set?"
            level = max(0, min(100, int(volume)))
            resp = await client.put(f"{API}/me/player/volume", headers=headers, params={"volume_percent": level})
            done = f"Spotify volume set to {level}%."
        elif action in ("seek", "restart"):
            if action == "seek" and not isinstance(seconds, (int, float)):
                return "What time in the song should I skip to?"
            now = await _progress()
            if now is None:
                return "Nothing is playing on Spotify right now."
            progress_ms, duration_ms = now
            if action == "restart":
                target_ms = 0
            elif relative:
                target_ms = progress_ms + int(seconds * 1000)
            else:
                target_ms = int(seconds * 1000)
            target_ms = max(0, target_ms)
            if duration_ms:
                target_ms = min(target_ms, max(0, duration_ms - 1000))
            resp = await client.put(f"{API}/me/player/seek", headers=headers, params={"position_ms": target_ms})
            done = "Back to the start of the song." if action == "restart" else f"Jumped to {_clock(target_ms)}."
        elif action == "previous":
            now = await _progress()
            resp = await client.post(f"{API}/me/player/previous", headers=headers)
            # Spotify's "previous" only restarts a song that's more than a few
            # seconds in; a second press is what actually goes to the last song.
            if now and now[0] > 3000 and resp.status_code in (200, 202, 204):
                await asyncio.sleep(0.5)
                resp = await client.post(f"{API}/me/player/previous", headers=headers)
            done = "Back to the previous song."
        elif action == "shuffle":
            if state_arg not in ("on", "off", "true", "false"):
                return "Should shuffle be on or off?"
            enabled = state_arg in ("on", "true")
            resp = await client.put(f"{API}/me/player/shuffle", headers=headers, params={"state": str(enabled).lower()})
            done = f"Shuffle {'on' if enabled else 'off'}."
        elif action == "repeat":
            modes = {"off": "off", "track": "track", "song": "track", "one": "track",
                     "playlist": "context", "context": "context", "all": "context", "on": "context"}
            if state_arg not in modes:
                return "Should repeat be off, repeat the song, or repeat the playlist?"
            resp = await client.put(f"{API}/me/player/repeat", headers=headers, params={"state": modes[state_arg]})
            done = {"off": "Repeat off.", "track": "Repeating this song.", "context": "Repeating the playlist."}[modes[state_arg]]
        elif action in routes:
            method, path = routes[action]
            resp = await client.request(method, f"{API}{path}", headers=headers)
            done = {"pause": "Paused.", "resume": "Resumed.", "next": "Skipped to the next track."}[action]
        else:
            return "Unknown Spotify action. Use pause, resume, next, previous, restart, seek, volume, shuffle or repeat."

    if resp.status_code in (200, 202, 204):
        return done
    if resp.status_code == 404:
        return "Spotify has no active player right now - ask me to play something first."
    if resp.status_code == 403:
        return "Spotify refused that - playback control needs a Spotify Premium account."
    return f"Spotify couldn't do that (error {resp.status_code})."


async def spotify_my_playlists(args: dict) -> str:
    try:
        headers = await _headers()
    except _NotConnected:
        return NOT_CONNECTED
    async with httpx.AsyncClient(timeout=20) as client:
        playlists = await _all_playlists(client, headers)
    names = [p["name"] for p in playlists]
    if not names:
        return "You don't have any playlists on Spotify yet, but your Liked Songs can be played."
    shown = ", ".join(names[:40]) + ("..." if len(names) > 40 else "")
    return f"Your playlists ({len(names)}): {shown}. Your Liked Songs can be played too."


async def spotify_now_playing(args: dict) -> str:
    if not await _spotify_running():
        return "Spotify isn't running, so nothing is playing."
    try:
        headers = await _headers()
    except _NotConnected:
        return NOT_CONNECTED
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(f"{API}/me/player/currently-playing", headers=headers)
    if resp.status_code == 204:
        return "Nothing is playing on Spotify right now."
    if resp.status_code != 200:
        return f"Spotify couldn't tell me what's playing (error {resp.status_code})."
    data = resp.json()
    item = data.get("item")
    if not item:
        return "Nothing is playing on Spotify right now."
    artists = ", ".join(a["name"] for a in item.get("artists", []))
    state = "Playing" if data.get("is_playing") else "Paused on"
    return f"{state}: {item['name']}" + (f" by {artists}" if artists else "") + "."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "spotify_play",
            "description": (
                "Starts playing something on this PC's Spotify app, found by name. kind is 'track' for a single "
                "song (the default), 'playlist', 'album', 'artist', or 'liked' for sir's Liked Songs (query can "
                "be empty). For a playlist, sir's own playlists are checked before Spotify's catalog, so \"play "
                "my gym playlist\" plays his. If he just says \"my playlist\" with no name, use kind 'playlist' "
                "and query 'my playlist' - the reply will list his playlists to choose from. Set shuffle true "
                "or false only if he asked for it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Song/playlist/album/artist name, plus artist if given."},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "shuffle": {"type": "boolean", "description": "Only if sir asked for shuffle on or off."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spotify_my_playlists",
            "description": "Lists the names of sir's own Spotify playlists, so he can pick one or ask what he has.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spotify_playback",
            "description": (
                "Controls Spotify playback. Actions: pause, resume, next (next song), previous (the last song), "
                "restart (start this song over), seek (jump within the current song), volume, shuffle, repeat. "
                "For seek, convert what sir said into seconds: \"skip to 1:30\" is seconds 90; \"go to 2 minutes "
                "in\" is 120. For \"skip ahead 30 seconds\" or \"go back 10 seconds\" use relative true with "
                "seconds 30 or -10. For volume use volume_percent 0-100. For shuffle use state on/off; for "
                "repeat use state off, song, or playlist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["pause", "resume", "next", "previous", "restart", "seek", "volume", "shuffle", "repeat"],
                    },
                    "seconds": {"type": "number", "description": "seek only: target position, or jump size if relative."},
                    "relative": {"type": "boolean", "description": "seek only: true to jump forward/back from now."},
                    "volume_percent": {"type": "number"},
                    "state": {"type": "string", "description": "shuffle: on/off. repeat: off, song, or playlist."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spotify_now_playing",
            "description": "Says what song is currently playing on Spotify.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

DISPATCH = {
    "spotify_play": lu.spoken(spotify_play),
    "spotify_my_playlists": lu.spoken(spotify_my_playlists),
    "spotify_playback": lu.spoken(spotify_playback),
    "spotify_now_playing": lu.spoken(spotify_now_playing),
}
