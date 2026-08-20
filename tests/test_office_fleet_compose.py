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
