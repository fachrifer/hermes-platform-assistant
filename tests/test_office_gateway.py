"""Tests for office_gateway health, guardrails, and adapters."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from office_gateway.actions import ActionError, ActionService, validate_propose
from office_gateway.adapters import WriteAdapter
from office_gateway.app import create_app
from office_gateway.collectors import HttpServiceCollector
from office_gateway.config import GatewayConfig, _scale_bounds, _write_targets
from office_gateway.host import HostVmCollector
from office_gateway.store import GatewayStore


def _config(tmp_path, **overrides) -> GatewayConfig:
    base = dict(
        token="test-token",
        service_urls={
            "litellm": "http://litellm.internal/health",
            "grafana": "http://grafana.internal/api/health",
            "openwebui": "http://openwebui.internal/health",
        },
        write_targets={
            "litellm": frozenset({"restart_service", "scale_replicas"}),
            "queue": frozenset({"clear_queue"}),
            "openwebui": frozenset({"set_feature_flag", "restart_service"}),
        },
        scale_bounds={"litellm": (1, 4)},
        adapter_endpoints={
            ("litellm", "restart_service"): "http://adapter.internal/restart",
            ("litellm", "scale_replicas"): "http://adapter.internal/scale",
        },
        db_path=str(tmp_path / "gateway.db"),
        action_ttl_seconds=600,
    )
    base.update(overrides)
    return GatewayConfig(**base)


def _auth(token: str = "test-token") -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_collector_status_without_internal_urls():
    async def request(url: str) -> tuple[int, float]:
        assert "internal" in url
        return 200, 12.0

    collector = HttpServiceCollector(
        {"litellm": "http://litellm.internal/health"}, request=request
    )
    services = await collector.collect()
    assert services == [{"name": "litellm", "status": "ok", "latency_ms": 12.0}]
    assert "litellm.internal" not in str(services)


@pytest.mark.asyncio
async def test_collector_treats_auth_challenge_as_up():
    async def request(url: str) -> tuple[int, float]:
        return 401, 4.0

    collector = HttpServiceCollector(
        {"agent-inference": "http://host.internal:8010/v1/models"}, request=request
    )
    services = await collector.collect()
    assert services[0]["status"] == "ok"


def test_write_targets_and_scale_bounds_parsing():
    targets = _write_targets("litellm:restart_service|scale_replicas,queue:clear_queue")
    assert targets["litellm"] == frozenset({"restart_service", "scale_replicas"})
    assert _scale_bounds("litellm:1-4")["litellm"] == (1, 4)
    with pytest.raises(ValueError):
        _write_targets("litellm:delete_cluster")


def test_validate_propose_scale_bounds():
    config = GatewayConfig(
        token="x",
        service_urls={"litellm": "http://x/health"},
        write_targets={"litellm": frozenset({"scale_replicas"})},
        scale_bounds={"litellm": (1, 4)},
    )
    assert validate_propose(config, "scale_replicas", "litellm", {"replicas": 2}) == {
        "replicas": 2
    }
    with pytest.raises(ActionError, match="outside bounds"):
        validate_propose(config, "scale_replicas", "litellm", {"replicas": 9})
    with pytest.raises(ActionError, match="not allowlisted"):
        validate_propose(config, "delete", "litellm", {})


@pytest.mark.asyncio
async def test_propose_execute_requires_pending_action(tmp_path):
    config = _config(tmp_path)
    store = GatewayStore(config.db_path)
    service = ActionService(config, store, adapter=WriteAdapter({}))

    pending = service.propose("restart_service", "litellm", {})
    assert pending.action_id
    assert "Restart" in pending.summary

    with pytest.raises(ActionError, match="unknown action_id"):
        await service.execute("missing-id")


@pytest.mark.asyncio
async def test_execute_without_adapter_records_failure(tmp_path):
    config = _config(tmp_path, adapter_endpoints={})
    store = GatewayStore(config.db_path)
    service = ActionService(config, store, adapter=WriteAdapter({}))
    pending = service.propose("restart_service", "litellm", {})
    with pytest.raises(ActionError, match="adapter not configured"):
        await service.execute(pending.action_id)
    events = store.list_audit()
    assert any(e["event"] == "propose" for e in events)
    assert any(e["event"] == "execute_failure" for e in events)


@pytest.mark.asyncio
async def test_execute_success_with_fake_adapter(tmp_path):
    calls: list[tuple[str, dict]] = []

    class FakeAdapter(WriteAdapter):
        async def execute(self, action: str, target: str, params: dict):
            calls.append((action, {"target": target, **params}))
            from office_gateway.adapters import WriteResult

            return WriteResult(ok=True, detail="ok")

    config = _config(tmp_path)
    store = GatewayStore(config.db_path)
    service = ActionService(config, store, adapter=FakeAdapter({}))
    pending = service.propose("scale_replicas", "litellm", {"replicas": 3})
    result = await service.execute(pending.action_id)
    assert result["status"] == "executed"
    assert calls == [("scale_replicas", {"target": "litellm", "replicas": 3})]


@pytest.mark.asyncio
async def test_expired_action_rejected(tmp_path):
    config = _config(tmp_path, action_ttl_seconds=60)
    store = GatewayStore(config.db_path)
    service = ActionService(config, store, adapter=WriteAdapter({}))
    pending = service.propose("restart_service", "litellm", {})
    # Force expiry in DB
    with store._connect() as conn:
        conn.execute(
            "UPDATE actions SET expires_at = ? WHERE action_id = ?",
            ("2000-01-01T00:00:00Z", pending.action_id),
        )
    with pytest.raises(ActionError, match="expired"):
        await service.execute(pending.action_id)


def test_api_auth_and_propose_execute_flow(tmp_path):
    config = _config(tmp_path)

    class FakeAdapter(WriteAdapter):
        async def execute(self, action: str, target: str, params: dict):
            from office_gateway.adapters import WriteResult

            return WriteResult(ok=True, detail="restarted")

    store = GatewayStore(config.db_path)
    app = create_app(
        config,
        store=store,
        collector=HttpServiceCollector(
            config.service_urls,
            request=lambda url: _ok(url),
        ),
        actions=ActionService(config, store, adapter=FakeAdapter({})),
    )

    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/v1/status").status_code == 401

    status = client.get("/v1/status", headers=_auth())
    assert status.status_code == 200
    assert {s["name"] for s in status.json()["services"]} >= {"litellm", "grafana"}

    bad = client.post(
        "/v1/actions/propose",
        headers=_auth(),
        json={"action": "restart_service", "target": "unknown"},
    )
    assert bad.status_code == 400

    proposed = client.post(
        "/v1/actions/propose",
        headers=_auth(),
        json={"action": "restart_service", "target": "litellm"},
    )
    assert proposed.status_code == 200
    body = proposed.json()
    assert body["approval_phrase"].startswith("APPROVE ")
    action_id = body["action_id"]

    executed = client.post(
        "/v1/actions/execute",
        headers=_auth(),
        json={"action_id": action_id},
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "executed"

    # Second execute must fail (no longer pending)
    again = client.post(
        "/v1/actions/execute",
        headers=_auth(),
        json={"action_id": action_id},
    )
    assert again.status_code == 400


async def _ok(url: str) -> tuple[int, float]:
    return 200, 5.0


@pytest.mark.asyncio
async def test_async_status_endpoint(tmp_path):
    config = _config(tmp_path)
    store = GatewayStore(config.db_path)

    async def request(url: str) -> tuple[int, float]:
        return (503, 1.0) if "grafana" in url else (200, 2.0)

    app = create_app(
        config,
        store=store,
        collector=HttpServiceCollector(config.service_urls, request=request),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/status", headers=_auth())
    assert response.status_code == 200
    by_name = {s["name"]: s for s in response.json()["services"]}
    assert by_name["litellm"]["status"] == "ok"
    assert by_name["grafana"]["status"] == "critical"


def _fake_docker(path: str):
    if path == "/_ping":
        return 200, "OK"
    if path.startswith("/containers/json"):
        return 200, [
            {"Names": ["/milvus-standalone"], "State": "running", "Status": "Up 3 hours"},
            {"Names": ["/qdrant_timai"], "State": "exited", "Status": "Exited (0) 2 minutes ago"},
        ]
    raise AssertionError(path)


def _fake_proc(tmp_path):
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("0.50 0.40 0.30 1/200 99\n")
    (proc / "stat").write_text("cpu 1 0 0 0\ncpu0 1 0 0 0\ncpu1 1 0 0 0\n")
    (proc / "meminfo").write_text("MemTotal:        8000000 kB\nMemAvailable:    2000000 kB\n")
    root = tmp_path / "root"
    root.mkdir()
    (root / "keep").write_text("x")
    return proc, root


def test_host_vm_snapshot_docker_and_performance(tmp_path):
    proc, root = _fake_proc(tmp_path)
    collector = HostVmCollector(
        docker_sock="/secret/docker.sock",
        host_proc=str(proc),
        host_root=str(root),
        docker_request=_fake_docker,
        disk_usage=lambda path: (500 * 1024**3, 200 * 1024**3, 300 * 1024**3),
    )
    snap = collector.snapshot()
    assert snap["name"] == "lab-vm"
    assert snap["status"] == "ok"
    assert snap["docker"]["daemon"] == "ok"
    assert snap["docker"]["running"] == 1
    assert snap["docker"]["exited"] == 1
    names = {c["name"] for c in snap["docker"]["containers"]}
    assert names == {"milvus-standalone", "qdrant_timai"}
    assert snap["cpu"]["load1"] == 0.5
    assert snap["cpu"]["percent"] == 25.0
    assert snap["memory"]["percent"] == 75.0
    assert snap["disk"]["percent"] == 40.0
    dumped = str(snap)
    assert "docker.sock" not in dumped
    assert str(proc) not in dumped


def test_host_vm_critical_when_docker_down(tmp_path):
    proc, root = _fake_proc(tmp_path)

    def docker_request(path: str):
        raise OSError("connection refused")

    snap = HostVmCollector(
        docker_sock="/var/run/docker.sock",
        host_proc=str(proc),
        host_root=str(root),
        docker_request=docker_request,
        disk_usage=lambda path: (100, 50, 50),
    ).snapshot()
    assert snap["docker"]["daemon"] == "critical"
    assert snap["status"] == "critical"


def test_host_vm_critical_when_disk_high(tmp_path):
    proc, root = _fake_proc(tmp_path)
    snap = HostVmCollector(
        docker_sock="/var/run/docker.sock",
        host_proc=str(proc),
        host_root=str(root),
        cpu_critical=90,
        memory_critical=90,
        disk_critical=80,
        docker_request=_fake_docker,
        disk_usage=lambda path: (100, 95, 5),
    ).snapshot()
    assert snap["disk"]["percent"] == 95.0
    assert snap["status"] == "critical"


def test_v1_host_requires_gateway_token(tmp_path):
    proc, root = _fake_proc(tmp_path)
    config = _config(tmp_path, host_enabled=True)
    host = HostVmCollector(
        docker_sock="/var/run/docker.sock",
        host_proc=str(proc),
        host_root=str(root),
        docker_request=_fake_docker,
        disk_usage=lambda path: (100, 40, 60),
    )
    app = create_app(
        config,
        collector=HttpServiceCollector(config.service_urls, request=lambda url: _ok(url)),
        host=host,
    )
    client = TestClient(app)
    assert client.get("/v1/host").status_code == 401
    body = client.get("/v1/host", headers=_auth()).json()
    assert body["name"] == "lab-vm"
    assert body["docker"]["daemon"] == "ok"
    status = client.get("/v1/status", headers=_auth()).json()
    assert status["host"]["name"] == "lab-vm"


def test_v1_host_disabled_when_not_configured(tmp_path):
    config = _config(tmp_path, host_enabled=False)
    app = create_app(
        config,
        collector=HttpServiceCollector(config.service_urls, request=lambda url: _ok(url)),
    )
    client = TestClient(app)
    assert client.get("/v1/host", headers=_auth()).status_code == 404
    status = client.get("/v1/status", headers=_auth()).json()
    assert "host" not in status
