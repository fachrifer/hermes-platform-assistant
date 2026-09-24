import asyncio
from dataclasses import replace

import httpx
from fastapi.testclient import TestClient

from office_gateway.app import create_app
from office_gateway.chat import bubble_from_messages, fetch_chat_bubble
from office_gateway.config import GatewayConfig


def _config(db_path: str) -> GatewayConfig:
    return GatewayConfig(
        tokens={
            "supervisor": "tok-sup",
            "lab-host": "tok-lab",
            "vector": "tok-vec",
            "cluster-gpu": "tok-gpu",
            "llm": "tok-llm",
            "obs": "tok-obs",
        },
        service_urls={"gateway": "http://gateway.internal/health"},
        db_path=db_path,
    )


def test_user_message_means_athena_is_thinking():
    bubble = bubble_from_messages(
        [{"role": "user", "content": "check milvus", "timestamp": 1_000}],
        now=1_002,
    )
    assert bubble["kind"] == "think"
    assert "moment" in bubble["text"].lower()


def test_assistant_reply_becomes_spoken_line():
    bubble = bubble_from_messages(
        [
            {"role": "user", "content": "status?", "timestamp": 1_000},
            {
                "role": "assistant",
                "content": "The fleet looks healthy. I can check Surtr next.",
                "timestamp": 1_001,
            },
        ],
        now=1_005,
    )
    assert bubble["kind"] == "talk"
    assert bubble["text"] == "Done."


def test_assistant_reply_stays_spoken_after_a_few_minutes():
    bubble = bubble_from_messages(
        [
            {"role": "user", "content": "status of platform", "timestamp": 1_000},
            {
                "role": "assistant",
                "content": "Platform status check could not complete: all five specialists timed out.",
                "timestamp": 1_001,
            },
        ],
        now=1_000 + 180,
    )
    assert bubble["kind"] == "talk"
    assert bubble["text"] == "Done."


def test_old_assistant_reply_returns_to_listening():
    bubble = bubble_from_messages(
        [{"role": "assistant", "content": "Done.", "timestamp": 1_000}],
        now=1_000 + 20 * 60,
    )
    assert bubble["kind"] == "listen"


def test_a2a_orchestrate_result_asks_specialists():
    bubble = bubble_from_messages(
        [
            {"role": "user", "content": "status of platform", "timestamp": 2_000},
            {
                "role": "tool",
                "tool_name": "a2a_orchestrate",
                "content": "Orchestrated '*' to 5 peer(s).",
                "timestamp": 2_010,
            },
        ],
        now=2_011,
    )
    assert bubble["kind"] == "ask"
    assert "everyone" in bubble["text"].lower()


def test_a2a_tool_call_asks_named_specialist():
    bubble = bubble_from_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"name": "a2a_call", "arguments": {"agent": "lab-host"}}],
                "timestamp": 2_000,
            }
        ],
        now=2_001,
    )
    assert bubble["kind"] == "ask"
    assert "Hephaestus" in bubble["text"]


def test_supervisor_can_read_fleet_chat(tmp_path):
    client = TestClient(create_app(replace(_config(str(tmp_path / "gw.db")))))
    response = client.get("/v1/fleet/chat", headers={"Authorization": "Bearer tok-sup"})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "listen"
    assert body["text"]


def test_lab_host_cannot_read_fleet_chat(tmp_path):
    client = TestClient(create_app(replace(_config(str(tmp_path / "gw.db")))))
    response = client.get("/v1/fleet/chat", headers={"Authorization": "Bearer tok-lab"})
    assert response.status_code == 403


def test_fetch_chat_bubble_logs_in_then_speaks_assistant_reply():
    logged_in = {"ok": False}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/api/auth/providers"):
            return httpx.Response(
                200,
                json={"providers": [{"name": "basic", "supports_password": True}]},
            )
        if path.endswith("/auth/password-login"):
            logged_in["ok"] = True
            return httpx.Response(
                200,
                json={"ok": True, "next": "/"},
                headers={"set-cookie": "hermes_session_at=session-cookie; Path=/; HttpOnly"},
            )
        cookie = request.headers.get("cookie", "")
        if "hermes_session_at=session-cookie" not in cookie:
            return httpx.Response(
                401,
                json={"error": "unauthenticated", "reason": "no_cookie"},
            )
        if path.endswith("/api/sessions"):
            return httpx.Response(
                200,
                json={"sessions": [{"id": "s1", "message_count": 2}]},
            )
        if path.endswith("/messages"):
            now = __import__("time").time()
            return httpx.Response(
                200,
                json={
                    "messages": [
                        {"role": "user", "content": "status of platform", "timestamp": now - 8},
                        {
                            "role": "assistant",
                            "content": "All five specialists answered. The fleet is up.",
                            "timestamp": now - 2,
                        },
                    ]
                },
            )
        return httpx.Response(404, json={"detail": path})

    transport = httpx.MockTransport(handler)

    async def _run() -> dict:
        async with httpx.AsyncClient(transport=transport, timeout=2.0) as client:
            return await fetch_chat_bubble(
                base_url="http://hermes-agent:9119",
                username="athena",
                password="unused",
                client=client,
            )

    bubble = asyncio.run(_run())
    assert logged_in["ok"] is True
    assert bubble["kind"] == "talk"
    assert bubble["text"] == "Done."
