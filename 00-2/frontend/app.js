const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");

const WAKE_WORD = "jarvis";
const STATE = { LISTENING: "listening", THINKING: "thinking", SPEAKING: "speaking" };

let state = STATE.LISTENING;
let recognition = null;
let shouldAutoRestart = true;
let awake = sessionStorage.getItem("jarvis_awake") === "true";

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

async function playReply(data) {
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
  if (!recognition || state !== STATE.LISTENING) return;
  try {
    recognition.start();
  } catch (e) {
    // already started; ignore
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

    if (awake && lower.includes("night jarvis")) {
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
  log(`Say "${WAKE_WORD}" followed by your request.`);
  startRecognition();
}

const TOGGLE_KEYS = ["ai_enabled", "screen_access", "camera_access", "desk_guard_enabled", "calling_enabled", "call_notifications_enabled", "location_access"];

function setIndicator(key, active) {
  const el = document.getElementById(`ind-${key}`);
  if (el) el.classList.toggle("active", Boolean(active));
}

async function loadSettings() {
  try {
    const resp = await fetch("/api/settings");
    const data = await resp.json();
    for (const key of TOGGLE_KEYS) {
      const el = document.getElementById(`toggle-${key}`);
      if (el) el.checked = Boolean(data[key]);
      setIndicator(key, data[key]);
    }
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
  } catch (err) {
    errorEl.textContent = err.message;
    const el = document.getElementById(`toggle-${key}`);
    if (el) el.checked = !value;
    setIndicator(key, !value);
    return;
  }

  if (key === "location_access" && value) {
    shareLocation();
  }
}

function shareLocation() {
  if (!navigator.geolocation) return;
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      fetch("/api/location", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          latitude: pos.coords.latitude,
          longitude: pos.coords.longitude,
          label: navigator.userAgentData?.mobile ? "phone" : "PC",
        }),
      }).catch((err) => console.warn("Failed to share location:", err));
    },
    (err) => console.warn("Location permission denied or unavailable:", err),
    { maximumAge: 60000, timeout: 10000 }
  );
}

const LOCATION_REFRESH_MS = 5 * 60 * 1000;

function initSettingsPanel() {
  loadSettings().then(() => {
    const locToggle = document.getElementById("toggle-location_access");
    if (locToggle && locToggle.checked) shareLocation();
  });
  for (const key of TOGGLE_KEYS) {
    const el = document.getElementById(`toggle-${key}`);
    if (el) el.addEventListener("change", () => updateSetting(key, el.checked));
  }
  setInterval(() => {
    const locToggle = document.getElementById("toggle-location_access");
    if (locToggle && locToggle.checked) shareLocation();
  }, LOCATION_REFRESH_MS);
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

initRecognition();
initSettingsPanel();
initFileAttach();
initEnrollButton();
initAudioPanel();
initPhoneBookPanel();
tickClock();
setInterval(tickClock, 1000);