"use strict";

(function () {
  const API = "/approvals/api";
  const POLL_MS = 10000;
  const AGENT_NAMES = {
    "lab-host": "Hephaestus",
    ingress: "Janus",
    obs: "Argus",
  };
  const ACTION_LABELS = {
    restart_service: "Restart service",
    apply_edge_routes: "Apply HTTPS routes",
    rollback_edge_routes: "Roll back HTTPS routes",
  };

  const els = {
    status: document.getElementById("status"),
    pending: document.getElementById("pending"),
    pendingCount: document.getElementById("pending-count"),
    recent: document.getElementById("recent"),
  };
  let busy = false;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function agentName(role) {
    return AGENT_NAMES[role] ? `${AGENT_NAMES[role]} (${role})` : role;
  }

  function actionLabel(action) {
    return ACTION_LABELS[action] || action;
  }

  function when(iso) {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? String(iso || "") : date.toLocaleString();
  }

  function setStatus(text, kind) {
    els.status.textContent = text;
    els.status.className = `status ${kind || ""}`.trim();
  }

  async function api(path, options) {
    const response = await fetch(API + path, {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      ...options,
    });
    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      body = null;
    }
    if (!response.ok) {
      const detail = body && body.detail ? body.detail : response.statusText;
      throw new Error(`${response.status} ${detail}`);
    }
    return body;
  }

  function field(dl, label, value) {
    if (value === undefined || value === null || value === "") return;
    dl.appendChild(el("dt", null, label));
    dl.appendChild(el("dd", null, value));
  }

  function renderCard(item) {
    const card = el("article", "card");
    card.id = `action-${item.action_id}`;
    card.appendChild(el("h3", null, `${actionLabel(item.action)}: ${item.target}`));
    if (item.summary) card.appendChild(el("p", "summary", item.summary));

    const dl = el("dl");
    field(dl, "Asked by", agentName(item.role));
    field(dl, "Reason", item.reason);
    field(dl, "Requested", when(item.created_at));
    field(dl, "Expires", when(item.expires_at));
    field(dl, "Id", item.action_id);
    card.appendChild(dl);

    if (Array.isArray(item.diff) && item.diff.length) {
      const pre = el("pre", "diff");
      for (const line of item.diff) {
        const text = String(line);
        const kind = text.startsWith("+") ? "add" : text.startsWith("-") ? "del" : "";
        pre.appendChild(el("span", kind, `${text}\n`));
      }
      card.appendChild(pre);
    }

    const buttons = el("div", "buttons");
    const approve = el("button", "approve", "Approve");
    approve.type = "button";
    approve.addEventListener("click", () => decide(item, "approve"));
    const reject = el("button", "reject", "Reject");
    reject.type = "button";
    reject.addEventListener("click", () => decide(item, "reject"));
    buttons.append(approve, reject);
    card.appendChild(buttons);
    return card;
  }

  function renderPending(actions) {
    els.pending.replaceChildren();
    els.pendingCount.textContent = actions.length ? `(${actions.length})` : "";
    if (!actions.length) {
      els.pending.appendChild(el("p", "empty", "Nothing is waiting for approval."));
      return;
    }
    for (const item of actions) els.pending.appendChild(renderCard(item));
  }

  function renderRecent(actions) {
    els.recent.replaceChildren();
    const decided = actions.filter((item) => item.status !== "pending");
    if (!decided.length) {
      const row = el("tr");
      const cell = el("td", "empty", "No decisions yet.");
      cell.colSpan = 6;
      row.appendChild(cell);
      els.recent.appendChild(row);
      return;
    }
    for (const item of decided) {
      const row = el("tr");
      row.appendChild(el("td", null, when(item.created_at)));
      row.appendChild(el("td", null, agentName(item.role)));
      row.appendChild(el("td", null, actionLabel(item.action)));
      row.appendChild(el("td", null, item.target));
      const status = el("td", `st st-${item.status}`, item.status);
      if (item.detail) status.title = String(item.detail);
      row.appendChild(status);
      row.appendChild(el("td", null, item.approver || ""));
      els.recent.appendChild(row);
    }
  }

  async function refresh() {
    if (busy) return;
    try {
      const [pending, recent] = await Promise.all([
        api("?status=pending&limit=50"),
        api("?status=all&limit=20"),
      ]);
      renderPending(pending.actions || []);
      renderRecent(recent.actions || []);
      setStatus(`Updated ${new Date().toLocaleTimeString()}`, "");
    } catch (error) {
      setStatus(`Could not load approvals: ${error.message}`, "error");
    }
  }

  async function decide(item, verb) {
    const question =
      verb === "approve"
        ? `Approve "${actionLabel(item.action)}" on ${item.target}?\nThis runs immediately.`
        : `Reject "${actionLabel(item.action)}" on ${item.target}?`;
    if (!window.confirm(question)) return;
    busy = true;
    setStatus(`${verb === "approve" ? "Approving" : "Rejecting"} ${item.action_id}...`, "");
    try {
      const result = await api(`/${encodeURIComponent(item.action_id)}/${verb}`, { method: "POST" });
      const detail = result && result.detail ? ` - ${result.detail}` : "";
      setStatus(`${item.action_id}: ${result ? result.status : "done"}${detail}`, "ok");
    } catch (error) {
      setStatus(`${verb} failed: ${error.message}`, "error");
    } finally {
      busy = false;
      refresh();
    }
  }

  refresh();
  window.setInterval(refresh, POLL_MS);
})();
