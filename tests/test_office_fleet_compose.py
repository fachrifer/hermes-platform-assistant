import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

COMPOSE = Path("deploy/office-assistant/docker-compose.yml")
DEPLOY_ROOT = COMPOSE.parent
HERMES_ROLES = (
    "supervisor",
    "lab-host",
    "vector",
    "cluster-gpu",
    "llm-edge",
    "obs",
)
HERMES_SERVICES = {
    "hermes-agent": "supervisor",
    "hermes-lab-host": "lab-host",
    "hermes-vector": "vector",
    "hermes-cluster-gpu": "cluster-gpu",
    "hermes-llm-edge": "llm-edge",
    "hermes-obs": "obs",
}


def _service_block(text: str, name: str) -> str:
    start = text.index(f"  {name}:")
    ends = [
        text.find(f"\n  {other}:", start + 1)
        for other in (
            "office-gateway-init",
            "office-gateway",
            "office-console",
            *HERMES_SERVICES,
        )
        if other != name
    ]
    valid_ends = [end for end in ends if end != -1]
    return text[start : min(valid_ends) if valid_ends else len(text)]


def _env_keys(path: Path) -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in path.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }


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
        nxt = rest.find("\n  hermes-") if spec != "hermes-obs" else rest.find("\n  office-console:")
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
            nxt = rest.find("\n  office-console:")
        else:
            nxt = rest.find("\n  hermes-", len(name) + 2)
        block = rest[:nxt] if nxt != -1 else rest
        assert "/var/run/docker.sock" not in block, f"{name} must not mount docker.sock"


def test_hermes_services_use_role_isolated_env_files():
    text = COMPOSE.read_text()
    gateway = _service_block(text, "office-gateway")
    assert "\n      - .env\n" in gateway

    for service, role in HERMES_SERVICES.items():
        block = _service_block(text, service)
        assert "- ./models.env" in block
        assert f"- ./hermes/{role}/.env" in block
        assert block.index(f"- ./hermes/{role}/.env") < block.index("- ./models.env")
        assert "\n      - .env\n" not in block
        assert "OFFICE_GATEWAY_TOKEN_" not in block
        assert "A2A_TOKEN_" not in block
        for other_role in HERMES_ROLES:
            if other_role != role:
                assert f"./hermes/{other_role}/.env" not in block


def test_role_env_examples_contain_only_role_credentials():
    specialist_keys = {
        "OFFICE_GATEWAY_TOKEN",
        "A2A_BEARER_TOKEN",
        "A2A_HOST",
        "A2A_PORT",
        "A2A_AGENT_NAME",
        "A2A_PUBLIC_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
    }
    for role in HERMES_ROLES[1:]:
        path = DEPLOY_ROOT / "hermes" / role / ".env.example"
        assert path.is_file()
        assert _env_keys(path) == specialist_keys

    supervisor = _env_keys(
        DEPLOY_ROOT / "hermes" / "supervisor" / ".env.example"
    )
    assert supervisor == {
        "OFFICE_GATEWAY_TOKEN",
        "A2A_TOKEN_LAB_HOST",
        "A2A_TOKEN_VECTOR",
        "A2A_TOKEN_CLUSTER_GPU",
        "A2A_TOKEN_LLM_EDGE",
        "A2A_TOKEN_OBS",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "HERMES_DASHBOARD_USERNAME",
        "HERMES_DASHBOARD_PASSWORD",
    }


def test_specialist_compose_stamps_llm_config_at_boot():
    compose = COMPOSE.read_text()
    supervisor = _service_block(compose, "hermes-agent")
    assert "./hermes/supervisor/config.yaml:/opt/data/config.yaml:ro" in supervisor
    assert "10-office-llm" not in supervisor
    assert "hermes-config.template.yaml" not in supervisor

    for service, role in HERMES_SERVICES.items():
        if service == "hermes-agent":
            continue
        block = _service_block(compose, service)
        assert (
            f"./hermes/{role}/config.yaml:/opt/data/config.yaml:ro" not in block
        )
        assert (
            f"./hermes/{role}/config.yaml:/opt/hermes-config.template.yaml:ro"
            in block
        )
        assert (
            "./scripts/apply-office-llm-config.py:"
            "/opt/office/apply-office-llm-config.py:ro" in block
        )
        assert (
            "./scripts/hermes-office-llm-cont-init.sh:"
            "/etc/cont-init.d/10-office-llm:ro" in block
        )


def test_hermes_v2026_8_a2a_model_and_dashboard_config():
    for role in HERMES_ROLES:
        text = (DEPLOY_ROOT / "hermes" / role / "config.yaml").read_text()
        assert "model:" in text
        assert "providers:" in text
        assert "office-litellm:" in text
        assert "provider: office-litellm" in text
        assert "base_url: ${OPENAI_BASE_URL}" in text
        assert "api_key: ${OPENAI_API_KEY}" in text
        assert "key_env: OPENAI_API_KEY" in text
        assert "api_mode: chat_completions" in text
        assert "default: ${OPENAI_MODEL}" in text
        assert ":-gpt-4o" not in text
        assert "OPENAI_API_BASE" not in text

    for role in HERMES_ROLES[1:]:
        text = (DEPLOY_ROOT / "hermes" / role / "config.yaml").read_text()
        assert "gateway:" in text
        assert "platforms:" in text
        assert "a2a:" in text
        assert "enabled: true" in text
        assert "extra:" in text
        assert "port: 9900" in text
        assert "bearer_token" not in text
        assert 'backend: "off"' in text
        assert "mcp_servers: {}" in text
        assert "request_timeout_seconds: 60" in text
        assert "build_wait_timeout: 90" in text
        assert "    - hermes-cli" not in text
        assert "platform_toolsets:" in text

    supervisor = (
        DEPLOY_ROOT / "hermes" / "supervisor" / "config.yaml"
    ).read_text()
    assert supervisor.count("auth: { type: bearer, token:") == 5
    assert "platform_toolsets:" in supervisor
    assert "- a2a" in supervisor
    assert "    - hermes-cli" not in supervisor
    assert 'backend: "off"' in supervisor
    assert "mcp_servers: {}" in supervisor
    assert "request_timeout_seconds: 60" in supervisor
    assert "build_wait_timeout: 90" in supervisor
    assert supervisor.count("timeout: 45") == 5
    assert "basic_auth:" in supervisor
    assert "username: ${HERMES_DASHBOARD_USERNAME}" in supervisor
    assert "password: ${HERMES_DASHBOARD_PASSWORD}" in supervisor
    assert "bearer_token" not in supervisor


def test_compose_mounts_kubeconfig_and_pins_hermes_image():
    compose = COMPOSE.read_text()
    init = _service_block(compose, "office-gateway-init")
    assert "${OFFICE_KUBECONFIG:-./kubeconfig.absent}:/etc/office/kubeconfig:ro" in init
    assert "-s /etc/office/kubeconfig" in init
    assert "cp /etc/office/kubeconfig /var/lib/hermes-office-gateway/kubeconfig" in init
    assert "chmod 0640 /var/lib/hermes-office-gateway/kubeconfig" in init
    assert (DEPLOY_ROOT / "kubeconfig.absent").is_file()
    assert (DEPLOY_ROOT / "kubeconfig.absent").stat().st_size == 0

    gateway = _service_block(compose, "office-gateway")
    assert "KUBECONFIG: /var/lib/hermes-office-gateway/kubeconfig" in gateway
    assert "${OFFICE_KUBECONFIG}" not in gateway

    root_env = (DEPLOY_ROOT / ".env.example").read_text()
    image_line = next(
        line for line in root_env.splitlines() if line.startswith("HERMES_IMAGE=")
    )
    assert ":latest" not in image_line
    assert "v2026.8." in image_line
    assert "WARNING" in root_env
    assert 'HERMES_API_TIMEOUT: "60"' in compose
    assert 'A2A_REPLY_TIMEOUT: "90"' in compose


def test_console_shows_athena_and_specialists():
    html = (DEPLOY_ROOT / "console" / "www" / "index.html").read_text()
    js = (DEPLOY_ROOT / "console" / "www" / "app.js").read_text()
    assert "Athena" in html
    assert "Agent Specialist Fleet" in html
    assert "Athena - Orchestrator" in html
    assert "specialists" in html
    assert "athena-vtuber-idle.png" in html
    assert "athena-mouth" in html
    assert "athena-eyes" in html
    assert 'id="bubble"' in html
    css = (DEPLOY_ROOT / "console" / "www" / "styles.css").read_text()
    assert ".bubble" in css
    assert "#ebf3fb" in css
    assert "position: relative" in css
    assert "class=\"layout\"" in html or 'class="layout"' in html
    assert "/api/fleet/brief" in js
    assert "/api/fleet/chat" in js
    assert "Please sign in" in js
    assert "The fleet is settled." in js
    assert 'chatSrc' in js
    assert '"/dash/"' in js or "'/dash/'" in js
    nginx = (DEPLOY_ROOT / "console" / "nginx.conf.template").read_text()
    assert "location /api/fleet/" in nginx
    assert "resolver 127.0.0.11" in nginx
    assert "set $office_gateway" in nginx
    assert "set $hermes_agent" in nginx
    assert "office-gateway:8080" in nginx
    assert "hermes-agent:9119" in nginx
    assert "proxy_pass http://office-gateway:8080/v1/fleet/" not in nginx
    assert "location = /login" in nginx
    assert "location /auth/" in nginx
    assert "location /fonts/" in nginx
    assert "location /assets/" in nginx
    assert "location /fonts-terminal/" in nginx
    assert "location /dashboard-plugins/" in nginx
    assert "proxy_redirect /login?next=%2F /dash/login?next=%2Fdash%2F;" in nginx
    assert "absolute_redirect off;" in nginx
    assert "proxy_set_header Host $http_host;" in nginx
    assert "OFFICE_GATEWAY_TOKEN_SUPERVISOR" in nginx
    assert "alias /avatars/" in nginx
    assert "agent.purpose" in js
    assert "Give me a moment" in js
    assert "layers/mouth-" in js
    assert 'id="guide"' in html
    assert 'id="guide-open"' in html
    assert 'id="guide-open-chat"' not in html
    assert html.count('class="guide-open"') == 1
    assert "How to use" in html
    assert "Hephaestus" in html
    assert "Mnemosyne" in html
    assert "Surtr" in html
    assert "Iris" in html
    assert "Argus" in html
    assert "a2a_call to lab-host only" in html
    assert "APPROVE" in html
    assert "data-prompt" in html
    assert "athena-guide-dismissed" in js
    assert "guide-open" in js
    assert "layers/eyes-blink.png" in js
    assert "Whenever you're ready." in js
    assert "function visemesFor(" in js
    assert "VISEMES[visemeIndex % VISEMES.length]" not in js
    assert "stopTalking, 4800" not in js
    layers = DEPLOY_ROOT / "assets" / "avatars" / "athena" / "vtuber" / "layers"
    for name in ("mouth-a.png", "mouth-e.png", "mouth-i.png", "mouth-o.png", "mouth-u.png", "eyes-blink.png"):
        assert (layers / name).is_file(), name
    assert not (layers / "eyes-listen.png").exists()
    vtuber = DEPLOY_ROOT / "assets" / "avatars" / "athena" / "vtuber"
    assert (vtuber / "athena-vtuber-idle.png").is_file()
    assert not (vtuber / "raw").exists()
    assert not (vtuber / "sprites.json").exists()
    for leftover in ("athena-vtuber-a.png", "athena-vtuber-blink.png", "athena-vtuber-listen.png"):
        assert not (vtuber / leftover).exists(), leftover
    old = DEPLOY_ROOT / "assets" / "avatars" / "athena"
    assert not (old / "athena-idle.png").exists()
    assert not (DEPLOY_ROOT / "scripts" / "stabilize-athena-vtuber.py").exists()
    compose = COMPOSE.read_text()
    assert "office-console:" in compose
    assert "${HERMES_CONSOLE_PUBLISH:-10.216.4.80:80}:80" in compose
    compose = COMPOSE.read_text()
    assert "./assets/avatars:/avatars:ro" in compose
    css = (DEPLOY_ROOT / "console" / "www" / "styles.css").read_text()
    assert "object-position: center 18%" in css
    assert ".specialists img" in css
    assert "width: 64px" in css
    root_env = (DEPLOY_ROOT / ".env.example").read_text()
    assert "HERMES_CONSOLE_PUBLISH=10.216.4.80:80" in root_env


def _visemes_for(text: str) -> list:
    js = (DEPLOY_ROOT / "console" / "www" / "app.js").read_text()
    match = re.search(r"function visemesFor\(text\) \{.*?\n\}", js, re.S)
    assert match, "visemesFor(text) must exist in app.js"
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to check viseme mapping")
    raw = subprocess.check_output(
        [
            node,
            "-e",
            match.group(0) + f"; console.log(JSON.stringify(visemesFor({json.dumps(text)})))",
        ],
        text=True,
    )
    return json.loads(raw)


def test_mouth_visemes_follow_spoken_words():
    spoken = [shape for shape in _visemes_for("Done.") if shape]
    assert spoken == ["o"]
    asking = [shape for shape in _visemes_for("Give me a second — I'm asking Hephaestus.") if shape]
    assert asking[0] == "i"
    assert "a" in asking
    assert "o" in asking


def test_docker_compose_config_parses(tmp_path):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("docker CLI is not installed")
    version = subprocess.run(
        [docker, "compose", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if version.returncode != 0:
        pytest.skip("docker compose plugin is not installed")

    deploy = tmp_path / "office-assistant"
    shutil.copytree(DEPLOY_ROOT, deploy)
    shutil.copyfile(deploy / ".env.example", deploy / ".env")
    shutil.copyfile(deploy / "models.env.example", deploy / "models.env")
    for role in HERMES_ROLES:
        shutil.copyfile(
            deploy / "hermes" / role / ".env.example",
            deploy / "hermes" / role / ".env",
        )

    result = subprocess.run(
        [docker, "compose", "config"],
        cwd=deploy,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


ROOT = Path("deploy/office-assistant/skills")


def test_supervisor_skill_forbids_direct_platform_calls_and_uses_a2a():
    text = (ROOT / "supervisor" / "SKILL.md").read_text()
    assert "a2a_call" in text
    assert "APPROVE" in text
    assert "delegate_task" in text and "do not" in text.lower()
    assert "office-gateway write" not in text.lower() or "never call write APIs" in text.lower()
    assert "never call write" in text.lower() or "specialist" in text.lower()
    assert "new operator" in text.lower() or "first session" in text.lower()
    assert "How to use" in text or "how to use" in text.lower()
    assert "paste" in text.lower()


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
