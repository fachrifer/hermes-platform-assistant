import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

DEPLOY_ROOT = Path(__file__).resolve().parents[1] / "deploy" / "office-assistant"
COMPOSE = DEPLOY_ROOT / "docker-compose.yml"
AGENTS = {
    "hermes-agent": "supervisor",
    "hermes-lab-host": "lab-host",
    "hermes-ingress": "ingress",
    "hermes-llm": "llm",
    "hermes-cluster-gpu": "cluster-gpu",
    "hermes-vector": "vector",
    "hermes-obs": "obs",
}
PORTS = {
    "hermes-lab-host": "9121",
    "hermes-vector": "9122",
    "hermes-cluster-gpu": "9123",
    "hermes-llm": "9124",
    "hermes-obs": "9125",
    "hermes-ingress": "9126",
}
RETIRED = ("office-gw", "office-watch", "s6-", "airgap", "hermes-office-llm", "apply-office-llm", "cron", "kanban")
VOLUMES = {
    "office_gateway_data",
    "hermes_supervisor_v2",
    "hermes_lab_host_v2",
    "hermes_ingress_v2",
    "hermes_llm_v2",
    "hermes_cluster_gpu_v2",
    "hermes_vector_v2",
    "hermes_obs_v2",
}


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_services_and_volumes(compose):
    services = set(compose["services"])
    assert set(AGENTS) <= services
    assert {"office-gateway", "office-gateway-init", "office-www", "office-edge"} <= services
    assert not {"hermes-edge", "hermes-llm-edge"} & services
    assert set(compose["volumes"]) == VOLUMES


@pytest.mark.parametrize("service,role", sorted(AGENTS.items()))
def test_agent_service_shape(compose, service, role):
    svc = compose["services"][service]
    assert svc["image"] == "${HERMES_IMAGE:-nousresearch/hermes-agent:v2026.9.21}"
    assert svc["command"] == ["gateway", "run"]
    assert svc["env_file"] == ["./models.env", f"./hermes/{role}/.env"]
    volumes = svc["volumes"]
    assert f"./hermes/{role}/config.yaml:/opt/data/config.yaml:ro" in volumes
    assert f"./skills/{role}:/opt/data/skills/office-{role}:ro" in volumes
    assert "./scripts/hermes-bot-mode-marker-cont-init.sh:/etc/cont-init.d/30-bot-mode-marker:ro" in volumes
    assert any(v.endswith("_v2:/opt/data") for v in volumes)
    for volume in volumes:
        assert not any(word in volume for word in RETIRED), volume
    env = svc["environment"]
    assert not any(key.startswith("A2A_") for key in env)
    assert "HERMES_OFFLINE" not in env and "OFFICE_GATEWAY_URL" not in env
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:9"
    no_proxy = env["NO_PROXY"].split(",")
    assert set(AGENTS) | {"office-gateway", "10.216.221.100"} <= set(no_proxy)
    assert not any("/" in entry for entry in no_proxy)
    assert env["no_proxy"] == env["NO_PROXY"]
    assert env["API_SERVER_PORT"] == "8642"


def test_specialist_ports(compose):
    for service, port in PORTS.items():
        ports = compose["services"][service]["ports"]
        assert ports == [f"${{HERMES_BOT_BIND:-10.216.4.80}}:{port}:9119"], service


def test_supervisor_waits_for_specialists(compose):
    depends = set(compose["services"]["hermes-agent"]["depends_on"])
    assert depends == {"office-gateway"} | (set(AGENTS) - {"hermes-agent"})


def test_gateway_and_edge(compose):
    gateway = compose["services"]["office-gateway"]
    assert gateway["environment"]["OFFICE_EDGE_BACKUP_DIR"] == "/edge/backups"
    assert gateway["environment"]["OFFICE_CONSOLE_URL"].startswith("https://")
    assert any(v.endswith(":/run/dbus/system_bus_socket:ro") for v in gateway["volumes"])
    edge = compose["services"]["office-edge"]
    assert "./edge/approvers.htpasswd:/etc/traefik/approvers.htpasswd:ro" in edge["volumes"]
    assert set(AGENTS) <= set(edge["depends_on"])


def test_no_agent_mounts_gateway_approver_token():
    for role in AGENTS.values():
        example = DEPLOY_ROOT / "hermes" / role / ".env.example"
        assert "OFFICE_GATEWAY_TOKEN_APPROVER" not in example.read_text(encoding="utf-8")


def test_docker_compose_config_parses(tmp_path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker CLI is not installed")
    deploy = tmp_path / "office-assistant"
    shutil.copytree(DEPLOY_ROOT, deploy, ignore=shutil.ignore_patterns(".env", "*.env", "images", "spike"))
    shutil.copyfile(deploy / ".env.example", deploy / ".env")
    shutil.copyfile(deploy / "models.env.example", deploy / "models.env")
    for role in AGENTS.values():
        shutil.copyfile(deploy / "hermes" / role / ".env.example", deploy / "hermes" / role / ".env")
    result = subprocess.run([docker, "compose", "config", "-q"], cwd=deploy, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
