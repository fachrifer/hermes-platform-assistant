import io
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

DEPLOY_ROOT = Path("deploy/office-assistant")
SCRIPTS = DEPLOY_ROOT / "scripts"
HERMES_ROLES = ("supervisor", "lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs")
SPECIALISTS = HERMES_ROLES[1:]
REQUIRED_SCRIPTS = (
    "copy-env.sh",
    "save-images.sh",
    "load-and-start.sh",
    "deploy-and-start.sh",
    "local-up.sh",
    "stop.sh",
    "status.sh",
    "restart.sh",
    "doctor.sh",
    "pair-env.sh",
    "desktop-connect.sh",
    "render-traefik-core.sh",
    "ensure-approver.sh",
    "migrate-env-phase1b.sh",
    "unpack-phase1b.sh",
)
RETIRED_SCRIPTS = (
    "office-gw-fast.sh",
    "office-gw-get.sh",
    "office-gw-propose.sh",
    "office-gw-watch.sh",
    "office-watch-summary.sh",
    "s6-office-watch-run",
    "patch-hermes-airgap-provider.py",
    "hermes-airgap-provider-cont-init.sh",
    "apply-office-llm-config.py",
    "hermes-office-llm-cont-init.sh",
    "hermes-supervisor-cron-init.sh",
)


def _script(name: str) -> Path:
    return SCRIPTS / name


def _read(name: str) -> str:
    return _script(name).read_text()


def _run(args, timeout=60, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout, **kwargs)


def env_map(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key] = value
    return out


def _deploy_copy(tmp_path: Path) -> Path:
    deploy = tmp_path / "office"
    shutil.copytree(
        DEPLOY_ROOT,
        deploy,
        ignore=shutil.ignore_patterns(".env", "models.env", ".local-login", "images", ".smoke", "spike"),
    )
    (deploy / "edge" / "approvers.htpasswd").unlink(missing_ok=True)
    return deploy


@pytest.mark.parametrize("name", REQUIRED_SCRIPTS)
def test_deploy_script_exists_executable_and_parses(name):
    path = _script(name)
    assert path.is_file(), f"missing {path}"
    assert path.stat().st_mode & stat.S_IXUSR, f"{path} must be executable"
    assert path.read_text().splitlines()[0] == "#!/usr/bin/env bash"
    assert b"\r" not in path.read_bytes()
    result = _run(["bash", "-n", str(path)])
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", RETIRED_SCRIPTS)
def test_retired_scripts_are_gone(name):
    assert not _script(name).exists()


def test_lib_lists_current_roles_and_image():
    lib = _read("lib.sh")
    assert 'HERMES_ROLES="supervisor lab-host ingress llm cluster-gpu vector obs"' in lib
    assert "nousresearch/hermes-agent:v2026.9.21" in lib


def test_copy_env_copies_examples_without_overwriting(tmp_path):
    deploy = _deploy_copy(tmp_path)
    script = deploy / "scripts" / "copy-env.sh"
    first = _run(["bash", str(script)])
    assert first.returncode == 0, first.stderr
    root_env = deploy / ".env"
    assert root_env.is_file()
    assert (deploy / "models.env").is_file()
    for role in HERMES_ROLES:
        assert (deploy / "hermes" / role / ".env").is_file()

    marker = "# keep-existing-operator-env\n"
    root_env.write_text(marker + root_env.read_text())
    second = _run(["bash", str(script)])
    assert second.returncode == 0, second.stderr
    assert root_env.read_text().startswith(marker)
    assert "keep " in second.stdout
    assert "Fill secrets" not in second.stdout


@pytest.mark.parametrize("role", HERMES_ROLES)
def test_role_env_examples_use_new_keys(role):
    keys = env_map(DEPLOY_ROOT / "hermes" / role / ".env.example")
    assert {"OFFICE_GATEWAY_TOKEN", "OFFICE_LLM_API_KEY", "API_SERVER_KEY", "HERMES_DASHBOARD_SESSION_TOKEN"} <= set(keys)
    assert not [k for k in keys if k.startswith(("OPENAI_", "A2A_"))]
    if role == "supervisor":
        peers = {f"HERMES_PEER_PEER_{r.upper().replace('-', '_')}_KEY" for r in SPECIALISTS}
        assert peers <= set(keys)


def test_models_env_example_has_base_url_only():
    assert set(env_map(DEPLOY_ROOT / "models.env.example")) == {"OFFICE_LLM_BASE_URL"}


def _write_root_tokens(deploy: Path) -> None:
    (deploy / ".env").write_text(
        "OFFICE_GATEWAY_TOKEN_SUPERVISOR=sup-gw\n"
        "OFFICE_GATEWAY_TOKEN_LAB_HOST=lab-gw\n"
        "OFFICE_GATEWAY_TOKEN_INGRESS=ing-gw\n"
        "OFFICE_GATEWAY_TOKEN_LLM=llm-gw\n"
        "OFFICE_GATEWAY_TOKEN_CLUSTER=clu-gw\n"
        "OFFICE_GATEWAY_TOKEN_VECTOR=vec-gw\n"
        "OFFICE_GATEWAY_TOKEN_OBS=obs-gw\n"
    )


def test_pair_env_copies_gateway_tokens_and_peer_keys(tmp_path):
    deploy = _deploy_copy(tmp_path)
    _write_root_tokens(deploy)
    for role in HERMES_ROLES:
        (deploy / "hermes" / role / ".env").write_text(f"OFFICE_GATEWAY_TOKEN=wrong\nAPI_SERVER_KEY=api-{role}\n")
    (deploy / ".local-login").write_text("approver_user=timai\napprover_password=keep-me\n")
    result = _run(["bash", str(deploy / "scripts" / "pair-env.sh")])
    assert result.returncode == 0, result.stderr + result.stdout
    for secret in ("sup-gw", "lab-gw", "api-lab-host", "keep-me"):
        assert secret not in result.stdout

    expected = {
        "supervisor": "sup-gw",
        "lab-host": "lab-gw",
        "ingress": "ing-gw",
        "llm": "llm-gw",
        "cluster-gpu": "clu-gw",
        "vector": "vec-gw",
        "obs": "obs-gw",
    }
    session = env_map(deploy / ".env")["HERMES_DASHBOARD_SESSION_TOKEN"]
    for role, gateway in expected.items():
        data = env_map(deploy / "hermes" / role / ".env")
        assert data["OFFICE_GATEWAY_TOKEN"] == gateway
        assert data["HERMES_DASHBOARD_SESSION_TOKEN"] == session
        assert "A2A_BEARER_TOKEN" not in data
    supervisor = env_map(deploy / "hermes" / "supervisor" / ".env")
    for role in SPECIALISTS:
        assert supervisor[f"HERMES_PEER_PEER_{role.upper().replace('-', '_')}_KEY"] == f"api-{role}"
    login = env_map(deploy / ".local-login")
    assert login["approver_password"] == "keep-me"
    assert login["session_token"] == session
    assert stat.S_IMODE((deploy / ".local-login").stat().st_mode) == 0o600


def test_ensure_approver_creates_login_once(tmp_path):
    deploy = _deploy_copy(tmp_path)
    script = deploy / "scripts" / "ensure-approver.sh"
    first = _run(["bash", str(script)])
    assert first.returncode == 0, first.stderr
    htpasswd = deploy / "edge" / "approvers.htpasswd"
    user, hashed = htpasswd.read_text().strip().split(":", 1)
    assert user == "timai" and hashed.startswith("$apr1$")
    login = env_map(deploy / ".local-login")
    password = login["approver_password"]
    assert login["approver_user"] == "timai"
    assert password not in first.stdout + first.stderr
    assert stat.S_IMODE((deploy / ".local-login").stat().st_mode) == 0o600
    salt = hashed.split("$")[2]
    check = _run(["openssl", "passwd", "-apr1", "-salt", salt, password])
    assert check.stdout.strip() == hashed

    second = _run(["bash", str(script)])
    assert second.returncode == 0
    assert htpasswd.read_text().strip() == f"{user}:{hashed}"
    assert env_map(deploy / ".local-login")["approver_password"] == password


def test_ensure_approver_rejects_bad_user(tmp_path):
    deploy = _deploy_copy(tmp_path)
    result = _run(
        ["bash", str(deploy / "scripts" / "ensure-approver.sh")],
        env={**os.environ, "OFFICE_APPROVER_USER": "bad:user"},
    )
    assert result.returncode != 0
    assert not (deploy / "edge" / "approvers.htpasswd").exists()


def _old_style_tree(tmp_path: Path, base_url: str = "http://10.216.78.129:4000/v1") -> Path:
    deploy = _deploy_copy(tmp_path)
    (deploy / ".env").write_text(
        "HERMES_IMAGE=nousresearch/hermes-agent:v2026.8.31\n"
        "OFFICE_GATEWAY_TOKEN_SUPERVISOR=old-sup\n"
        "OFFICE_GATEWAY_TOKEN_LAB_HOST=lab-gw\n"
        "OFFICE_GATEWAY_TOKEN_VECTOR=vec-gw\n"
        "OFFICE_GATEWAY_TOKEN_CLUSTER=clu-gw\n"
        "OFFICE_GATEWAY_TOKEN_LLM=llm-gw\n"
        "OFFICE_GATEWAY_TOKEN_OBS=obs-gw\n"
        "OFFICE_GATEWAY_TOKEN_EDGE=edge-gw\n"
        "OFFICE_WRITE_TARGETS=a,b\n"
        "OFFICE_READ_LOGS_TARGETS=a\n"
        "OFFICE_VECTOR_ENV=prod\n"
        "OFFICE_AUTOHEAL_DENY=x\n"
        "OFFICE_SERVICE_URLS=gateway=http://office-gateway:8080/health,"
        "hermes-edge=http://hermes-edge:9900/.well-known/agent.json,"
        "custom=http://10.0.0.1/health\n"
        "HERMES_DASHBOARD_PUBLISH=10.216.4.80:9119\n"
    )
    (deploy / "models.env").write_text(f"OPENAI_BASE_URL={base_url}\nOPENAI_MODEL=qwen3.8-fast\n")
    old_roles = ("supervisor", "lab-host", "vector", "cluster-gpu", "obs", "edge", "llm-edge")
    for role in old_roles:
        path = deploy / "hermes" / role / ".env"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"OPENAI_API_KEY=key-{role}",
            f"OPENAI_BASE_URL={base_url}",
            "OPENAI_MODEL=qwen3.8-fast",
            "OFFICE_GATEWAY_TOKEN=stale",
            "A2A_BEARER_TOKEN=a2a",
            "A2A_PORT=9900",
        ]
        if role != "vector":
            lines.append(f"API_SERVER_KEY=api-{role}")
        if role == "supervisor":
            lines += ["A2A_TOKEN_LAB_HOST=x", "HERMES_PEER_LAB_HOST_KEY=old-peer", "HERMES_PEER_LLM_EDGE_KEY=old"]
        path.write_text("\n".join(lines) + "\n")
    return deploy


def test_migrate_env_phase1b_rewrites_old_tree(tmp_path):
    deploy = _old_style_tree(tmp_path)
    result = _run(["bash", str(deploy / "scripts" / "migrate-env-phase1b.sh")])
    assert result.returncode == 0, result.stderr + result.stdout
    out = result.stdout + result.stderr
    for secret in ("old-sup", "edge-gw", "key-lab-host", "api-lab-host"):
        assert secret not in out
    assert "WARNING" not in out

    root = env_map(deploy / ".env")
    assert root["OFFICE_GATEWAY_TOKEN_INGRESS"] == "edge-gw"
    assert "OFFICE_GATEWAY_TOKEN_EDGE" not in root
    assert root["OFFICE_GATEWAY_TOKEN_APPROVER"]
    assert root["OFFICE_GATEWAY_TOKEN_SUPERVISOR"] not in ("", "old-sup")
    assert root["HERMES_IMAGE"] == "nousresearch/hermes-agent:v2026.9.21"
    for gone in ("OFFICE_WRITE_TARGETS", "OFFICE_READ_LOGS_TARGETS", "OFFICE_VECTOR_ENV", "OFFICE_AUTOHEAL_DENY"):
        assert gone not in root
    assert root["OFFICE_SERVICE_URLS"] == "gateway=http://office-gateway:8080/health,custom=http://10.0.0.1/health"

    assert env_map(deploy / "models.env") == {"OFFICE_LLM_BASE_URL": "http://10.216.78.129:4000/v1"}

    source_role = {"ingress": "edge", "llm": "llm-edge"}
    api_keys = {}
    for role in HERMES_ROLES:
        data = env_map(deploy / "hermes" / role / ".env")
        assert data["OFFICE_LLM_API_KEY"] == f"key-{source_role.get(role, role)}"
        assert not [k for k in data if k.startswith(("OPENAI_", "A2A_"))]
        assert data["API_SERVER_KEY"]
        api_keys[role] = data["API_SERVER_KEY"]
    assert api_keys["ingress"] == "api-edge"
    supervisor = env_map(deploy / "hermes" / "supervisor" / ".env")
    assert supervisor["OFFICE_GATEWAY_TOKEN"] == root["OFFICE_GATEWAY_TOKEN_SUPERVISOR"]
    for role in SPECIALISTS:
        assert supervisor[f"HERMES_PEER_PEER_{role.upper().replace('-', '_')}_KEY"] == api_keys[role]
    assert "HERMES_PEER_LAB_HOST_KEY" not in supervisor
    assert "HERMES_PEER_LLM_EDGE_KEY" not in supervisor

    assert list(deploy.glob(".env.pre-phase1b-*"))
    assert list(deploy.glob("models.env.pre-phase1b-*"))
    assert list((deploy / "hermes" / "supervisor").glob(".env.pre-phase1b-*"))
    assert not (deploy / "hermes" / "edge").exists()
    assert not (deploy / "hermes" / "llm-edge").exists()
    assert list((deploy / "hermes").glob("edge.pre-phase1b-*"))

    htpasswd = (deploy / "edge" / "approvers.htpasswd").read_text()
    password = env_map(deploy / ".local-login")["approver_password"]
    assert htpasswd.startswith("timai:$apr1$")
    assert password not in out

    rotated = root["OFFICE_GATEWAY_TOKEN_SUPERVISOR"]
    again = _run(["bash", str(deploy / "scripts" / "migrate-env-phase1b.sh")])
    assert again.returncode == 0, again.stderr + again.stdout
    assert env_map(deploy / ".env")["OFFICE_GATEWAY_TOKEN_SUPERVISOR"] == rotated
    assert "rotated" not in again.stdout
    assert (deploy / "edge" / "approvers.htpasswd").read_text() == htpasswd


def test_migrate_env_warns_when_llm_host_not_in_no_proxy(tmp_path):
    deploy = _old_style_tree(tmp_path, base_url="http://10.9.9.9:4000/v1")
    result = _run(["bash", str(deploy / "scripts" / "migrate-env-phase1b.sh")])
    assert result.returncode == 0, result.stderr + result.stdout
    assert "WARNING: LiteLLM host 10.9.9.9 is not in NO_PROXY" in result.stdout


def test_migrate_env_fails_on_missing_llm_key(tmp_path):
    deploy = _old_style_tree(tmp_path)
    path = deploy / "hermes" / "obs" / ".env"
    path.write_text(path.read_text().replace("OPENAI_API_KEY=key-obs", "OPENAI_API_KEY="))
    result = _run(["bash", str(deploy / "scripts" / "migrate-env-phase1b.sh")])
    assert result.returncode != 0
    assert "obs" in result.stderr


def test_migrate_env_refuses_old_tree(tmp_path):
    deploy = _old_style_tree(tmp_path)
    compose = deploy / "docker-compose.yml"
    compose.write_text(compose.read_text().replace("hermes-ingress", "hermes-edge"))
    result = _run(["bash", str(deploy / "scripts" / "migrate-env-phase1b.sh")])
    assert result.returncode != 0
    assert "Phase 1b tree" in result.stderr


def _tgz(path: Path, files: dict[str, str]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))


def test_unpack_keeps_env_and_live_routes_and_retires_old_files(tmp_path):
    home = tmp_path / "home"
    vm = home / "hermes-assistant"
    (vm / "images").mkdir(parents=True)
    (vm / "edge").mkdir()
    (vm / "edge" / "edge-routes").write_text("live routes\n")
    (vm / "scripts").mkdir()
    (vm / "scripts" / "office-gw-get.sh").write_text("old\n")
    (vm / "office_gateway").mkdir()
    (vm / "office_gateway" / "autoheal.py").write_text("stale\n")
    (vm / "hermes" / "edge").mkdir(parents=True)
    (vm / "hermes" / "edge" / ".env").write_text("OPENAI_API_KEY=k\n")
    (vm / ".env").write_text("KEEP=1\n")
    _tgz(
        vm / "images" / "fleet-p1b-tree.tgz",
        {
            "docker-compose.yml": "services: {hermes-ingress: {}}\n",
            "edge/edge-routes": "shipped routes\n",
            "edge/traefik-dynamic/routes.yml": "shipped\n",
            "scripts/migrate-env-phase1b.sh": "#!/usr/bin/env bash\n",
            "scripts/lib.sh": "# lib\n",
        },
    )
    _tgz(vm / "images" / "fleet-p1b-gateway.tgz", {"office_gateway/app.py": "new\n", "Dockerfile.office-gateway": "FROM x\n"})
    env = {**os.environ, "HOME": str(home), "PATH": "/usr/bin:/bin"}
    result = _run(["bash", str(_script("unpack-phase1b.sh"))], env=env)
    assert result.returncode == 0, result.stderr + result.stdout

    assert (vm / "edge" / "edge-routes").read_text() == "live routes\n"
    assert (vm / "edge" / "traefik-dynamic" / "routes.yml").read_text() == "shipped\n"
    assert (vm / ".env").read_text() == "KEEP=1\n"
    assert (vm / "hermes" / "edge" / ".env").exists()
    assert not (vm / "scripts" / "office-gw-get.sh").exists()
    assert not (vm / "office_gateway" / "autoheal.py").exists()
    assert (vm / "office_gateway" / "app.py").read_text() == "new\n"
    assert os.access(vm / "scripts" / "migrate-env-phase1b.sh", os.X_OK)
    stash = next(vm.glob(".retired-pre-phase1b-*"))
    assert (stash / "scripts" / "office-gw-get.sh").exists()
    backup = next(home.glob("hermes-assistant-pre-phase1b-*.tgz"))
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    with tarfile.open(backup) as tar:
        names = tar.getnames()
    assert "./.env" in names
    assert not [n for n in names if n.startswith("./images")]


def test_unpack_refuses_crlf_tree(tmp_path):
    home = tmp_path / "home"
    vm = home / "hermes-assistant"
    (vm / "images").mkdir(parents=True)
    (vm / ".env").write_text("KEEP=1\n")
    _tgz(vm / "images" / "fleet-p1b-tree.tgz", {"scripts/lib.sh": "# lib\r\n"})
    _tgz(vm / "images" / "fleet-p1b-gateway.tgz", {"office_gateway/app.py": "new\n"})
    env = {**os.environ, "HOME": str(home), "PATH": "/usr/bin:/bin"}
    result = _run(["bash", str(_script("unpack-phase1b.sh"))], env=env)
    assert result.returncode != 0
    assert "CRLF" in result.stderr
    assert not list(home.glob("hermes-assistant-pre-phase1b-*.tgz"))


def test_ship_script_ships_committed_files_only():
    text = _read("ship-phase1b.ps1")
    assert "git archive" in text
    assert "HEAD:deploy/office-assistant" in text
    assert "ls-files --eol" in text
    assert "core.fileMode=false" in text
    assert '"core.autocrlf=false"' in text
    assert "id_ed25519_hermes_lab" in text
    assert "BatchMode=yes" in text
    assert "unpack-phase1b.sh" in text
    assert "nousresearch/hermes-agent:v2026.9.21" in text
    assert "tar -czf" not in text
    assert "deploy.sh" not in text.replace("deploy-and-start.sh", "")


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
    assert "render-traefik-core.sh" in text
    assert text.index("ensure-approver.sh") < text.index("docker compose up")
    assert "deploy-and-start.sh" in _read("load-and-start.sh")


def test_deploy_and_start_refuses_laptop_checkout():
    assert "office_is_lab_tree" in _read("lib.sh")
    text = _read("deploy-and-start.sh")
    assert text.index("office_is_lab_tree") < text.index('echo "docker load')
    result = _run(["bash", str(_script("deploy-and-start.sh"))], timeout=15)
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "local-up.sh" in combined
    assert "Loaded image" not in combined


def test_load_and_start_rejects_wrong_image_architecture():
    lib = _read("lib.sh")
    text = _read("deploy-and-start.sh")
    assert "office_docker_arch" in lib
    assert "office_image_arch" in lib
    assert "architecture mismatch" in text
    assert "docker pull --platform" not in text


def test_load_and_start_stops_old_publishers_before_up():
    text = _read("deploy-and-start.sh")
    assert "office_stop_port_holders" in _read("lib.sh")
    assert text.index("docker compose down") < text.index("up -d")


def test_stop_status_restart_use_compose():
    assert "down" in _read("stop.sh")
    assert "ps" in _read("status.sh")
    assert "restart" in _read("restart.sh")


def test_doctor_runs_hermes_doctor_in_each_container():
    text = _read("doctor.sh")
    assert "office_compose exec -T" in text
    for svc in (
        "hermes-agent",
        "hermes-lab-host",
        "hermes-vector",
        "hermes-cluster-gpu",
        "hermes-llm",
        "hermes-obs",
        "hermes-ingress",
    ):
        assert f"  {svc}\n" in text
    assert "llm-edge" not in text


def test_desktop_connect_lists_current_roles():
    text = _read("desktop-connect.sh")
    assert "9124  llm" in text
    assert "9126  ingress" in text
    assert "llm-edge" not in text


def test_local_up_writes_new_env_keys_only():
    text = _read("local-up.sh")
    assert "127.0.0.1:9119" in text
    assert "OFFICE_LLM_BASE_URL" in text
    assert "OFFICE_LLM_API_KEY" in text
    assert "OFFICE_GATEWAY_TOKEN_APPROVER" in text
    assert "pair-env.sh" in text
    assert "ensure-approver.sh" in text
    assert "docker-compose.local.yml" in text
    assert '"OFFICE_GATEWAY_CONTEXT": "../.."' in text
    for stale in ("OPENAI_", "A2A_", "llm-edge"):
        assert stale not in text
