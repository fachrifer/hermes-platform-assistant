"""Release whatever holds Athena's Bot Chat lease, without deleting the session.

Two holders exist. A dashboard TUI is a ``tui_gateway`` child process: it is stopped. A Desktop
connection lives inside the dashboard process itself, which must not be killed: its lease entry is
removed from the registry instead.
"""

from __future__ import annotations

import json
import os
from typing import Any

from office_gateway.docker_ops import DockerOps

ATHENA_CONTAINER = "office-hermes-agent-1"
_PYTHON = "/opt/hermes/.venv/bin/python3"
_INSPECT = r"""
import json, sqlite3
from pathlib import Path

db = sqlite3.connect("/opt/data/state.db")
ids = [row[0] for row in db.execute("select id from sessions where title = ?", ("Bot Chat",))]
if ids:
    marks = ",".join("?" * len(ids))
    ids += [
        row[0]
        for row in db.execute(
            f"select id from sessions where parent_session_id in ({marks})", ids
        )
    ]
wanted = list(dict.fromkeys(ids))
path = Path("/opt/data/runtime/active_sessions.json")
entries = []
if path.is_file():
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        entries = raw.get("entries") or []
leases = []
cmdlines = {}
for entry in entries:
    if not isinstance(entry, dict):
        continue
    sid = str(entry.get("session_id") or "")
    if sid not in wanted:
        continue
    leases.append(
        {
            "session_id": sid,
            "surface": str(entry.get("surface") or ""),
            "pid": entry.get("pid"),
            "lease_id": str(entry.get("lease_id") or ""),
        }
    )
    try:
        pid = int(entry.get("pid"))
    except (TypeError, ValueError):
        continue
    if pid <= 1:
        continue
    cmd_path = Path(f"/proc/{pid}/cmdline")
    if cmd_path.is_file():
        cmdlines[str(pid)] = cmd_path.read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
print(json.dumps({"session_ids": wanted, "leases": leases, "cmdlines": cmdlines}))
"""

# A Desktop connection is not a TUI child: its runtime lives inside the dashboard process, so the
# lease carries the dashboard's pid and stays valid for as long as the dashboard runs. The only
# way to free it without restarting the dashboard is to drop the entry from the registry, under the
# same flock Hermes takes (hermes_cli/active_sessions.py), writing the same {"entries": [...]} shape.
_CLEAR = r"""
import fcntl, json, os, sys
from pathlib import Path

wanted = set(json.loads(sys.argv[1]))
sessions = set(json.loads(sys.argv[2]))
runtime = Path(sys.argv[3] if len(sys.argv) > 3 else "/opt/data/runtime")
state = runtime / "active_sessions.json"
removed = 0
remaining = 0
with open(runtime / "active_sessions.lock", "a+b") as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    raw = json.loads(state.read_text(encoding="utf-8")) if state.is_file() else {}
    entries = raw.get("entries") if isinstance(raw, dict) else raw
    entries = entries if isinstance(entries, list) else []
    kept = [
        e for e in entries
        if not (isinstance(e, dict) and str(e.get("lease_id") or "") in wanted)
    ]
    removed = len(entries) - len(kept)
    if removed:
        tmp = state.with_name(state.name + ".tmp")
        tmp.write_text(json.dumps({"entries": kept}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, state)
    remaining = sum(1 for e in kept if isinstance(e, dict) and str(e.get("session_id") or "") in sessions)
print(json.dumps({"removed": removed, "remaining": remaining}))
"""


class SessionLockError(RuntimeError):
    pass


def holder_pids(session_ids: set[str], leases: list[dict[str, Any]], cmdlines: dict[int, str]) -> list[int]:
    """TUI pids whose live lease is one of these sessions and whose command is tui_gateway."""
    pids: list[int] = []
    for entry in leases:
        if not isinstance(entry, dict) or str(entry.get("surface") or "") != "tui":
            continue
        if str(entry.get("session_id") or "") not in session_ids:
            continue
        try:
            pid = int(entry.get("pid"))
        except (TypeError, ValueError):
            continue
        if pid <= 1 or "tui_gateway" not in cmdlines.get(pid, ""):
            continue
        if pid not in pids:
            pids.append(pid)
    return pids


def _athena_container() -> str:
    return os.getenv("OFFICE_ATHENA_CONTAINER", "").strip() or ATHENA_CONTAINER


def _inspect_athena(
    docker: DockerOps, name: str
) -> tuple[list[str], list[dict[str, Any]], dict[int, str]]:
    """Bot Chat session ids (root first, then compression children), their leases, holder cmdlines."""
    try:
        inspected = docker.exec_run(name, [_PYTHON, "-c", _INSPECT], user="hermes")
    except ValueError as exc:
        raise SessionLockError("Athena container is not running") from exc
    except RuntimeError as exc:
        raise SessionLockError("could not inspect Athena") from exc
    if int(inspected.get("exit_code", 1)) != 0:
        raise SessionLockError("could not inspect Athena")
    try:
        payload = json.loads(str(inspected.get("stdout") or ""))
    except (ValueError, TypeError) as exc:
        raise SessionLockError("could not inspect Athena") from exc
    if not isinstance(payload, dict):
        raise SessionLockError("could not inspect Athena")
    session_ids = [str(sid) for sid in payload.get("session_ids") or [] if isinstance(sid, str) and sid]
    cmdlines: dict[int, str] = {}
    for key, value in (payload.get("cmdlines") or {}).items():
        try:
            cmdlines[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    leases = payload.get("leases") if isinstance(payload.get("leases"), list) else []
    return session_ids, leases, cmdlines


def bot_chat_info(docker: DockerOps, container: str | None = None) -> dict[str, Any]:
    """Where Bot Chat is and who holds it. The web Dashboard hides it, so it is opened by id:
    ``/dash/chat?resume=<session_id>`` (the Dashboard follows the id to its newest continuation)."""
    session_ids, leases, _ = _inspect_athena(docker, container or _athena_container())
    held = held_leases(set(session_ids), leases)
    return {
        "session_id": session_ids[0] if session_ids else None,
        "held": bool(held),
        "holders": [{"surface": str(entry.get("surface") or ""), "pid": entry.get("pid")} for entry in held],
    }


def release_bot_chat(docker: DockerOps, container: str | None = None) -> dict[str, Any]:
    name = container or _athena_container()
    session_ids, leases, cmdlines = _inspect_athena(docker, name)
    if not session_ids:
        return {"released": False, "reason": "no_session", "session_ids": [], "stopped": [], "cleared": []}
    pids = holder_pids(set(session_ids), leases, cmdlines)
    held = held_leases(set(session_ids), leases)
    if not pids and not held:
        return {
            "released": False,
            "reason": "not_held",
            "session_ids": session_ids,
            "stopped": [],
            "cleared": [],
        }
    stopped: list[int] = []
    for pid in pids:
        try:
            killed = docker.exec_run(name, ["kill", "-TERM", str(pid)])
        except (ValueError, RuntimeError) as exc:
            raise SessionLockError("could not stop the stuck TUI") from exc
        if int(killed.get("exit_code", 1)) != 0:
            raise SessionLockError("could not stop the stuck TUI")
        stopped.append(pid)
    cleared = _clear_leases(docker, name, session_ids, held)
    return {
        "released": True,
        "reason": "released",
        "session_ids": session_ids,
        "stopped": stopped,
        "cleared": cleared,
    }


def held_leases(session_ids: set[str], leases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every registry lease on the Bot Chat sessions, whoever holds it (TUI, Desktop, stale pid)."""
    held: list[dict[str, Any]] = []
    for entry in leases:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("session_id") or "") not in session_ids:
            continue
        if not str(entry.get("lease_id") or ""):
            continue
        held.append(entry)
    return held


def _clear_leases(
    docker: DockerOps, name: str, session_ids: list[str], held: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not held:
        return []
    lease_ids = [str(entry["lease_id"]) for entry in held]
    try:
        cleared = docker.exec_run(
            name, [_PYTHON, "-c", _CLEAR, json.dumps(lease_ids), json.dumps(session_ids)], user="hermes"
        )
    except (ValueError, RuntimeError) as exc:
        raise SessionLockError("could not clear the Bot Chat lease") from exc
    if int(cleared.get("exit_code", 1)) != 0:
        raise SessionLockError("could not clear the Bot Chat lease")
    try:
        result = json.loads(str(cleared.get("stdout") or ""))
    except (ValueError, TypeError) as exc:
        raise SessionLockError("could not clear the Bot Chat lease") from exc
    if not isinstance(result, dict) or int(result.get("remaining", 1)) != 0:
        raise SessionLockError("Bot Chat is still leased after clearing")
    return [
        {"session_id": entry.get("session_id"), "surface": entry.get("surface"), "pid": entry.get("pid")}
        for entry in held
    ]
