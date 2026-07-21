from pathlib import Path


def test_observer_compose_has_no_inbound_port_and_persists_its_spool():
    text = Path("docker-compose.observer.yml").read_text()

    assert "ports:" not in text
    assert "/var/lib/hermes-observer" in text
    assert "read_only: true" in text


def test_relay_compose_exposes_only_the_office_lan_listener():
    text = Path("docker-compose.relay.yml").read_text()

    assert "OFFICE_RELAY_BIND_ADDRESS" in text
    assert "8443" in text
    assert "OFFICE_RELAY_CLIENT_CA_FILE" in Path("deploy/relay.env.example").read_text()


def test_documented_compose_commands_load_their_role_environment_file():
    text = Path("README.md").read_text()

    assert "docker compose --env-file deploy/cloud.env -f docker-compose.cloud.yml" in text
    assert "docker compose --env-file deploy/observer.env -f docker-compose.observer.yml" in text
    assert "docker compose --env-file deploy/relay.env -f docker-compose.relay.yml" in text
