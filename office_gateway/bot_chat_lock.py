"""Release the TUI process holding Athena's Bot Chat, without deleting the session."""

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
        {"session_id": sid, "surface": str(entry.get("surface") or ""), "pid": entry.get("pid")}
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


def release_bot_chat(docker: DockerOps, container: str | None = None) -> dict[str, Any]:
    name = container or _athena_container()
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
    if not session_ids:
        return {"released": False, "reason": "no_session", "session_ids": [], "stopped": []}
    cmdlines: dict[int, str] = {}
    for key, value in (payload.get("cmdlines") or {}).items():
        try:
            cmdlines[int(key)] = str(value)
        except (TypeError, ValueError):
            continue
    leases = payload.get("leases") if isinstance(payload.get("leases"), list) else []
    pids = holder_pids(set(session_ids), leases, cmdlines)
    if not pids:
        return {"released": False, "reason": "not_held", "session_ids": session_ids, "stopped": []}
    stopped: list[int] = []
    for pid in pids:
        try:
            killed = docker.exec_run(name, ["kill", "-TERM", str(pid)])
        except (ValueError, RuntimeError) as exc:
            raise SessionLockError("could not stop the stuck TUI") from exc
        if int(killed.get("exit_code", 1)) != 0:
            raise SessionLockError("could not stop the stuck TUI")
        stopped.append(pid)
    return {"released": True, "reason": "released", "session_ids": session_ids, "stopped": stopped}
