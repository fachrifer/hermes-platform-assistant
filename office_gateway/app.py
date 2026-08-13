"""FastAPI application for the office gateway."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from office_gateway.actions import ActionError, ActionService
from office_gateway.collectors import HttpServiceCollector
from office_gateway.config import GatewayConfig
from office_gateway.host import HostVmCollector
from office_gateway.store import GatewayStore


class ProposeBody(BaseModel):
    action: str
    target: str
    params: dict = Field(default_factory=dict)


class ExecuteBody(BaseModel):
    action_id: str


def create_app(
    config: GatewayConfig,
    *,
    store: GatewayStore | None = None,
    collector: HttpServiceCollector | None = None,
    actions: ActionService | None = None,
    host: HostVmCollector | None = None,
) -> FastAPI:
    store = store or GatewayStore(config.db_path)
    collector = collector or HttpServiceCollector(config.service_urls)
    actions = actions or ActionService(config, store)
    if host is None and config.host_enabled:
        host = HostVmCollector(
            docker_sock=config.docker_sock,
            host_proc=config.host_proc,
            host_root=config.host_root,
            cpu_critical=config.host_cpu_critical,
            memory_critical=config.host_memory_critical,
            disk_critical=config.host_disk_critical,
        )

    app = FastAPI(title="Hermes Office Gateway", docs_url=None, redoc_url=None)
    app.state.config = config
    app.state.store = store
    app.state.collector = collector
    app.state.actions = actions
    app.state.host = host

    def require_token(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {config.token}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/v1/status", dependencies=[Depends(require_token)])
    async def status() -> dict:
        services = await collector.collect()
        payload = {
            "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "services": services,
        }
        if config.host_enabled and host is not None:
            payload["host"] = host.snapshot()
        return payload

    @app.get("/v1/host", dependencies=[Depends(require_token)])
    async def host_status() -> dict:
        if not config.host_enabled or host is None:
            raise HTTPException(status_code=404, detail="host collector disabled")
        return host.snapshot()

    @app.get("/v1/services/{name}", dependencies=[Depends(require_token)])
    async def service(name: str) -> dict:
        item = await collector.collect_one(name)
        if item is None:
            raise HTTPException(status_code=404, detail="unknown service")
        return item

    @app.post("/v1/actions/propose", dependencies=[Depends(require_token)])
    async def propose(body: ProposeBody) -> dict:
        try:
            pending = actions.propose(body.action, body.target, body.params)
        except ActionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "action_id": pending.action_id,
            "summary": pending.summary,
            "expires_at": pending.expires_at,
            "approval_phrase": f"APPROVE {pending.action_id}",
        }

    @app.post("/v1/actions/execute", dependencies=[Depends(require_token)])
    async def execute(body: ExecuteBody) -> dict:
        try:
            return await actions.execute(body.action_id)
        except ActionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/audit", dependencies=[Depends(require_token)])
    async def audit(limit: int = 50) -> dict:
        return {"events": store.list_audit(limit=max(1, min(limit, 200)))}

    return app
