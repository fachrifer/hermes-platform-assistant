from __future__ import annotations

import os
import re
import time

import httpx

from office_gateway import grafana_api
from office_gateway.docker_ops import DockerOps
from office_gateway.edge_ops import EdgeRoutesOps, RouteApplyError, route_diff
from office_gateway.grafana_api import GrafanaClientError
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
    if action.action in (
        "apply_edge_routes",
        "rollback_edge_routes",
        "create_dashboard",
        "add_mcp_tool",
        "archive_dashboard",
        "restart_gateway",
        "add_script",
    ):
        view["diff"] = params.get("diff", [])
    if action.action in ("add_mcp_tool", "add_script"):
        view["source"] = params.get("source", "")
    return view


def _adapter_endpoints() -> dict[str, str]:
    raw = os.getenv("OFFICE_ADAPTER_ENDPOINTS", "").strip()
    endpoints: dict[str, str] = {}
    for item in (part.strip() for part in raw.split(",") if part.strip()):
        name, separator, url = item.partition("=")
        if separator and _SERVICE_NAME_RE.fullmatch(name) and url.startswith(("http://", "https://")):
            endpoints[name] = url
    return endpoints


def _builtin_tools() -> set[str]:
    from office_gateway.tools import REGISTRY

    return set(REGISTRY)


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
        params = dict(params or {})
        reason = str(params.get("reason") or "").strip()[:_REASON_MAX]
        plugin = self._plugin_action(action)
        if plugin is not None:
            from office_gateway.mcp_plugins import PluginError
            if plugin.role != role:
                raise ActionError("action not allowed for role", "forbidden")
            try:
                return plugin.propose(self, role, target, params, reason)
            except ActionError:
                raise
            except PluginError as exc:
                raise ActionError(exc.detail, exc.category) from exc
        if not can_propose(role, action):
            raise ActionError("action not allowed for role", "forbidden")
        if action == "restart_service":
            return self._propose_restart(role, target, reason)
        if action == "apply_edge_routes":
            return self._propose_apply(role, target, params.get("content"), reason)
        if action == "rollback_edge_routes":
            return self._propose_rollback(role, target, params.get("backup"), reason)
        if action == "create_dashboard":
            return self._propose_dashboard(role, target, params, reason)
        if action == "add_mcp_tool":
            return self._propose_mcp_tool(role, target, params, reason)
        if action == "restart_gateway":
            return self._propose_gateway_restart(role, reason)
        if action == "add_script":
            return self._propose_script(role, target, params, reason)
        if action == "archive_dashboard":
            return self._propose_archive(role, target, params, reason)
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
        if claimed.action == "create_dashboard":
            return self._execute_dashboard(claimed)
        if claimed.action == "archive_dashboard":
            return self._execute_archive(claimed)
        if claimed.action == "add_mcp_tool":
            return self._execute_mcp_tool(claimed)
        if claimed.action == "add_script":
            return self._execute_script(claimed)
        if claimed.action == "restart_gateway":
            done = self._finish(claimed, True, "ok", {"note": "gateway restarts itself"})
            self._restart_gateway()
            return done
        plugin = self._plugin_action(claimed.action)
        if plugin is not None:
            from office_gateway.mcp_plugins import PluginError
            try:
                done = plugin.execute(self, claimed)
            except PluginError as exc:
                return self._finish(claimed, False, "failed:invalid", {"detail": exc.detail[:200]})
            except Exception as exc:
                return self._finish(claimed, False, "failed:invalid", {"detail": f"{type(exc).__name__}: {exc}"[:200]})
            if done is None:
                return self._finish(claimed, False, "failed:invalid", {"detail": "plugin execute returned nothing"})
            return done
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

    def _propose_dashboard(self, role: str, target: str, params: dict, reason: str) -> PendingAction:
        try:
            title = grafana_api.check_title(str(params.get("title") or target))
            panels = grafana_api.parse_panels(params.get("panels"))
            existing = grafana_api.search_dashboards(self.config.grafana_base_url, self.config.grafana_token)
        except GrafanaClientError as exc:
            raise ActionError(exc.detail, exc.category) from exc
        except ValueError as exc:
            raise ActionError(str(exc), "invalid_argument") from exc
        taken = {row["title"].lower() for row in existing}
        if title.lower() in taken:
            titled = f"{title} {time.strftime('%Y-%m-%d')}"
            title = grafana_api.check_title(titled[:80])
            if title.lower() in taken:
                raise ActionError("a dashboard with that title already exists", "invalid_argument")
        diff = [f"+ title: {title}", "+ folder: Hermes Fleet, or General if that folder does not exist"]
        diff.extend(f"+ {panel['type']}: {panel['title']} ({panel['query']})" for panel in panels)
        return self.store.propose(
            action="create_dashboard",
            target=title,
            role=role,
            params={"title": title, "panels": panels, "diff": diff, "reason": reason},
            summary=f"create Grafana dashboard {title} ({len(panels)} panels)",
        )

    def _execute_dashboard(self, claimed: PendingAction) -> PendingAction:
        try:
            result = grafana_api.create_dashboard(
                self.config.grafana_base_url,
                self.config.grafana_token,
                title=claimed.params.get("title", claimed.target),
                panels=claimed.params.get("panels") or [],
                folder_uid=self.config.grafana_folder_uid,
                datasource_uid=self.config.grafana_datasource_uid,
                message=str(claimed.params.get("reason") or claimed.summary),
            )
        except GrafanaClientError as exc:
            return self._finish(claimed, False, f"failed:{exc.category}", {"detail": exc.detail[:200]})
        except ValueError as exc:
            return self._finish(claimed, False, "failed:invalid", {"detail": str(exc)[:200]})
        return self._finish(claimed, True, "ok", result)

    def _propose_archive(self, role: str, target: str, params: dict, reason: str) -> PendingAction:
        uid = str(params.get("uid") or target).strip()
        hard_delete = bool(params.get("hard_delete"))
        try:
            record = grafana_api.dashboard_record(self.config.grafana_base_url, self.config.grafana_token, uid)
        except GrafanaClientError as exc:
            raise ActionError(exc.detail, exc.category) from exc
        if record["folder"] == grafana_api.ARCHIVED_FOLDER and not hard_delete:
            raise ActionError(f"dashboard {uid} is already archived", "not_found")
        title = record["title"]
        folder = record["folder"]
        move = (
            "DELETE the dashboard. This cannot be undone."
            if hard_delete
            else f"Move it from folder {folder or 'General'} to {grafana_api.ARCHIVED_FOLDER}."
        )
        diff = [
            f"dashboard: {title}",
            f"uid: {uid}",
            f"folder: {folder or 'General'}",
            "warning: This will remove the dashboard from its current folder",
            move,
        ]
        verb = "delete" if hard_delete else "archive"
        return self.store.propose(
            action="archive_dashboard",
            target=uid,
            role=role,
            params={
                "uid": uid,
                "title": title,
                "folder": folder,
                "reason": reason,
                "hard_delete": hard_delete,
                "diff": diff,
            },
            summary=f"{verb} Grafana dashboard {title}",
        )

    def _execute_archive(self, claimed: PendingAction) -> PendingAction:
        try:
            result = grafana_api.archive_dashboard(
                self.config.grafana_base_url,
                self.config.grafana_token,
                uid=str(claimed.params.get("uid") or claimed.target),
                reason=str(claimed.params.get("reason") or claimed.summary),
                hard_delete=bool(claimed.params.get("hard_delete")),
            )
        except GrafanaClientError as exc:
            detail = "failed:not_found" if exc.category == "not_found" else "failed:invalid"
            return self._finish(claimed, False, detail, {"detail": exc.detail[:200]})
        return self._finish(claimed, True, "ok", result)

    def _propose_mcp_tool(self, role: str, target: str, params: dict, reason: str) -> PendingAction:
        name = str(params.get("name") or target).strip()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", name):
            raise ActionError("tool name must be a short snake_case identifier", "invalid_argument")
        spec = str(params.get("spec") or "").strip()
        source = str(params.get("source") or "")
        if not spec or name not in spec:
            raise ActionError("spec must describe that tool", "invalid_argument")
        if len(spec) > 6000:
            raise ActionError("spec is too long", "invalid_argument")
        from office_gateway.mcp_plugins import PluginError, validate_source

        try:
            validate_source(name, source, _builtin_tools(), replace=bool(params.get("replace")))
        except PluginError as exc:
            raise ActionError(exc.detail, exc.category) from exc
        open_same = [
            item
            for item in self.store.list_actions(("pending",))
            if item.action == "add_mcp_tool" and item.target == name
        ]
        if open_same:
            raise ActionError("that tool spec is already pending", "conflict")
        summary = str(params.get("summary") or f"add MCP tool {name}").strip()[:_REASON_MAX]
        replacing = bool(params.get("replace"))
        diff = [
            f"+ tool: {name}",
            "+ replaces the built-in tool" if replacing else "+ adds a tool",
            "+ approval installs this module and restarts the gateway",
        ]
        for line in spec.splitlines():
            if len(diff) >= 40:
                diff.append("+ …")
                break
            diff.append(f"+ {line[:180]}")
        return self.store.propose(
            action="add_mcp_tool",
            target=name,
            role=role,
            params={
                "name": name,
                "summary": summary,
                "spec": spec,
                "source": source,
                "replace": replacing,
                "diff": diff,
                "reason": reason,
            },
            summary=summary,
        )

    def _execute_mcp_tool(self, claimed: PendingAction) -> PendingAction:
        from office_gateway.mcp_plugins import PluginError, install_plugin, plugin_dir

        name = str(claimed.params.get("name") or claimed.target)
        source = str(claimed.params.get("source") or "")
        try:
            path = install_plugin(
                plugin_dir(self.config),
                name,
                source,
                _builtin_tools(),
                replace=bool(claimed.params.get("replace")),
            )
        except PluginError as exc:
            return self._finish(claimed, False, "failed:invalid", {"detail": exc.detail[:200]})
        done = self._finish(
            claimed,
            True,
            "ok",
            {
                "implemented": True,
                "tool": name,
                "path": path.name,
                "note": "module installed; gateway restarts itself",
            },
        )
        self._restart_gateway()
        return done

    def _propose_script(self, role: str, target: str, params: dict, reason: str) -> PendingAction:
        from office_gateway.source_view import gateway_roots, script_destination

        rel = str(params.get("path") or target).strip()
        content = str(params.get("content") or "")
        replace = bool(params.get("replace"))
        try:
            script_destination(gateway_roots(), rel, content, replace)
        except ValueError as exc:
            raise ActionError(str(exc), "invalid_argument") from exc
        verb = "replace" if replace else "add"
        return self.store.propose(
            action="add_script",
            target=rel,
            role=role,
            params={
                "path": rel,
                "content": content,
                "source": content,
                "replace": replace,
                "reason": reason,
                "diff": [f"+ {rel}", f"+ {verb} this script"],
            },
            summary=f"{verb} script {rel}",
        )

    def _execute_script(self, claimed: PendingAction) -> PendingAction:
        from office_gateway.source_view import gateway_roots, write_script

        try:
            result = write_script(
                gateway_roots(),
                str(claimed.params.get("path") or claimed.target),
                str(claimed.params.get("content") or ""),
                bool(claimed.params.get("replace")),
            )
        except ValueError as exc:
            return self._finish(claimed, False, "failed:invalid", {"detail": str(exc)[:200]})
        except OSError as exc:
            return self._finish(claimed, False, "failed:invalid", {"detail": f"could not write the script: {exc}"[:200]})
        return self._finish(claimed, True, "ok", result)

    def _propose_gateway_restart(self, role: str, reason: str) -> PendingAction:
        return self.store.propose(
            action="restart_gateway",
            target="office-gateway",
            role=role,
            params={"reason": reason, "diff": ["+ restart the office gateway"]},
            summary="restart the office gateway",
        )

    def _restart_gateway(self) -> None:
        try:
            names = self.container_names()
        except Exception:
            return
        for name in names:
            if _is_office_gateway_target(name):
                try:
                    self._restart_target(name)
                except Exception:
                    return
                return

    def _plugin_action(self, action: str):
        from office_gateway.mcp_plugins import load_plugins, plugin_dir

        return load_plugins(plugin_dir(self.config), _builtin_tools()).actions.get(action)

    def _restart_target(self, target: str) -> None:
        endpoints = _adapter_endpoints()
        if target in endpoints:
            response = httpx.post(endpoints[target], timeout=10.0)
            if response.status_code == 404:
                raise ValueError(f"container not found: {target}")
            response.raise_for_status()
            return
        self.docker_ops.restart(target)
