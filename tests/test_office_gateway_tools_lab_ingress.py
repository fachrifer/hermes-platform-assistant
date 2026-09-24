import asyncio
import json

from gw_helpers import FakeDockerOps, make_config
from office_gateway.actions import ActionService
from office_gateway.edge_ops import EdgeRoutesOps
from office_gateway.store import GatewayStore
from office_gateway.tools import REGISTRY, tools_for_role
from office_gateway.tools.core import RESULT_CAP, ToolContext, call_tool

ROUTES = "grafana /grafana/ 10.0.0.1:3000 0\n"


class FakeProc:
    def host_usage(self):
        gib = 1024**3
        return {
            "loadavg": [4.0, 3.0, 2.0],
            "cpu_count": 8,
            "memory_bytes": {"total": 16 * gib, "available": 4 * gib, "free": 1 * gib},
            "swap_bytes": {"total": 2 * gib, "used": 1 * gib},
            "disk_bytes": {"total": 100 * gib, "used": 90 * gib, "free": 10 * gib, "path": "/"},
            "uptime_seconds": 7200.0,
        }


class FakeSystemd:
    def list_units(self, unit_type="service"):
        return {
            "units": [
                {"id": "docker.service", "description": "", "load_state": "loaded", "active_state": "active", "sub_state": "running"},
                {"id": "nginx.service", "description": "", "load_state": "loaded", "active_state": "failed", "sub_state": "failed"},
            ],
            "truncated": False,
        }


def _ctx(tmp_path, docker=None):
    config = make_config(tmp_path)
    store = GatewayStore(config.db_path)
    routes = tmp_path / "edge-routes"
    routes.write_text(ROUTES, encoding="utf-8")
    edge = EdgeRoutesOps(str(routes), str(tmp_path / "routes.yml"))
    docker = docker or FakeDockerOps(
        logs={
            "aiplatform-api": [f"line {i} ok" for i in range(300)] + ["ERROR token=abcdef123456"],
            "office-office-edge-1": ["GET /grafana 200", "GET /attu 502"],
        }
    )
    actions = ActionService(config, store, docker_ops=docker, edge_ops=edge)
    return ToolContext(config, store, actions, docker, edge, None, None, FakeProc(), FakeSystemd())


def _call(ctx, role, name, args=None):
    out = asyncio.run(call_tool(ctx, REGISTRY, role, name, args or {}))
    assert len(json.dumps(out)) <= RESULT_CAP
    return out


def test_systemd_ops_imports():
    import office_gateway.systemd_ops  # noqa: F401


def test_role_catalogs():
    lab = {t.name for t in tools_for_role("lab-host")}
    assert lab == {
        "list_containers", "inspect_container", "tail_logs", "host_resources",
        "list_host_services", "propose_restart", "action_status",
    }
    ingress = {t.name for t in tools_for_role("ingress")}
    assert ingress == {
        "list_routes", "edge_status", "tls_status", "tail_traefik_logs",
        "validate_route_change", "propose_route_change", "propose_route_rollback", "action_status",
    }


def test_list_and_inspect_containers(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "lab-host", "list_containers")["data"]
    assert data["total"] == 3 and data["not_running"] == ["broken-worker (exited)"]
    bad = _call(ctx, "lab-host", "inspect_container", {"name": "nope"})
    assert bad["error"]["category"] == "invalid_argument" and "aiplatform-api" in bad["error"]["valid"]
    info = _call(ctx, "lab-host", "inspect_container", {"name": "aiplatform-api"})["data"]
    assert info["env_names"] == ["PATH", "SECRET_TOKEN"] and "env" not in info


def test_tail_logs_is_bounded_redacted_and_filterable(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "lab-host", "tail_logs", {"name": "aiplatform-api", "lines": 100})["data"]
    assert data["lines"][-1] == "ERROR token=[redacted]"
    assert data["omitted"] > 0
    only = _call(ctx, "lab-host", "tail_logs", {"name": "aiplatform-api", "contains": "error"})["data"]
    assert only["lines"] == ["ERROR token=[redacted]"]


def test_host_resources_and_services(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "lab-host", "host_resources")["data"]
    assert data["mem_used_pct"] == 75.0 and data["disk_used_pct"] == 90.0 and data["load_per_core"] == 0.5
    services = _call(ctx, "lab-host", "list_host_services")["data"]
    assert services["failed"] == 1 and services["units"] == ["nginx.service (failed/failed)"]


def test_propose_restart_and_action_status(tmp_path):
    ctx = _ctx(tmp_path)
    missing = _call(ctx, "lab-host", "propose_restart", {"container": "aiplatform-api"})
    assert missing["error"]["category"] == "invalid_argument"
    data = _call(ctx, "lab-host", "propose_restart", {"container": "aiplatform-api", "reason": "hung"})["data"]
    assert data["status"] == "pending" and data["approve_at"] == "https://console.test/approvals/"
    status = _call(ctx, "lab-host", "action_status", {"action_id": data["action_id"]})["data"]
    assert status["status"] == "pending"
    assert _call(ctx, "ingress", "action_status", {"action_id": data["action_id"]})["ok"] is False
    assert ctx.docker.restarted == []


def test_ingress_routes_validate_and_propose(tmp_path):
    ctx = _ctx(tmp_path)
    assert _call(ctx, "ingress", "list_routes")["data"]["routes"][0]["name"] == "grafana"
    bad = _call(ctx, "ingress", "validate_route_change", {"content": "x /x 1 0\n"})
    assert bad["error"]["category"] == "invalid_argument"
    new = ROUTES + "attu /attu/ 10.0.0.2:8000 1\n"
    ok = _call(ctx, "ingress", "validate_route_change", {"content": new})["data"]
    assert ok["valid"] is True and "+attu /attu/ 10.0.0.2:8000 1" in ok["diff"]
    proposed = _call(ctx, "ingress", "propose_route_change", {"content": new, "reason": "add attu"})["data"]
    assert proposed["status"] == "pending"
    none = _call(ctx, "ingress", "propose_route_rollback", {"reason": "undo"})
    assert none["error"]["category"] == "invalid_argument"


def test_traefik_logs_only_edge_container(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "ingress", "tail_traefik_logs", {"contains": "502"})["data"]
    assert data["lines"] == ["GET /attu 502"]
