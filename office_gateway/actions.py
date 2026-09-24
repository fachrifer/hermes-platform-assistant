from __future__ import annotations

import json
import os
import re

import httpx

from office_gateway.autoheal import is_autoheal_denied, is_faulted
from office_gateway.docker_ops import DockerOps
from office_gateway.edge_ops import EdgeRoutesOps
from office_gateway.roles import can_write

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OFFICE_GATEWAY_TARGET_RE = re.compile(
    r"(?:^|[-_.])office-gateway(?:[-_.]\d+)?$"
)
_EDGE_SERVICE = "office-edge"
# Transition alias: older skills/docs said office-console (nginx).
_EDGE_SERVICE_ALIASES = frozenset({_EDGE_SERVICE, "office-console"})


class ActionError(Exception):
    pass


def _resolve_container(docker_ops: DockerOps, target: str) -> dict:
    try:
        return docker_ops.inspect(target)
    except ValueError:
        listing = docker_ops.list_containers()
        for item in listing.get("containers", []):
            if item.get("name") == target:
                return {
                    "name": target,
                    "status": item.get("status", ""),
                    "health": item.get("health"),
                    "compose_service": item.get("compose_service", ""),
                }
        raise ActionError("unknown target") from None


def validate_propose(
    config,
    role: str,
    action: str,
    target: str,
    *,
    auto_execute: bool = False,
    docker_ops: DockerOps | None = None,
) -> None:
    if not can_write(role):
        raise ActionError("role cannot write")
    if auto_execute:
        if role != "lab-host" or action != "restart_service":
            raise ActionError("action not allowlisted")
        if docker_ops is None:
            raise ActionError("unknown target")
        info = _resolve_container(docker_ops, target)
        if is_autoheal_denied(
            str(info.get("name") or target),
            str(info.get("compose_service") or ""),
            extra_deny=config.autoheal_deny,
        ):
            raise ActionError("autoheal denied")
        if not is_faulted(
            str(info.get("status") or ""),
            info.get("health") if isinstance(info.get("health"), str) else None,
        ):
            raise ActionError("target not faulted")
        return
    if config.vector_env.get(target) == "prod":
        raise ActionError("writes disabled for prod")
    if role == "edge" and action == "restart_service":
        return
    allowed = config.write_targets.get(role, frozenset())
    if target not in allowed:
        raise ActionError("unknown target")


def validate_execute(
    config,
    role: str,
    proposed_role: str,
    action: str,
    target: str,
    *,
    auto_execute: bool = False,
    docker_ops: DockerOps | None = None,
) -> None:
    if role != proposed_role:
        raise ActionError("forbidden")
    validate_propose(
        config,
        role,
        action,
        target,
        auto_execute=auto_execute,
        docker_ops=docker_ops,
    )


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


def _is_office_edge_container(info: dict) -> bool:
    return info.get("compose_service") in _EDGE_SERVICE_ALIASES


def _edge_container_names(docker_ops: DockerOps) -> list[str]:
    listing = docker_ops.list_containers()
    names: list[str] = []
    for item in listing.get("containers", []):
        if _is_office_edge_container(item):
            names.append(str(item["name"]))
    return names


class ActionService:
    def __init__(
        self,
        config,
        store,
        docker_ops: DockerOps | None = None,
        edge_ops: EdgeRoutesOps | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.docker_ops = docker_ops or DockerOps()
        self.edge_ops = edge_ops or EdgeRoutesOps(None, None)

    def propose(
        self,
        role: str,
        action: str,
        target: str,
        params: dict | None = None,
        auto_execute: bool = False,
    ):
        if role == "edge":
            if auto_execute:
                raise ActionError("action not allowlisted")
            return self._propose_edge(role, action, target, params)
        if action != "restart_service":
            raise ActionError("action not allowlisted")
        validate_propose(
            self.config,
            role,
            action,
            target,
            auto_execute=auto_execute,
            docker_ops=self.docker_ops,
        )
        summary = f"restart {target}"
        pending = self.store.propose(
            action=action,
            target=target,
            role=role,
            params=params,
            summary=summary,
        )
        if auto_execute:
            return self.execute(role, pending.action_id, auto_execute=True)
        return pending

    def _propose_edge(
        self, role: str, action: str, target: str, params: dict | None = None
    ):
        if action == "apply_edge_routes":
            if target != "edge-routes":
                raise ActionError("unknown target")
            if not self.edge_ops.configured():
                raise ActionError("action not allowlisted")
            content = (params or {}).get("content")
            if not isinstance(content, str):
                raise ActionError("action not allowlisted")
            try:
                self.edge_ops.validate(content)
            except ValueError as exc:
                raise ActionError(str(exc)) from exc
            validate_propose(self.config, role, action, target)
            summary = "apply edge-routes"
            return self.store.propose(
                action=action,
                target=target,
                role=role,
                params=params,
                summary=summary,
            )
        if action == "restart_service":
            if not self._is_office_edge_target(target):
                raise ActionError("unknown target")
            validate_propose(self.config, role, action, target)
            summary = f"restart {target}"
            return self.store.propose(
                action=action,
                target=target,
                role=role,
                params=params,
                summary=summary,
            )
        raise ActionError("action not allowlisted")

    def execute(self, role: str, action_id: str, *, auto_execute: bool = False):
        if not can_write(role):
            raise ActionError("role cannot write")
        action = self.store.get_action(action_id)
        if action is None:
            raise ActionError("action not found")
        validate_execute(
            self.config,
            role,
            action.role,
            action.action,
            action.target,
            auto_execute=auto_execute,
            docker_ops=self.docker_ops,
        )
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
        if claimed.action == "apply_edge_routes":
            return self._execute_apply_edge_routes(action_id, claimed)
        if claimed.action == "restart_service" and claimed.role == "edge":
            return self._execute_restart_console(action_id, claimed)
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

    def _execute_apply_edge_routes(self, action_id: str, claimed) -> object:
        params = json.loads(claimed.params_json or "{}")
        content = params.get("content", "")
        ok = False
        detail = "failed:adapter_unavailable"
        try:
            self.edge_ops.apply(content)
            ok = True
            detail = "ok"
        except ValueError:
            detail = "failed:invalid"
        except Exception:
            detail = "failed:adapter_unavailable"
        result = self.store.mark_executed(action_id, ok=ok, detail=detail)
        if result is None:
            raise ActionError("action not pending")
        return result

    def _execute_restart_console(self, action_id: str, claimed) -> object:
        ok = False
        detail = "failed:adapter_unavailable"
        try:
            for name in self._resolve_edge_restart_targets(claimed.target):
                self.docker_ops.restart(name)
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

    def _find_edge_containers(self) -> list[str]:
        return _edge_container_names(self.docker_ops)

    def _resolve_edge_restart_targets(self, target: str) -> list[str]:
        if target in _EDGE_SERVICE_ALIASES:
            names = self._find_edge_containers()
            if not names:
                raise ValueError(f"container not found: {target}")
            return names
        try:
            info = self.docker_ops.inspect(target)
        except ValueError as exc:
            raise ValueError(f"container not found: {target}") from exc
        if not _is_office_edge_container(info):
            raise ValueError(f"container not allowed: {target}")
        return [target]

    def _is_office_edge_target(self, target: str) -> bool:
        if target in _EDGE_SERVICE_ALIASES:
            return bool(self._find_edge_containers())
        try:
            info = self.docker_ops.inspect(target)
        except ValueError:
            return False
        return _is_office_edge_container(info)

    def _restart_target(self, target: str) -> None:
        endpoints = _adapter_endpoints()
        if target in endpoints:
            response = httpx.post(endpoints[target], timeout=30.0)
            if response.status_code == 404:
                raise ValueError(f"container not found: {target}")
            response.raise_for_status()
            return
        self.docker_ops.restart(target)
