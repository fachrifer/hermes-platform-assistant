import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

DEPLOY_ROOT = Path("deploy/office-assistant")
RENDER = DEPLOY_ROOT / "scripts" / "render-traefik-core.sh"
TEMPLATE = DEPLOY_ROOT / "edge" / "traefik-dynamic" / "core.yml.template"
APPROVALS = DEPLOY_ROOT / "console" / "www" / "approvals"
BOT_ROLES = ("lab-host", "vector", "cluster-gpu", "llm", "obs", "ingress")
SUPERVISOR_TOKEN = "s" * 64
APPROVER_TOKEN = "a" * 64


def _render(tmp_path, **env_tokens):
    out = tmp_path / "core.yml"
    env = {k: v for k, v in os.environ.items() if not k.startswith("OFFICE_GATEWAY_TOKEN_")}
    env.update(env_tokens)
    env["OFFICE_TRAEFIK_CORE_OUT"] = str(out)
    proc = subprocess.run(
        ["bash", str(RENDER), str(tmp_path / "missing.env")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return yaml.safe_load(out.read_text(encoding="utf-8")), out.read_text(encoding="utf-8"), proc.stderr


@pytest.fixture
def rendered(tmp_path):
    return _render(
        tmp_path,
        OFFICE_GATEWAY_TOKEN_SUPERVISOR=SUPERVISOR_TOKEN,
        OFFICE_GATEWAY_TOKEN_APPROVER=APPROVER_TOKEN,
    )


def test_render_fills_every_placeholder(rendered):
    _, text, stderr = rendered
    assert "__" not in text
    assert "MISSING_" not in text
    assert "warning" not in stderr


def test_render_reads_tokens_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"OFFICE_GATEWAY_TOKEN_SUPERVISOR={SUPERVISOR_TOKEN}\n"
        f"OFFICE_GATEWAY_TOKEN_APPROVER='{APPROVER_TOKEN}'\n",
        encoding="utf-8",
    )
    out = tmp_path / "core.yml"
    env = {k: v for k, v in os.environ.items() if not k.startswith("OFFICE_GATEWAY_TOKEN_")}
    env["OFFICE_TRAEFIK_CORE_OUT"] = str(out)
    subprocess.run(["bash", str(RENDER), str(env_file)], env=env, check=True, capture_output=True)
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    headers = doc["http"]["middlewares"]["approver-token"]["headers"]["customRequestHeaders"]
    assert headers["Authorization"] == f"Bearer {APPROVER_TOKEN}"


def test_render_warns_when_tokens_missing(tmp_path):
    _, text, stderr = _render(tmp_path)
    assert "OFFICE_GATEWAY_TOKEN_SUPERVISOR empty" in stderr
    assert "OFFICE_GATEWAY_TOKEN_APPROVER empty" in stderr
    assert "MISSING_APPROVER_TOKEN" in text


def test_approvals_middlewares(rendered):
    doc, _, _ = rendered
    mws = doc["http"]["middlewares"]
    auth = mws["approver-auth"]["basicAuth"]
    assert auth["usersFile"] == "/etc/traefik/approvers.htpasswd"
    assert auth["headerField"] == "X-Approver"
    assert auth["removeHeader"] is True
    headers = mws["approver-token"]["headers"]["customRequestHeaders"]
    assert headers["Authorization"] == f"Bearer {APPROVER_TOKEN}"
    rewrite = mws["approvals-api-rewrite"]["replacePathRegex"]
    assert re.sub(rewrite["regex"], rewrite["replacement"].replace("$1", r"\1"), "/approvals/api/abc/approve") == (
        "/v1/approvals/abc/approve"
    )
    assert re.sub(rewrite["regex"], rewrite["replacement"].replace("$1", r"\1"), "/approvals/api") == "/v1/approvals"
    assert re.sub(
        rewrite["regex"], rewrite["replacement"].replace("$1", r"\1"), "/approvals/api/bot-chat/release"
    ) == "/v1/approvals/bot-chat/release"
    assert re.sub(
        rewrite["regex"], rewrite["replacement"].replace("$1", r"\1"), "/approvals/api/bot-chat/open"
    ) == "/v1/approvals/bot-chat/open"


def test_approvals_routers_are_https_only_and_authenticated(rendered):
    doc, _, _ = rendered
    routers = doc["http"]["routers"]
    api = routers["approvals-api"]
    assert api["entryPoints"] == ["hermes"]
    assert "tls" in api
    assert api["middlewares"] == ["approver-auth", "approver-token", "approvals-api-rewrite"]
    assert api["service"] == "office-gateway"
    page = routers["approvals-page"]
    assert page["entryPoints"] == ["hermes"]
    assert page["middlewares"] == ["approver-auth"]
    assert page["service"] == "office-www"
    assert api["priority"] > page["priority"]
    others = [
        r for name, r in routers.items()
        if name not in ("approvals-api", "approvals-page") and "/approvals" in r.get("rule", "")
    ]
    assert others == []


def test_approver_token_only_on_approvals_api(rendered):
    doc, _, _ = rendered
    for name, router in doc["http"]["routers"].items():
        if "approver-token" in router.get("middlewares", []):
            assert name == "approvals-api"


def test_bot_routes_use_current_role_ids(rendered):
    doc, text, _ = rendered
    routers = doc["http"]["routers"]
    services = doc["http"]["services"]
    for role in BOT_ROLES:
        assert f"bots-{role}" in routers
        assert f"bots-{role}-http" in routers
        assert services[f"hermes-{role}"]["loadBalancer"]["servers"] == [{"url": f"http://hermes-{role}:9119"}]
    assert "llm-edge" not in text
    assert "bots-edge" not in text


def test_template_and_repo_hold_no_rendered_secret():
    assert not re.search(r"\b[0-9a-f]{32,}\b", TEMPLATE.read_text(encoding="utf-8"))
    ignored = Path(".gitignore").read_text(encoding="utf-8").splitlines()
    assert "deploy/office-assistant/edge/traefik-dynamic/core.yml" in ignored
    assert "deploy/office-assistant/edge/approvers.htpasswd" in ignored


def test_approvals_page_is_safe_and_same_origin():
    js = (APPROVALS / "approvals.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert 'credentials: "same-origin"' in js
    assert "window.confirm(" in js
    assert 'const API = "/approvals/api"' in js
    assert '"/bot-chat/release"' in js and '"/bot-chat"' in js
    assert '/dash/chat?resume=' in js
    assert "Authorization" not in js
    html = (APPROVALS / "index.html").read_text(encoding="utf-8")
    assert "/approvals/approvals.js" in html
    assert "/approvals/approvals.css" in html
    assert 'id="release-bot-chat"' in html and 'id="open-bot-chat"' in html


def test_console_links_to_approvals():
    html = (DEPLOY_ROOT / "console" / "www" / "index.html").read_text(encoding="utf-8")
    assert 'href="/approvals/"' in html
    assert "APPROVE &lt;id&gt;" not in html

def test_public_https_belongs_to_the_ai_platform(rendered):
    # :443/:80 are the AI platform's; the Athena console lives on its own :9443 entry point.
    doc, _, _ = rendered
    routers = doc["http"]["routers"]
    public = {name: r for name, r in routers.items() if "websecure" in r.get("entryPoints", [])}
    assert set(public) == {"aiplatform", "aiplatform-root", "hermes-pointer"}
    # The app is built with basePath /aiplatform: keep the path, and send `/` there.
    assert public["aiplatform"]["rule"] == "PathPrefix(`/aiplatform`)"
    assert public["aiplatform"]["service"] == "aiplatform"
    root = public["aiplatform-root"]
    assert root["rule"] == "Path(`/`)" and root["middlewares"] == ["aiplatform-root-redirect"]
    redirect = doc["http"]["middlewares"]["aiplatform-root-redirect"]["redirectRegex"]
    assert redirect["replacement"] == "https://${1}/aiplatform/"
    # /hermes only points at the console; the SPA cannot live under a prefix.
    pointer = public["hermes-pointer"]
    assert pointer["rule"] == "PathPrefix(`/hermes`)" and pointer["middlewares"] == ["hermes-redirect"]
    assert doc["http"]["middlewares"]["hermes-redirect"]["redirectRegex"]["replacement"] == "https://${1}:9443/"
    assert doc["http"]["services"]["aiplatform"]["loadBalancer"]["servers"] == [
        {"url": "http://host.docker.internal:3001"}
    ]
    for name, router in routers.items():
        entries = router["entryPoints"]
        assert entries in (["websecure"], ["hermes"], ["web"]), name
        if name.startswith("bots-") and name.endswith("-http"):
            assert entries == ["web"]  # plain-HTTP /bots/ stays reachable for Desktop
    assert routers["athena"]["entryPoints"] == ["hermes"]
    assert routers["dash"]["entryPoints"] == ["hermes"]
    assert routers["fleet"]["entryPoints"] == ["hermes"]


def test_traefik_entry_points_and_compose_publish_9443():
    static = yaml.safe_load((DEPLOY_ROOT / "edge" / "traefik.yml").read_text(encoding="utf-8"))
    assert {k: v["address"] for k, v in static["entryPoints"].items()} == {
        "web": ":80", "websecure": ":443", "hermes": ":9443",
    }
    compose = yaml.safe_load((DEPLOY_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    edge_ports = compose["services"]["office-edge"]["ports"]
    assert any(p.endswith(":9443") and "HERMES_CONSOLE_HERMES_PUBLISH" in p for p in edge_ports)
    assert any(p.endswith(":443") and "HERMES_CONSOLE_TLS_PUBLISH" in p for p in edge_ports)
    assert compose["services"]["office-gateway"]["environment"]["OFFICE_CONSOLE_URL"].endswith(":9443}")


def test_edge_routes_do_not_duplicate_the_ai_platform_route():
    lines = (DEPLOY_ROOT / "edge" / "edge-routes").read_text(encoding="utf-8").splitlines()
    paths = [line.split()[1] for line in lines if line.strip() and not line.lstrip().startswith("#")]
    assert paths and "/aiplatform/" not in paths
    assert "/" not in paths
