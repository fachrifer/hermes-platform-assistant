from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from office_gateway.actions import ActionError, ActionService
from office_gateway.config import GatewayConfig
from office_gateway.roles import ROLES, role_for_token


def _test_config(db_path: str = "/tmp/test-gateway.db") -> GatewayConfig:
    return GatewayConfig(
        tokens={
            "supervisor": "tok-sup",
            "lab-host": "tok-lab",
            "vector": "tok-vec",
            "cluster-gpu": "tok-gpu",
            "llm-edge": "tok-llm",
            "obs": "tok-obs",
        },
        service_urls={"grafana": "http://grafana.internal/api/health"},
        write_targets={
            "lab-host": frozenset({"aiplatform-dashboard"}),
            "vector": frozenset({"milvus-standalone", "attu"}),
        },
        vector_env={"milvus-standalone": "dev", "milvus-prod": "prod"},
        db_path=db_path,
    )


def test_roles_are_the_six_fleet_roles():
    assert ROLES == frozenset(
        {"supervisor", "lab-host", "vector", "cluster-gpu", "llm-edge", "obs"}
    )


def test_role_for_token_maps_each_bearer():
    config = GatewayConfig(
        tokens={
            "supervisor": "tok-sup",
            "lab-host": "tok-lab",
            "vector": "tok-vec",
            "cluster-gpu": "tok-gpu",
            "llm-edge": "tok-llm",
            "obs": "tok-obs",
        },
        service_urls={"grafana": "http://grafana.internal/api/health"},
        write_targets={"lab-host": frozenset({"aiplatform-dashboard"})},
        vector_env={"milvus-standalone": "dev", "milvus-prod": "prod"},
    )
    assert role_for_token(config, "tok-lab") == "lab-host"
    assert role_for_token(config, "nope") is None


@pytest.fixture
def client(tmp_path):
    from office_gateway.app import create_app

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    return TestClient(create_app(config))


def test_cluster_gpu_cannot_propose(client):
    response = client.post(
        "/v1/actions/propose",
        headers={"Authorization": "Bearer tok-gpu"},
        json={"action": "restart_service", "target": "milvus-standalone"},
    )
    assert response.status_code == 403


def test_vector_cannot_propose_aiplatform_dashboard(client):
    response = client.post(
        "/v1/actions/propose",
        headers={"Authorization": "Bearer tok-vec"},
        json={"action": "restart_service", "target": "aiplatform-dashboard"},
    )
    assert response.status_code == 403


def test_vector_cannot_propose_prod_target(client):
    response = client.post(
        "/v1/actions/propose",
        headers={"Authorization": "Bearer tok-vec"},
        json={"action": "restart_service", "target": "milvus-prod"},
    )
    assert response.status_code == 403


def test_validate_propose_rejects_non_writer(tmp_path):
    from office_gateway.store import GatewayStore

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    service = ActionService(config, GatewayStore(config.db_path, config.action_ttl_seconds))
    with pytest.raises(ActionError):
        service.propose("cluster-gpu", "restart_service", "milvus-standalone")


def test_validate_propose_rejects_wrong_allowlist(tmp_path):
    from office_gateway.store import GatewayStore

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    service = ActionService(config, GatewayStore(config.db_path, config.action_ttl_seconds))
    with pytest.raises(ActionError):
        service.propose("vector", "restart_service", "aiplatform-dashboard")


def test_validate_propose_rejects_prod(tmp_path):
    from office_gateway.store import GatewayStore

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    service = ActionService(config, GatewayStore(config.db_path, config.action_ttl_seconds))
    with pytest.raises(ActionError):
        service.propose("vector", "restart_service", "milvus-prod")
