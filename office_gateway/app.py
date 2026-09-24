from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

import httpx

from office_gateway.actions import ActionError, ActionService
from office_gateway.autoheal import compact_listing
from office_gateway.brief import build_brief
from office_gateway.chat import fetch_chat_bubble
from office_gateway.collectors import HttpServiceCollector
from office_gateway.config import GatewayConfig
from office_gateway.docker_ops import DockerOps
from office_gateway.edge_ops import AdapterNotConfigured as EdgeAdapterNotConfigured
from office_gateway.edge_ops import EdgeRoute, EdgeRoutesOps
from office_gateway.tls_ops import tls_cert_status
from office_gateway.grafana_links import build_links
from office_gateway.k8s_ops import AdapterNotConfigured, K8sOps
from office_gateway.llm_ops import (
    LiteLLMNotConfigured,
    fetch_litellm_status,
    proxy_litellm_get,
)
from office_gateway.metrics import instant_query
from office_gateway.mig import compare_mig
from office_gateway.roles import can_post_watch, can_read, can_write, role_for_token
from office_gateway.store import GatewayStore

_bearer = HTTPBearer(auto_error=False)


class ProposeRequest(BaseModel):
    action: str
    target: str
    params: Optional[dict] = None
    auto_execute: bool = False


class ExecuteRequest(BaseModel):
    action_id: str


class EdgeValidateRequest(BaseModel):
    content: str


class WatchSnapshotRequest(BaseModel):
    snapshot: str
    ts: str
    alert: bool
    summary: str
    role: Optional[str] = None


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
            if item.get("compose_service") in ("office-edge", "office-console"):
                return {"name": item["name"], "status": item["status"]}
    except Exception:
        return None
    return None


def create_app(
    config: GatewayConfig,
    docker_ops: DockerOps | None = None,
    k8s_ops: K8sOps | None = None,
    edge_ops: EdgeRoutesOps | None = None,
    collector: HttpServiceCollector | None = None,
) -> FastAPI:
    store = GatewayStore(config.db_path, config.action_ttl_seconds)
    ops = docker_ops or DockerOps()
    k8s = k8s_ops or K8sOps(service_urls=config.service_urls)
    edge = edge_ops or EdgeRoutesOps(
        config.edge_routes_path or None,
        config.edge_locations_path or None,
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

    def _require_read(role: str, route_prefix: str) -> None:
        if not can_read(role, route_prefix):
            raise HTTPException(status_code=403, detail="forbidden")

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
        """List Lab Docker containers (lab-host). Prefer this over per-name inspect."""
        _require_read(role, "/v1/docker")
        try:
            listing = ops.list_containers(all=all)
            if compact:
                return compact_listing(
                    listing.get("containers") or [],
                    config.autoheal_deny,
                )
            return listing
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="adapter not configured"
            ) from exc

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
        _require_read(role, "/v1/docker")
        allowed = config.log_targets.get(role, frozenset())
        if container not in allowed:
            raise HTTPException(status_code=403, detail="forbidden")
        tail = max(1, min(int(tail), 200))
        try:
            return ops.logs(container, tail=tail)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="container not found") from exc

    @app.get("/v1/docker/networks")
    async def docker_networks(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/docker")
        try:
            return ops.list_networks()
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="adapter not configured"
            ) from exc

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
        normalized = kind.strip().lower()
        if role == "llm-edge" and normalized not in {"httproute", "gateway"}:
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            return await k8s.list_resources(normalized, namespace)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except AdapterNotConfigured as exc:
            raise HTTPException(status_code=503, detail="adapter not configured") from exc

    @app.get("/v1/llm/status")
    async def llm_status(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/llm")
        return await fetch_litellm_status(
            config.litellm_url, config.litellm_master_key
        )

    @app.get("/v1/litellm/{path:path}")
    async def litellm_proxy(
        path: str,
        request: Request,
        role: str = Depends(_role_from_request),
    ) -> JSONResponse:
        _require_read(role, "/v1/litellm")
        try:
            status, payload = await proxy_litellm_get(
                config.litellm_url,
                config.litellm_master_key,
                path,
                params=dict(request.query_params),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LiteLLMNotConfigured as exc:
            raise HTTPException(
                status_code=503, detail="litellm not configured"
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail="litellm upstream error"
            ) from exc
        return JSONResponse(status_code=status, content=payload)

    @app.get("/v1/edge/routes")
    async def edge_routes(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/edge")
        try:
            text = edge.read_text()
            routes = edge.list_routes()
        except EdgeAdapterNotConfigured as exc:
            raise HTTPException(status_code=503, detail="adapter not configured") from exc
        return {"text": text, "routes": [_edge_route_dict(r) for r in routes]}

    @app.get("/v1/edge/status")
    async def edge_status(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/edge")
        if not edge.configured():
            raise HTTPException(status_code=503, detail="adapter not configured")
        return {
            "routes_path": config.edge_routes_path,
            "locations_path": config.edge_locations_path,
            "configured": True,
            "edge": _edge_status(ops),
            "console": _edge_status(ops),  # alias during nginx→Traefik transition
            "tls": tls_cert_status(config.edge_tls_cert_path),
        }

    @app.get("/v1/edge/tls")
    async def edge_tls(role: str = Depends(_role_from_request)) -> dict:
        """Read-only leaf certificate metadata (expiry / subject). No private key."""
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
        query: str = "",
        role: str = Depends(_role_from_request),
    ) -> dict:
        _require_read(role, "/v1/metrics/query")
        try:
            return await instant_query(config.metrics_url, query)
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
                raise HTTPException(
                    status_code=502, detail="metrics upstream error"
                ) from exc
            raise HTTPException(status_code=400, detail=detail) from exc
        except httpx.HTTPError:
            raise HTTPException(status_code=502, detail="metrics upstream error")

    @app.post("/v1/watch/snapshot")
    async def watch_snapshot(
        body: WatchSnapshotRequest,
        role: str = Depends(_role_from_request),
    ) -> dict[str, str]:
        if not can_post_watch(role):
            raise HTTPException(status_code=403, detail="forbidden")
        store.upsert_watch(
            role,
            body.snapshot,
            body.ts,
            body.alert,
            body.summary,
        )
        return {"status": "ok"}

    @app.get("/v1/watch/summary")
    async def watch_summary(role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/watch")
        return store.list_watch_summary(datetime.now(timezone.utc))

    @app.post("/v1/actions/propose")
    async def propose_action(
        body: ProposeRequest,
        role: str = Depends(_role_from_request),
    ) -> dict:
        if not can_write(role):
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            pending = actions.propose(
                role,
                body.action,
                body.target,
                body.params,
                body.auto_execute,
            )
        except ActionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {
            "action_id": pending.action_id,
            "action": pending.action,
            "target": pending.target,
            "summary": pending.summary,
            "created_at": pending.created_at,
            "expires_at": pending.expires_at,
            "status": pending.status,
        }

    @app.post("/v1/actions/execute")
    async def execute_action(
        body: ExecuteRequest,
        role: str = Depends(_role_from_request),
    ) -> dict:
        if not can_write(role):
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            result = actions.execute(role, body.action_id)
        except ActionError as exc:
            msg = str(exc)
            if msg == "action not found":
                raise HTTPException(status_code=404, detail=msg) from exc
            raise HTTPException(status_code=403, detail=msg) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="action not found")
        return {
            "action_id": result.action_id,
            "status": result.status,
            "target": result.target,
            "summary": result.summary,
        }

    return app
