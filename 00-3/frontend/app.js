const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");

const WAKE_WORD = "jarvis";
const STATE = { LISTENING: "listening", THINKING: "thinking", SPEAKING: "speaking" };

let state = STATE.LISTENING;
let recognition = null;
let shouldAutoRestart = true;
let awake = sessionStorage.getItem("jarvis_awake") === "true";

// The native background listener (listener.py) already listens continuously,
// independent of any browser tab - so this tab's own speech recognition is
// redundant by default, and having both active at once meant two separate
// engines could transcribe the same thing you said slightly differently,
// each firing its own full reply (heard as overlapping or back-to-back
// "two voices"). Off by default; opt in per-browser via Settings if wanted.
const DASHBOARD_MIC_KEY = "jarvis_dashboard_mic_enabled";
let micEnabled = localStorage.getItem(DASHBOARD_MIC_KEY) === "true";

function setAwake(value) {
  awake = value;
  sessionStorage.setItem("jarvis_awake", value ? "true" : "false");
  if (state === STATE.LISTENING) setState(STATE.LISTENING);
}

function setState(next) {
  state = next;
  document.body.classList.remove("thinking", "speaking");
  if (next === STATE.THINKING) document.body.classList.add("thinking");
  if (next === STATE.SPEAKING) document.body.classList.add("speaking");
  statusEl.textContent = next === STATE.LISTENING ? (awake ? "awake" : "listening") : next;
}

function log(text) {
  logEl.textContent = text;
}

function extractCommand(transcript) {
  const lower = transcript.toLowerCase();
  const idx = lower.indexOf(WAKE_WORD);
  if (idx === -1) return null;
  const after = transcript.slice(idx + WAKE_WORD.length).replace(/^[\s,.:!-]+/, "");
  return after.trim();
}

function showCanvas(html, title) {
  const panel = document.getElementById("canvasPanel");
  const frame = document.getElementById("canvasFrame");
  const titleEl = document.getElementById("canvasTitle");
  if (!panel || !frame) return;
  frame.srcdoc = html;
  if (titleEl) titleEl.textContent = title ? `- ${title}` : "";
  panel.hidden = false;
}

async function loadCanvasOnStartup() {
  try {
    const [canvasResp, settingsResp] = await Promise.all([fetch("/api/canvas"), fetch("/api/settings")]);
    const data = await canvasResp.json();
    const settings = await settingsResp.json().catch(() => ({}));
    if (data.html) {
      showCanvas(data.html, data.title);
    } else if (settings.code_canvas_enabled) {
      showCanvas(
        '<body style="font-family: system-ui, sans-serif; color: #94a3b8; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; padding: 20px;"><p>Nothing built yet - ask Jarvis to build you something.</p></body>',
        ""
      );
    }
  } catch (err) {
    console.error("Failed to load canvas:", err);
  }
}

async function playReply(data) {
  if (data.canvas_html) showCanvas(data.canvas_html, data.canvas_title);

  if (!data.reply || !data.audio) {
    // Jarvis is switched off - stay properly silent, not even a spoken
    // acknowledgment, and just resume listening for when it's turned back on.
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
    return;
  }

  log(`Jarvis: ${data.reply}`);
  setState(STATE.SPEAKING);

  const audio = new Audio(`data:${data.mime};base64,${data.audio}`);
  const speakerId = localStorage.getItem("jarvis_speaker");
  if (speakerId && typeof audio.setSinkId === "function") {
    try {
      await audio.setSinkId(speakerId);
    } catch (err) {
      console.warn("Could not switch output device:", err);
    }
  }
  const resume = () => {
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
  };
  audio.onended = resume;
  audio.onerror = resume;
  await audio.play();
}

function pauseListening() {
  shouldAutoRestart = false;
  if (recognition) recognition.stop();
  setState(STATE.THINKING);
}

async function sendToJarvis(message) {
  pauseListening();
  log(`You: ${message}`);

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!resp.ok) throw new Error(`Server error ${resp.status}`);
    const data = await resp.json();
    await playReply(data);
  } catch (err) {
    console.error(err);
    log(`Error: ${err.message}`);
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
  }
}

async function uploadFile(file) {
  pauseListening();
  log(`Uploading ${file.name}...`);

  try {
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch("/api/upload", { method: "POST", body: form });
    if (!resp.ok) throw new Error(`Server error ${resp.status}`);
    const data = await resp.json();
    await playReply(data);
  } catch (err) {
    console.error(err);
    log(`Error: ${err.message}`);
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
  }
}

function startRecognition() {
  if (!micEnabled || !recognition || state !== STATE.LISTENING) return;
  try {
    recognition.start();
  } catch (e) {
    // already started; ignore
  }
}

function setDashboardMic(enabled) {
  micEnabled = enabled;
  localStorage.setItem(DASHBOARD_MIC_KEY, enabled ? "true" : "false");
  if (enabled) {
    shouldAutoRestart = true;
    setState(STATE.LISTENING);
    startRecognition();
    log(`Say "${WAKE_WORD}" followed by your request.`);
  } else {
    shouldAutoRestart = false;
    if (recognition) recognition.stop();
    statusEl.textContent = "muted (native listener active)";
    log("This tab's microphone is off - Jarvis is still listening via the background service.");
  }
}

function initRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    setState(STATE.LISTENING);
    statusEl.textContent = "unsupported browser";
    log("Speech recognition requires Chrome or Edge.");
    return;
  }

  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.lang = "en-GB";

  recognition.onresult = (event) => {
    const last = event.results[event.results.length - 1];
    const transcript = last[0].transcript;
    const lower = transcript.toLowerCase();

    if (awake && lower.includes("night") && lower.includes(WAKE_WORD)) {
      setAwake(false);
      log('Back to sleep - say "Jarvis" to wake me again.');
      return;
    }

    if (awake) {
      sendToJarvis(transcript.trim());
      return;
    }

    const command = extractCommand(transcript);
    if (command) {
      setAwake(true);
      sendToJarvis(command);
    } else {
      log(`Heard: "${transcript}" (no wake word)`);
    }
  };

  recognition.onerror = (event) => {
    console.warn("Recognition error:", event.error);
    log(`Mic error: ${event.error}`);
  };

  recognition.onend = () => {
    if (shouldAutoRestart && state === STATE.LISTENING) {
      startRecognition();
    }
  };

  setState(STATE.LISTENING);
  if (micEnabled) {
    log(`Say "${WAKE_WORD}" followed by your request.`);
    startRecognition();
  } else {
    statusEl.textContent = "muted (native listener active)";
    log("This tab's microphone is off by default - the always-on background listener already covers voice commands. Turn this on in Settings if you also want this tab listening.");
  }
}

const TOGGLE_KEYS = ["ai_enabled", "screen_access", "camera_access", "desk_guard_enabled", "calling_enabled", "call_notifications_enabled", "browser_control_enabled", "browser_pixel_fallback_enabled", "code_canvas_enabled"];

function setIndicator(key, active) {
  const el = document.getElementById(`ind-${key}`);
  if (el) el.classList.toggle("active", Boolean(active));
}

function isMobileDevice() {
  if (navigator.userAgentData) return Boolean(navigator.userAgentData.mobile);
  return /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);
}

const LOCATION_MODE_CYCLE = ["off", "pc", "phone"];
const LOCATION_MODE_LABEL = { off: "OFF", pc: "PC", phone: "PHONE" };
let currentLocationMode = "off";

function renderLocationMode(mode) {
  currentLocationMode = mode;
  const ind = document.getElementById("ind-location_mode");
  if (ind) {
    ind.textContent = `LOC: ${LOCATION_MODE_LABEL[mode]}`;
    ind.classList.toggle("active", mode !== "off");
    ind.classList.toggle("mode-pc", mode === "pc");
    ind.classList.toggle("mode-phone", mode === "phone");
  }
  const btn = document.getElementById("toggle-location_mode");
  if (btn) btn.textContent = `Location: ${LOCATION_MODE_LABEL[mode]} (click to change)`;
}

async function cycleLocationMode() {
  const errorEl = document.getElementById("settingsError");
  errorEl.textContent = "";
  const nextMode = LOCATION_MODE_CYCLE[(LOCATION_MODE_CYCLE.indexOf(currentLocationMode) + 1) % 3];
  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ location_mode: nextMode }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `Server error ${resp.status}`);
    }
  } catch (err) {
    errorEl.textContent = err.message;
    return;
  }
  renderLocationMode(nextMode);
  maybeShareLocation();
}

function maybeShareLocation() {
  const thisDeviceMode = isMobileDevice() ? "phone" : "pc";
  if (currentLocationMode !== thisDeviceMode) return;
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      fetch("/api/location", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          label: thisDeviceMode === "phone" ? "phone" : "PC",
        }),
      }).catch((err) => console.warn("Failed to share location:", err));
    },
    (err) => console.warn("Location permission denied or unavailable:", err),
    { maximumAge: 60000, timeout: 10000 }
  );
}

const LOCATION_REFRESH_MS = 5 * 60 * 1000;

async function loadSettings() {
  try {
    const resp = await fetch("/api/settings");
    const data = await resp.json();
    for (const key of TOGGLE_KEYS) {
      const el = document.getElementById(`toggle-${key}`);
      if (el) el.checked = Boolean(data[key]);
      setIndicator(key, data[key]);
    }
    renderLocationMode(data.location_mode || "off");
  } catch (err) {
    console.error("Failed to load settings:", err);
  }
}

async function updateSetting(key, value) {
  const errorEl = document.getElementById("settingsError");
  errorEl.textContent = "";
  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    });
    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      throw new Error(data.detail || `Server error ${resp.status}`);
    }
    setIndicator(key, value);
    if (key === "code_canvas_enabled") {
      if (value) loadCanvasOnStartup();
      else {
        const panel = document.getElementById("canvasPanel");
        if (panel) panel.hidden = true;
      }
    }
  } catch (err) {
    errorEl.textContent = err.message;
    const el = document.getElementById(`toggle-${key}`);
    if (el) el.checked = !value;
    setIndicator(key, !value);
  }
}

function initSettingsPanel() {
  loadSettings().then(() => maybeShareLocation());
  for (const key of TOGGLE_KEYS) {
    const el = document.getElementById(`toggle-${key}`);
    if (el) el.addEventListener("change", () => updateSetting(key, el.checked));
  }
  const locBtn = document.getElementById("toggle-location_mode");
  if (locBtn) locBtn.addEventListener("click", cycleLocationMode);
  setInterval(maybeShareLocation, LOCATION_REFRESH_MS);
}

function initFileAttach() {
  const input = document.getElementById("fileInput");
  if (!input) return;
  input.addEventListener("change", () => {
    if (input.files && input.files[0]) {
      uploadFile(input.files[0]);
      input.value = "";
    }
  });
}

function initEnrollButton() {
  const btn = document.getElementById("enrollBtn");
  const resultEl = document.getElementById("enrollResult");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    resultEl.textContent = "Capturing... look at the camera.";
    try {
      const resp = await fetch("/api/security/enroll", { method: "POST" });
      const data = await resp.json();
      resultEl.textContent = `Captured ${data.captured}/${data.requested} reference photos.`;
    } catch (err) {
      resultEl.textContent = `Error: ${err.message}`;
    } finally {
      btn.disabled = false;
    }
  });
}

function tickClock() {
  const el = document.getElementById("clock");
  if (el) el.textContent = new Date().toLocaleTimeString([], { hour12: false });
}

let levelStream = null;
let levelAudioCtx = null;
let levelAnimFrame = null;

function stopLevelMeter() {
  if (levelAnimFrame) cancelAnimationFrame(levelAnimFrame);
  if (levelStream) levelStream.getTracks().forEach((t) => t.stop());
  if (levelAudioCtx) levelAudioCtx.close();
  levelStream = null;
  levelAudioCtx = null;
  levelAnimFrame = null;
  const bar = document.getElementById("levelBar");
  if (bar) bar.style.width = "0%";
}

async function startLevelMeter(deviceId) {
  stopLevelMeter();
  try {
    levelStream = await navigator.mediaDevices.getUserMedia({
      audio: deviceId ? { deviceId: { exact: deviceId } } : true,
    });
  } catch (err) {
    console.warn("Could not open microphone for level meter:", err);
    return;
  }

  levelAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = levelAudioCtx.createMediaStreamSource(levelStream);
  const analyser = levelAudioCtx.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser);
  const data = new Uint8Array(analyser.frequencyBinCount);
  const bar = document.getElementById("levelBar");

  function tick() {
    analyser.getByteFrequencyData(data);
    const avg = data.reduce((a, b) => a + b, 0) / data.length;
    if (bar) bar.style.width = `${Math.min(100, (avg / 255) * 200)}%`;
    levelAnimFrame = requestAnimationFrame(tick);
  }
  tick();
}

async function populateAudioDevices() {
  const micSelect = document.getElementById("micSelect");
  const speakerSelect = document.getElementById("speakerSelect");
  if (!micSelect || !speakerSelect) return;

  const devices = await navigator.mediaDevices.enumerateDevices();
  const mics = devices.filter((d) => d.kind === "audioinput");
  const speakers = devices.filter((d) => d.kind === "audiooutput");

  micSelect.innerHTML = "";
  speakerSelect.innerHTML = "";
  mics.forEach((d, i) => micSelect.add(new Option(d.label || `Microphone ${i + 1}`, d.deviceId)));
  speakers.forEach((d, i) => speakerSelect.add(new Option(d.label || `Speaker ${i + 1}`, d.deviceId)));

  const savedMic = localStorage.getItem("jarvis_mic");
  const savedSpeaker = localStorage.getItem("jarvis_speaker");
  if (savedMic && mics.some((d) => d.deviceId === savedMic)) micSelect.value = savedMic;
  if (savedSpeaker && speakers.some((d) => d.deviceId === savedSpeaker)) speakerSelect.value = savedSpeaker;
}

function initAudioPanel() {
  const panel = document.getElementById("audioPanel");
  const micSelect = document.getElementById("micSelect");
  const speakerSelect = document.getElementById("speakerSelect");
  if (!panel || !micSelect || !speakerSelect) return;

  panel.addEventListener("toggle", async () => {
    if (!panel.open) {
      stopLevelMeter();
      return;
    }
    try {
      const primer = await navigator.mediaDevices.getUserMedia({ audio: true });
      primer.getTracks().forEach((t) => t.stop());
    } catch (err) {
      console.warn("Microphone permission not granted:", err);
    }
    await populateAudioDevices();
    if (micSelect.value) startLevelMeter(micSelect.value);
  });

  micSelect.addEventListener("change", () => {
    localStorage.setItem("jarvis_mic", micSelect.value);
    startLevelMeter(micSelect.value);
  });

  speakerSelect.addEventListener("change", () => {
    localStorage.setItem("jarvis_speaker", speakerSelect.value);
  });
}

function renderPhoneBook(entries) {
  const list = document.getElementById("phoneBookList");
  if (!list) return;

  const countEl = document.getElementById("pbCount");
  if (countEl) countEl.textContent = entries.length ? `(${entries.length})` : "";

  list.innerHTML = "";
  for (const entry of entries) {
    const li = document.createElement("li");
    const info = document.createElement("span");
    info.innerHTML = `<span class="pb-name">${entry.name}</span><br><span class="pb-number">${entry.number}</span>`;
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "pb-remove";
    delBtn.textContent = "remove";
    delBtn.addEventListener("click", () => deletePhoneBookEntry(entry.name));
    li.appendChild(info);
    li.appendChild(delBtn);
    list.appendChild(li);
  }
}

async function loadPhoneBook() {
  try {
    const resp = await fetch("/api/phonebook");
    const entries = await resp.json();
    renderPhoneBook(entries);
  } catch (err) {
    console.error("Failed to load phone book:", err);
  }
}

async function deletePhoneBookEntry(name) {
  try {
    const resp = await fetch(`/api/phonebook/${encodeURIComponent(name)}`, { method: "DELETE" });
    const entries = await resp.json();
    renderPhoneBook(entries);
  } catch (err) {
    console.error("Failed to delete phone book entry:", err);
  }
}

function initPhoneBookPanel() {
  const form = document.getElementById("phoneBookForm");
  if (!form) return;
  loadPhoneBook();
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const nameEl = document.getElementById("pbName");
    const numberEl = document.getElementById("pbNumber");
    const name = nameEl.value.trim();
    const number = numberEl.value.trim();
    if (!name || !number) return;

    try {
      const resp = await fetch("/api/phonebook", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, number }),
      });
      const entries = await resp.json();
      renderPhoneBook(entries);
      nameEl.value = "";
      numberEl.value = "";
    } catch (err) {
      console.error("Failed to add phone book entry:", err);
    }
  });
}

function initDashboardMicToggle() {
  const el = document.getElementById("toggle-dashboard-mic");
  if (!el) return;
  el.checked = micEnabled;
  el.addEventListener("change", () => setDashboardMic(el.checked));
}

function buildPluginRow(plugin, errorEl) {
  const row = document.createElement("label");
  row.className = "switch-row";
  const span = document.createElement("span");
  span.textContent = plugin.label + (plugin.always_on ? " (always on)" : "");
  const vram = document.createElement("span");
  vram.className = "vram-tag";
  vram.textContent = plugin.vram_cost || "no local model";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = plugin.enabled;
  input.disabled = plugin.always_on;
  const switchSpan = document.createElement("span");
  switchSpan.className = "switch";
  row.append(span, vram, input, switchSpan);

  if (!plugin.always_on) {
    input.addEventListener("change", async () => {
      errorEl.textContent = "";
      try {
        const resp = await fetch(`/api/plugins/${encodeURIComponent(plugin.name)}/toggle`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: input.checked }),
        });
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({}));
          throw new Error(data.detail || `Server error ${resp.status}`);
        }
      } catch (err) {
        errorEl.textContent = err.message;
        input.checked = !input.checked;
      }
    });
  }
  return row;
}

async function loadPlugins() {
  const connectedEl = document.getElementById("pluginsListConnected");
  const standaloneEl = document.getElementById("pluginsListStandalone");
  const errorEl = document.getElementById("pluginsError");
  if (!connectedEl || !standaloneEl) return;
  try {
    const resp = await fetch("/api/plugins");
    const plugins = await resp.json();
    connectedEl.innerHTML = "";
    standaloneEl.innerHTML = "";

    const connected = plugins.filter((p) => p.related_connection);
    const standalone = plugins.filter((p) => !p.related_connection);

    if (!connected.length) {
      connectedEl.innerHTML = '<p class="hint-text">None yet.</p>';
    } else {
      for (const plugin of connected) {
        const row = buildPluginRow(plugin, errorEl);
        const hint = document.createElement("p");
        hint.className = "hint-text";
        hint.textContent = `Needs the "${plugin.related_connection}" connection - see the Connections tab.`;
        connectedEl.append(row, hint);
      }
    }

    if (!standalone.length) {
      standaloneEl.innerHTML = '<p class="hint-text">None yet.</p>';
    } else {
      for (const plugin of standalone) {
        standaloneEl.appendChild(buildPluginRow(plugin, errorEl));
      }
    }
  } catch (err) {
    console.error("Failed to load plugins:", err);
  }
}

const TOKEN_FIELD_BY_SERVICE = {
  discord: { field: "bot_token", placeholder: "Bot token" },
  notion: { field: "integration_secret", placeholder: "Integration secret" },
};

async function loadConnections() {
  const listEl = document.getElementById("connectionsList");
  if (!listEl) return;
  try {
    const [connResp, pluginsResp] = await Promise.all([fetch("/api/connections"), fetch("/api/plugins")]);
    const connections = await connResp.json();
    const plugins = await pluginsResp.json().catch(() => []);
    listEl.innerHTML = "";
    for (const conn of connections) {
      const row = document.createElement("div");
      row.className = "connection-row";

      const label = document.createElement("span");
      label.textContent = conn.label;
      row.appendChild(label);

      if (conn.connected) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "hud-btn";
        btn.textContent = "Connected ✓ (Disconnect)";
        btn.addEventListener("click", async () => {
          await fetch(`/api/connections/${conn.name}/disconnect`, { method: "POST" });
          loadConnections();
        });
        row.appendChild(btn);
      } else if (conn.auth_style === "oauth" && !conn.configured) {
        const idInput = document.createElement("input");
        idInput.type = "text";
        idInput.placeholder = "Client ID";
        const secretInput = document.createElement("input");
        secretInput.type = "password";
        secretInput.placeholder = "Client secret";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "hud-btn";
        btn.textContent = "Save";
        btn.addEventListener("click", async () => {
          if (!idInput.value.trim() || !secretInput.value.trim()) return;
          const resp = await fetch(`/api/connections/${conn.name}/credentials`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ client_id: idInput.value.trim(), client_secret: secretInput.value.trim() }),
          });
          if (resp.ok) loadConnections();
        });
        row.append(idInput, secretInput, btn);
      } else if (conn.auth_style === "oauth") {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "hud-btn";
        btn.textContent = "Connect " + conn.label.split(" (")[0];
        btn.addEventListener("click", () => {
          window.location.href = `/api/connections/${conn.name}/start`;
        });
        row.appendChild(btn);
      } else if (conn.auth_style === "token") {
        const spec = TOKEN_FIELD_BY_SERVICE[conn.name];
        const input = document.createElement("input");
        input.type = "password";
        input.placeholder = spec ? spec.placeholder : "Token";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "hud-btn";
        btn.textContent = "Save";
        btn.addEventListener("click", async () => {
          if (!input.value.trim()) return;
          await fetch(`/api/connections/${conn.name}/token`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [spec.field]: input.value.trim() }),
          });
          loadConnections();
        });
        row.append(input, btn);
      }

      listEl.appendChild(row);

      const relatedPlugin = plugins.find((p) => p.related_connection === conn.name);
      if (relatedPlugin) {
        const pluginHint = document.createElement("p");
        pluginHint.className = "hint-text";
        pluginHint.textContent = conn.connected
          ? `Turn on the "${relatedPlugin.label}" plugin in the Plugins tab to actually use this.`
          : `Once connected, turn on the "${relatedPlugin.label}" plugin in the Plugins tab to use it.`;
        listEl.appendChild(pluginHint);
      }

      if (conn.name === "discord" && conn.invite_url) {
        const inviteP = document.createElement("p");
        inviteP.className = "hint-text";
        const a = document.createElement("a");
        a.href = conn.invite_url;
        a.target = "_blank";
        a.rel = "noopener";
        a.textContent = "Invite the bot to a server";
        inviteP.appendChild(a);
        listEl.appendChild(inviteP);
      }
    }
  } catch (err) {
    console.error("Failed to load connections:", err);
  }
}

function checkConnectionRedirectResult() {
  const params = new URLSearchParams(window.location.search);
  const service = params.get("google_connect") !== null ? "google"
    : params.get("github_connect") !== null ? "github" : null;
  if (!service) return;
  const result = params.get(`${service}_connect`);
  const msgEl = document.getElementById("connectionsStatusMsg");
  if (msgEl) {
    msgEl.textContent = result === "success"
      ? `${service[0].toUpperCase()}${service.slice(1)} account connected.`
      : `${service[0].toUpperCase()}${service.slice(1)} connection failed: ${params.get("detail") || "unknown error"}`;
  }
  activateTab("connections");
  window.history.replaceState({}, "", window.location.pathname);
}

function activateTab(name) {
  for (const btn of document.querySelectorAll(".tab-btn")) {
    btn.classList.toggle("active", btn.dataset.tab === name);
  }
  for (const panel of document.querySelectorAll(".tab-panel")) {
    panel.hidden = panel.id !== `tab-${name}`;
  }
  try {
    localStorage.setItem("jarvis_active_tab", name);
  } catch (err) {
    // storage unavailable - not worth failing over
  }
}

function initTabs() {
  const bar = document.querySelector(".tab-bar");
  if (!bar) return;
  bar.addEventListener("click", (event) => {
    const btn = event.target.closest(".tab-btn");
    if (btn) activateTab(btn.dataset.tab);
  });
  let saved = "settings";
  try {
    saved = localStorage.getItem("jarvis_active_tab") || "settings";
  } catch (err) {
    // storage unavailable - default to settings
  }
  activateTab(saved);
}

initRecognition();
initDashboardMicToggle();
initSettingsPanel();
initTabs();
initFileAttach();
initEnrollButton();
initAudioPanel();
initPhoneBookPanel();
loadPlugins();
loadConnections();
loadCanvasOnStartup();
checkConnectionRedirectResult();
tickClock();
setInterval(tickClock, 1000);