from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from office_gateway.brief import FLEET_AGENTS

LISTEN = {"kind": "listen", "text": "Whenever you're ready.", "target": None}
THINK = {"kind": "think", "text": "Give me a moment", "target": None}
TALK = {"kind": "talk", "text": "Done.", "target": None}
IDLE_AFTER = 15 * 60
_SESSION_COOKIE = "hermes_session_at"

_SPECIALISTS = {
    agent["id"]: agent["name"]
    for agent in FLEET_AGENTS
    if agent["id"] != "supervisor"
}
_NAME_HINTS = {name.lower(): name for name in _SPECIALISTS.values()}

_clients: dict[tuple[str, str], httpx.AsyncClient] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _as_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return " ".join(part.strip() for part in parts if part).strip()
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "").strip()
    return str(content).strip()


def _asked_specialist(message: dict) -> str | None:
    blob = json.dumps(message.get("tool_calls") or [], default=str).lower()
    blob += " " + str(message.get("name") or "").lower()
    blob += " " + str(message.get("tool_name") or "").lower()
    blob += " " + _as_text(message.get("content")).lower()[:200]
    for agent_id, name in _SPECIALISTS.items():
        if agent_id in blob or name.lower() in blob:
            return name
    for hint, name in _NAME_HINTS.items():
        if hint in blob:
            return name
    if "a2a" in blob or "specialist" in blob:
        return "a specialist"
    return None


def _age_seconds(stamp: Any, now: float) -> float:
    if stamp is None or stamp == "":
        return 0.0
    try:
        value = float(stamp)
        if value > 1e12:
            value = value / 1000.0
        age = now - value
    except (TypeError, ValueError):
        try:
            from datetime import datetime

            parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            age = now - parsed.timestamp()
        except ValueError:
            return 0.0
    return max(age, 0.0)


def bubble_from_messages(messages: list[dict], *, now: float | None = None) -> dict:
    """Map the latest Hermes session messages onto Athena's dialog bubble."""
    if not messages:
        return dict(LISTEN)
    current = now if now is not None else time.time()
    last = messages[-1]
    role = str(last.get("role") or last.get("type") or "").lower()
    age = _age_seconds(last.get("timestamp") or last.get("created_at") or last.get("ts"), current)
    asked = _asked_specialist(last)
    if role in {"tool", "function"} or (asked and role not in {"user", "assistant", "system"}):
        who = asked or "a specialist"
        return {
            "kind": "ask",
            "text": f"Give me a second — I'm asking {who}.",
            "target": None if who == "a specialist" else who,
        }
    if role == "user":
        return dict(THINK)
    if role in {"assistant", "system"}:
        text = _as_text(last.get("content"))
        if asked and not text:
            who = asked
            return {
                "kind": "ask",
                "text": f"Give me a second — I'm asking {who}.",
                "target": None if who == "a specialist" else who,
            }
        if not text:
            return dict(THINK)
        if age > IDLE_AFTER:
            return dict(LISTEN)
        return dict(TALK)
    if asked:
        who = asked
        return {
            "kind": "ask",
            "text": f"Give me a second — I'm asking {who}.",
            "target": None if who == "a specialist" else who,
        }
    return dict(LISTEN)


async def _password_login(http: httpx.AsyncClient, base: str, username: str, password: str) -> None:
    provider = "basic"
    listed = await http.get(f"{base}/api/auth/providers")
    if listed.status_code == 200:
        payload = listed.json()
        for item in payload.get("providers") or []:
            if item.get("supports_password") and item.get("name"):
                provider = str(item["name"])
                break
    resp = await http.post(
        f"{base}/auth/password-login",
        json={"provider": provider, "username": username, "password": password, "next": "/"},
    )
    resp.raise_for_status()


def _has_session_cookie(http: httpx.AsyncClient) -> bool:
    return bool(http.cookies.get(_SESSION_COOKIE))


async def _authed_get(
    http: httpx.AsyncClient,
    url: str,
    *,
    base: str,
    username: str,
    password: str,
    params: dict | None = None,
) -> httpx.Response:
    if not _has_session_cookie(http):
        await _password_login(http, base, username, password)
    resp = await http.get(url, params=params)
    if resp.status_code == 401:
        await _password_login(http, base, username, password)
        resp = await http.get(url, params=params)
    resp.raise_for_status()
    return resp


async def _shared_client(base: str, username: str) -> httpx.AsyncClient:
    key = (base, username)
    client = _clients.get(key)
    if client is None:
        client = httpx.AsyncClient(timeout=5.0)
        _clients[key] = client
        _locks[key] = asyncio.Lock()
    return client


async def fetch_chat_bubble(
    *,
    base_url: str,
    username: str,
    password: str,
    client: httpx.AsyncClient | None = None,
) -> dict:
    if not base_url or not username or not password:
        return dict(LISTEN)
    base = base_url.rstrip("/")
    own_client = client is None
    http = client or await _shared_client(base, username)
    lock = _locks.get((base, username)) if own_client else None
    try:
        if lock is not None:
            async with lock:
                return await _bubble_from_dashboard(http, base, username, password)
        return await _bubble_from_dashboard(http, base, username, password)
    except Exception:
        return dict(LISTEN)


async def _bubble_from_dashboard(
    http: httpx.AsyncClient,
    base: str,
    username: str,
    password: str,
) -> dict:
    listed = await _authed_get(
        http,
        f"{base}/api/sessions",
        base=base,
        username=username,
        password=password,
        params={"limit": 1, "order": "recent"},
    )
    payload = listed.json()
    sessions = payload.get("sessions") if isinstance(payload, dict) else payload
    if not sessions:
        return dict(LISTEN)
    session = sessions[0]
    session_id = session.get("id") or session.get("session_id")
    if not session_id:
        return dict(LISTEN)
    count = int(session.get("message_count") or 0)
    offset = max(0, count - 20) if count else 0
    messages_resp = await _authed_get(
        http,
        f"{base}/api/sessions/{session_id}/messages",
        base=base,
        username=username,
        password=password,
        params={"limit": 20, "offset": offset},
    )
    body = messages_resp.json()
    messages = body.get("messages") if isinstance(body, dict) else body
    if not messages and isinstance(body, dict):
        messages = body.get("data")
    return bubble_from_messages(messages or [])
