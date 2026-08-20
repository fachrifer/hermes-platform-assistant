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


ALLOWED_AUDIT_DETAILS = frozenset(
    {
        "ok",
        "failed:adapter_unavailable",
        "failed:not_found",
        "failed:claim_conflict",
        "failed:expired",
    }
)


def test_audit_detail_allowlisted_only(tmp_path):
    from office_gateway.store import ALLOWED_AUDIT_DETAIL_CODES, GatewayStore

    store = GatewayStore(str(tmp_path / "gateway.db"))
    pending = store.propose("restart_service", "milvus-standalone", role="vector")
    assert store.claim_pending(pending.action_id) is not None
    store.mark_executed(
        pending.action_id,
        ok=False,
        detail="failed:adapter_unavailable",
    )

    entries = store.list_audit(limit=10)
    for entry in entries:
        assert entry["detail"] in ALLOWED_AUDIT_DETAIL_CODES
    failed = next(e for e in entries if e["event"] == "failed")
    assert failed["detail"] == "failed:adapter_unavailable"


def test_audit_api_returns_allowlisted_codes_only(tmp_path):
    from office_gateway.app import create_app
    from office_gateway.store import ALLOWED_AUDIT_DETAIL_CODES, GatewayStore

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    client = TestClient(create_app(config))
    store = GatewayStore(config.db_path)
    pending = store.propose("restart_service", "milvus-standalone", role="vector")
    assert store.claim_pending(pending.action_id) is not None
    store.mark_executed(
        pending.action_id,
        ok=False,
        detail="failed:adapter_unavailable",
    )

    response = client.get(
        "/v1/audit",
        headers={"Authorization": "Bearer tok-sup"},
    )
    assert response.status_code == 200
    for entry in response.json()["entries"]:
        assert entry["detail"] in ALLOWED_AUDIT_DETAIL_CODES


def test_action_error_messages_are_fixed(tmp_path):
    from office_gateway.store import GatewayStore

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    service = ActionService(config, GatewayStore(config.db_path, config.action_ttl_seconds))

    with pytest.raises(ActionError, match="action not allowlisted"):
        service.propose("vector", "evil_action", "milvus-standalone")

    with pytest.raises(ActionError, match="unknown target"):
        service.propose("vector", "restart_service", "aiplatform-dashboard")

    with pytest.raises(ActionError, match="writes disabled for prod"):
        service.propose("vector", "restart_service", "milvus-prod")


def test_execute_unknown_action_returns_404(client):
    response = client.post(
        "/v1/actions/execute",
        headers={"Authorization": "Bearer tok-vec"},
        json={"action_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404


def test_execute_second_call_rejects_not_pending(tmp_path):
    from office_gateway.store import GatewayStore

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    service = ActionService(config, GatewayStore(config.db_path, config.action_ttl_seconds))
    pending = service.propose("vector", "restart_service", "milvus-standalone")
    service.execute("vector", pending.action_id)
    with pytest.raises(ActionError, match="action not pending"):
        service.execute("vector", pending.action_id)


def test_claim_pending_is_atomic(tmp_path):
    from office_gateway.store import GatewayStore

    store = GatewayStore(str(tmp_path / "gateway.db"))
    pending = store.propose("restart_service", "milvus-standalone", role="vector")
    first = store.claim_pending(pending.action_id)
    second = store.claim_pending(pending.action_id)
    assert first is not None
    assert second is None


def test_claim_pending_rejects_expired_without_two_step_check(tmp_path):
    from datetime import datetime, timedelta, timezone

    from office_gateway.store import GatewayStore, _iso_z

    store = GatewayStore(str(tmp_path / "gateway.db"), action_ttl_seconds=1)
    pending = store.propose("restart_service", "milvus-standalone", role="vector")
    expired_at = _iso_z(datetime.now(timezone.utc) - timedelta(seconds=5))
    with store._connect() as conn:
        conn.execute(
            "UPDATE actions SET expires_at = ? WHERE action_id = ?",
            (expired_at, pending.action_id),
        )
    assert store.claim_pending(pending.action_id) is None
    row = store.get_action(pending.action_id)
    assert row is not None
    assert row.status == "pending"


def test_execute_expired_action_returns_action_expired(tmp_path):
    from datetime import datetime, timedelta, timezone

    from office_gateway.store import GatewayStore, _iso_z

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    store = GatewayStore(config.db_path, action_ttl_seconds=1)
    service = ActionService(config, store)
    pending = service.propose("vector", "restart_service", "milvus-standalone")
    expired_at = _iso_z(datetime.now(timezone.utc) - timedelta(seconds=5))
    with store._connect() as conn:
        conn.execute(
            "UPDATE actions SET expires_at = ? WHERE action_id = ?",
            (expired_at, pending.action_id),
        )
    with pytest.raises(ActionError, match="action expired"):
        service.execute("vector", pending.action_id)


def test_propose_persists_role(tmp_path):
    from office_gateway.store import GatewayStore

    store = GatewayStore(str(tmp_path / "gateway.db"))
    pending = store.propose("restart_service", "milvus-standalone", role="vector")
    row = store.get_action(pending.action_id)
    assert row is not None
    assert row.role == "vector"


def test_execute_wrong_role_returns_forbidden_without_claiming(tmp_path):
    from office_gateway.store import GatewayStore

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    service = ActionService(config, GatewayStore(config.db_path, config.action_ttl_seconds))
    pending = service.propose("vector", "restart_service", "milvus-standalone")
    with pytest.raises(ActionError, match="forbidden"):
        service.execute("lab-host", pending.action_id)
    row = service.store.get_action(pending.action_id)
    assert row is not None
    assert row.status == "pending"


def test_execute_wrong_role_api_returns_403_without_claiming(client):
    propose = client.post(
        "/v1/actions/propose",
        headers={"Authorization": "Bearer tok-vec"},
        json={"action": "restart_service", "target": "milvus-standalone"},
    )
    assert propose.status_code == 200
    action_id = propose.json()["action_id"]
    response = client.post(
        "/v1/actions/execute",
        headers={"Authorization": "Bearer tok-lab"},
        json={"action_id": action_id},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "forbidden"


def test_validate_execute_rejects_role_mismatch(tmp_path):
    from office_gateway.store import GatewayStore

    config = replace(_test_config(), db_path=str(tmp_path / "gateway.db"))
    service = ActionService(config, GatewayStore(config.db_path, config.action_ttl_seconds))
    pending = service.propose("lab-host", "restart_service", "aiplatform-dashboard")
    with pytest.raises(ActionError, match="forbidden"):
        service.execute("vector", pending.action_id)
    row = service.store.get_action(pending.action_id)
    assert row is not None
    assert row.status == "pending"


def test_office_gateway_self_restart_is_audited_before_restart(tmp_path):
    from office_gateway.store import GatewayStore

    events: list[str] = []

    class RecordingStore(GatewayStore):
        def mark_executed(self, action_id, ok, detail):
            events.append("mark_executed")
            return super().mark_executed(action_id, ok, detail)

    class RecordingDockerOps:
        def restart(self, target):
            events.append(f"restart:{target}")

    target = "hermes-assistant-office-gateway-1"
    config = replace(
        _test_config(),
        db_path=str(tmp_path / "gateway.db"),
        write_targets={"lab-host": frozenset({target})},
    )
    store = RecordingStore(config.db_path, config.action_ttl_seconds)
    service = ActionService(config, store, docker_ops=RecordingDockerOps())
    pending = service.propose("lab-host", "restart_service", target)

    result = service.execute("lab-host", pending.action_id)

    assert result.status == "executed"
    assert events == ["mark_executed", f"restart:{target}"]


def test_store_recovers_stale_executing_actions_on_init(tmp_path):
    from datetime import datetime, timedelta, timezone

    from office_gateway.store import GatewayStore, _iso_z

    db_path = str(tmp_path / "gateway.db")
    store = GatewayStore(db_path, action_ttl_seconds=60)
    pending = store.propose("restart_service", "milvus-standalone", role="vector")
    assert store.claim_pending(pending.action_id) is not None
    stale = _iso_z(datetime.now(timezone.utc) - timedelta(seconds=120))
    with store._connect() as conn:
        conn.execute(
            """
            UPDATE actions
            SET created_at = ?, expires_at = ?, executing_at = ?
            WHERE action_id = ?
            """,
            (stale, stale, stale, pending.action_id),
        )

    recovered = GatewayStore(db_path, action_ttl_seconds=60)

    row = recovered.get_action(pending.action_id)
    assert row is not None
    assert row.status == "failed"
    audit = recovered.list_audit(limit=10)
    assert any(
        entry["action_id"] == pending.action_id
        and entry["event"] == "failed"
        and entry["detail"] == "failed:adapter_unavailable"
        for entry in audit
    )
