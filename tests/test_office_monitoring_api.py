from fastapi.testclient import TestClient

from core import main
from core.db import Database
from core.office_monitor import OfficeMonitoringService, sign_snapshot


def _snapshot() -> dict:
    return {
        "observer_id": "office-observer-1",
        "sequence": 1,
        "observed_at": "2026-07-20T00:00:00+00:00",
        "services": [{"name": "openwebui", "status": "ok", "latency_ms": 20}],
        "metrics": {},
    }


def test_office_ingestion_requires_matching_identity_signature_and_mtls_subject(tmp_path, monkeypatch):
    snapshot = _snapshot()
    main.app.state.office_monitoring = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    monkeypatch.setattr(main.settings, "office_observer_id", "office-observer-1", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_shared_secret", "test-secret", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_mtls_subject", "CN=office-relay", raising=False)
    client = TestClient(main.app)

    denied = client.post("/api/v1/office/snapshots", json=snapshot)
    assert denied.status_code == 401

    accepted = client.post(
        "/api/v1/office/snapshots",
        json=snapshot,
        headers={
            "X-Hermes-Observer": "office-observer-1",
            "X-Hermes-Signature": sign_snapshot(snapshot, "test-secret"),
            "X-Hermes-Observer-Subject": "CN=office-relay",
        },
    )

    assert accepted.status_code == 202
    assert accepted.json() == {"accepted": True}


def test_office_ingestion_rejects_a_relay_with_wrong_mtls_subject(tmp_path, monkeypatch):
    snapshot = _snapshot()
    main.app.state.office_monitoring = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    monkeypatch.setattr(main.settings, "office_observer_id", "office-observer-1", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_shared_secret", "test-secret", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_mtls_subject", "CN=office-relay", raising=False)
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/office/snapshots",
        json=snapshot,
        headers={
            "X-Hermes-Observer": "office-observer-1",
            "X-Hermes-Signature": sign_snapshot(snapshot, "test-secret"),
            "X-Hermes-Observer-Subject": "CN=untrusted",
        },
    )

    assert response.status_code == 401


def test_office_ingestion_rejects_a_signed_snapshot_for_another_observer(tmp_path, monkeypatch):
    snapshot = _snapshot()
    snapshot["observer_id"] = "other-observer"
    main.app.state.office_monitoring = OfficeMonitoringService(Database(str(tmp_path / "hermes.db")))
    monkeypatch.setattr(main.settings, "office_observer_id", "office-observer-1", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_shared_secret", "test-secret", raising=False)
    monkeypatch.setattr(main.settings, "office_observer_mtls_subject", "CN=office-relay", raising=False)
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/office/snapshots",
        json=snapshot,
        headers={
            "X-Hermes-Observer": "office-observer-1",
            "X-Hermes-Signature": sign_snapshot(snapshot, "test-secret"),
            "X-Hermes-Observer-Subject": "CN=office-relay",
        },
    )

    assert response.status_code == 401
