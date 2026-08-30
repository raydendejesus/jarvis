import asyncio
import time

# How long the extension's long-poll request waits before returning empty if
# nothing's queued - it just polls again immediately afterward.
POLL_TIMEOUT_SECONDS = 25

# How long a tool call waits for the extension to execute an action and report
# back before giving up and telling the model it failed.
COMMAND_TIMEOUT_SECONDS = 20

# Comfortably longer than POLL_TIMEOUT_SECONDS so a poll that's mid-wait is
# never mistaken for a dropped connection.
CONNECTION_STALE_SECONDS = POLL_TIMEOUT_SECONDS + 15

_last_poll_at = 0.0
_command_ready = asyncio.Event()
_result_ready = asyncio.Event()
_pending_command: dict | None = None
_pending_result: dict | None = None
_busy = asyncio.Lock()


def is_connected() -> bool:
    return (time.time() - _last_poll_at) < CONNECTION_STALE_SECONDS


async def wait_for_command(timeout: float = POLL_TIMEOUT_SECONDS) -> dict | None:
    """Called by the extension's background script in a loop. Marks the
    extension as connected just by calling this at all, and returns the next
    queued command once one exists, or None if the poll simply timed out with
    nothing to do (normal - the extension immediately polls again)."""
    global _last_poll_at
    _last_poll_at = time.time()
    try:
        await asyncio.wait_for(_command_ready.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    _command_ready.clear()
    return _pending_command


def submit_result(result: dict) -> None:
    """Called by the extension once it's finished executing whatever command
    it was just given, to hand the outcome back to the tool call waiting on it."""
    global _pending_result
    _pending_result = result
    _result_ready.set()


async def run_command(command: dict) -> dict:
    """Called by a tool function. Queues a command for the connected browser
    extension and blocks until it reports a result. Only one command is ever
    in flight at a time, matching there being exactly one browser tab Jarvis
    is acting in at once."""
    async with _busy:
        if not is_connected():
            raise RuntimeError(
                "No browser tab is currently connected - the Jarvis browser extension "
                "needs to be installed and open in a tab for this to work."
            )
        global _pending_command, _pending_result
        _pending_command = command
        _pending_result = None
        _result_ready.clear()
        _command_ready.set()
        try:
            await asyncio.wait_for(_result_ready.wait(), timeout=COMMAND_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            raise RuntimeError("The browser extension didn't respond in time.")
        return _pending_result
