(function (root) {
  const SPECIALISTS = [
    { id: "hephaestus", name: "Hephaestus", x: 102, y: 168 },
    { id: "janus", name: "Janus", x: 108, y: 80 },
    { id: "iris", name: "Iris", x: 244, y: 148 },
    { id: "surtr", name: "Surtr", x: 275, y: 325 },
    { id: "argus", name: "Argus", x: 165, y: 442 },
    { id: "mnemosyne", name: "Mnemosyne", x: 78, y: 348 },
  ];
  const ATHENA = { id: "athena", name: "Athena", x: 170, y: 252 };
  const REPLIES = {
    Hephaestus: "Lab Docker ok.",
    Mnemosyne: "Milvus healthy.",
    Surtr: "MIG map ready.",
    Iris: "LiteLLM ready.",
    Argus: "Metrics green.",
    Janus: "TLS 89 days left.",
  };
  const LINES = {
    athena: "Talk to me in Ops chat. I route the specialists.",
    hephaestus: "Hephaestus — Lab Docker. Restarts only after you approve.",
    janus: "Janus — HTTPS edge and TLS. Restarts office-edge only after you approve.",
    iris: "Iris — LiteLLM and HTTP routes. Read-only.",
    surtr: "Surtr — GPU cluster / MIG. Read-only.",
    argus: "Argus — metrics and Grafana. Read-only.",
    mnemosyne: "Mnemosyne — vector store. Prod is read-only.",
  };
  const IDLE_LINE = "Village is quiet. Click a specialist.";
  const FX = [
    { cls: "fx-flame", x: 88, y: 158, delay: "0s" },
    { cls: "fx-flame", x: 96, y: 152, delay: "-0.25s" },
    { cls: "fx-flame", x: 104, y: 160, delay: "-0.5s" },
    { cls: "fx-smoke", x: 94, y: 146, delay: "0s" },
    { cls: "fx-smoke", x: 102, y: 140, delay: "-0.9s" },
    { cls: "fx-gate", x: 108, y: 66, delay: "0s" },
    { cls: "fx-fire", x: 278, y: 322, delay: "-0.1s" },
    { cls: "fx-flame", x: 270, y: 328, delay: "-0.35s" },
    { cls: "fx-smoke", x: 284, y: 312, delay: "-0.6s" },
    { cls: "fx-water", x: 70, y: 352, delay: "-0.6s" },
    { cls: "fx-water", x: 82, y: 358, delay: "-1.2s" },
    { cls: "fx-shrine", x: 165, y: 428, delay: "-0.2s" },
    { cls: "fx-flame", x: 96, y: 70, delay: "-0.2s" },
    { cls: "fx-flame", x: 122, y: 70, delay: "-0.75s" },
    { cls: "fx-flame", x: 266, y: 138, delay: "-0.4s" },
    { cls: "fx-flame", x: 56, y: 338, delay: "-0.9s" },
    { cls: "fx-flame", x: 298, y: 308, delay: "-0.35s" },
    { cls: "fx-water", x: 76, y: 355, delay: "-0.3s" },
    { cls: "fx-leaf", x: 38, y: 118, delay: "0s" },
    { cls: "fx-leaf", x: 310, y: 190, delay: "-0.8s" },
    { cls: "fx-leaf", x: 48, y: 400, delay: "-1.5s" },
    { cls: "fx-leaf", x: 292, y: 418, delay: "-0.5s" },
    { cls: "fx-leaf", x: 24, y: 240, delay: "-1.1s" },
    { cls: "fx-petal", x: 152, y: 432, delay: "0s" },
    { cls: "fx-petal", x: 176, y: 436, delay: "-0.9s" },
    { cls: "fx-owl", x: 142, y: 244, delay: "0s" },
    { cls: "fx-owl owl-flip", x: 200, y: 246, delay: "-1.2s" },
  ];
  const ALL_IDS = ["hephaestus", "janus", "iris", "surtr", "argus", "mnemosyne"];
  const DEMO_AGENTS = SPECIALISTS.map((agent) => ({
    id: agent.id,
    name: agent.name,
    status: "ok",
  }));
  const DEMO = [
    { wait: 1100, snap: { kind: "think", text: "Hmm. Checking the village…", targets: [], completed: [] } },
    { wait: 2000, snap: { kind: "ask", text: "Janus — TLS on the north gate?", targets: ["janus"], completed: [] } },
    { wait: 1400, snap: { kind: "ask", text: "Janus — TLS on the north gate?", targets: ["janus"], completed: ["janus"] } },
    { wait: 1800, snap: { kind: "talk", text: "Edge is fine. I'll ask everyone.", targets: [], completed: ["janus"] } },
    { wait: 3200, snap: { kind: "ask", text: "I'm asking everyone.", targets: ALL_IDS, completed: [] } },
    { wait: 1000, snap: { kind: "ask", text: "I'm asking everyone.", targets: ALL_IDS, completed: ["janus"] } },
    { wait: 900, snap: { kind: "ask", text: "I'm asking everyone.", targets: ALL_IDS, completed: ["janus", "hephaestus"] } },
    { wait: 900, snap: { kind: "ask", text: "I'm asking everyone.", targets: ALL_IDS, completed: ["janus", "hephaestus", "iris"] } },
    { wait: 900, snap: { kind: "ask", text: "I'm asking everyone.", targets: ALL_IDS, completed: ["janus", "hephaestus", "iris", "surtr"] } },
    { wait: 900, snap: { kind: "ask", text: "I'm asking everyone.", targets: ALL_IDS, completed: ["janus", "hephaestus", "iris", "surtr", "argus"] } },
    { wait: 900, snap: { kind: "ask", text: "I'm asking everyone.", targets: ALL_IDS, completed: ALL_IDS } },
    { wait: 2400, snap: { kind: "talk", text: "Fleet is settled. Click anyone for a briefing.", targets: [], completed: ALL_IDS } },
    { wait: 3600, snap: { kind: "listen", text: "", targets: [], completed: [] } },
  ];
  const ONE_SHOT = {
    dispatch: { after: "think", ms: 600 },
    responding: { after: "idle", ms: 650 },
    complete: { after: "idle", ms: 500 },
    receive: { after: "idle", ms: 450 },
  };
  const PIN_STATUSES = new Set(["critical", "warn", "ok", "unknown"]);

  const seenGold = new Set();
  const seenGreen = new Set();
  const poseTimers = Object.create(null);
  const playedOneShot = Object.create(null);
  let athenaShown = null;
  let redCount = 0;
  let mounted = false;
  let typeTimer = 0;
  let inspectUntil = 0;
  let demoOn = false;
  let demoTimer = 0;
  let lastSnapshot = {
    kind: "listen",
    text: "",
    targets: [],
    completed: [],
    agents: [],
  };

  let village;
  let actorsEl;
  let pinsEl;
  let balloonLayer;
  let map;
  let orbLayer;
  let owlLayer;
  let fxLayer;
  const fxNodes = [];

  function prefersReducedMotion() {
    try {
      return root.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch (_err) {
      return false;
    }
  }

  /** Pure orb planner — unit-tested from node without a DOM. */
  function decideOrbs({
    targetIds = [],
    completedIds = [],
    statuses = {},
    seenGold: goldSeen = [],
    seenGreen: greenSeen = [],
    redCount: redCap = 0,
  } = {}) {
    const nextGold = new Set(goldSeen);
    const nextGreen = new Set(greenSeen);
    let nextRed = redCap;
    const gold = [];
    const green = [];
    const red = [];

    if (targetIds.length === 0 && completedIds.length === 0) {
      nextGold.clear();
      nextGreen.clear();
    }

    targetIds.forEach((id) => {
      if (!nextGold.has(id)) {
        nextGold.add(id);
        gold.push(id);
      }
    });
    completedIds.forEach((id) => {
      if (!nextGreen.has(id)) {
        nextGreen.add(id);
        green.push(id);
      }
    });

    const specialistIds = Object.keys(statuses);
    const anyCritical = specialistIds.some((id) => statuses[id] === "critical");
    if (!anyCritical) {
      nextRed = 0;
    } else if (nextRed < 2) {
      const criticalId = specialistIds.find(
        (id) => statuses[id] === "critical" && !targetIds.includes(id)
      );
      if (criticalId) {
        nextRed += 1;
        red.push(criticalId);
      }
    }

    return {
      gold,
      green,
      red,
      seenGold: Array.from(nextGold),
      seenGreen: Array.from(nextGreen),
      redCount: nextRed,
    };
  }

  function resolveAgent(token) {
    const key = String(token || "").trim().toLowerCase();
    if (!key) {
      return null;
    }
    return (
      SPECIALISTS.find((agent) => agent.id === key || agent.name.toLowerCase() === key) || null
    );
  }

  function resolveIds(names) {
    const ids = [];
    const seen = new Set();
    (names || []).forEach((token) => {
      const agent = resolveAgent(token);
      if (agent && !seen.has(agent.id)) {
        seen.add(agent.id);
        ids.push(agent.id);
      }
    });
    return ids;
  }

  function mapPoint(x, y) {
    const pt = map.createSVGPoint();
    pt.x = x;
    pt.y = y;
    const loc = pt.matrixTransform(map.getScreenCTM());
    const box = village.getBoundingClientRect();
    return { left: `${loc.x - box.left}px`, top: `${loc.y - box.top}px` };
  }

  function place(el, x, y) {
    if (!el || !map || !map.getScreenCTM()) {
      return;
    }
    const pos = mapPoint(x, y);
    el.style.left = pos.left;
    el.style.top = pos.top;
  }

  function balloonEl(id) {
    if (id === "athena") {
      return null;
    }
    return document.getElementById(`balloon-${id}`);
  }

  function placeAgentBalloon(id) {
    const el = balloonEl(id);
    if (!el || el.hidden) {
      return;
    }
    const actor = document.getElementById(`actor-${id}`);
    if (!actor) {
      return;
    }
    const villageBox = village.getBoundingClientRect();
    const box = actor.getBoundingClientRect();
    const gap = 16;
    let top = box.top - villageBox.top - el.offsetHeight - gap;
    if (top < 4) {
      top = 4;
    }
    el.style.left = `${box.left + box.width / 2 - villageBox.left}px`;
    el.style.top = `${top}px`;
  }

  function placeBalloon() {
    SPECIALISTS.forEach((agent) => placeAgentBalloon(agent.id));
  }

  function layout() {
    if (!mounted) {
      return;
    }
    place(document.getElementById("actor-athena"), ATHENA.x, ATHENA.y);
    SPECIALISTS.forEach((agent) => {
      place(document.getElementById(`actor-${agent.id}`), agent.x, agent.y);
      place(document.getElementById(`pin-${agent.id}`), agent.x, agent.y);
    });
    placeBalloon();
    fxNodes.forEach((node) => place(node.el, node.x, node.y));
  }

  function figure(agent, extraClass) {
    const el = document.createElement("div");
    el.className = `actor ${agent.id} ${extraClass || "idle"}`;
    el.id = `actor-${agent.id}`;
    el.title = agent.name;
    el.setAttribute("role", "button");
    el.tabIndex = 0;
    el.innerHTML = '<span class="sparks" aria-hidden="true"></span>';
    el.addEventListener("click", (event) => {
      event.stopPropagation();
      inspect(agent.id);
    });
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        inspect(agent.id);
      }
    });
    return el;
  }

  function clearPoseTimer(id) {
    if (poseTimers[id]) {
      root.clearTimeout(poseTimers[id]);
      delete poseTimers[id];
    }
  }

  function setPose(id, pose) {
    const el = document.getElementById(`actor-${id}`);
    if (!el) {
      return;
    }
    const inspecting = el.classList.contains("inspect");
    const keep = id === "athena" ? "actor athena" : `actor ${id}`;
    const shot = ONE_SHOT[pose];
    if (shot) {
      if (playedOneShot[id] === pose) {
        pose = shot.after;
      } else {
        playedOneShot[id] = pose;
        clearPoseTimer(id);
        if (!prefersReducedMotion()) {
          const expected = pose;
          poseTimers[id] = root.setTimeout(() => {
            delete poseTimers[id];
            const node = document.getElementById(`actor-${id}`);
            if (!node) {
              return;
            }
            const current = node.className.replace(keep, "").trim();
            if (current === expected) {
              node.className = `${keep} ${shot.after}`;
              if (inspecting) {
                node.classList.add("inspect");
              }
            }
          }, shot.ms);
        } else {
          pose = shot.after;
        }
      }
    } else {
      clearPoseTimer(id);
      delete playedOneShot[id];
    }
    el.className = `${keep} ${pose}`;
    if (inspecting) {
      el.classList.add("inspect");
    }
  }

  function setAgentBalloon(id, text, kind) {
    const el = balloonEl(id);
    if (!el) {
      return;
    }
    const label = el.querySelector(".balloon-text");
    const waiting = kind === "think" && !text;
    if (!text && !waiting) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    const base = id === "athena" ? "balloon" : "balloon agent";
    el.className = waiting ? `${base} think` : base;
    if (label) {
      label.textContent = text || "";
    }
    placeAgentBalloon(id);
  }

  function typeAthena(text) {
    const el = document.getElementById("athena-msg");
    if (!el) {
      return;
    }
    root.clearTimeout(typeTimer);
    if (prefersReducedMotion() || !text) {
      el.textContent = text || "";
      return;
    }
    el.textContent = "";
    let i = 0;
    function step() {
      i += 1;
      el.textContent = text.slice(0, i);
      if (i < text.length) {
        typeTimer = root.setTimeout(step, 28);
      }
    }
    step();
  }

  function setAthenaBalloon(text, kind) {
    const el = document.getElementById("athena-msg");
    const win = document.getElementById("athena-window");
    if (!el) {
      return;
    }
    const speaking = kind !== "listen" && Boolean(text);
    const shown = speaking ? text : "";
    if (win) {
      win.classList.toggle("speaking", speaking);
    }
    if (shown === athenaShown) {
      return;
    }
    athenaShown = shown;
    typeAthena(shown);
  }

  function inspect(id) {
    inspectUntil = Date.now() + 4500;
    document.querySelectorAll(".actor.inspect").forEach((node) => {
      node.classList.remove("inspect");
    });
    const actor = document.getElementById(`actor-${id}`);
    if (actor) {
      actor.classList.add("inspect");
    }
    apply(
      {
        kind: "talk",
        text: LINES[id] || "",
        targets: [],
        completed: id === "athena" ? [] : [id],
        agents: lastSnapshot.agents,
      },
      true
    );
  }

  function startDemo() {
    if (demoOn || prefersReducedMotion()) {
      return;
    }
    demoOn = true;
    runDemo(0);
  }

  function stopDemo() {
    demoOn = false;
    root.clearTimeout(demoTimer);
  }

  function runDemo(index) {
    if (!demoOn || index >= DEMO.length) {
      demoOn = false;
      return;
    }
    const step = DEMO[index];
    apply({
      ...step.snap,
      agents: lastSnapshot.agents.length ? lastSnapshot.agents : DEMO_AGENTS,
    });
    demoTimer = root.setTimeout(() => runDemo(index + 1), step.wait);
  }

  function syncSpecialistBalloons(targetIds, completedIds) {
    const working = new Set(targetIds);
    const done = new Set(completedIds);
    SPECIALISTS.forEach((agent) => {
      if (done.has(agent.id)) {
        setAgentBalloon(agent.id, REPLIES[agent.name] || "", "talk");
      } else if (working.has(agent.id)) {
        setAgentBalloon(agent.id, "", "think");
      } else {
        setAgentBalloon(agent.id, "", "");
      }
    });
  }

  function spawnOrb(kind, agentId, dir) {
    if (prefersReducedMotion() || !owlLayer || !map) {
      return;
    }
    const path = document.getElementById(`path-${agentId}`);
    if (!path || typeof path.getTotalLength !== "function") {
      return;
    }
    const len = path.getTotalLength();
    if (!len) {
      return;
    }
    const el = document.createElement("span");
    const destRight = agentId === "iris" || agentId === "surtr";
    const faceRight = dir === "out" ? destRight : !destRight;
    el.className = `owl-courier owl-${kind}${faceRight ? "" : " owl-flip"}`;
    owlLayer.appendChild(el);
    const dur = 1100;
    const t0 = performance.now();
    function frame(now) {
      const t = Math.min(1, (now - t0) / dur);
      const along = dir === "in" ? 1 - t : t;
      const pt = path.getPointAtLength(along * len);
      place(el, pt.x, pt.y);
      if (t < 1) {
        root.requestAnimationFrame(frame);
      } else {
        el.remove();
      }
    }
    root.requestAnimationFrame(frame);
    root.setTimeout(() => {
      if (el.parentNode) {
        el.remove();
      }
    }, dur + 80);
  }

  function statusById(agents) {
    const statuses = Object.fromEntries(SPECIALISTS.map((agent) => [agent.id, "unknown"]));
    (agents || []).forEach((agent) => {
      if (!agent || agent.id === "supervisor") {
        return;
      }
      const resolved = resolveAgent(agent.id) || resolveAgent(agent.name);
      if (!resolved) {
        return;
      }
      const status = String(agent.status || "unknown").toLowerCase();
      statuses[resolved.id] = PIN_STATUSES.has(status) ? status : "unknown";
    });
    return statuses;
  }

  function deriveAthenaPose(kind, targetIds, completedIds) {
    if (kind === "think") {
      return "think";
    }
    if (kind === "ask") {
      if (completedIds.length) {
        return "aggregating";
      }
      if (targetIds.length) {
        return "dispatch";
      }
      return "think";
    }
    if (kind === "talk") {
      return "responding";
    }
    return "idle";
  }

  function deriveSpecialistPose(id, targetIds, completedIds, kind) {
    if (targetIds.includes(id)) {
      return "work";
    }
    if (completedIds.includes(id) && (kind === "ask" || kind === "talk")) {
      return "complete";
    }
    return "idle";
  }

  function apply(snapshot, fromInspect) {
    if (!mounted) {
      return;
    }
    if (!fromInspect && Date.now() < inspectUntil) {
      return;
    }
    if (!fromInspect) {
      document.querySelectorAll(".actor.inspect").forEach((node) => {
        node.classList.remove("inspect");
      });
    }
    const next = snapshot || {};
    const kind = next.kind || "listen";
    const text = next.text || "";
    const prevTargetIds = resolveIds(lastSnapshot.targets);
    const prevCompletedIds = resolveIds(lastSnapshot.completed);
    const targetIds = resolveIds(next.targets);
    const completedIds = resolveIds(next.completed);
    const statuses = statusById(next.agents);

    lastSnapshot = {
      kind,
      text,
      targets: next.targets || [],
      completed: next.completed || [],
      agents: next.agents || [],
    };

    setPose("athena", deriveAthenaPose(kind, targetIds, completedIds));
    SPECIALISTS.forEach((agent) => {
      setPose(agent.id, deriveSpecialistPose(agent.id, targetIds, completedIds, kind));
      const pin = document.getElementById(`pin-${agent.id}`);
      if (pin) {
        const dot = pin.querySelector(".dot");
        if (dot) {
          dot.className = `dot ${statuses[agent.id]}`;
        }
      }
      const station = document.querySelector(`.station[data-id="${agent.id}"]`);
      if (station) {
        station.classList.toggle("critical", statuses[agent.id] === "critical");
      }
    });

    if (kind === "listen") {
      setAthenaBalloon(IDLE_LINE, "listen");
    } else {
      setAthenaBalloon(text, kind);
    }
    syncSpecialistBalloons(targetIds, completedIds);

    // Clear seen sets on idle ticks (think/listen). Also drop ids that left
    // the previous tick's targets/completed so a re-ask can fire again.
    if (targetIds.length === 0 && completedIds.length === 0) {
      seenGold.clear();
      seenGreen.clear();
    } else {
      prevTargetIds.forEach((id) => {
        if (!targetIds.includes(id)) {
          seenGold.delete(id);
        }
      });
      prevCompletedIds.forEach((id) => {
        if (!completedIds.includes(id)) {
          seenGreen.delete(id);
        }
      });
    }

    const plan = decideOrbs({
      targetIds,
      completedIds,
      statuses,
      seenGold: Array.from(seenGold),
      seenGreen: Array.from(seenGreen),
      redCount,
    });
    seenGold.clear();
    plan.seenGold.forEach((id) => seenGold.add(id));
    seenGreen.clear();
    plan.seenGreen.forEach((id) => seenGreen.add(id));
    redCount = plan.redCount;

    plan.gold.forEach((id) => spawnOrb("gold", id, "out"));
    plan.green.forEach((id) => spawnOrb("green", id, "in"));
    plan.red.forEach((id) => spawnOrb("red", id, "out"));

    requestAnimationFrame(layout);
  }

  function mount() {
    village = document.getElementById("village");
    actorsEl = document.getElementById("actors");
    pinsEl = document.getElementById("pins");
    balloonLayer = document.querySelector(".balloon-layer");
    map = document.getElementById("ops-map") || document.getElementById("map");
    orbLayer = document.getElementById("orb-layer");
    owlLayer = document.getElementById("owl-layer");
    fxLayer = document.getElementById("fx-layer");
    if (!village || !actorsEl || !pinsEl || !balloonLayer || !map || !owlLayer) {
      return;
    }
    if (mounted) {
      layout();
      return;
    }

    actorsEl.appendChild(figure(ATHENA, "idle"));
    SPECIALISTS.forEach((agent) => {
      actorsEl.appendChild(figure(agent, "idle"));
      const pin = document.createElement("div");
      pin.className = "pin";
      pin.id = `pin-${agent.id}`;
      pin.innerHTML = `<span class="dot unknown"></span><span class="name">${agent.name}</span>`;
      pin.addEventListener("click", (event) => {
        event.stopPropagation();
        inspect(agent.id);
      });
      pinsEl.appendChild(pin);
      const reply = document.createElement("p");
      reply.className = "balloon agent";
      reply.id = `balloon-${agent.id}`;
      reply.hidden = true;
      reply.innerHTML = `<span class="balloon-text"></span><span class="dots" aria-hidden="true"></span>`;
      balloonLayer.appendChild(reply);
    });

    if (fxLayer && fxNodes.length === 0) {
      FX.forEach((spec) => {
        const el = document.createElement("span");
        el.className = spec.cls;
        el.style.animationDelay = spec.delay;
        fxLayer.appendChild(el);
        fxNodes.push({ el, x: spec.x, y: spec.y });
      });
    }

    const athenaWin = document.getElementById("athena-window");
    if (athenaWin) {
      athenaWin.setAttribute("role", "button");
      athenaWin.tabIndex = 0;
      athenaWin.title = "Athena";
      athenaWin.addEventListener("click", () => inspect("athena"));
      athenaWin.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          inspect("athena");
        }
      });
    }

    mounted = true;
    layout();
    requestAnimationFrame(layout);
    apply(lastSnapshot);
  }

  root.OpsMap = {
    mount,
    apply,
    layout,
    decideOrbs,
    startDemo,
    stopDemo,
    inspect,
  };
})(typeof window !== "undefined" ? window : globalThis);
