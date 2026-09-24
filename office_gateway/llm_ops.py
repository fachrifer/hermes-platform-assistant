"""LiteLLM admin reads. The master key stays on office-gateway."""

from __future__ import annotations

import json
from typing import Any

import httpx

_TIMEOUT = httpx.Timeout(8.0)
_MAX_MODEL_IDS = 20


def proxy_root(url: str) -> str:
    root = (url or "").strip().rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")].rstrip("/")
    return root


def _model_ids(payload: Any) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    ids: list[str] = []
    if not isinstance(data, list):
        return ids
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id")
        if isinstance(model_id, str) and model_id and not model_id.startswith("sk-"):
            ids.append(model_id)
    return ids


def _key_count(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    keys = payload.get("keys")
    if isinstance(keys, list):
        return len(keys)
    return None


async def fetch_litellm_status(base_url: str, master_key: str) -> dict[str, Any]:
    if not (base_url or "").strip() or not (master_key or "").strip():
        return {"configured": False, "ok": False}
    root = proxy_root(base_url)
    headers = {"Authorization": f"Bearer {master_key}"}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            models_r = await client.get(f"{root}/v1/models", headers=headers)
        except httpx.HTTPError:
            return {
                "configured": True,
                "ok": False,
                "auth": "error",
                "error": "upstream",
            }
        if models_r.status_code in (401, 403):
            return {
                "configured": True,
                "ok": False,
                "auth": "unauthorized",
                "models": 0,
                "keys": 0,
                "error": "unauthorized",
            }
        if models_r.status_code >= 400:
            return {
                "configured": True,
                "ok": False,
                "auth": "error",
                "error": "upstream",
            }
        try:
            models_payload = models_r.json()
        except json.JSONDecodeError:
            return {
                "configured": True,
                "ok": False,
                "auth": "error",
                "error": "upstream",
            }
        model_ids = _model_ids(models_payload)
        keys_count: int | None = None
        try:
            keys_r = await client.get(f"{root}/key/list", headers=headers)
            if keys_r.status_code == 200:
                keys_count = _key_count(keys_r.json())
        except (httpx.HTTPError, json.JSONDecodeError):
            keys_count = None
        return {
            "configured": True,
            "ok": True,
            "auth": "ok",
            "models": len(model_ids),
            "model_ids": model_ids[:_MAX_MODEL_IDS],
            "keys": keys_count,
        }
