const MOUTH = document.getElementById("athena-mouth");
const EYES = document.getElementById("athena-eyes");
const BUBBLE = document.getElementById("bubble");
const BUBBLE_TEXT = document.getElementById("bubble-text");
const CHAT = document.getElementById("chat");
const BLINK = "/avatars/athena/vtuber/layers/eyes-blink.png";
const VISEME_MS = 140;
const GREET = "Welcome. Please sign in to use the dashboard.";
const GUIDE_KEY = "athena-guide-dismissed";
const GUIDE = document.getElementById("guide");
const GUIDE_CLOSE = document.getElementById("guide-close");
const SETTLED = "The fleet is settled.";
const WAITING = [
  "Whenever you're ready.",
  "I'm here if you need me.",
  "Waiting for your next question.",
];
const THINKING = "Give me a moment";
const DONE_MS = 2800;

let chatting = false;
let visemeTimer = 0;
let visemeIndex = 0;
let visemeFrames = [];
let blinking = false;
let mailIndex = 0;
let lastChatText = "";
let chatSnap = { kind: "listen", text: "" };
let mailbox = [];
let doneUntil = 0;

function visemesFor(text) {
  const vowel = { a: "a", e: "e", i: "i", o: "o", u: "u", y: "i" };
  const frames = [];
  const words = String(text).toLowerCase().match(/[a-z]+/g) || [];
  for (let w = 0; w < words.length; w += 1) {
    let word = words[w];
    if (word.length > 2 && word.endsWith("e") && /[aeiouy]/.test(word.slice(0, -1))) {
      word = word.slice(0, -1);
    }
    let last = "";
    for (let i = 0; i < word.length; i += 1) {
      const shape = vowel[word[i]];
      if (shape && shape !== last) {
        frames.push(shape);
        last = shape;
      }
    }
    frames.push("");
  }
  return frames;
}

function mouthSrc(name) {
  return `/avatars/athena/vtuber/layers/mouth-${name}.png`;
}

function chatSrc() {
  return "/dash/";
}

function setBubble(text, kind) {
  if (!text) {
    BUBBLE.hidden = true;
    return;
  }
  BUBBLE.hidden = false;
  BUBBLE.className = `bubble ${kind || "listen"}`;
  if (BUBBLE_TEXT.textContent === text) {
    return;
  }
  BUBBLE.classList.add("swap");
  BUBBLE_TEXT.textContent = text;
  window.setTimeout(() => BUBBLE.classList.remove("swap"), 160);
}

function idleLines() {
  return mailbox.length ? mailbox : [SETTLED].concat(WAITING);
}

function mailboxLine(advance) {
  const lines = idleLines();
  if (advance) {
    mailIndex += 1;
  }
  return lines[mailIndex % lines.length];
}

function showMouth(name) {
  MOUTH.src = mouthSrc(name);
  MOUTH.classList.add("on");
}

function hideMouth() {
  MOUTH.classList.remove("on");
}

function showEyes(src) {
  EYES.src = src;
  EYES.classList.add("on");
}

function hideEyes() {
  EYES.classList.remove("on");
}

function startTalking(text) {
  stopTalking();
  hideEyes();
  visemeFrames = visemesFor(text);
  visemeIndex = 0;
  if (!visemeFrames.length) {
    return;
  }
  const tick = () => {
    if (visemeIndex >= visemeFrames.length) {
      stopTalking();
      return;
    }
    const shape = visemeFrames[visemeIndex];
    visemeIndex += 1;
    if (shape) {
      showMouth(shape);
    } else {
      hideMouth();
    }
  };
  tick();
  visemeTimer = window.setInterval(tick, VISEME_MS);
}

function stopTalking() {
  if (visemeTimer) {
    window.clearInterval(visemeTimer);
    visemeTimer = 0;
  }
  visemeFrames = [];
  visemeIndex = 0;
  hideMouth();
}

function speak(text, kind, live) {
  chatting = Boolean(live);
  if (kind === "think") {
    stopTalking();
  } else if (text !== lastChatText) {
    startTalking(text);
  }
  setBubble(text, kind);
  lastChatText = text;
}

function operatorSignedIn() {
  try {
    const path = CHAT.contentWindow.location.pathname || "";
    if (!path) {
      return false;
    }
    return !/\/login|\/auth\//.test(path);
  } catch (_err) {
    return false;
  }
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
  const done = () => speak("Paste that into Ops chat, then send.", "talk", false);
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(value).then(done).catch(done);
    return;
  }
  done();
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

function paint(advanceMail) {
  if (!operatorSignedIn()) {
    speak(GREET, "listen", false);
    return;
  }
  const kind = chatSnap.kind || "listen";
  const text = chatSnap.text || THINKING;
  if (kind === "think" || kind === "ask") {
    speak(kind === "think" ? text || THINKING : text, kind === "ask" ? "talk" : "think", true);
    return;
  }
  if (kind === "talk" && Date.now() < doneUntil) {
    speak(text || "Done.", "talk", true);
    return;
  }
  speak(mailboxLine(advanceMail), mailbox.length ? "talk" : "listen", false);
}

function renderSpecialists(agents) {
  const root = document.getElementById("specialists");
  root.innerHTML = agents
    .filter((agent) => agent.id !== "supervisor")
    .map(
      (agent) => `
      <li>
        <img src="${agent.avatar}" alt="${agent.name}" />
        <div>
          <strong>${agent.name}</strong>
          <em>${agent.purpose || agent.role}</em>
        </div>
        <span class="dot ${agent.status}" title="${agent.status}"></span>
      </li>`
    )
    .join("");
}

async function refreshBrief() {
  try {
    const response = await fetch("/api/fleet/brief");
    if (!response.ok) {
      throw new Error("brief failed");
    }
    const payload = await response.json();
    renderSpecialists(payload.agents || []);
    const nextMail = (payload.activity || [])
      .filter((item) => item && item.kind !== "listen" && item.text)
      .map((item) => item.text);
    if (nextMail.join("\0") !== mailbox.join("\0")) {
      mailbox = nextMail;
      mailIndex = 0;
    }
    if (!chatting) {
      paint(false);
    }
  } catch (_err) {
    if (!chatting && operatorSignedIn()) {
      speak("I lost sight of the fleet. One moment", "think", true);
    }
  }
}

async function refreshChat() {
  try {
    const response = await fetch("/api/fleet/chat");
    if (!response.ok) {
      paint(false);
      return;
    }
    const snap = await response.json();
    const prevKind = chatSnap.kind;
    chatSnap = snap && snap.kind ? snap : { kind: "listen", text: "" };
    if (chatSnap.kind === "talk" && prevKind !== "talk") {
      doneUntil = Date.now() + DONE_MS;
    }
    paint(false);
  } catch (_err) {
    paint(false);
  }
}

CHAT.src = chatSrc();
CHAT.addEventListener("load", () => paint(false));
bindGuide();
speak(GREET, "listen", false);
refreshBrief();
refreshChat();
setInterval(refreshBrief, 20000);
setInterval(refreshChat, 1200);
setInterval(() => paint(false), 1000);
setInterval(() => {
  if (chatting || blinking || !operatorSignedIn()) {
    return;
  }
  paint(true);
}, 14000);
setInterval(() => {
  if (chatting || blinking) {
    return;
  }
  blinking = true;
  showEyes(BLINK);
  setTimeout(() => {
    blinking = false;
    hideEyes();
  }, 140);
}, 4200);
