(() => {
  if (window.__jarvisContentScriptLoaded) return;
  window.__jarvisContentScriptLoaded = true;

  const STYLE = `
    #jarvis-cursor {
      position: fixed; z-index: 2147483647; pointer-events: none;
      transition: left 0.4s ease, top 0.4s ease; left: -100px; top: -100px;
      display: flex; align-items: center; gap: 6px;
    }
    #jarvis-cursor .dot {
      width: 18px; height: 18px; border-radius: 50% 50% 50% 0;
      background: #2f6fed; border: 2px solid #ffffff;
      box-shadow: 0 0 8px rgba(47, 111, 237, 0.8);
      transform: rotate(-45deg);
    }
    #jarvis-cursor .label {
      background: #2f6fed; color: #fff; font: 600 11px/1.4 system-ui, sans-serif;
      padding: 2px 6px; border-radius: 4px; white-space: nowrap;
      box-shadow: 0 1px 4px rgba(0,0,0,0.4);
    }
    .jarvis-ripple {
      position: fixed; z-index: 2147483646; pointer-events: none;
      width: 24px; height: 24px; margin-left: -12px; margin-top: -12px;
      border-radius: 50%; border: 2px solid #2f6fed;
      animation: jarvis-ripple-anim 0.5s ease-out forwards;
    }
    @keyframes jarvis-ripple-anim {
      from { transform: scale(0.3); opacity: 1; }
      to { transform: scale(2.2); opacity: 0; }
    }
    #jarvis-pixel-warning {
      position: fixed; inset: 0; z-index: 2147483647;
      background: rgba(0, 0, 0, 0.75); display: flex;
      align-items: center; justify-content: center;
    }
    #jarvis-pixel-warning .box {
      background: #12191c; color: #dfeef2; max-width: 420px; padding: 20px 24px;
      border: 2px solid #ffb347; border-radius: 8px; font: 14px/1.5 system-ui, sans-serif;
      box-shadow: 0 0 30px rgba(255, 179, 71, 0.4);
    }
    #jarvis-pixel-warning .title {
      font-weight: 700; color: #ffb347; margin-bottom: 8px; font-size: 15px;
    }
    #jarvis-pixel-warning button {
      margin-top: 14px; margin-right: 8px; padding: 8px 14px; border: none;
      border-radius: 4px; cursor: pointer; font: 600 13px system-ui, sans-serif;
    }
    #jarvis-pixel-warning #jarvis-warn-allow { background: #ffb347; color: #1a1200; }
    #jarvis-pixel-warning #jarvis-warn-deny { background: #333; color: #eee; }
  `;

  function ensureStyle() {
    if (document.getElementById("jarvis-style")) return;
    const style = document.createElement("style");
    style.id = "jarvis-style";
    style.textContent = STYLE;
    document.documentElement.appendChild(style);
  }

  function ensureCursor() {
    ensureStyle();
    let cursor = document.getElementById("jarvis-cursor");
    if (!cursor) {
      cursor = document.createElement("div");
      cursor.id = "jarvis-cursor";
      cursor.innerHTML = '<div class="dot"></div><div class="label">Jarvis</div>';
      document.documentElement.appendChild(cursor);
    }
    return cursor;
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  async function moveCursorTo(x, y) {
    const cursor = ensureCursor();
    cursor.style.left = `${x - 9}px`;
    cursor.style.top = `${y - 9}px`;
    await sleep(420);
  }

  function showClickRipple(x, y) {
    const ripple = document.createElement("div");
    ripple.className = "jarvis-ripple";
    ripple.style.left = `${x}px`;
    ripple.style.top = `${y}px`;
    document.documentElement.appendChild(ripple);
    setTimeout(() => ripple.remove(), 550);
  }

  // --- Page scanning -------------------------------------------------------

  let elementMap = new Map();
  let nextId = 1;
  const MAX_ELEMENTS = 150;

  const INTERACTIVE_SELECTOR = [
    "button", "a[href]", "input", "textarea", "select",
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
    '[role="textbox"]', '[role="combobox"]', '[role="menuitem"]', '[role="tab"]',
    '[contenteditable="true"]', "[onclick]",
  ].join(", ");

  function isVisible(el) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return false;
    const style = getComputedStyle(el);
    if (style.visibility === "hidden" || style.display === "none") return false;
    if (parseFloat(style.opacity) === 0) return false;
    return true;
  }

  function describeElement(el) {
    const tag = el.tagName.toLowerCase();
    let role = el.getAttribute("role");
    if (!role) {
      if (tag === "a") role = "link";
      else if (tag === "button") role = "button";
      else if (tag === "input") {
        const type = (el.getAttribute("type") || "text").toLowerCase();
        role = type === "checkbox" ? "checkbox" : type === "radio" ? "radio" : "textbox";
      } else if (tag === "textarea") role = "textbox";
      else if (tag === "select") role = "combobox";
      else if (el.isContentEditable) role = "textbox";
      else role = "element";
    }

    let label = el.getAttribute("aria-label") || "";
    if (!label && el.labels && el.labels.length) label = el.labels[0].innerText.trim();
    if (!label) {
      label = (el.innerText || el.value || el.getAttribute("placeholder") || el.getAttribute("title") || el.getAttribute("alt") || "")
        .trim().slice(0, 80);
    }

    const value = tag === "input" || tag === "textarea" ? el.value : el.isContentEditable ? el.innerText : "";

    return {
      role,
      label: label || "(unlabeled)",
      value: value ? String(value).slice(0, 80) : "",
      disabled: Boolean(el.disabled),
    };
  }

  function scanPage() {
    elementMap = new Map();
    nextId = 1;
    const elements = [];
    for (const el of document.querySelectorAll(INTERACTIVE_SELECTOR)) {
      if (!isVisible(el)) continue;
      const id = nextId++;
      elementMap.set(id, el);
      elements.push({ id, ...describeElement(el) });
      if (elements.length >= MAX_ELEMENTS) break;
    }
    return { ok: true, data: { elements } };
  }

  // --- Actions ---------------------------------------------------------

  function setNativeValue(el, value) {
    const proto = Object.getPrototypeOf(el);
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
  }

  async function clickElement(targetId) {
    const el = elementMap.get(targetId);
    if (!el) return { ok: false, error: `No element with id ${targetId} - the page may have changed, scan it again.` };
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(300);
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    await moveCursorTo(x, y);
    showClickRipple(x, y);
    el.click();
    return { ok: true };
  }

  async function typeIntoElement(targetId, text) {
    const el = elementMap.get(targetId);
    if (!el) return { ok: false, error: `No element with id ${targetId} - the page may have changed, scan it again.` };
    el.scrollIntoView({ block: "center", behavior: "smooth" });
    await sleep(300);
    const rect = el.getBoundingClientRect();
    await moveCursorTo(rect.left + rect.width / 2, rect.top + rect.height / 2);
    el.focus();

    if (el.isContentEditable) {
      el.innerText = text;
      el.dispatchEvent(new InputEvent("input", { bubbles: true }));
    } else {
      setNativeValue(el, "");
      for (const ch of text) {
        setNativeValue(el, el.value + ch);
        el.dispatchEvent(new InputEvent("input", { bubbles: true, data: ch }));
        await sleep(20);
      }
    }
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true };
  }

  function scrollPage(direction) {
    window.scrollBy({ top: direction === "down" ? window.innerHeight * 0.8 : -window.innerHeight * 0.8, behavior: "smooth" });
    return { ok: true };
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function showPixelWarning(description) {
    ensureStyle();
    return new Promise((resolve) => {
      const overlay = document.createElement("div");
      overlay.id = "jarvis-pixel-warning";
      overlay.innerHTML = `
        <div class="box">
          <div class="title">⚠ Jarvis wants direct screen control</div>
          <p>Jarvis couldn't read this page's structure normally, so it wants to click directly
          on a pixel location it believes matches: "<b>${escapeHtml(description)}</b>".</p>
          <p>This briefly attaches Chrome's own debugger to this tab (you'll see Chrome's own
          warning bar while it happens) and clicks that exact spot, once.</p>
          <button id="jarvis-warn-allow">Allow this one click</button>
          <button id="jarvis-warn-deny">Cancel</button>
        </div>`;
      document.documentElement.appendChild(overlay);
      const finish = (ok) => { overlay.remove(); resolve({ ok }); };
      overlay.querySelector("#jarvis-warn-allow").addEventListener("click", () => finish(true));
      overlay.querySelector("#jarvis-warn-deny").addEventListener("click", () => finish(false));
    });
  }

  const MAX_PAGE_TEXT_CHARS = 4000;

  function readPage() {
    const title = document.title;
    const url = location.href;
    const text = (document.body.innerText || "").replace(/\s+/g, " ").trim().slice(0, MAX_PAGE_TEXT_CHARS);
    return { ok: true, data: { title, url, text } };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    (async () => {
      if (msg.type === "scan") sendResponse(scanPage());
      else if (msg.type === "read_page") sendResponse(readPage());
      else if (msg.type === "click") sendResponse(await clickElement(msg.target_id));
      else if (msg.type === "type") sendResponse(await typeIntoElement(msg.target_id, msg.text));
      else if (msg.type === "scroll") sendResponse(scrollPage(msg.direction));
      else if (msg.type === "show_pixel_warning") sendResponse(await showPixelWarning(msg.description));
      else sendResponse({ ok: false, error: `Unknown command: ${msg.type}` });
    })();
    return true;
  });
})();
