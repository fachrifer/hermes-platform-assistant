from __future__ import annotations

import asyncio
import json
import urllib.parse
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from office_gateway.actions import ActionError, ActionService, _check_approver, action_view
from office_gateway.bot_chat_lock import SessionLockError, bot_chat_info, release_bot_chat
from office_gateway.brief import build_brief
from office_gateway.chat import fetch_chat_bubble
from office_gateway.collectors import HttpServiceCollector
from office_gateway.config import GatewayConfig
from office_gateway.docker_ops import DockerOps, compact_listing
from office_gateway.edge_ops import AdapterNotConfigured as EdgeAdapterNotConfigured
from office_gateway.edge_ops import EdgeRoute, EdgeRoutesOps
from office_gateway.grafana_links import build_links
from office_gateway.k8s_ops import AdapterNotConfigured, K8sOps
from office_gateway.llm_ops import fetch_litellm_status
from office_gateway.mcp_server import build_router
from office_gateway.metrics import instant_query
from office_gateway.mig import compare_mig
from office_gateway.proc_ops import ProcOps
from office_gateway.redact import redact_text
from office_gateway.reports import queries
from office_gateway.roles import APPROVER_ROLE, can_read, can_write, role_for_token
from office_gateway.store import GatewayStore
from office_gateway.systemd_ops import SystemdOps
from office_gateway.tls_ops import tls_cert_status
from office_gateway.tools.core import ToolContext

_bearer = HTTPBearer(auto_error=False)

_HTTP_FOR_CATEGORY = {
    "forbidden": 403,
    "invalid_argument": 400,
    "not_configured": 503,
    "not_found": 404,
    "conflict": 409,
}


class ProposeRequest(BaseModel):
    action: str
    target: str
    params: Optional[dict] = None


class EdgeValidateRequest(BaseModel):
    content: str


def _edge_route_dict(route: EdgeRoute) -> dict:
    return {
        "name": route.name,
        "path": route.path,
        "upstream": route.upstream,
        "websocket": route.websocket,
    }


def _edge_status(ops: DockerOps) -> dict | None:
    try:
        for item in ops.list_containers().get("containers", []):
            if item.get("compose_service") == "office-edge":
                return {"name": item["name"], "status": item["status"]}
    except Exception:
        return None
    return None


def _action_http_error(exc: ActionError) -> HTTPException:
    status = _HTTP_FOR_CATEGORY.get(exc.category, 400)
    detail: dict = {"error": str(exc)}
    if exc.valid:
        detail["valid"] = exc.valid[:40]
    return HTTPException(status_code=status, detail=detail)


def create_app(
    config: GatewayConfig,
    docker_ops: DockerOps | None = None,
    k8s_ops: K8sOps | None = None,
    edge_ops: EdgeRoutesOps | None = None,
    collector: HttpServiceCollector | None = None,
    proc_ops=None,
    systemd_ops=None,
) -> FastAPI:
    store = GatewayStore(config.db_path, config.action_ttl_seconds)
    ops = docker_ops or DockerOps()
    k8s = k8s_ops or K8sOps(service_urls=config.service_urls)
    edge = edge_ops or EdgeRoutesOps(
        config.edge_routes_path or None,
        config.edge_locations_path or None,
        backup_dir=config.edge_backup_dir or None,
    )
    actions = ActionService(config, store, docker_ops=ops, edge_ops=edge)
    collector = collector or HttpServiceCollector(config.service_urls)
    app = FastAPI(title="office-gateway")

    def _role_from_request(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    ) -> str:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(status_code=401, detail="missing bearer token")
        role = role_for_token(config, credentials.credentials)
        if role is None:
            raise HTTPException(status_code=401, detail="invalid token")
        return role

    tool_ctx = ToolContext(
        config=config,
        store=store,
        actions=actions,
        docker=ops,
        edge=edge,
        k8s=k8s,
        collector=collector,
        proc=proc_ops or ProcOps(),
        systemd=systemd_ops or SystemdOps(),
    )
    app.include_router(build_router(tool_ctx, _role_from_request))

    def _require_read(role: str, route_prefix: str) -> None:
        if not can_read(role, route_prefix):
            raise HTTPException(status_code=403, detail="forbidden")

    def _require_approver(role: str) -> None:
        if role != APPROVER_ROLE:
            raise HTTPException(status_code=403, detail="forbidden")

    def _edge_container_names() -> set[str]:
        listing = ops.list_containers()
        return {
            str(c.get("name"))
            for c in listing.get("containers", [])
            if c.get("compose_service") == "office-edge"
        }

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/status")
    async def status(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/status")
        services = await collector.collect()
        return {"services": services}

    @app.get("/v1/fleet/brief")
    async def fleet_brief(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/fleet")
        services = await collector.collect()
        links = build_links(
            base=config.grafana_base_url,
            dashboards=config.grafana_dashboards,
            panels=config.grafana_panel_ids,
        )
        try:
            mig_data = await k8s.get_mig_actual()
            mig = compare_mig(config.mig_expected, mig_data.get("actual", {}))
            if mig_data.get("error"):
                mig["ok"] = False
                mig["error"] = mig_data["error"]
        except Exception:
            mig = {"ok": False, "error": "mig unavailable"}
        return build_brief(services=services, mig=mig, grafana_links=links)

    @app.get("/v1/fleet/chat")
    async def fleet_chat(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/fleet")
        return await fetch_chat_bubble(
            base_url=config.hermes_dashboard_url,
            username=config.hermes_dashboard_username,
            password=config.hermes_dashboard_password,
        )

    @app.get("/v1/services/{name}")
    async def service_status(
        name: str,
        role: str = Depends(_role_from_request),
    ) -> dict:
        _require_read(role, "/v1/services")
        return await collector.collect_one(name)

    @app.get("/v1/audit")
    async def audit(
        role: str = Depends(_role_from_request),
        limit: int = 50,
    ) -> dict:
        _require_read(role, "/v1/audit")
        return {"entries": store.list_audit(limit=limit)}

    @app.get("/v1/docker/containers")
    async def docker_containers(
        role: str = Depends(_role_from_request),
        all: bool = True,
        compact: bool = False,
    ) -> dict:
        _require_read(role, "/v1/docker")
        try:
            listing = ops.list_containers(all=all)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="adapter not configured") from exc
        if compact:
            return compact_listing(listing.get("containers") or [])
        return listing

    @app.get("/v1/docker/inspect/{container}")
    async def docker_inspect(
        container: str,
        role: str = Depends(_role_from_request),
    ) -> dict:
        _require_read(role, "/v1/docker")
        try:
            return ops.inspect(container)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="container not found") from exc

    @app.get("/v1/docker/logs/{container}")
    async def docker_logs(
        container: str,
        role: str = Depends(_role_from_request),
        tail: int = 80,
    ) -> dict:
        _require_read(role, "/v1/docker/logs")
        if role == "ingress" and container not in _edge_container_names():
            raise HTTPException(status_code=403, detail="forbidden")
        tail = max(1, min(int(tail), 200))
        try:
            result = ops.logs(container, tail=tail)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="container not found") from exc
        result["lines"] = [redact_text(line) for line in result.get("lines", [])]
        return result

    @app.get("/v1/docker/networks")
    async def docker_networks(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/docker")
        try:
            return ops.list_networks()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="adapter not configured") from exc

    @app.get("/v1/grafana/links")
    async def grafana_links(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/grafana/links")
        links = build_links(
            base=config.grafana_base_url,
            dashboards=config.grafana_dashboards,
            panels=config.grafana_panel_ids,
        )
        return {"links": links}

    @app.get("/v1/gpu/mig")
    async def gpu_mig(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/gpu/mig")
        mig_data = await k8s.get_mig_actual()
        actual = mig_data.get("actual", {})
        result = compare_mig(config.mig_expected, actual)
        if mig_data.get("error"):
            result["actual"] = {}
            result["error"] = mig_data["error"]
            result["ok"] = False
        return result

    @app.get("/v1/host/gpu")
    async def host_gpu(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/host/gpu")
        try:
            return await k8s.get_gpu_metrics()
        except AdapterNotConfigured as exc:
            raise HTTPException(status_code=503, detail="adapter not configured") from exc

    @app.get("/v1/k8s/resources")
    async def k8s_resources(
        kind: str = "",
        namespace: Optional[str] = None,
        role: str = Depends(_role_from_request),
    ) -> dict:
        _require_read(role, "/v1/k8s/resources")
        if not kind.strip():
            raise HTTPException(status_code=400, detail="kind required")
        try:
            return await k8s.list_resources(kind.strip().lower(), namespace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AdapterNotConfigured as exc:
            raise HTTPException(status_code=503, detail="adapter not configured") from exc

    @app.get("/v1/llm/status")
    async def llm_status(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/llm")
        return await fetch_litellm_status(config.litellm_url, config.litellm_master_key)

    @app.get("/v1/edge/routes")
    async def edge_routes(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/edge")
        try:
            routes = edge.list_routes()
        except EdgeAdapterNotConfigured as exc:
            raise HTTPException(status_code=503, detail="adapter not configured") from exc
        return {"routes": [_edge_route_dict(r) for r in routes], "backups": edge.list_backups()}

    @app.get("/v1/edge/status")
    async def edge_status(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/edge")
        if not edge.configured():
            raise HTTPException(status_code=503, detail="adapter not configured")
        return {
            "configured": True,
            "edge": _edge_status(ops),
            "tls": tls_cert_status(config.edge_tls_cert_path),
        }

    @app.get("/v1/edge/tls")
    async def edge_tls(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/edge")
        return tls_cert_status(config.edge_tls_cert_path)

    @app.post("/v1/edge/validate")
    async def edge_validate(
        body: EdgeValidateRequest,
        role: str = Depends(_role_from_request),
    ) -> dict:
        _require_read(role, "/v1/edge")
        if not edge.configured():
            raise HTTPException(status_code=503, detail="adapter not configured")
        try:
            routes = edge.validate(body.content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "routes": [_edge_route_dict(r) for r in routes]}

    @app.get("/v1/metrics/query")
    async def metrics_query(
        name: str = "",
        instance: str = "",
        window: str = "",
        role: str = Depends(_role_from_request),
    ) -> dict:
        _require_read(role, "/v1/metrics/query")
        try:
            promql = queries.render(name, instance or None, window or None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            return await instant_query(config.metrics_url, promql)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=502, detail="metrics upstream error") from exc
        except ValueError as exc:
            msg = str(exc)
            if msg == "query not allowlisted":
                detail = "query not allowlisted"
            elif msg == "query tidak valid":
                detail = "query not valid"
            elif msg == "OFFICE_METRICS_URL tidak diisi":
                detail = "metrics not configured"
            else:
                raise HTTPException(status_code=502, detail="metrics upstream error") from exc
            raise HTTPException(status_code=400, detail=detail) from exc
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="metrics upstream error")

    @app.post("/v1/actions/propose")
    async def propose_action(body: ProposeRequest, role: str = Depends(_role_from_request)) -> dict:
        if not can_write(role):
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            pending = actions.propose(role, body.action, body.target, body.params)
        except ActionError as exc:
            raise _action_http_error(exc) from exc
        return action_view(pending)

    @app.get("/v1/actions/{action_id}")
    async def action_status(action_id: str, role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/actions")
        try:
            return action_view(actions.status(role, action_id))
        except ActionError as exc:
            raise HTTPException(status_code=404, detail="action not found") from exc

    @app.get("/v1/approvals/bot-chat")
    async def bot_chat_status(role: str = Depends(_role_from_request)) -> dict:
        _require_approver(role)
        try:
            return await asyncio.to_thread(bot_chat_info, ops)
        except SessionLockError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/v1/approvals/bot-chat/open")
    async def open_bot_chat(role: str = Depends(_role_from_request)) -> RedirectResponse:
        """A bookmarkable door: the Dashboard hides Bot Chat, so send the browser to it by id."""
        _require_approver(role)
        try:
            info = await asyncio.to_thread(bot_chat_info, ops)
        except SessionLockError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not info["session_id"]:
            raise HTTPException(status_code=404, detail="no session titled Bot Chat")
        return RedirectResponse(
            f"/dash/chat?resume={urllib.parse.quote(info['session_id'], safe='')}", status_code=302
        )

    @app.post("/v1/approvals/bot-chat/release")
    async def release_bot_chat_lock(
        role: str = Depends(_role_from_request),
        x_approver: str = Header(default=""),
    ) -> dict:
        _require_approver(role)
        try:
            _check_approver(x_approver.strip())
        except ActionError as exc:
            raise _action_http_error(exc) from exc
        try:
            return await asyncio.to_thread(release_bot_chat, ops)
        except SessionLockError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/v1/approvals")
    async def list_approvals(
        role: str = Depends(_role_from_request), status: str = "pending", limit: int = 50
    ) -> dict:
        _require_approver(role)
        statuses = ("pending",) if status == "pending" else None
        return {"actions": [action_view(a) for a in store.list_actions(statuses, limit)]}

    @app.post("/v1/approvals/{action_id}/approve")
    async def approve_action(
        action_id: str,
        role: str = Depends(_role_from_request),
        x_approver: str = Header(default=""),
    ) -> dict:
        _require_approver(role)
        try:
            return action_view(actions.approve(action_id, x_approver.strip()))
        except ActionError as exc:
            raise _action_http_error(exc) from exc

    @app.post("/v1/approvals/{action_id}/reject")
    async def reject_action(
        action_id: str,
        role: str = Depends(_role_from_request),
        x_approver: str = Header(default=""),
    ) -> dict:
        _require_approver(role)
        try:
            return action_view(actions.reject(action_id, x_approver.strip()))
        except ActionError as exc:
            raise _action_http_error(exc) from exc

    return app
