from fastapi.testclient import TestClient

from gw_helpers import FakeDockerOps, auth, make_config
from office_gateway.app import create_app
from office_gateway.edge_ops import EdgeRoutesOps

ROUTES = "grafana /grafana/ 10.0.0.1:3000 0\n"


def _client(tmp_path, docker=None):
    routes = tmp_path / "edge" / "edge-routes"
    routes.parent.mkdir()
    routes.write_text(ROUTES, encoding="utf-8")
    edge = EdgeRoutesOps(str(routes), str(tmp_path / "edge" / "routes.yml"))
    docker = docker or FakeDockerOps()
    app = create_app(make_config(tmp_path), docker_ops=docker, edge_ops=edge)
    return TestClient(app), docker, routes


def _propose_restart(client, target="aiplatform-api"):
    return client.post(
        "/v1/actions/propose",
        headers=auth("lab-host"),
        json={"action": "restart_service", "target": target, "params": {"reason": "hung"}},
    )


def test_propose_restart_is_pending_and_not_executed(tmp_path):
    client, docker, _ = _client(tmp_path)
    r = _propose_restart(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending" and body["reason"] == "hung"
    assert docker.restarted == []


def test_propose_unknown_container_lists_valid_names(tmp_path):
    client, _, _ = _client(tmp_path)
    r = _propose_restart(client, "nope")
    assert r.status_code == 400
    assert "aiplatform-api" in r.json()["detail"]["valid"]


def test_auto_execute_and_execute_endpoint_are_gone(tmp_path):
    client, docker, _ = _client(tmp_path)
    r = client.post(
        "/v1/actions/propose",
        headers=auth("lab-host"),
        json={"action": "restart_service", "target": "aiplatform-api", "auto_execute": True},
    )
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert docker.restarted == []
    r = client.post("/v1/actions/execute", headers=auth("lab-host"), json={"action_id": "x"})
    assert r.status_code in (404, 405)


def test_agents_cannot_approve(tmp_path):
    client, docker, _ = _client(tmp_path)
    action_id = _propose_restart(client).json()["action_id"]
    for role in ("lab-host", "supervisor"):
        r = client.post(f"/v1/approvals/{action_id}/approve", headers=auth(role, approver="x"))
        assert r.status_code == 403
    assert docker.restarted == []


def test_approve_requires_x_approver(tmp_path):
    client, docker, _ = _client(tmp_path)
    action_id = _propose_restart(client).json()["action_id"]
    r = client.post(f"/v1/approvals/{action_id}/approve", headers=auth("approver"))
    assert r.status_code == 400
    assert docker.restarted == []


def test_approve_executes_once_and_records_approver(tmp_path):
    client, docker, _ = _client(tmp_path)
    action_id = _propose_restart(client).json()["action_id"]
    r = client.post(f"/v1/approvals/{action_id}/approve", headers=auth("approver", approver="tim"))
    assert r.status_code == 200
    assert r.json()["status"] == "succeeded" and r.json()["approver"] == "tim"
    assert docker.restarted == ["aiplatform-api"]
    again = client.post(f"/v1/approvals/{action_id}/approve", headers=auth("approver", approver="tim"))
    assert again.status_code == 409
    assert docker.restarted == ["aiplatform-api"]
    audit = client.get("/v1/audit", headers=auth("approver")).json()["entries"]
    assert [e["event"] for e in audit][:3] == ["succeeded", "approved", "proposed"]


def test_approve_unknown_action_is_404(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.post("/v1/approvals/nope/approve", headers=auth("approver", approver="tim"))
    assert r.status_code == 404


def test_restart_failure_is_recorded(tmp_path):
    client, _, _ = _client(tmp_path, docker=FakeDockerOps(fail_restart=True))
    action_id = _propose_restart(client).json()["action_id"]
    r = client.post(f"/v1/approvals/{action_id}/approve", headers=auth("approver", approver="tim"))
    assert r.json()["status"] == "failed"
    assert r.json()["detail"] == "failed:adapter_unavailable"


def test_reject_then_status_visible_to_proposer_only(tmp_path):
    client, docker, _ = _client(tmp_path)
    action_id = _propose_restart(client).json()["action_id"]
    r = client.post(f"/v1/approvals/{action_id}/reject", headers=auth("approver", approver="tim"))
    assert r.json()["status"] == "rejected"
    assert client.get(f"/v1/actions/{action_id}", headers=auth("lab-host")).json()["status"] == "rejected"
    assert client.get(f"/v1/actions/{action_id}", headers=auth("ingress")).status_code == 404
    assert docker.restarted == []


def test_list_pending_and_recent(tmp_path):
    client, _, _ = _client(tmp_path)
    first = _propose_restart(client).json()["action_id"]
    second = _propose_restart(client, "broken-worker").json()["action_id"]
    client.post(f"/v1/approvals/{first}/reject", headers=auth("approver", approver="tim"))
    pending = client.get("/v1/approvals", headers=auth("approver")).json()["actions"]
    assert [a["action_id"] for a in pending] == [second]
    recent = client.get("/v1/approvals?status=recent", headers=auth("approver")).json()["actions"]
    assert {a["action_id"] for a in recent} == {first, second}
    assert client.get("/v1/approvals", headers=auth("lab-host")).status_code == 403


def test_route_change_flow_with_diff_backup_and_rollback(tmp_path):
    client, _, routes = _client(tmp_path)
    new = ROUTES + "attu /attu/ 10.0.0.2:8000 1\n"
    r = client.post(
        "/v1/actions/propose",
        headers=auth("ingress"),
        json={"action": "apply_edge_routes", "target": "edge-routes", "params": {"content": new, "reason": "add attu"}},
    )
    assert r.status_code == 200
    assert "+attu /attu/ 10.0.0.2:8000 1" in r.json()["diff"]
    applied = client.post(
        f"/v1/approvals/{r.json()['action_id']}/approve", headers=auth("approver", approver="tim")
    ).json()
    assert applied["status"] == "succeeded" and applied["result"]["backup"]
    assert routes.read_text(encoding="utf-8") == new
    rb = client.post(
        "/v1/actions/propose",
        headers=auth("ingress"),
        json={"action": "rollback_edge_routes", "target": "edge-routes", "params": {"reason": "undo"}},
    )
    assert rb.status_code == 200
    done = client.post(
        f"/v1/approvals/{rb.json()['action_id']}/approve", headers=auth("approver", approver="tim")
    ).json()
    assert done["status"] == "succeeded"
    assert routes.read_text(encoding="utf-8") == ROUTES


def test_no_change_route_proposal_rejected(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.post(
        "/v1/actions/propose",
        headers=auth("ingress"),
        json={"action": "apply_edge_routes", "target": "edge-routes", "params": {"content": ROUTES}},
    )
    assert r.status_code == 400


def test_ingress_cannot_restart_and_vector_cannot_write(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.post(
        "/v1/actions/propose",
        headers=auth("ingress"),
        json={"action": "restart_service", "target": "office-office-edge-1"},
    )
    assert r.status_code == 403
    r = client.post(
        "/v1/actions/propose",
        headers=auth("vector"),
        json={"action": "restart_service", "target": "aiplatform-api"},
    )
    assert r.status_code == 403


def test_docker_logs_rbac(tmp_path):
    docker = FakeDockerOps(logs={"office-office-edge-1": ["GET / 200"], "aiplatform-api": ["token=abc123456789"]})
    client, _, _ = _client(tmp_path, docker=docker)
    assert client.get("/v1/docker/logs/office-office-edge-1", headers=auth("ingress")).status_code == 200
    assert client.get("/v1/docker/logs/aiplatform-api", headers=auth("ingress")).status_code == 403
    lines = client.get("/v1/docker/logs/aiplatform-api", headers=auth("lab-host")).json()["lines"]
    assert lines == ["token=[redacted]"]


def test_retired_endpoints_are_gone(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/v1/litellm/models", headers=auth("llm")).status_code == 404
    assert client.post("/v1/watch/snapshot", headers=auth("lab-host"), json={}).status_code == 404
    assert client.get("/v1/watch/summary", headers=auth("supervisor")).status_code == 404


def test_no_autoheal_module():
    import importlib.util

    assert importlib.util.find_spec("office_gateway.autoheal") is None
