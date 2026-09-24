import pytest

from gw_helpers import TOKENS, make_config
from office_gateway.config import GatewayConfig
from office_gateway.roles import (
    AGENT_ROLES,
    ROLES,
    WRITE_ACTIONS,
    can_propose,
    can_read,
    can_write,
    role_for_token,
)

ENV = {
    "OFFICE_GATEWAY_TOKEN_SUPERVISOR": "t-sup",
    "OFFICE_GATEWAY_TOKEN_LAB_HOST": "t-lab",
    "OFFICE_GATEWAY_TOKEN_INGRESS": "t-ing",
    "OFFICE_GATEWAY_TOKEN_LLM": "t-llm",
    "OFFICE_GATEWAY_TOKEN_CLUSTER": "t-gpu",
    "OFFICE_GATEWAY_TOKEN_VECTOR": "t-vec",
    "OFFICE_GATEWAY_TOKEN_OBS": "t-obs",
    "OFFICE_GATEWAY_TOKEN_APPROVER": "t-app",
}


def _env(monkeypatch, tmp_path, **extra):
    for key, value in {**ENV, **extra}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OFFICE_GATEWAY_DB_PATH", str(tmp_path / "gw.db"))


def test_roles_are_renamed_and_approver_is_not_an_agent():
    assert {"ingress", "llm", "approver"} <= ROLES
    assert not {"edge", "llm-edge"} & ROLES
    assert "approver" not in AGENT_ROLES
    assert len(AGENT_ROLES) == 7


def test_write_actions_per_role():
    assert can_propose("lab-host", "restart_service")
    assert can_propose("ingress", "apply_edge_routes")
    assert can_propose("ingress", "rollback_edge_routes")
    assert not can_propose("ingress", "restart_service")
    for role in ("vector", "supervisor", "approver", "llm", "cluster-gpu"):
        assert not can_propose(role, "restart_service")
        assert not can_write(role)
    assert WRITE_ACTIONS["obs"] == frozenset()


def test_supervisor_reads_only_status_and_fleet():
    assert can_read("supervisor", "/v1/fleet")
    assert can_read("supervisor", "/v1/status")
    for prefix in ("/v1/litellm", "/v1/metrics/query", "/v1/watch", "/v1/audit", "/v1/docker"):
        assert not can_read("supervisor", prefix)


def test_ingress_reads_logs_but_not_other_docker_routes():
    assert can_read("ingress", "/v1/docker/logs")
    assert not can_read("ingress", "/v1/docker")
    assert can_read("lab-host", "/v1/docker/logs")


def test_approver_reads_only_approvals_audit_status():
    assert can_read("approver", "/v1/approvals")
    assert can_read("approver", "/v1/audit")
    assert not can_read("approver", "/v1/docker")


def test_role_for_token(tmp_path):
    cfg = make_config(tmp_path)
    assert role_for_token(cfg, TOKENS["ingress"]) == "ingress"
    assert role_for_token(cfg, TOKENS["approver"]) == "approver"
    assert role_for_token(cfg, "nope") is None
    assert role_for_token(cfg, "") is None


def test_from_env_reads_renamed_tokens(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    cfg = GatewayConfig.from_env()
    assert cfg.tokens["ingress"] == "t-ing"
    assert cfg.tokens["llm"] == "t-llm"
    assert cfg.tokens["approver"] == "t-app"
    assert set(cfg.tokens) == ROLES


def test_from_env_requires_approver_token(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, OFFICE_GATEWAY_TOKEN_APPROVER="")
    with pytest.raises(ValueError, match="OFFICE_GATEWAY_TOKEN_APPROVER"):
        GatewayConfig.from_env()


def test_from_env_rejects_duplicate_tokens(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, OFFICE_GATEWAY_TOKEN_OBS="t-sup")
    with pytest.raises(ValueError, match="distinct"):
        GatewayConfig.from_env()


def test_default_service_urls_use_api_server_health(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, OFFICE_SERVICE_URLS="")
    cfg = GatewayConfig.from_env()
    assert cfg.service_urls["gateway"] == "http://office-gateway:8080/health"
    for name in (
        "hermes-agent",
        "hermes-lab-host",
        "hermes-ingress",
        "hermes-llm",
        "hermes-cluster-gpu",
        "hermes-vector",
        "hermes-obs",
    ):
        assert cfg.service_urls[name] == f"http://{name}:8642/health"
    assert not any(url.endswith("agent.json") for url in cfg.service_urls.values())


def test_vector_instances_default_and_override(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    cfg = GatewayConfig.from_env()
    assert set(cfg.vector_instances) == {"milvus-dev", "milvus-prod", "qdrant-dev"}
    _env(monkeypatch, tmp_path, OFFICE_VECTOR_INSTANCES="milvus-dev=http://m:9091/healthz")
    cfg = GatewayConfig.from_env()
    assert cfg.vector_instances == {"milvus-dev": "http://m:9091/healthz"}


def test_console_url_and_backup_dir(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, OFFICE_CONSOLE_URL="https://10.216.4.80/", OFFICE_EDGE_BACKUP_DIR="/edge/backups")
    cfg = GatewayConfig.from_env()
    assert cfg.console_url == "https://10.216.4.80"
    assert cfg.edge_backup_dir == "/edge/backups"
