"""OpenAI-compatible Qwen3 8B server for CML Applications."""

from __future__ import annotations

import time
import uuid
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class Engine(Protocol):
    model_id: str

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 256,
        temperature: float = 0.2,
    ) -> str: ...


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "Qwen3-8B"
    messages: list[ChatMessage]
    max_tokens: int = Field(default=256, ge=1, le=4096)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


def _model_aliases(engine: Engine) -> set[str]:
    """Accept common client names for the single local model."""
    aliases = {
        engine.model_id,
        "Qwen3-8B",
        "qwen3-8b",
        "Qwen/Qwen3-8B",
        "qwen/qwen3-8b",
    }
    # Also accept bare name after last slash (Qwen/Qwen3-8B → Qwen3-8B)
    if "/" in engine.model_id:
        aliases.add(engine.model_id.rsplit("/", 1)[-1])
    return {a for a in aliases if a}


def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="Qwen3 8B CML", docs_url=None, redoc_url=None)
    app.state.engine = engine

    @app.get("/")
    @app.get("/health")
    async def health() -> dict[str, str]:
        # CML Application probes "/" (sometimes "//"); keep both healthy.
        loaded = bool(getattr(engine, "is_loaded", False))
        loading = bool(getattr(engine, "is_loading", False))
        error = getattr(engine, "load_error", None)
        if error:
            status = "error"
        elif loaded:
            status = "ok"
        elif loading:
            status = "loading"
        else:
            status = "loading"
        payload = {
            "status": status,
            "model_loaded": "true" if loaded else "false",
            "model_id": str(getattr(engine, "model_id", "")),
            "model_path": str(getattr(engine, "model_path", "")),
        }
        if error:
            payload["error"] = str(error)[:1000]
        return payload

    @app.get("/models")
    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        # Advertise both HF id and short alias so clients can pick either.
        # This endpoint does NOT download weights — it only lists configured ids.
        ids = []
        for model_id in (engine.model_id, "Qwen3-8B"):
            if model_id and model_id not in ids:
                ids.append(model_id)
        return {
            "object": "list",
            "data": [
                {
                    "id": model_id,
                    "object": "model",
                    "owned_by": "local",
                }
                for model_id in ids
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionRequest) -> dict[str, Any]:
        requested = (body.model or "").strip()
        if requested and requested not in _model_aliases(engine):
            raise HTTPException(
                status_code=404,
                detail=(
                    f"model not found: {requested!r}. "
                    f"Use one of: {sorted(_model_aliases(engine))}"
                ),
            )
        content = engine.generate(
            [message.model_dump() for message in body.messages],
            max_tokens=body.max_tokens,
            temperature=body.temperature,
        )
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": engine.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }

    return app
