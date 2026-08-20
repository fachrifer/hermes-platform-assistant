from __future__ import annotations

import os
import re

import httpx

from office_gateway.docker_ops import DockerOps
from office_gateway.roles import can_write

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OFFICE_GATEWAY_TARGET_RE = re.compile(
    r"(?:^|[-_.])office-gateway(?:[-_.]\d+)?$"
)


class ActionError(Exception):
    pass


def validate_propose(config, role: str, action: str, target: str) -> None:
    if not can_write(role):
        raise ActionError("role cannot write")
    if config.vector_env.get(target) == "prod":
        raise ActionError("writes disabled for prod")
    allowed = config.write_targets.get(role, frozenset())
    if target not in allowed:
        raise ActionError("unknown target")


def validate_execute(config, role: str, proposed_role: str, action: str, target: str) -> None:
    if role != proposed_role:
        raise ActionError("forbidden")
    validate_propose(config, role, action, target)


def _adapter_endpoints() -> dict[str, str]:
    raw = os.getenv("OFFICE_ADAPTER_ENDPOINTS", "").strip()
    if not raw:
        return {}
    endpoints: dict[str, str] = {}
    for item in (part.strip() for part in raw.split(",") if part.strip()):
        name, separator, url = item.partition("=")
        if not separator or not _SERVICE_NAME_RE.fullmatch(name) or not url.startswith(
            ("http://", "https://")
        ):
            continue
        endpoints[name] = url
    return endpoints


def _is_office_gateway_target(target: str) -> bool:
    return bool(_OFFICE_GATEWAY_TARGET_RE.search(target))


class ActionService:
    def __init__(self, config, store, docker_ops: DockerOps | None = None) -> None:
        self.config = config
        self.store = store
        self.docker_ops = docker_ops or DockerOps()

    def propose(self, role: str, action: str, target: str, params: dict | None = None):
        if action != "restart_service":
            raise ActionError("action not allowlisted")
        validate_propose(self.config, role, action, target)
        summary = f"restart {target}"
        return self.store.propose(
            action=action,
            target=target,
            role=role,
            params=params,
            summary=summary,
        )

    def execute(self, role: str, action_id: str):
        if not can_write(role):
            raise ActionError("role cannot write")
        action = self.store.get_action(action_id)
        if action is None:
            raise ActionError("action not found")
        validate_execute(self.config, role, action.role, action.action, action.target)
        claimed = self.store.claim_pending(action_id)
        if claimed is None:
            action = self.store.get_action(action_id)
            if action is None:
                raise ActionError("action not found")
            if action.status == "expired" or (
                action.status == "pending" and self.store._is_expired(action)
            ):
                self.store.mark_expired_if_needed(action)
                raise ActionError("action expired")
            raise ActionError("action not pending")
        if _is_office_gateway_target(claimed.target):
            result = self.store.mark_executed(action_id, ok=True, detail="ok")
            if result is None:
                raise ActionError("action not pending")
            self._restart_target(claimed.target)
            return result
        ok = False
        detail = "failed:adapter_unavailable"
        try:
            self._restart_target(claimed.target)
            ok = True
            detail = "ok"
        except ValueError:
            detail = "failed:not_found"
        except Exception:
            detail = "failed:adapter_unavailable"
        result = self.store.mark_executed(action_id, ok=ok, detail=detail)
        if result is None:
            raise ActionError("action not pending")
        return result

    def _restart_target(self, target: str) -> None:
        endpoints = _adapter_endpoints()
        if target in endpoints:
            response = httpx.post(endpoints[target], timeout=30.0)
            if response.status_code == 404:
                raise ValueError(f"container not found: {target}")
            response.raise_for_status()
            return
        self.docker_ops.restart(target)
