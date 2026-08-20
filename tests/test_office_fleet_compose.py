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
