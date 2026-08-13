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

    assert "ship-to-vm.sh" in text
    assert "/home/timai/hermes-assistant" in text
    assert "10.216.4.80" in text
    assert "docker compose --env-file .env -f docker-compose.yml" in text
    # Legacy commands remain documented for existing deploys.
    assert "docker compose --env-file deploy/cloud.env -f docker-compose.cloud.yml" in text
    assert "docker compose --env-file deploy/observer.env -f docker-compose.observer.yml" in text
    assert "docker compose --env-file deploy/relay.env -f docker-compose.relay.yml" in text


def test_office_assistant_compose_keeps_gateway_internal():
    text = Path("deploy/office-assistant/docker-compose.yml").read_text()
    assert "office-gateway:" in text
    assert "hermes-agent:" in text
    assert "expose:" in text
    # Gateway must not publish a host port by default.
    gateway_block = text.split("office-gateway:")[1].split("hermes-agent:")[0]
    assert "ports:" not in gateway_block
    assert Path("deploy/office-assistant/.env.example").exists()
    assert Path("deploy/office-assistant/skills/office-platform/SKILL.md").exists()


def test_office_assistant_compose_is_air_gapped_friendly():
    text = Path("deploy/office-assistant/docker-compose.yml").read_text()
    assert "pull_policy: never" in text
    assert "build:" not in text
    assert "OFFICE_GATEWAY_IMAGE" in text
    assert "platform: ${OFFICE_IMAGE_PLATFORM:-linux/amd64}" in text
    assert Path("deploy/office-assistant/scripts/export-images.sh").exists()
    export_sh = Path("deploy/office-assistant/scripts/export-images.sh").read_text()
    assert 'PLATFORM="${OFFICE_IMAGE_PLATFORM:-linux/amd64}"' in export_sh
    assert "buildx build" in export_sh
    assert "--platform" in export_sh
    assert "--load" in export_sh
    assert Path("deploy/office-assistant/scripts/load-images.sh").exists()
    assert Path("deploy/office-assistant/scripts/start.sh").exists()
    assert Path("deploy/office-assistant/scripts/load-and-start.sh").exists()
    start_sh = Path("deploy/office-assistant/scripts/start.sh").read_text()
    assert "docker compose --env-file .env -f docker-compose.yml" in start_sh
    assert "up -d --force-recreate" in start_sh
    assert Path("deploy/office-assistant/scripts/ship-to-vm.sh").exists()
    compose = Path("deploy/office-assistant/docker-compose.yml").read_text()
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in compose
    assert "/proc:/host/proc:ro" in compose
    assert "OFFICE_HOST_ENABLED" in compose
    env = Path("deploy/office-assistant/.env.example").read_text()
    assert "OFFICE_HOST_ENABLED=1" in env
    skill = Path("deploy/office-assistant/skills/office-platform/SKILL.md").read_text()
    assert "/v1/host" in skill
    assert Path("deploy/office-assistant/scripts/build-and-ship.sh").exists()
    ship_sh = Path("deploy/office-assistant/scripts/ship-to-vm.sh").read_text()
    assert "--scripts-only" in ship_sh
    build_ship = Path("deploy/office-assistant/scripts/build-and-ship.sh").read_text()
    assert "export-images.sh" in build_ship
    assert "ship-to-vm.sh" in build_ship
    assert "host.docker.internal:host-gateway" in text
    env = Path("deploy/office-assistant/.env.example").read_text()
    assert "OFFICE_GATEWAY_IMAGE=" in env
    assert "OFFICE_VM_HOST=10.216.4.80" in env
    assert "OFFICE_VM_DIR=/home/timai/hermes-assistant" in env
    assert "HERMES_DASHBOARD_PUBLISH=10.216.4.80:9119" in env
    assert "agent-inference=http://host.docker.internal:8010/health" in env
    assert "LITELLM_BASE_URL=http://10.216.221.100/llm/v1" in env
    assert "litellm=http://10.216.221.100/llm/v1/models" in env
    assert "LITELLM_MODEL=qwen3.5-fast" in env
    assert "HERMES_DASHBOARD_BASIC_AUTH_USERNAME=" in env
    hermes_env = Path("deploy/office-assistant/hermes/.env.example").read_text()
    assert "LITELLM_MODEL=qwen3.5-fast" in hermes_env
    assert "LITELLM_BASE_URL=http://10.216.221.100/llm/v1" in hermes_env
    assert "OFFICE_GATEWAY_URL=http://office-gateway:8080" in hermes_env
    assert "HERMES_DASHBOARD_USERNAME=" not in hermes_env
    assert "qdrant=http://host.docker.internal:6333/readyz" in env
    assert ":8010" in env
    hermes_cfg = Path("deploy/office-assistant/hermes/config.yaml").read_text()
    assert "${LITELLM_BASE_URL}" in hermes_cfg
    assert "${LITELLM_MODEL}" in hermes_cfg
    assert "provider: custom" in hermes_cfg
    compose = Path("deploy/office-assistant/docker-compose.yml").read_text()
    assert "LITELLM_MODEL:" in compose
    assert "LITELLM_BASE_URL:" in compose
    readme = Path("deploy/office-assistant/README.md").read_text()
    assert "export-images.sh" in readme
    assert "load-images.sh" in readme
    assert "ship-to-vm.sh" in readme
    assert "10.216.4.80" in readme
    assert "/home/timai/hermes-assistant" in readme
    assert "air-gapped" in readme.lower() or "no internet" in readme.lower()
    compose = Path("deploy/office-assistant/docker-compose.yml").read_text()
    assert "10.216.4.80:9119" in compose
    assert 'command: ["gateway", "run"]' in compose
    assert 'HERMES_DASHBOARD: "1"' in compose
    assert "HERMES_DASHBOARD_BASIC_AUTH_USERNAME" in compose


def test_office_assistant_dashboard_proxy_publishes_http_80():
    compose = Path("deploy/office-assistant/docker-compose.yml").read_text()
    assert "dashboard-proxy:" in compose
    assert "DASHBOARD_PROXY_IMAGE" in compose
    assert "HERMES_DASHBOARD_HTTP_PUBLISH" in compose
    assert "10.216.4.80:80" in compose
    assert "pull_policy: never" in compose.split("dashboard-proxy:")[1]
    nginx_conf = Path("deploy/office-assistant/nginx/dashboard-proxy.conf").read_text()
    assert "listen 80" in nginx_conf
    assert "hermes-agent:9119" in nginx_conf
    assert "Upgrade" in nginx_conf
    env = Path("deploy/office-assistant/.env.example").read_text()
    assert "DASHBOARD_PROXY_IMAGE=" in env
    assert "HERMES_DASHBOARD_HTTP_PUBLISH=10.216.4.80:80" in env
    export_sh = Path("deploy/office-assistant/scripts/export-images.sh").read_text()
    assert "DASHBOARD_PROXY_IMAGE" in export_sh
    load_sh = Path("deploy/office-assistant/scripts/load-images.sh").read_text()
    assert "DASHBOARD_PROXY_IMAGE" in load_sh


def test_export_images_script_resolves_repo_dockerfile(tmp_path):
    import subprocess

    script = Path("deploy/office-assistant/scripts/export-images.sh")
    result = subprocess.run(
        [
            "bash",
            "-c",
            r"""
            SCRIPT_DIR="$(cd "$(dirname "$1")" && pwd)"
            DEPLOY="$(cd "$SCRIPT_DIR/.." && pwd)"
            ROOT="$(cd "$DEPLOY/../.." && pwd)"
            echo "$ROOT/Dockerfile.office-gateway"
            """,
            "_",
            str(script.resolve()),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    dockerfile = Path(result.stdout.strip())
    assert dockerfile.exists()
    assert dockerfile.name == "Dockerfile.office-gateway"
