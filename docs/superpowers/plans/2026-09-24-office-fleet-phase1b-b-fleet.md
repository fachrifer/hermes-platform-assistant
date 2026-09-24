# Office Fleet Phase 1b-B — Fleet (configs, skills, compose, console, rollout) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the seven Hermes agents on `v2026.9.21` with MCP-only tools, thinking off, hard brakes, one short skill each, human approvals in the console, and deploy that to the Lab VM with a rollback path.

**Architecture:** Every agent mounts its `config.yaml` read-only at `/opt/data/config.yaml` (native `${VAR}` expansion, no stamping), talks to `office-gateway` only through `mcp_servers.office`, and has `terminal`/A2A/Kanban/cron/office-watch removed. The supervisor delegates with Bot Mode `message_agent` to `peer-<role>` targets. Traefik serves an Approvals page and the approver API on HTTPS behind basic auth. Rollout uses fresh `_v2` volumes so the old stack can be restored untouched.

**Tech Stack:** Hermes Agent `v2026.9.21`, Docker Compose, Traefik v3.3, nginx (static console), bash + python3 (VM scripts), pytest (Docker runner from Plan 1b-A).

**Depends on:** Plan 1b-A complete (gateway `/mcp`, approvals API, tokens renamed).

## Global Constraints

- Roles/services: `hermes-agent` (supervisor, dashboard `:9119`), `hermes-lab-host` `:9121`, `hermes-vector` `:9122`, `hermes-cluster-gpu` `:9123`, `hermes-llm` `:9124`, `hermes-obs` `:9125`, `hermes-ingress` `:9126` (host port → container `9119`).
- Env dirs: `hermes/{supervisor,lab-host,ingress,llm,cluster-gpu,vector,obs}/.env`.
- Per-agent env keys: `OFFICE_GATEWAY_TOKEN`, `OFFICE_LLM_API_KEY`, `API_SERVER_KEY`, `HERMES_DASHBOARD_SESSION_TOKEN`; supervisor also `HERMES_PEER_PEER_<ROLE>_KEY` (six). No `OPENAI_*`, no `A2A_*` in agent envs.
- `models.env`: `OFFICE_LLM_BASE_URL` only.
- Thinking off on every agent: `agent.reasoning_effort: none` + `providers.office-litellm.extra_body.chat_template_kwargs.enable_thinking: false`.
- Toolsets: `platform_toolsets` for `cli, tui, api_server, gui, desktop, dashboard, web` = `[mcp-office, skills]`; `agent.disabled_toolsets` = `[terminal, file, web, browser, code_execution, delegation, memory, session_search, todo, kanban, clarify, a2a, vision, image_gen, tts, cronjob]`.
- Never print secret values. Never overwrite a VM env file without a timestamped backup next to it. Do not use `scripts/deploy.sh` (it pushes PC env files over the Lab's).
- SSH to the VM only with `-i ~/.ssh/id_ed25519_hermes_lab` (key auth). The user installs the key once.
- Commit with `git -c user.name=fachrifer -c user.email=fferdianachmad@gmail.com commit …`.

## Test command

```powershell
docker run --rm -v "${PWD}:/src" -w /src office-gw-test python -m pytest -q -p no:cacheprovider <paths>
```

Tests that parse YAML need `pyyaml`; add `pyyaml==6.0.2` to `Dockerfile.office-gateway-test` in Task B2 and rebuild.

## File map

| File | Change |
|---|---|
| `deploy/office-assistant/scripts/hermes-bot-mode-marker-cont-init.sh` | New (from `spike/`), also writes `.no-bundled-skills` |
| `deploy/office-assistant/hermes/<role>/config.yaml` | Rewritten ×7; `edge/` → `ingress/`, `llm-edge/` → `llm/` |
| `deploy/office-assistant/hermes/<role>/.env.example` | Rewritten ×7 |
| `deploy/office-assistant/models.env.example` | `OFFICE_LLM_BASE_URL` |
| `deploy/office-assistant/skills/<role>/SKILL.md` | Rewritten ×7; `edge/` → `ingress/`, `llm-edge/` → `llm/`; `office-platform/` deleted |
| `deploy/office-assistant/docker-compose.yml` | Rewritten agent services, new volumes, gateway env |
| `deploy/office-assistant/edge/traefik-dynamic/core.yml.template` | Approvals routers + middlewares |
| `deploy/office-assistant/scripts/render-traefik-core.sh` | Approver token + htpasswd users; new bot roles |
| `deploy/office-assistant/console/www/approvals/{index.html,approvals.js}` | New page |
| `deploy/office-assistant/console/www/{app.js,ops-map.js}` | Agent ids `ingress`, `llm` |
| `deploy/office-assistant/scripts/{lib.sh,pair-env.sh,desktop-connect.sh}` | New roles, peer keys |
| `deploy/office-assistant/scripts/migrate-env-phase1b.sh` | New (runs on VM) |
| `deploy/office-assistant/scripts/ship-phase1b.ps1` | New (runs on PC) |
| Removed | `scripts/office-gw-*.sh`, `scripts/office-watch-*`, `scripts/s6-*`, `scripts/patch-hermes-airgap-provider.py`, `scripts/hermes-airgap-provider-cont-init.sh`, `scripts/apply-office-llm-config.py`, `scripts/hermes-office-llm-cont-init.sh`, `scripts/hermes-supervisor-cron-init.sh`, `hermes/supervisor/cron.*`, `hermes/config.yaml`, `hermes/.env.example`, `tests/test_apply_office_llm_config.py` |
| Tests | `tests/test_office_fleet_configs.py` (new), `tests/test_office_fleet_skills.py` (new), `tests/test_office_fleet_compose.py` (rewrite), `tests/test_office_fleet_scripts.py` (trim to surviving scripts) |

---

### Task B1: Bot Mode marker cont-init

**Files:** Create `deploy/office-assistant/scripts/hermes-bot-mode-marker-cont-init.sh`; delete `deploy/office-assistant/spike/bot-mode-marker-cont-init.sh` and point `spike/docker-compose.spike.yml` at the new path.

- [ ] **Step 1: Write the script**

<!-- file: deploy/office-assistant/scripts/hermes-bot-mode-marker-cont-init.sh -->
```sh
#!/command/with-contenv sh
set -eu
HOME_DIR=/opt/data
MARKER="$HOME_DIR/profile.yaml"
if [ ! -f "$MARKER" ]; then
  printf 'ui_meta:\n  hermes-bots: {}\n' > "$MARKER"
elif ! grep -q 'hermes-bots' "$MARKER"; then
  echo "bot-mode-marker: $MARKER exists without hermes-bots; refusing to edit" >&2
  exit 1
fi
if [ ! -f "$HOME_DIR/.no-bundled-skills" ]; then
  printf 'Office fleet: bundled skills are not seeded.\n' > "$HOME_DIR/.no-bundled-skills"
fi
owner="$(stat -c %u:%g "$HOME_DIR")"
chown "$owner" "$MARKER" "$HOME_DIR/.no-bundled-skills"
```

- [ ] **Step 2: Commit** (`chore(fleet): bot mode marker cont-init with bundled-skills opt-out`)

---

### Task B2: Agent configs and invariants test

**Files:** Rewrite `hermes/{supervisor,lab-host,vector,cluster-gpu,obs}/config.yaml`; `git mv hermes/edge hermes/ingress`, `git mv hermes/llm-edge hermes/llm` then rewrite; delete `hermes/config.yaml`, `hermes/.env.example`, `hermes/office-gateway.openapi.json` (if Plan A left it), `hermes/supervisor/cron.*`. Create `tests/test_office_fleet_configs.py`. Add `pyyaml==6.0.2` to `Dockerfile.office-gateway-test`.

- [ ] **Step 1: Write the failing invariants test**

<!-- file: tests/test_office_fleet_configs.py -->
```python
from pathlib import Path

import pytest
import yaml

HERMES = Path(__file__).resolve().parents[1] / "deploy" / "office-assistant" / "hermes"
ROLES = ("supervisor", "lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs")
SPECIALISTS = ROLES[1:]
SURFACES = ("cli", "tui", "api_server", "gui", "desktop", "dashboard", "web")
MUST_DISABLE = {"terminal", "file", "web", "browser", "code_execution", "delegation", "memory",
                "session_search", "todo", "kanban", "clarify", "a2a"}


def _cfg(role):
    return yaml.safe_load((HERMES / role / "config.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("role", ROLES)
def test_brakes_and_thinking(role):
    cfg = _cfg(role)
    agent = cfg["agent"]
    assert agent["name"] == role
    assert agent["max_turns"] == 6
    assert agent["api_max_retries"] == 1
    assert agent["reasoning_effort"] == "none"
    assert "offline" not in agent
    assert MUST_DISABLE <= set(agent["disabled_toolsets"])
    provider = cfg["providers"]["office-litellm"]
    assert provider["key_env"] == "OFFICE_LLM_API_KEY"
    assert provider["api"] == "${OFFICE_LLM_BASE_URL}"
    assert provider["request_timeout_seconds"] == 60
    assert provider["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert cfg["model"] == {"default": "qwen3.8-fast", "provider": "office-litellm"}
    guard = cfg["tool_loop_guardrails"]
    assert guard["hard_stop_enabled"] is True
    assert set(guard["hard_stop_after"].values()) == {2}


@pytest.mark.parametrize("role", ROLES)
def test_mcp_only_tools_on_every_surface(role):
    cfg = _cfg(role)
    office = cfg["mcp_servers"]["office"]
    assert office["url"] == "http://office-gateway:8080/mcp"
    assert office["headers"] == {"Authorization": "Bearer ${OFFICE_GATEWAY_TOKEN}"}
    assert (office["timeout"], office["connect_timeout"]) == (15, 5)
    assert set(cfg["mcp_servers"]) == {"office"}
    assert set(cfg["platform_toolsets"]) == set(SURFACES)
    for surface in SURFACES:
        assert cfg["platform_toolsets"][surface] == ["mcp-office", "skills"]
    assert cfg["skills"]["auto_load"] == [f"office-{role}"]
    assert cfg["mcp"]["discovery_concurrency"] == 1


@pytest.mark.parametrize("role", ROLES)
def test_airgap_settings(role):
    cfg = _cfg(role)
    assert cfg["model_catalog"] == {"enabled": False}
    override = cfg["model_overrides"]["custom:office-litellm"]["qwen3.8-fast"]
    assert override["supports_reasoning"] is False and override["supports_tools"] is True
    assert isinstance(override["context_window"], int) and override["context_window"] >= 32768
    assert cfg["auxiliary"]["title_generation"]["model_upgrade_enabled"] is False
    assert cfg["network"]["force_ipv4"] is True
    assert cfg["updates"]["check"] is False
    assert cfg["browser"]["backend"] == "off"
    assert cfg["security"]["allow_lazy_installs"] is False
    assert cfg["gateway"]["platforms"]["api_server"]["enabled"] is True
    assert "a2a" not in cfg["gateway"]["platforms"]
    assert "cron" not in cfg and "kanban" not in cfg and "a2a_agents" not in cfg


def test_supervisor_peers():
    cfg = _cfg("supervisor")
    assert cfg["bot_peers"] == {f"peer-{r}": {"url": f"http://hermes-{r}:8642"} for r in SPECIALISTS}
    assert cfg["agent"].get("bot_mode_protocol", True) is True


@pytest.mark.parametrize("role", SPECIALISTS)
def test_specialists(role):
    cfg = _cfg(role)
    assert cfg["agent"]["bot_mode_protocol"] is False
    assert cfg["agent"]["run_budget_seconds"] == 180
    assert "bot_peers" not in cfg
    assert cfg["compression"]["threshold_tokens"] == 32000


def test_old_role_dirs_gone():
    assert not (HERMES / "edge").exists() and not (HERMES / "llm-edge").exists()
    assert not (HERMES / "config.yaml").exists()
```

- [ ] **Step 2: Run to verify failure** (rebuild the test image first with `pyyaml`). Expected: FAIL (old configs).

- [ ] **Step 3: Write the supervisor config**

<!-- file: deploy/office-assistant/hermes/supervisor/config.yaml -->
```yaml
# Supervisor (Athena). Tools: MCP fleet_status + skills; delegates with message_agent.
agent:
  name: supervisor
  build_wait_timeout: 30
  api_max_retries: 1
  max_turns: 6
  reasoning_effort: none
  disabled_toolsets: [terminal, file, web, browser, code_execution, delegation, memory, session_search, todo, kanban, clarify, a2a, vision, image_gen, tts, cronjob]

model:
  default: qwen3.8-fast
  provider: office-litellm

providers:
  office-litellm:
    api: ${OFFICE_LLM_BASE_URL}
    key_env: OFFICE_LLM_API_KEY
    transport: chat_completions
    request_timeout_seconds: 60
    extra_body:
      chat_template_kwargs:
        enable_thinking: false

mcp_servers:
  office:
    url: http://office-gateway:8080/mcp
    headers:
      Authorization: "Bearer ${OFFICE_GATEWAY_TOKEN}"
    timeout: 15
    connect_timeout: 5
mcp:
  discovery_concurrency: 1

platform_toolsets:
  cli: [mcp-office, skills]
  tui: [mcp-office, skills]
  api_server: [mcp-office, skills]
  gui: [mcp-office, skills]
  desktop: [mcp-office, skills]
  dashboard: [mcp-office, skills]
  web: [mcp-office, skills]

skills:
  auto_load: [office-supervisor]

tool_loop_guardrails:
  warnings_enabled: true
  hard_stop_enabled: true
  warn_after: {exact_failure: 1, same_tool_failure: 1, idempotent_no_progress: 1}
  hard_stop_after: {exact_failure: 2, same_tool_failure: 2, idempotent_no_progress: 2}

model_catalog:
  enabled: false
model_overrides:
  custom:office-litellm:
    qwen3.8-fast: {context_window: 131072, supports_tools: true, supports_reasoning: false}
auxiliary:
  title_generation:
    model_upgrade_enabled: false
network:
  force_ipv4: true
updates:
  check: false
browser:
  backend: "off"
security:
  allow_lazy_installs: false

dashboard:
  enabled: true
  host: 0.0.0.0
  port: 9119
  public_url: ${HERMES_DASHBOARD_PUBLIC_URL}
  trusted_proxies:
    - "172.16.0.0/12"
    - "172.30.80.0/24"
  basic_auth:
    username: ${HERMES_DASHBOARD_USERNAME}
    password: ${HERMES_DASHBOARD_PASSWORD}

gateway:
  platforms:
    api_server:
      enabled: true

# Keys: HERMES_PEER_PEER_<ROLE>_KEY in hermes/supervisor/.env (= each specialist API_SERVER_KEY).
bot_peers:
  peer-lab-host:
    url: http://hermes-lab-host:8642
  peer-ingress:
    url: http://hermes-ingress:8642
  peer-llm:
    url: http://hermes-llm:8642
  peer-cluster-gpu:
    url: http://hermes-cluster-gpu:8642
  peer-vector:
    url: http://hermes-vector:8642
  peer-obs:
    url: http://hermes-obs:8642
```

`context_window: 131072` is the spike value; Task B8 replaces it on the VM with the literal `max_input_tokens` from LiteLLM `/model/info` (all seven files) before `up`.

- [ ] **Step 4: Write the six specialist configs**

Each specialist file is the supervisor file with these differences (and nothing else):

| Key | Specialist value |
|---|---|
| header comment | `# <Role> specialist. Tools: MCP office (<role> catalog) + skills.` |
| `agent.name` | role (`lab-host`, `ingress`, `llm`, `cluster-gpu`, `vector`, `obs`) |
| `agent.bot_mode_protocol` | `false` (after `reasoning_effort`) |
| `agent.run_budget_seconds` | `180` |
| `skills.auto_load` | `[office-<role>]` |
| `compression` | `threshold_tokens: 32000` (new top-level block after `skills`) |
| `bot_peers` | absent (drop block and its comment) |

- [ ] **Step 5: Delete retired files** listed under Files; `git mv` the two renamed dirs first so history follows.

- [ ] **Step 6: Run the invariants test.** Expected: all PASS.

- [ ] **Step 7: Commit** (`feat(fleet): MCP-only agent configs with brakes, thinking off, all surfaces pinned`)

---

### Task B3: Skills (one per role) and skills test

**Files:** Rewrite `skills/<role>/SKILL.md` for all seven (`git mv skills/edge skills/ingress`, `git mv skills/llm-edge skills/llm`); delete `skills/office-platform/`, the `.gitkeep` files next to SKILL.md. Create `tests/test_office_fleet_skills.py`.

Each skill has frontmatter `name: office-<role>`, `description`, `version: 1.0.0`, and these sections, all in English (replies to the user are Indonesian — supervisor only):

- **Supervisor:** identity (Athena, coordinator; no tools besides `fleet_status` and `message_agent`); routing table domain → `peer-<role>` with one line of scope each; rules: status-only → `fleet_status` and answer; otherwise message at most two specialists, at most once each per user request, never re-send (including after a failed/timeout/`target_busy` notification); after sending, tell the user who is checking and end the turn; relay results when notifications arrive; never use relay-roster handles (`@name@…`); reply format (Indonesian headline, one line per domain, pending approvals with `https://10.216.4.80/approvals/`); failure → domain `unknown` + reason.
- **Specialists:** identity + scope (what the domain is, hosts/IPs from spec §11.4 relevant to that role); tool list with one line each; procedure: 1–3 tool calls, then answer; never call the same tool with the same arguments twice; error reaction per category (report and stop); writes: only `propose_*`, report `PROPOSED <action_id> <summary>`, never claim it was executed, `action_status` at most once; reply format (the 6-line `STATUS/FINDINGS/CAUSE/NEXT/PROPOSED` block, ≤ 12 lines). When opened directly by a human (Desktop), answer in the human's language with the same facts.

- [ ] **Step 1: Write the failing test**

<!-- file: tests/test_office_fleet_skills.py -->
```python
import re
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "deploy" / "office-assistant" / "skills"
ROLES = ("supervisor", "lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs")
BANNED = re.compile(r"curl|office-gw|terminal|AUTOHEAL|auto_execute|a2a_call|kanban|OFFICE_GATEWAY_URL|SNAPSHOT:|FAST:", re.I)
TOOLS = {
    "supervisor": ["fleet_status", "message_agent"],
    "lab-host": ["list_containers", "inspect_container", "tail_logs", "host_resources", "list_host_services", "propose_restart", "action_status"],
    "ingress": ["list_routes", "edge_status", "tls_status", "tail_traefik_logs", "validate_route_change", "propose_route_change", "propose_route_rollback", "action_status"],
    "llm": ["llm_status", "list_models"],
    "cluster-gpu": ["k8s_get", "mig_map", "gpu_usage"],
    "vector": ["vector_status"],
    "obs": ["metrics_query", "grafana_links"],
}


def _text(role):
    return (SKILLS / role / "SKILL.md").read_text(encoding="utf-8")


def test_exactly_seven_skill_dirs():
    assert sorted(p.name for p in SKILLS.iterdir() if p.is_dir()) == sorted(ROLES)


@pytest.mark.parametrize("role", ROLES)
def test_frontmatter_size_and_banned_words(role):
    text = _text(role)
    assert text.startswith("---\n")
    front = text.split("---\n", 2)[1]
    assert f"name: office-{role}" in front
    assert "description:" in front
    assert len(text) <= 6000, len(text)
    assert not BANNED.search(text), BANNED.search(text).group(0)


@pytest.mark.parametrize("role", ROLES)
def test_mentions_every_tool(role):
    text = _text(role)
    for tool in TOOLS[role]:
        assert tool in text, tool


def test_supervisor_routing_and_rules():
    text = _text("supervisor")
    for role in ROLES[1:]:
        assert f"peer-{role}" in text
    assert "at most two" in text.lower() and "never re-send" in text.lower()
    assert "/approvals/" in text


@pytest.mark.parametrize("role", ROLES[1:])
def test_specialist_reply_format(role):
    text = _text(role)
    for field in ("STATUS:", "FINDINGS:", "CAUSE:", "NEXT:"):
        assert field in text
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Write the seven SKILL.md files** per the section list above.

- [ ] **Step 4: Run the test.** Expected: all PASS.

- [ ] **Step 5: Commit** (`feat(fleet): one short MCP skill per role`)

---

### Task B4: Compose

**Files:** Rewrite `deploy/office-assistant/docker-compose.yml`; rewrite `tests/test_office_fleet_compose.py`; update `docker-compose.local.yml` service names if it overrides agents.

Required shape:

- Anchors: `x-hermes-airgap-hosts` (unchanged list), `x-hermes-env` with `HERMES_HOME=/opt/data`, `TZ=Asia/Jakarta`, dashboard vars (the existing `x-hermes-bot-dashboard` content, including `API_SERVER_*`), `HTTPS_PROXY`/`HTTP_PROXY`/`https_proxy`/`http_proxy` = `http://127.0.0.1:9`, `NO_PROXY`/`no_proxy` = `localhost,127.0.0.1,10.0.0.0/8,172.16.0.0/12,office-gateway,hermes-agent,hermes-lab-host,hermes-ingress,hermes-llm,hermes-cluster-gpu,hermes-vector,hermes-obs`.
- `x-hermes-agent` anchor: `image: ${HERMES_IMAGE:-nousresearch/hermes-agent:v2026.9.21}`, `restart: unless-stopped`, `networks: [office]`, `depends_on: [office-gateway]`, `extra_hosts: *hermes-airgap-hosts`, `command: ["gateway", "run"]`.
- Each agent: `<<: *hermes-agent`, `env_file: [./models.env, ./hermes/<role>/.env]`, `environment: {<<: *hermes-env, HERMES_DASHBOARD_PUBLIC_URL: ...}`, `ports` as today, volumes exactly: `hermes_<role>_v2:/opt/data`, `./hermes/<role>/config.yaml:/opt/data/config.yaml:ro`, `./skills/<role>:/opt/data/skills/office-<role>:ro`, `./scripts/hermes-cli-symlink-cont-init.sh:/etc/cont-init.d/08-hermes-cli:ro`, `./scripts/hermes-bot-mode-marker-cont-init.sh:/etc/cont-init.d/30-bot-mode-marker:ro`.
- Supervisor `hermes-agent` additionally `depends_on` all six specialists and keeps `HERMES_DASHBOARD_PUBLISH` port mapping.
- Volume names: `hermes_supervisor_v2`, `hermes_lab_host_v2`, `hermes_ingress_v2`, `hermes_llm_v2`, `hermes_cluster_gpu_v2`, `hermes_vector_v2`, `hermes_obs_v2`, plus `office_gateway_data` (kept). Old volumes are not declared (they stay on disk for rollback).
- `office-gateway`: keep everything, add `OFFICE_EDGE_BACKUP_DIR: /edge/backups`, `OFFICE_CONSOLE_URL: https://${HERMES_CONSOLE_TLS_HOST:-10.216.4.80}`, and `/run/dbus/system_bus_socket:/run/dbus/system_bus_socket:ro`, `/proc:/host/proc:ro` only if `proc_ops` reads `/host/proc` (check `ProcOps.__init__` default; mount accordingly).
- `office-edge`: `depends_on` renamed services; add `./edge/approvers.htpasswd:/etc/traefik/approvers.htpasswd:ro`.
- Removed: `A2A_*`, `HERMES_OFFLINE`, `OFFICE_HERMES_STAMP_CONFIG`, `OFFICE_FAST_ROLE`, `OFFICE_GATEWAY_URL`, NPM/UV env, `kanban_data`, all `office-gw-*`/`office-watch-*`/`s6-*`/airgap/office-llm/cron mounts.

- [ ] **Step 1: Write the failing compose test** — load YAML with `yaml.safe_load` (anchors resolve), then assert: the seven agent service names; every agent image tag `v2026.9.21`; config mounted at `/opt/data/config.yaml:ro`; skill mount path `/opt/data/skills/office-<role>`; marker cont-init mounted; no volume string containing `office-gw`, `office-watch`, `s6-`, `airgap`, `office-llm`, `cron`, `kanban`; no env key starting `A2A_` or equal to `HERMES_OFFLINE`; `HTTPS_PROXY == "http://127.0.0.1:9"` and every agent service name is in `NO_PROXY`; ports 9121–9126 map to the right services; `office-gateway` env has `OFFICE_EDGE_BACKUP_DIR`; office-edge mounts `approvers.htpasswd`; volumes set equals the eight names above.

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Rewrite compose.**
- [ ] **Step 4: Run the test, plus `docker compose -f deploy/office-assistant/docker-compose.yml config -q` with a throwaway env (`HERMES_DASHBOARD_USERNAME=x` etc.) to catch syntax errors.** Expected: PASS, exit 0.
- [ ] **Step 5: Commit** (`feat(fleet): compose on v2026.9.21 with MCP-only agents and v2 volumes`)

---

### Task B5: Console Approvals page and Traefik routing

**Files:** Create `console/www/approvals/index.html`, `console/www/approvals/approvals.js`; modify `edge/traefik-dynamic/core.yml.template`, `scripts/render-traefik-core.sh`; update `console/www/app.js`/`ops-map.js` agent ids (`edge`→`ingress`, `llm-edge`→`llm`); add a link to `/approvals/` in `console/www/index.html`. Extend `tests/test_office_fleet_scripts.py` with a render test.

- [ ] **Step 1: Traefik template additions**

Middlewares:

```yaml
    approver-auth:
      basicAuth:
        usersFile: /etc/traefik/approvers.htpasswd
        headerField: X-Approver
        removeHeader: true
        realm: "Hermes approvals"
    approver-token:
      headers:
        customRequestHeaders:
          Authorization: "Bearer __OFFICE_GATEWAY_TOKEN_APPROVER__"
    approvals-api-rewrite:
      replacePathRegex:
        regex: "^/approvals/api(.*)"
        replacement: "/v1/approvals$1"
```

Routers (websecure only; the existing `http-to-https` router already redirects plain HTTP):

```yaml
    approvals-api:
      rule: "PathPrefix(`/approvals/api`)"
      entryPoints: [websecure]
      tls: {}
      priority: 120
      middlewares: [approver-auth, approver-token, approvals-api-rewrite]
      service: office-gateway
    approvals-page:
      rule: "PathPrefix(`/approvals`)"
      entryPoints: [websecure]
      tls: {}
      priority: 115
      middlewares: [approver-auth]
      service: office-www
```

- [ ] **Step 2: Render script** — read `OFFICE_GATEWAY_TOKEN_APPROVER` like the supervisor token, replace `__OFFICE_GATEWAY_TOKEN_APPROVER__`, warn (not fail) if missing; `BOT_ROLES` → `lab-host, vector, cluster-gpu, llm, obs, ingress` with services `hermes-<role>`.

- [ ] **Step 3: Approvals page** — `index.html` (table of pending actions: created, role, action, target, reason, summary, diff in `<pre>`, Approve / Reject buttons; a "Recent" table below), `approvals.js` (fetch `/approvals/api?status=pending` and `/approvals/api?status=recent&limit=30` every 10 s; POST `/approvals/api/<id>/approve|reject` with `credentials: "same-origin"`; `confirm()` before approve; show the returned status; escape all text with `textContent`, never `innerHTML` for data).

- [ ] **Step 4: Render test** — run `render-traefik-core.sh` against a temp env with both tokens (bash is available in the test image? if not, run the embedded python directly) and assert the output contains both routers, `headerField: X-Approver`, the approver bearer, and `/bots/ingress`, and no `__...__` placeholders. If bash is unavailable in `office-gw-test`, add `bash` via `apt-get` in the test Dockerfile.

- [ ] **Step 5: Commit** (`feat(console): approvals page behind basic auth with approver token injection`)

---

### Task B6: Scripts cleanup and env migration

**Files:** delete the "Removed" scripts in the file map and their tests; modify `scripts/lib.sh` (`HERMES_ROLES="supervisor lab-host ingress llm cluster-gpu vector obs"`, default image `v2026.9.21`), `scripts/pair-env.sh` (maps below), `scripts/desktop-connect.sh` (role names on 9124/9126), `.env.example`, `hermes/<role>/.env.example`, `models.env.example`. Create `scripts/migrate-env-phase1b.sh`, `scripts/ship-phase1b.ps1`.

- [ ] **Step 1: `pair-env.sh` maps**

```python
gateway_map = {
    "supervisor": "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "lab-host": "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "ingress": "OFFICE_GATEWAY_TOKEN_INGRESS",
    "llm": "OFFICE_GATEWAY_TOKEN_LLM",
    "cluster-gpu": "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "vector": "OFFICE_GATEWAY_TOKEN_VECTOR",
    "obs": "OFFICE_GATEWAY_TOKEN_OBS",
}
peer_map = {r: f"HERMES_PEER_PEER_{r.upper().replace('-', '_')}_KEY"
            for r in ("lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs")}
```

Remove the A2A map and the `A2A_BEARER_TOKEN` upsert.

- [ ] **Step 2: `migrate-env-phase1b.sh`** (bash + embedded python3, idempotent, prints key names only):

1. Refuse to run unless `docker-compose.yml` in cwd contains `hermes-ingress` (new tree already shipped).
2. `ts=$(date +%Y%m%d-%H%M%S)`; copy `.env`, `models.env`, every `hermes/*/.env` to `<file>.pre-phase1b-$ts`.
3. `hermes/edge` → `hermes/ingress`, `hermes/llm-edge` → `hermes/llm`: move `.env` if the new one is missing.
4. Root `.env`: rename `OFFICE_GATEWAY_TOKEN_EDGE` → `_INGRESS`; add `OFFICE_GATEWAY_TOKEN_APPROVER` (random 32-byte urlsafe) if missing; set `HERMES_IMAGE=nousresearch/hermes-agent:v2026.9.21`; drop `OFFICE_WRITE_*`, `OFFICE_READ_LOGS_*`, `OFFICE_VECTOR_ENV`, `OFFICE_AUTOHEAL_DENY`; if `OFFICE_SERVICE_URLS` is set, rewrite any `:9900/.well-known/agent.json` entries to `:8642/health` and rename `hermes-edge`/`hermes-llm-edge` entries.
5. `models.env`: `OFFICE_LLM_BASE_URL` = existing `OPENAI_BASE_URL`; remove `OPENAI_*`.
6. Each agent `.env`: `OFFICE_LLM_API_KEY` = existing `OPENAI_API_KEY` (fail with the role name if empty); remove `OPENAI_*`, `A2A_*`; ensure `API_SERVER_KEY`.
7. Supervisor `.env`: `HERMES_PEER_PEER_<ROLE>_KEY` = that specialist's `API_SERVER_KEY`; remove old `HERMES_PEER_<ROLE>_KEY` and `A2A_TOKEN_*`.
8. Run `pair-env.sh` (propagates renamed gateway tokens and the session token).
9. Approver login: if `edge/approvers.htpasswd` is missing, create user `${OFFICE_APPROVER_USER:-timai}` with a random password via `openssl passwd -apr1`, write `approver_user=`/`approver_password=` lines into `.local-login` (mode 600), `chmod 644` the htpasswd (Traefik reads it). Print `approver login stored in .local-login` — never the password.
10. Print the list of changed key names per file.

- [ ] **Step 3: `ship-phase1b.ps1`** (PC side):

```powershell
param([string]$Remote = "timai@10.216.4.80", [string]$Dir = "/home/timai/hermes-assistant")
$ErrorActionPreference = "Stop"
$key = "$env:USERPROFILE\.ssh\id_ed25519_hermes_lab"
$repo = (Resolve-Path "$PSScriptRoot\..\..\..").Path
$out = Join-Path $repo "deploy\office-assistant\images"
New-Item -ItemType Directory -Force $out | Out-Null
docker build -f "$repo\Dockerfile.office-gateway" -t office-gw:local $repo
docker save nousresearch/hermes-agent:v2026.9.21 office-gw:local -o "$out\fleet-p1b-images.tar"
$tree = Join-Path $out "fleet-p1b-tree.tgz"
tar -czf $tree -C "$repo\deploy\office-assistant" --exclude=.env --exclude=models.env --exclude="hermes/*/.env" --exclude=images --exclude=spike --exclude=.local-login --exclude="*.pre-phase1b-*" --exclude=__pycache__ .
tar -czf "$out\fleet-p1b-gateway.tgz" -C $repo office_gateway Dockerfile.office-gateway
scp -i $key "$out\fleet-p1b-images.tar" "$out\fleet-p1b-tree.tgz" "$out\fleet-p1b-gateway.tgz" "${Remote}:${Dir}/images/"
```

The images tarball is uncompressed (the PC has no gzip); `docker load -i` reads it directly.

- [ ] **Step 4: Update the scripts test** — keep tests for surviving scripts; add a test that runs `migrate-env-phase1b.sh` on a temp tree containing old-style env files (fake values) and asserts the resulting key names (no `OPENAI_*`/`A2A_*`, peer keys equal specialist `API_SERVER_KEY`, backups exist, the password is not in stdout). Needs `bash`, `python3`, `openssl` in the test image — add `bash openssl` via apt in `Dockerfile.office-gateway-test`.

- [ ] **Step 5: Run the whole suite** (`tests`). Expected: all PASS.

- [ ] **Step 6: Commit** (`feat(deploy): phase1b env migration and PC ship script; remove retired scripts`)

---

### Task B7: Local full-fleet smoke on the PC

Uses a throwaway env tree under `deploy/office-assistant/.smoke/` (gitignored) and `docker compose -p office-smoke` with ports on `127.0.0.1`.

- [ ] **Step 1:** Generate smoke env files with fake gateway tokens and the user's LiteLLM key from the spike (`.env.spike` is deleted — ask the user to paste the key into `.smoke/hermes-key.txt`, or reuse the existing `OFFICE_LLM_API_KEY` the user provides). `OFFICE_LLM_BASE_URL` = the LiteLLM prod URL used in the spike.
- [ ] **Step 2:** `docker compose -p office-smoke up -d`; wait for all seven `/health` via the gateway `fleet_status`.
- [ ] **Step 3:** Checks (each recorded in the results doc):
  - `hermes tools --summary` in lab-host and supervisor shows only `mcp-office` + `skills` on every surface (pty wrapper from the spike).
  - Supervisor `--oneshot "Status fleet?"` answers from `fleet_status` without `message_agent`.
  - Supervisor `--oneshot "Cek container yang mati di Lab"` sends exactly one `message_agent` to `peer-lab-host` and relays a `STATUS:` reply; count `message_agent` calls in the session DB.
  - Lab-host `--oneshot "restart aiplatform-api"` (fake name in smoke) returns `PROPOSED …`; approve via `curl -u` through Traefik `https://127.0.0.1:<tls>/approvals/api/<id>/approve`; `action_status` shows `succeeded` or `failed` with an audit row carrying the approver.
  - System prompt size: `hermes` context breakdown for a specialist < 4k tokens with `.no-bundled-skills`.
  - Startup: first agent build < 10 s in logs.
- [ ] **Step 4:** `docker compose -p office-smoke down -v`; delete `.smoke/`.
- [ ] **Step 5:** Commit smoke notes into `docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md` under "Phase 1b local smoke".

---

### Task B8: Lab VM rollout and acceptance

Needs the user once: install the SSH key (`type $env:USERPROFILE\.ssh\id_ed25519_hermes_lab.pub | ssh timai@10.216.4.80 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"`, typing their own password).

- [ ] **Step 1: Pre-flight (read-only)** — `ssh … "cd ~/hermes-assistant && docker compose ps --format '{{.Service}} {{.State}}' && docker volume ls --format '{{.Name}}' | grep office_ && df -h ."`. Confirm ≥ 10 GB free.
- [ ] **Step 2: Backup** — `tar -czf ~/hermes-assistant-pre-phase1b-<ts>.tgz --exclude=images -C ~ hermes-assistant` (includes env files; stays on the VM, mode 600).
- [ ] **Step 3: Ship** — run `scripts/ship-phase1b.ps1` on the PC.
- [ ] **Step 4: Stop old stack** — `docker compose down --remove-orphans` (old volumes stay).
- [ ] **Step 5: Unpack** — in `~/hermes-assistant`: remove retired scripts/skills dirs that the new tree dropped (list from the file map), `tar -xzf images/fleet-p1b-tree.tgz`, `tar -xzf images/fleet-p1b-gateway.tgz`, `docker load -i images/fleet-p1b-images.tar`, `chmod +x scripts/*.sh`.
- [ ] **Step 6: Migrate env** — `./scripts/migrate-env-phase1b.sh`.
- [ ] **Step 7: Context window** — `curl -s -H "Authorization: Bearer $OFFICE_LITELLM_MASTER_KEY" <litellm>/model/info` inside the gateway container (python one-liner that prints only `max_input_tokens` for `qwen3.8-fast`); `sed -i` that number into `context_window:` of all seven configs.
- [ ] **Step 8: Start** — `./scripts/deploy-and-start.sh images/does-not-exist` (skips docker load, renders Traefik incl. approvals, `compose up -d`).
- [ ] **Step 9: Acceptance**
  - All containers `running`; gateway `/health`; MCP `tools/list` per role via `docker exec office-hermes-lab-host-1 hermes mcp list` or the contract script.
  - Offline startup: no log line waiting on outbound connections; first agent build < 10 s.
  - Loop scenarios (spec §13.2) from the CLI inside the supervisor container.
  - Approval flow end to end with a harmless target (propose restart of `office-www`), approve in the browser at `https://10.216.4.80/approvals/` (user), check audit.
  - **User checks:** S2 — web Dashboard, resume "Bot Chat", ask a delegating question, see `message_agent` to `peer-lab-host` and the relayed answer. S3 — Hermes Desktop connected to all seven; same question; close Desktop mid-delivery; answer still arrives in the web Dashboard. If S2 fails: stop and re-open the communication decision.
- [ ] **Step 10: Rollback procedure (only if acceptance fails)** — `docker compose down`; restore `~/hermes-assistant-pre-phase1b-<ts>.tgz` over the directory; `./scripts/deploy-and-start.sh images/does-not-exist`. Old volumes are untouched, so the old fleet comes back with its state.
- [ ] **Step 11: Record results** in the spike-results doc ("Phase 1b VM rollout"), commit.
