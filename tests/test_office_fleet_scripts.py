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
    "stop.sh",
    "status.sh",
    "restart.sh",
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
    for dest in [deploy / ".env"] + [
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


def test_save_images_ships_hermes_gateway_and_busybox():
    text = _read("save-images.sh")
    assert "docker pull" in text
    assert "docker build" in text
    assert "Dockerfile.office-gateway" in text
    assert "office-gw:local" in text
    assert "busybox:1.36" in text
    assert "docker save" in text
    assert "gzip" in text
    assert "HERMES_IMAGE" in text
    assert ":latest" not in text
    assert "office-fleet-images.tar.gz" in text


def test_load_and_start_loads_tarball_copies_env_and_starts_compose():
    text = _read("load-and-start.sh")
    assert "copy-env.sh" in text
    assert "docker load" in text
    assert "docker compose" in text
    assert "up -d" in text
    assert "office-gw:local" in text
    assert "--build" in text
    assert "10.216.4.80:9119" in text or "HERMES_DASHBOARD_PUBLISH" in text


def test_stop_status_restart_use_compose():
    assert "docker compose" in _read("stop.sh")
    assert "down" in _read("stop.sh")
    assert "docker compose" in _read("status.sh")
    assert "ps" in _read("status.sh")
    assert "docker compose" in _read("restart.sh")
    assert "restart" in _read("restart.sh")


def test_readme_points_at_deploy_scripts():
    readme = (DEPLOY_ROOT / "README.md").read_text()
    assert "scripts/copy-env.sh" in readme
    assert "scripts/save-images.sh" in readme
    assert "scripts/load-and-start.sh" in readme
    assert "scripts/stop.sh" in readme
    assert "scripts/status.sh" in readme
