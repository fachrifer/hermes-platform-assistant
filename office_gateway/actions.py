from __future__ import annotations

import os
import re

import httpx

from office_gateway.docker_ops import DockerOps
from office_gateway.edge_ops import EdgeRoutesOps, RouteApplyError, route_diff
from office_gateway.roles import can_propose
from office_gateway.store import PendingAction

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OFFICE_GATEWAY_TARGET_RE = re.compile(r"(?:^|[-_.])office-gateway(?:[-_.]\d+)?$")
_APPROVER_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
_REASON_MAX = 200
_EDGE_TARGET = "edge-routes"


class ActionError(Exception):
    def __init__(self, message: str, category: str = "forbidden", valid=None) -> None:
        super().__init__(message)
        self.category = category
        self.valid = list(valid or [])


def action_view(action: PendingAction) -> dict:
    params = action.params
    view = {
        "action_id": action.action_id,
        "action": action.action,
        "target": action.target,
        "role": action.role,
        "summary": action.summary,
        "reason": params.get("reason", ""),
        "status": action.status,
        "created_at": action.created_at,
        "expires_at": action.expires_at,
        "approver": action.approver,
        "detail": action.detail,
        "result": action.result,
    }
    if action.action in ("apply_edge_routes", "rollback_edge_routes"):
        view["diff"] = params.get("diff", [])
    return view


def _adapter_endpoints() -> dict[str, str]:
    raw = os.getenv("OFFICE_ADAPTER_ENDPOINTS", "").strip()
    endpoints: dict[str, str] = {}
    for item in (part.strip() for part in raw.split(",") if part.strip()):
        name, separator, url = item.partition("=")
        if separator and _SERVICE_NAME_RE.fullmatch(name) and url.startswith(("http://", "https://")):
            endpoints[name] = url
    return endpoints


def _is_office_gateway_target(target: str) -> bool:
    return bool(_OFFICE_GATEWAY_TARGET_RE.search(target))


def _check_approver(approver: str) -> None:
    if not _APPROVER_RE.fullmatch(approver or ""):
        raise ActionError("X-Approver header required", "invalid_argument")


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

    def container_names(self) -> list[str]:
        listing = self.docker_ops.list_containers()
        return sorted(str(c.get("name") or "") for c in listing.get("containers", []))

    def propose(self, role: str, action: str, target: str, params: dict | None = None) -> PendingAction:
        if not can_propose(role, action):
            raise ActionError("action not allowed for role", "forbidden")
        params = dict(params or {})
        reason = str(params.get("reason") or "").strip()[:_REASON_MAX]
        if action == "restart_service":
            return self._propose_restart(role, target, reason)
        if action == "apply_edge_routes":
            return self._propose_apply(role, target, params.get("content"), reason)
        if action == "rollback_edge_routes":
            return self._propose_rollback(role, target, params.get("backup"), reason)
        raise ActionError("unknown action", "invalid_argument")

    def _propose_restart(self, role: str, target: str, reason: str) -> PendingAction:
        names = self.container_names()
        if target not in names:
            raise ActionError("unknown container", "invalid_argument", names)
        return self.store.propose(
            action="restart_service",
            target=target,
            role=role,
            params={"reason": reason},
            summary=f"restart {target}",
        )

    def _require_edge(self, target: str) -> None:
        if target != _EDGE_TARGET:
            raise ActionError("unknown target", "invalid_argument", [_EDGE_TARGET])
        if not self.edge_ops.configured():
            raise ActionError("edge adapter not configured", "not_configured")

    def _propose_apply(self, role: str, target: str, content, reason: str) -> PendingAction:
        self._require_edge(target)
        if not isinstance(content, str) or not content.strip():
            raise ActionError("content required", "invalid_argument")
        try:
            routes = self.edge_ops.validate(content)
        except ValueError as exc:
            raise ActionError(str(exc), "invalid_argument") from exc
        diff = route_diff(self.edge_ops.read_text_or_empty(), content)
        if not diff:
            raise ActionError("no change", "invalid_argument")
        return self.store.propose(
            action="apply_edge_routes",
            target=target,
            role=role,
            params={"content": content, "diff": diff, "reason": reason},
            summary=f"apply edge-routes ({len(routes)} routes)",
        )

    def _propose_rollback(self, role: str, target: str, backup, reason: str) -> PendingAction:
        self._require_edge(target)
        backups = self.edge_ops.list_backups()
        if not backups:
            raise ActionError("no backups", "invalid_argument")
        chosen = str(backup or backups[0])
        if chosen not in backups:
            raise ActionError("unknown backup", "invalid_argument", backups)
        diff = route_diff(self.edge_ops.read_text_or_empty(), self.edge_ops.read_backup(chosen))
        return self.store.propose(
            action="rollback_edge_routes",
            target=target,
            role=role,
            params={"backup": chosen, "diff": diff, "reason": reason},
            summary=f"rollback edge-routes to {chosen}",
        )

    def status(self, role: str, action_id: str) -> PendingAction:
        action = self.store.get_action(action_id)
        if action is None or action.role != role:
            raise ActionError("action not found", "invalid_argument")
        return action

    def _not_open_reason(self, action_id: str) -> ActionError:
        action = self.store.get_action(action_id)
        if action is None:
            return ActionError("action not found", "not_found")
        return ActionError(f"action already {action.status}", "conflict")

    def reject(self, action_id: str, approver: str) -> PendingAction:
        _check_approver(approver)
        action = self.store.reject(action_id, approver)
        if action is None:
            raise self._not_open_reason(action_id)
        return action

    def approve(self, action_id: str, approver: str) -> PendingAction:
        _check_approver(approver)
        claimed = self.store.claim_pending(action_id, approver)
        if claimed is None:
            raise self._not_open_reason(action_id)
        if claimed.action == "restart_service":
            return self._execute_restart(claimed)
        if claimed.action == "apply_edge_routes":
            return self._execute_edge(claimed, lambda: self.edge_ops.apply(claimed.params.get("content", "")))
        if claimed.action == "rollback_edge_routes":
            return self._execute_edge(claimed, lambda: self.edge_ops.restore(claimed.params.get("backup")))
        return self._finish(claimed, False, "failed:invalid")

    def _finish(self, claimed: PendingAction, ok: bool, detail: str, result: dict | None = None) -> PendingAction:
        done = self.store.mark_executed(claimed.action_id, ok=ok, detail=detail, result=result)
        if done is None:
            raise ActionError("action not executing", "conflict")
        return done

    def _execute_restart(self, claimed: PendingAction) -> PendingAction:
        if _is_office_gateway_target(claimed.target):
            done = self._finish(claimed, True, "ok", {"note": "gateway restarts itself"})
            self._restart_target(claimed.target)
            return done
        try:
            self._restart_target(claimed.target)
        except ValueError:
            return self._finish(claimed, False, "failed:not_found")
        except Exception:
            return self._finish(claimed, False, "failed:adapter_unavailable")
        return self._finish(claimed, True, "ok")

    def _execute_edge(self, claimed: PendingAction, run) -> PendingAction:
        try:
            result = run()
        except RouteApplyError as exc:
            detail = "failed:rolled_back" if exc.rolled_back else "failed:invalid"
            return self._finish(claimed, False, detail, {"rolled_back": exc.rolled_back})
        except ValueError:
            return self._finish(claimed, False, "failed:invalid", {"rolled_back": False})
        except Exception:
            return self._finish(claimed, False, "failed:adapter_unavailable", {"rolled_back": False})
        return self._finish(claimed, True, "ok", result)

    def _restart_target(self, target: str) -> None:
        endpoints = _adapter_endpoints()
        if target in endpoints:
            response = httpx.post(endpoints[target], timeout=10.0)
            if response.status_code == 404:
                raise ValueError(f"container not found: {target}")
            response.raise_for_status()
            return
        self.docker_ops.restart(target)
