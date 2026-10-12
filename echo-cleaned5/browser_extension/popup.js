async function refreshStatus() {
  const status = await chrome.runtime.sendMessage({ type: "get_status" });
  document.getElementById("pauseToggle").checked = Boolean(status.paused);
}

document.getElementById("pauseToggle").addEventListener("change", async (e) => {
  await chrome.storage.local.set({ paused: e.target.checked });
});

refreshStatus();
