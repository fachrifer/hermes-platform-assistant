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


def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="Qwen3 8B CML", docs_url=None, redoc_url=None)
    app.state.engine = engine

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": engine.model_id,
                    "object": "model",
                    "owned_by": "local",
                }
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(body: ChatCompletionRequest) -> dict[str, Any]:
        if body.model not in {engine.model_id, "Qwen3-8B", "qwen3-8b"}:
            raise HTTPException(status_code=404, detail="model not found")
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
