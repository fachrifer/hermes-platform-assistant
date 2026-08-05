"""Tests for CML Qwen3 OpenAI-compatible server (no GPU required)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from cml.qwen3_8b.serve import create_app


class FakeEngine:
    model_id = "Qwen3-8B"
    model_path = "Qwen3-8B"
    is_loaded = True
    is_loading = False
    load_error = None

    def generate(self, messages, *, max_tokens=256, temperature=0.2):
        last = messages[-1]["content"] if messages else ""
        return f"echo:{last}"


def test_qwen_openai_compatible_endpoints():
    client = TestClient(create_app(FakeEngine()))
    health = client.get("/health").json()
    assert health["status"] == "ok"
    assert client.get("/").json()["status"] == "ok"
    models = client.get("/v1/models").json()
    ids = {item["id"] for item in models["data"]}
    assert "Qwen3-8B" in ids
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


def test_qwen_accepts_hf_model_id_alias():
    class HfEngine:
        model_id = "Qwen/Qwen3-8B"
        model_path = "Qwen/Qwen3-8B"
        is_loaded = True
        is_loading = False
        load_error = None

        def generate(self, messages, *, max_tokens=256, temperature=0.2):
            return "ok"

    client = TestClient(create_app(HfEngine()))
    for model in ("Qwen/Qwen3-8B", "Qwen3-8B", "qwen3-8b"):
        response = client.post(
            "/v1/chat/completions",
            json={"model": model, "messages": [{"role": "user", "content": "x"}]},
        )
        assert response.status_code == 200, model


def test_health_reports_load_error():
    class BrokenEngine:
        model_id = "Qwen3-8B"
        model_path = "/missing"
        is_loaded = False
        is_loading = False
        load_error = "cuda sm_70 missing"

        def generate(self, messages, *, max_tokens=256, temperature=0.2):
            raise RuntimeError("not loaded")

    client = TestClient(create_app(BrokenEngine()))
    health = client.get("/health").json()
    assert health["status"] == "error"
    assert "sm_70" in health["error"]
