const BACKEND = "http://127.0.0.1:8765";

let paused = false;

chrome.storage.local.get(["paused"], (data) => {
  paused = Boolean(data.paused);
});

chrome.storage.onChanged.addListener((changes) => {
  if (changes.paused) paused = Boolean(changes.paused.newValue);
});

// A periodic alarm to nudge the service worker awake if Chrome ever suspends
// it during a quiet stretch - when that happens, this whole file re-runs from
// the top, which calls pollLoop() again at the bottom, so the long-poll loop
// naturally resumes either way.
chrome.alarms.create("jarvis-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => {});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "get_status") {
    sendResponse({ paused });
  }
  return true;
});

// Tracks whichever tab you last switched to inside Chrome, updated continuously
// via onActivated - this is deliberately NOT "whichever window currently has OS
// focus," since the normal way to use Jarvis is to be talking to it while Chrome
// sits unfocused in the background (querying by OS focus alone made the whole
// feature fail exactly when actually used that way).
let lastKnownActiveTabId = null;

chrome.tabs.onActivated.addListener(({ tabId }) => {
  lastKnownActiveTabId = tabId;
});

chrome.tabs.query({ active: true, lastFocusedWindow: true }, (tabs) => {
  if (tabs[0]) lastKnownActiveTabId = tabs[0].id;
});

async function getCurrentTabId() {
  if (lastKnownActiveTabId !== null) {
    try {
      await chrome.tabs.get(lastKnownActiveTabId);
      return lastKnownActiveTabId;
    } catch {
      lastKnownActiveTabId = null;
    }
  }
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  return tab ? tab.id : null;
}

async function ensureContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
    return { ok: true };
  } catch (err) {
    return {
      ok: false,
      error: "Sir is currently viewing a page Chrome doesn't allow extensions to act on (like a new tab page or a chrome:// page) - ask him to switch to a normal webpage first.",
    };
  }
}

async function report(result) {
  try {
    await fetch(`${BACKEND}/api/browser/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(result),
    });
  } catch (err) {
    console.warn("Jarvis: failed to report result to backend", err);
  }
}

async function handlePixelClick(tabId, command) {
  const approval = await chrome.tabs.sendMessage(tabId, {
    type: "show_pixel_warning",
    description: command.description || "",
  });
  if (!approval || !approval.ok) {
    return { ok: false, error: "Sir did not approve the on-screen warning, or it timed out." };
  }

  const [{ result: viewport }] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => ({ width: window.innerWidth, height: window.innerHeight }),
  });
  const x = Math.round(command.x_frac * viewport.width);
  const y = Math.round(command.y_frac * viewport.height);

  await chrome.debugger.attach({ tabId }, "1.3");
  try {
    await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
      type: "mousePressed", x, y, button: "left", clickCount: 1,
    });
    await chrome.debugger.sendCommand({ tabId }, "Input.dispatchMouseEvent", {
      type: "mouseReleased", x, y, button: "left", clickCount: 1,
    });
  } finally {
    await chrome.debugger.detach({ tabId });
  }
  return { ok: true };
}

async function handleCommand(command) {
  if (paused) {
    return report({ ok: false, error: "The Jarvis browser extension is currently paused from its popup." });
  }

  if (command.type === "screenshot") {
    try {
      const dataUrl = await chrome.tabs.captureVisibleTab(undefined, { format: "jpeg", quality: 70 });
      return report({ ok: true, data: { image: dataUrl.split(",")[1] } });
    } catch (err) {
      return report({ ok: false, error: String(err) });
    }
  }

  const tabId = await getCurrentTabId();
  if (tabId === null) {
    return report({ ok: false, error: "No browser window is currently focused for Jarvis to act in." });
  }

  const injected = await ensureContentScript(tabId);
  if (!injected.ok) {
    return report(injected);
  }

  try {
    if (command.type === "click_at") {
      return report(await handlePixelClick(tabId, command));
    }
    const result = await chrome.tabs.sendMessage(tabId, command);
    return report(result);
  } catch (err) {
    return report({ ok: false, error: `Couldn't act on the current tab: ${err}` });
  }
}

console.log("[Jarvis] background script starting up, poll loop about to begin");

async function pollLoop() {
  let loggedFailureOnce = false;
  while (true) {
    try {
      const resp = await fetch(`${BACKEND}/api/browser/poll`);
      const data = await resp.json();
      if (loggedFailureOnce) {
        console.log("[Jarvis] connected to backend successfully");
        loggedFailureOnce = false;
      }
      if (data.command) {
        console.log("[Jarvis] received command:", data.command);
        await handleCommand(data.command);
      }
    } catch (err) {
      if (!loggedFailureOnce) {
        console.error("[Jarvis] poll to backend failed:", err);
        loggedFailureOnce = true;
      }
      await new Promise((r) => setTimeout(r, 5000));
    }
  }
}

pollLoop();
