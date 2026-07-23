"""Tests for CML Qwen3 OpenAI-compatible server (no GPU required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from cml.qwen3_8b.serve import create_app


class FakeEngine:
    model_id = "Qwen3-8B"

    def generate(self, messages, *, max_tokens=256, temperature=0.2):
        last = messages[-1]["content"] if messages else ""
        return f"echo:{last}"


def test_qwen_openai_compatible_endpoints():
    client = TestClient(create_app(FakeEngine()))
    assert client.get("/health").json() == {"status": "ok"}
    models = client.get("/v1/models").json()
    assert models["data"][0]["id"] == "Qwen3-8B"
    completion = client.post(
        "/v1/chat/completions",
        json={
            "model": "Qwen3-8B",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert completion.status_code == 200
    body = completion.json()
    assert body["choices"][0]["message"]["content"] == "echo:hi"
