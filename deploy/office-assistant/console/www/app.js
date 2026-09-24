const CHAT = document.getElementById("chat");
const GUIDE = document.getElementById("guide");
const GUIDE_CLOSE = document.getElementById("guide-close");
const GUIDE_KEY = "athena-guide-dismissed";

if (window.self !== window.top) {
  window.location.replace("/dash/");
}

let chatSnap = { kind: "listen", text: "", targets: [], completed: [] };
let briefAgents = [];
let chatLive = false;
let demoTried = false;

function maybeDemo() {
  if (chatLive || demoTried) {
    return;
  }
  demoTried = true;
  if (window.OpsMap && window.OpsMap.startDemo) {
    window.OpsMap.startDemo();
  }
}

function chatSrc() {
  return "/dash/";
}

function applyMap() {
  if (!window.OpsMap || !chatLive) {
    return;
  }
  window.OpsMap.apply({
    kind: chatSnap.kind || "listen",
    text: chatSnap.text || "",
    targets: chatSnap.targets || [],
    completed: chatSnap.completed || [],
    agents: briefAgents,
  });
}

function openGuide() {
  GUIDE.hidden = false;
}

function closeGuide() {
  GUIDE.hidden = true;
  try {
    window.localStorage.setItem(GUIDE_KEY, "1");
  } catch (_err) {
    /* ignore private-mode storage */
  }
}

function copyPrompt(text) {
  const value = String(text || "").trim();
  if (!value) {
    return;
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(value).catch(() => {});
  }
}

function bindGuide() {
  document.querySelectorAll(".guide-open").forEach((button) => {
    button.addEventListener("click", openGuide);
  });
  if (GUIDE_CLOSE) {
    GUIDE_CLOSE.addEventListener("click", closeGuide);
  }
  GUIDE.addEventListener("click", (event) => {
    if (event.target === GUIDE) {
      closeGuide();
    }
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !GUIDE.hidden) {
      closeGuide();
    }
  });
  GUIDE.querySelectorAll("[data-prompt]").forEach((button) => {
    button.addEventListener("click", () => copyPrompt(button.getAttribute("data-prompt")));
  });
  try {
    if (!window.localStorage.getItem(GUIDE_KEY)) {
      openGuide();
    }
  } catch (_err) {
    openGuide();
  }
}

async function refreshBrief() {
  try {
    const response = await fetch("/api/fleet/brief");
    if (!response.ok) {
      throw new Error("brief failed");
    }
    const payload = await response.json();
    briefAgents = payload.agents || [];
    applyMap();
  } catch (_err) {
    /* keep last known pin statuses */
  }
}

async function refreshChat() {
  try {
    const response = await fetch("/api/fleet/chat");
    if (!response.ok) {
      maybeDemo();
      return;
    }
    const snap = await response.json();
    chatLive = true;
    if (window.OpsMap && window.OpsMap.stopDemo) {
      window.OpsMap.stopDemo();
    }
    chatSnap = snap && snap.kind ? snap : { kind: "listen", text: "", targets: [], completed: [] };
    applyMap();
  } catch (_err) {
    maybeDemo();
  }
}

CHAT.src = chatSrc();
CHAT.addEventListener("load", () => {
  try {
    const loc = CHAT.contentWindow.location;
    const path = loc.pathname || "";
    const next = loc.searchParams ? loc.searchParams.get("next") : "";
    const nested = CHAT.contentDocument && CHAT.contentDocument.getElementById("village");
    if (nested || path === "/") {
      CHAT.src = "/dash/";
      return;
    }
    if (path === "/login" && (next === "/" || next === "" || next == null)) {
      loc.replace("/dash/login?next=/dash/");
    }
  } catch (_err) {
    /* cross-origin iframe — Traefik should keep chat on /dash/ */
  }
});
bindGuide();
if (window.OpsMap) {
  window.OpsMap.mount();
}
window.addEventListener("resize", () => {
  if (window.OpsMap) {
    window.OpsMap.layout();
  }
});
applyMap();
refreshBrief();
refreshChat();
setTimeout(maybeDemo, 1600);
setInterval(refreshBrief, 20000);
setInterval(refreshChat, 1200);
