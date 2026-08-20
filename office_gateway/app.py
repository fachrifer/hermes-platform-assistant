from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from office_gateway.actions import ActionError, ActionService
from office_gateway.collectors import HttpServiceCollector
from office_gateway.config import GatewayConfig
from office_gateway.docker_ops import DockerOps
from office_gateway.roles import can_read, can_write, role_for_token
from office_gateway.store import GatewayStore

_bearer = HTTPBearer(auto_error=False)


class ProposeRequest(BaseModel):
    action: str
    target: str
    params: Optional[dict] = None


class ExecuteRequest(BaseModel):
    action_id: str


def create_app(config: GatewayConfig, docker_ops: DockerOps | None = None) -> FastAPI:
    store = GatewayStore(config.db_path, config.action_ttl_seconds)
    ops = docker_ops or DockerOps()
    actions = ActionService(config, store, docker_ops=ops)
    collector = HttpServiceCollector(config.service_urls)
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

    @app.get("/v1/docker/inspect/{container}")
    async def docker_inspect(
        container: str,
        role: str = Depends(_role_from_request),
    ) -> dict:
        _require_read(role, "/v1/docker/inspect")
        try:
            return ops.inspect(container)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="container not found") from exc

    @app.post("/v1/actions/propose")
    async def propose_action(
        body: ProposeRequest,
        role: str = Depends(_role_from_request),
    ) -> dict:
        if not can_write(role):
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            pending = actions.propose(role, body.action, body.target, body.params)
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
