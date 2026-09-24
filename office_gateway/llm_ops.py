"""LiteLLM admin reads. The master key stays on office-gateway."""

from __future__ import annotations

import json
from typing import Any

import httpx

_TIMEOUT = httpx.Timeout(8.0)
_PROXY_TIMEOUT = httpx.Timeout(15.0)
_MAX_MODEL_IDS = 20
_SECRET_NAME_PARTS = ("token", "secret", "password", "authorization")
_SECRET_NAMES = {
    "key",
    "api_key",
    "access_token",
    "authorization",
    "secret",
    "password",
    "master_key",
    "litellm_api_key",
}


class LiteLLMNotConfigured(Exception):
    pass


def _is_secret_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in _SECRET_NAMES or lowered.endswith("_key"):
        return True
    return any(part in lowered for part in _SECRET_NAME_PARTS)


def _redact_value(value: Any, master_key: str) -> Any:
    if isinstance(value, str):
        if value.startswith("sk-") or (master_key and master_key in value):
            return "[redacted]"
        return value
    if isinstance(value, list):
        return [_redact_value(item, master_key) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _is_secret_name(name) and isinstance(item, str):
                out[name] = "[redacted]"
            else:
                out[name] = _redact_value(item, master_key)
        return out
    return value


def _safe_subpath(path: str) -> str:
    raw = (path or "").strip().lstrip("/")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not raw or ".." in parts or ".." in raw:
        raise ValueError("invalid path")
    return "/".join(parts)


async def proxy_litellm_get(
    base_url: str,
    master_key: str,
    path: str,
    params: dict[str, str] | None = None,
) -> tuple[int, Any]:
    if not (base_url or "").strip() or not (master_key or "").strip():
        raise LiteLLMNotConfigured
    subpath = _safe_subpath(path)
    root = proxy_root(base_url)
    headers = {"Authorization": f"Bearer {master_key}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=_PROXY_TIMEOUT) as client:
        response = await client.get(
            f"{root}/{subpath}",
            headers=headers,
            params=params or {},
        )
    try:
        payload = response.json()
    except json.JSONDecodeError:
        text = response.text or ""
        if master_key and master_key in text:
            text = "[redacted]"
        elif "sk-" in text:
            text = "[redacted]"
        return response.status_code, {"text": text[:4000]}
    return response.status_code, _redact_value(payload, master_key)


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
