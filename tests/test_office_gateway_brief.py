from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from office_gateway.app import create_app
from office_gateway.brief import FLEET_AGENTS, build_brief
from office_gateway.config import GatewayConfig


def _config(db_path: str) -> GatewayConfig:
    return GatewayConfig(
        tokens={
            "supervisor": "tok-sup",
            "lab-host": "tok-lab",
            "vector": "tok-vec",
            "cluster-gpu": "tok-gpu",
            "llm-edge": "tok-llm",
            "obs": "tok-obs",
        },
        service_urls={"gateway": "http://gateway.internal/health"},
        grafana_base_url="http://grafana.internal",
        grafana_dashboards={"gpu": "migdash"},
        grafana_panel_ids={"gpu": 2},
        db_path=db_path,
    )


def test_fleet_agents_are_gplusf_identities():
    names = {agent["id"]: (agent["name"], agent["fate_class"]) for agent in FLEET_AGENTS}
    assert names["supervisor"] == ("Athena", "Ruler")
    assert names["lab-host"] == ("Hephaestus", "Archer")
    assert names["vector"] == ("Mnemosyne", "Caster")
    assert names["cluster-gpu"] == ("Surtr", "Berserker")
    assert names["llm-edge"] == ("Iris", "Rider")
    assert names["obs"] == ("Argus", "Watcher")


def test_build_brief_maps_services_and_identities():
    payload = build_brief(
        services=[{"name": "gateway", "status": "ok", "latency_ms": 4}],
        mig={"ok": True, "actual": {"1g.18gb": 7}, "expected": {"1g.18gb": 7}, "missing": {}},
        grafana_links=[{"name": "gpu", "url": "http://grafana.internal/d/migdash?viewPanel=2"}],
    )
    by_id = {agent["id"]: agent for agent in payload["agents"]}
    assert by_id["lab-host"]["purpose"] == "Lab host"
    assert by_id["vector"]["purpose"] == "Vector store"
    assert by_id["cluster-gpu"]["purpose"] == "GPU cluster"
    assert by_id["llm-edge"]["purpose"] == "LLM edge"
    assert by_id["obs"]["purpose"] == "Observability"
    assert by_id["supervisor"]["status"] == "ok"
    assert by_id["lab-host"]["status"] == "unknown"
    assert payload["grafana"][0]["name"] == "gpu"
    assert payload["mig"]["ok"] is True
    assert payload["activity"][0]["kind"] == "listen"
    assert payload["activity"][0]["text"] == "Whenever you're ready."


def test_brief_status_is_green_when_agent_a2a_is_ready():
    payload = build_brief(
        services=[
            {"name": "gateway", "status": "ok"},
            {"name": "hermes-lab-host", "status": "ok"},
            {"name": "hermes-vector", "status": "ok"},
            {"name": "hermes-cluster-gpu", "status": "ok"},
            {"name": "hermes-llm-edge", "status": "ok"},
            {"name": "hermes-obs", "status": "ok"},
        ]
    )
    by_id = {agent["id"]: agent["status"] for agent in payload["agents"]}
    assert by_id == {
        "supervisor": "ok",
        "lab-host": "ok",
        "vector": "ok",
        "cluster-gpu": "ok",
        "llm-edge": "ok",
        "obs": "ok",
    }


def test_brief_status_does_not_use_substring_hermes_match():
    payload = build_brief(
        services=[{"name": "hermes-vector", "status": "ok"}]
    )
    by_id = {agent["id"]: agent["status"] for agent in payload["agents"]}
    assert by_id["vector"] == "ok"
    assert by_id["lab-host"] == "unknown"
    assert by_id["supervisor"] == "unknown"


def test_activity_asks_specialist_when_service_is_unhealthy():
    payload = build_brief(
        services=[
            {"name": "gateway", "status": "ok"},
            {"name": "hermes-cluster-gpu", "status": "warn"},
            {"name": "hermes-vector", "status": "critical"},
        ]
    )
    texts = [item["text"] for item in payload["activity"]]
    kinds = {item["kind"] for item in payload["activity"]}
    assert kinds == {"talk"}
    assert any("asking Surtr" in text for text in texts)
    assert any("Mnemosyne" in text for text in texts)


def test_supervisor_can_read_fleet_brief(tmp_path):
    collector = MagicMock()
    collector.collect = AsyncMock(
        return_value=[{"name": "gateway", "status": "ok", "latency_ms": 3}]
    )
    k8s = MagicMock()
    k8s.get_mig_actual = AsyncMock(return_value={"actual": {"1g.18gb": 7}})
    client = TestClient(
        create_app(
            replace(_config(str(tmp_path / "gw.db"))),
            k8s_ops=k8s,
            collector=collector,
        )
    )
    response = client.get(
        "/v1/fleet/brief",
        headers={"Authorization": "Bearer tok-sup"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["agents"]) == 6
    assert body["agents"][0]["name"] == "Athena"


def test_lab_host_cannot_read_fleet_brief(tmp_path):
    client = TestClient(create_app(replace(_config(str(tmp_path / "gw.db")))))
    response = client.get(
        "/v1/fleet/brief",
        headers={"Authorization": "Bearer tok-lab"},
    )
    assert response.status_code == 403
