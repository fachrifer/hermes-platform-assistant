import pytest

from office_observer.config import ObserverConfig


def test_observer_config_parses_named_health_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("OFFICE_OBSERVER_ID", "office-observer-1")
    monkeypatch.setenv("OFFICE_OBSERVER_SHARED_SECRET", "test-secret")
    monkeypatch.setenv("OFFICE_RELAY_URL", "https://laptop.office.local:8443")
    monkeypatch.setenv("OFFICE_OBSERVER_SPOOL_PATH", str(tmp_path / "observer.db"))
    monkeypatch.setenv("OFFICE_RELAY_CA_FILE", "/certs/relay-ca.pem")
    monkeypatch.setenv("OFFICE_RELAY_CLIENT_CERT", "/certs/observer.pem")
    monkeypatch.setenv("OFFICE_RELAY_CLIENT_KEY", "/certs/observer-key.pem")
    monkeypatch.setenv(
        "OFFICE_SERVICE_URLS",
        "openwebui=https://openwebui.office/health,grafana=https://grafana.office/api/health",
    )

    config = ObserverConfig.from_env()

    assert config.observer_id == "office-observer-1"
    assert config.service_urls == {
        "openwebui": "https://openwebui.office/health",
        "grafana": "https://grafana.office/api/health",
    }


def test_observer_config_rejects_invalid_service_names(monkeypatch):
    monkeypatch.setenv("OFFICE_OBSERVER_ID", "office-observer-1")
    monkeypatch.setenv("OFFICE_OBSERVER_SHARED_SECRET", "test-secret")
    monkeypatch.setenv("OFFICE_RELAY_URL", "https://laptop.office.local:8443")
    monkeypatch.setenv("OFFICE_SERVICE_URLS", "Open WebUI=https://openwebui.office/health")

    with pytest.raises(ValueError, match="service"):
        ObserverConfig.from_env()
