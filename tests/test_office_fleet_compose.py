from pathlib import Path

COMPOSE = Path("deploy/office-assistant/docker-compose.yml")


def test_compose_has_supervisor_five_specialists_and_gateway():
    text = COMPOSE.read_text()
    for name in (
        "office-gateway",
        "hermes-agent",
        "hermes-lab-host",
        "hermes-vector",
        "hermes-cluster-gpu",
        "hermes-llm-edge",
        "hermes-obs",
    ):
        assert f"{name}:" in text
    gateway = text.split("office-gateway:")[1].split("hermes-agent:")[0]
    assert "ports:" not in gateway
    assert "/var/run/docker.sock" in gateway
    for spec in ("hermes-lab-host", "hermes-vector", "hermes-cluster-gpu", "hermes-llm-edge", "hermes-obs"):
        start = text.index(f"{spec}:")
        rest = text[start:]
        nxt = rest.find("\n  hermes-") if spec != "hermes-obs" else rest.find("\nvolumes:")
        if nxt == -1:
            nxt = rest.find("\nvolumes:")
        block = rest[:nxt]
        assert "ports:" not in block
    assert "9119" in text


def test_compose_gateway_volume_init_and_docker_group():
    text = COMPOSE.read_text()
    init = text.split("office-gateway-init:")[1].split("office-gateway:")[0]
    assert "busybox" in init
    assert "chown" in init
    assert "10001:10001" in init
    assert "office_gateway_data" in init

    gateway = text.split("office-gateway:")[1].split("hermes-agent:")[0]
    assert "group_add" in gateway
    assert "${DOCKER_GID:-998}" in gateway
    assert "office-gateway-init" in gateway
    assert "service_completed_successfully" in gateway
    assert "/var/run/docker.sock" in gateway

    for name in (
        "hermes-agent",
        "hermes-lab-host",
        "hermes-vector",
        "hermes-cluster-gpu",
        "hermes-llm-edge",
        "hermes-obs",
    ):
        start = text.index(f"{name}:")
        rest = text[start:]
        if name == "hermes-obs":
            nxt = rest.find("\nvolumes:")
        else:
            nxt = rest.find("\n  hermes-", len(name) + 2)
        block = rest[:nxt] if nxt != -1 else rest
        assert "/var/run/docker.sock" not in block, f"{name} must not mount docker.sock"


ROOT = Path("deploy/office-assistant/skills")


def test_supervisor_skill_forbids_direct_platform_calls_and_uses_a2a():
    text = (ROOT / "supervisor" / "SKILL.md").read_text()
    assert "a2a_call" in text
    assert "APPROVE" in text
    assert "delegate_task" in text and "do not" in text.lower()
    assert "office-gateway write" not in text.lower() or "never call write APIs" in text.lower()
    assert "never call write" in text.lower() or "specialist" in text.lower()


def test_vector_skill_prod_is_read_only():
    text = (ROOT / "vector" / "SKILL.md").read_text()
    assert "milvus-standalone" in text
    assert "attu" in text
    assert "prod" in text.lower()
    assert "do not" in text.lower() or "must not" in text.lower()


def test_cluster_skill_requires_mig_map():
    text = (ROOT / "cluster-gpu" / "SKILL.md").read_text()
    assert "/v1/gpu/mig" in text
    assert "Grafana" in text or "grafana" in text


def test_lab_host_warns_self_restart():
    text = (ROOT / "lab-host" / "SKILL.md").read_text()
    assert "session" in text.lower()
    assert "APPROVE" in text


def test_nightly_fleet_check_instructions_exist():
    text = Path("deploy/office-assistant/hermes/supervisor/cron.fleet-check.md").read_text()
    assert "nightly fleet check" in text
    assert "MIG" in text
    assert "Grafana" in text
    assert "block" in text.lower()


def test_all_skills_forbid_delegate_task():
    ban_phrase = "do not use `delegate_task`"
    for role in (
        "supervisor",
        "lab-host",
        "vector",
        "cluster-gpu",
        "llm-edge",
        "obs",
    ):
        skill_md = ROOT / role / "SKILL.md"
        assert skill_md.is_file(), f"missing {skill_md}"
        normalized = skill_md.read_text().lower().replace("*", "")
        assert ban_phrase in normalized, f"{skill_md} must forbid delegate_task"
