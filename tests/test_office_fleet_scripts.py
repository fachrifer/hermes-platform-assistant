import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

DEPLOY_ROOT = Path("deploy/office-assistant")
SCRIPTS = DEPLOY_ROOT / "scripts"
HERMES_ROLES = (
    "supervisor",
    "lab-host",
    "vector",
    "cluster-gpu",
    "llm-edge",
    "obs",
)
REQUIRED_SCRIPTS = (
    "copy-env.sh",
    "save-images.sh",
    "load-and-start.sh",
    "deploy-and-start.sh",
    "deploy.sh",
    "local-up.sh",
    "stop.sh",
    "status.sh",
    "restart.sh",
    "doctor.sh",
    "pair-env.sh",
)


def _script(name: str) -> Path:
    return SCRIPTS / name


def _read(name: str) -> str:
    return _script(name).read_text()


@pytest.mark.parametrize("name", REQUIRED_SCRIPTS)
def test_deploy_script_exists_executable_and_parses(name):
    path = _script(name)
    assert path.is_file(), f"missing {path}"
    mode = path.stat().st_mode
    assert mode & stat.S_IXUSR, f"{path} must be executable"
    shebang = path.read_text().splitlines()[0]
    assert shebang == "#!/usr/bin/env bash"
    result = subprocess.run(
        ["bash", "-n", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_copy_env_copies_examples_without_overwriting(tmp_path):
    deploy = tmp_path / "office-assistant"
    shutil.copytree(DEPLOY_ROOT, deploy)
    script = deploy / "scripts" / "copy-env.sh"
    for dest in [deploy / ".env", deploy / "models.env"] + [
        deploy / "hermes" / role / ".env" for role in HERMES_ROLES
    ]:
        dest.unlink(missing_ok=True)

    first = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr
    root_env = deploy / ".env"
    assert root_env.is_file()
    assert (deploy / "models.env").is_file()
    for role in HERMES_ROLES:
        assert (deploy / "hermes" / role / ".env").is_file()

    marker = "# keep-existing-operator-env\n"
    root_env.write_text(marker + root_env.read_text())
    second = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0, second.stderr
    assert root_env.read_text().startswith(marker)
    assert "keep " in second.stdout
    assert "Fill secrets" not in second.stdout


def test_pair_env_copies_root_gateway_and_a2a_tokens(tmp_path, monkeypatch):
    deploy = tmp_path / "office"
    shutil.copytree(DEPLOY_ROOT, deploy)
    (deploy / ".env").write_text(
        "OFFICE_GATEWAY_TOKEN_SUPERVISOR=sup-gw\n"
        "OFFICE_GATEWAY_TOKEN_LAB_HOST=lab-gw\n"
        "OFFICE_GATEWAY_TOKEN_VECTOR=vec-gw\n"
        "OFFICE_GATEWAY_TOKEN_CLUSTER=clu-gw\n"
        "OFFICE_GATEWAY_TOKEN_LLM=llm-gw\n"
        "OFFICE_GATEWAY_TOKEN_OBS=obs-gw\n"
    )
    (deploy / "hermes" / "supervisor" / ".env").write_text(
        "OFFICE_GATEWAY_TOKEN=wrong\n"
        "A2A_TOKEN_LAB_HOST=lab-a2a\n"
        "A2A_TOKEN_VECTOR=vec-a2a\n"
        "A2A_TOKEN_CLUSTER_GPU=clu-a2a\n"
        "A2A_TOKEN_LLM_EDGE=llm-a2a\n"
        "A2A_TOKEN_OBS=obs-a2a\n"
    )
    for role in HERMES_ROLES[1:]:
        (deploy / "hermes" / role / ".env").write_text(
            "OFFICE_GATEWAY_TOKEN=wrong\nA2A_BEARER_TOKEN=wrong\n"
        )
    result = subprocess.run(
        ["bash", str(deploy / "scripts" / "pair-env.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "sup-gw" not in result.stdout
    assert "lab-gw" not in result.stdout
    assert "lab-a2a" not in result.stdout

    def env_map(path: Path) -> dict[str, str]:
        out = {}
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                out[key] = value
        return out

    supervisor = env_map(deploy / "hermes" / "supervisor" / ".env")
    assert supervisor["OFFICE_GATEWAY_TOKEN"] == "sup-gw"
    expected = {
        "lab-host": ("lab-gw", "lab-a2a"),
        "vector": ("vec-gw", "vec-a2a"),
        "cluster-gpu": ("clu-gw", "clu-a2a"),
        "llm-edge": ("llm-gw", "llm-a2a"),
        "obs": ("obs-gw", "obs-a2a"),
    }
    for role, (gateway, a2a) in expected.items():
        data = env_map(deploy / "hermes" / role / ".env")
        assert data["OFFICE_GATEWAY_TOKEN"] == gateway
        assert data["A2A_BEARER_TOKEN"] == a2a


def test_deploy_and_start_is_lab_entrypoint_and_keeps_env():
    text = _read("deploy-and-start.sh")
    assert "copy-env.sh" in text
    assert "token_urlsafe" not in text
    assert "write_text" not in text
    assert "docker load" in text
    assert "docker compose down" in text
    assert "--remove-orphans" in text
    assert "up -d" in text
    assert "OFFICE_GATEWAY_CONTEXT" in text
    wrapper = _read("load-and-start.sh")
    assert "deploy-and-start.sh" in wrapper


def test_deploy_and_start_refuses_laptop_checkout():
    lib = _read("lib.sh")
    text = _read("deploy-and-start.sh")
    assert "office_is_lab_tree" in lib
    assert "office_is_lab_tree" in text
    assert "local-up.sh" in text
    assert "deploy.sh" in text
    assert text.index("office_is_lab_tree") < text.index('echo "docker load')
    result = subprocess.run(
        ["bash", str(_script("deploy-and-start.sh"))],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "local-up.sh" in combined
    assert "deploy.sh" in combined
    assert "Loaded image" not in combined


def test_deploy_rsync_replaces_lab_env_from_laptop():
    text = _read("deploy.sh")
    assert "--exclude '.env'" in text
    assert "--exclude 'models.env'" in text
    assert "--exclude 'hermes/*/.env'" in text
    assert "--exclude '.kubeconfig.local'" in text
    assert "--exclude '.local-login'" in text
    assert "--exclude 'docker-compose.local.yml'" in text
    assert "office_gateway newer than tarball" in text
    assert "Dockerfile.office-gateway" in text
    assert "deploy-and-start.sh" in text
    assert "./scripts/deploy-and-start.sh" in text
    assert "pair-env.sh" in text
    assert "touch .env" not in text
    assert "--ignore-existing" not in text
    assert "push_env" in text
    assert "models.env" in text
    assert "hermes/$role/.env" in text or 'hermes/$role/.env' in text
    assert "OFFICE_GATEWAY_CONTEXT=." in text
    assert "10.216.4.80:9119" in text
    assert "10.216.4.80:80" in text
    assert "kubeconfig.absent" in text


def test_save_images_ships_hermes_gateway_and_busybox():
    text = _read("save-images.sh")
    assert "docker pull" in text
    assert "docker build" in text
    assert "Dockerfile.office-gateway" in text
    assert "office-gw:local" in text
    assert "busybox:1.36" in text
    assert "nginx:1.27-alpine" in text
    assert "docker save" in text
    assert "gzip" in text
    assert "HERMES_IMAGE" in text
    assert ":latest" not in text
    assert "office-fleet-images.tar.gz" in text
    assert "--platform" in text
    assert "linux/amd64" in text
    assert "FROM" in text
    assert "pin_single_platform" in text
    assert "{{.Architecture}}" in _read("lib.sh")


def test_load_and_start_rejects_wrong_image_architecture():
    lib = _read("lib.sh")
    text = _read("deploy-and-start.sh")
    assert "office_docker_arch" in lib
    assert "office_image_arch" in lib
    assert "office_docker_arch" in text
    assert "office_image_arch" in text
    assert "architecture mismatch" in text
    assert "save-images.sh" in text
    assert "docker pull --platform" not in text
    assert "registry-1.docker.io" not in text


def test_load_and_start_stops_old_publishers_before_up():
    lib = _read("lib.sh")
    text = _read("deploy-and-start.sh")
    assert "docker compose down" in text
    assert "--remove-orphans" in text
    assert "office_stop_port_holders" in lib
    assert "office_stop_port_holders" in text
    assert 'publish=${port}' in lib or 'publish="${port}"' in lib
    assert "docker stop" in lib
    down_at = text.index("docker compose down")
    up_at = text.index("up -d")
    assert down_at < up_at


def test_load_and_start_loads_tarball_copies_env_and_starts_compose():
    text = _read("deploy-and-start.sh")
    assert "copy-env.sh" in text
    assert "docker load" in text
    assert "docker compose" in text
    assert "up -d" in text
    assert "office-gw:local" in text
    assert "--build" in text
    assert "10.216.4.80:9119" in text or "HERMES_DASHBOARD_PUBLISH" in text
    assert "Athena console" in text
    assert "10.216.4.80:80" in text or "HERMES_CONSOLE_PUBLISH" in text


def test_deploy_ships_to_lab_vm_loads_and_starts():
    text = _read("deploy.sh")
    assert "10.216.4.80" in text
    assert "timai" in text
    assert "/home/timai/hermes-assistant" in text
    assert "hermes-agent-ffa" not in text
    assert "rsync" in text
    assert "ssh" in text
    assert "save-images.sh" in text
    assert "load-and-start.sh" in text
    assert "copy-env.sh" in text
    assert "stop.sh" in text
    assert "status.sh" in text
    assert "restart.sh" in text
    assert "doctor.sh" in text
    assert "lib.sh" in text
    assert "office_gateway" in text
    assert "Dockerfile.office-gateway" in text
    assert "office-fleet-images.tar.gz" in text
    assert "deploy/office-assistant/" in text or '"$DEPLOY_DIR/"' in text
    assert "BatchMode=yes" not in text
    assert "BatchMode=no" in text
    assert "Athena console: http://10.216.4.80" in text
    assert "keyboard-interactive" in text
    assert "password" in text
    assert "ControlMaster" in text
    assert 'CONTROL_PATH="/tmp/office-deploy-%C"' in text
    assert "${TMPDIR:-/tmp}/office-deploy" not in text
    assert "mkdir $REMOTE:$OFFICE_DEPLOY_DIR" not in text


def test_stop_status_restart_use_compose():
    assert "docker compose" in _read("stop.sh")
    assert "down" in _read("stop.sh")
    assert "docker compose" in _read("status.sh")
    assert "ps" in _read("status.sh")
    assert "docker compose" in _read("restart.sh")
    assert "restart" in _read("restart.sh")


def test_doctor_runs_hermes_doctor_in_each_container():
    text = _read("doctor.sh")
    assert "hermes doctor" in text
    assert "office_compose exec -T" in text
    for svc in (
        "hermes-agent",
        "hermes-lab-host",
        "hermes-vector",
        "hermes-cluster-gpu",
        "hermes-llm-edge",
        "hermes-obs",
    ):
        assert svc in text


def test_readme_points_at_deploy_scripts():
    readme = (DEPLOY_ROOT / "README.md").read_text()
    assert "scripts/copy-env.sh" in readme
    assert "scripts/save-images.sh" in readme
    assert "scripts/load-and-start.sh" in readme
    assert "scripts/deploy-and-start.sh" in readme
    assert "scripts/deploy.sh" in readme
    assert "scripts/stop.sh" in readme
    assert "scripts/status.sh" in readme
    assert "scripts/doctor.sh" in readme
    assert "hermes doctor" in readme
    assert "timai@10.216.4.80" in readme
    assert "/home/timai/hermes-assistant" in readme


def test_local_up_binds_laptop_dashboard():
    text = _read("local-up.sh")
    assert "127.0.0.1:9119" in text
    assert "127.0.0.1:9120" in text
    assert "models.env" in text
    assert "OPENAI_MODEL" in text
    assert "HERMES_DASHBOARD_URL" in text
    assert "key.strip(), value.strip()" in text
    assert "docker-compose.local.yml" in text
    compose_local = (DEPLOY_ROOT / "docker-compose.local.yml").read_text()
    assert "127.0.0.1:9119:9119" in compose_local
    assert "127.0.0.1:9120:80" in compose_local
    laptop_pin = text[text.index("Local laptop must not") :]
    assert '"OFFICE_GATEWAY_CONTEXT": "../.."' in laptop_pin
