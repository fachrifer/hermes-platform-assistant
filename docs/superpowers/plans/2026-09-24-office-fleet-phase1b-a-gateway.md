# Office Fleet Phase 1b-A — Gateway (MCP, tool catalog, approvals) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `office_gateway` into the fleet's only tool layer: a role-filtered MCP endpoint at `/mcp` with the Phase 1 tool catalog, a human-only approval flow, and safe route changes with backup and rollback.

**Architecture:** Keep the existing FastAPI app and ops modules. Add `office_gateway/tools/` (one module per domain, all built on the existing `*_ops.py`), a hand-written JSON-RPC MCP endpoint (`office_gateway/mcp_server.py`, the Hermes MCP SDK 2.0.0 client accepts plain `application/json` replies), a new approval state machine in `store.py`/`actions.py` with approver-only REST endpoints, and backup/restore in `edge_ops.py`. Remove auto-execute, AUTOHEAL, the LiteLLM passthrough, the watch endpoints, and vector writes.

**Tech Stack:** Python 3.11, FastAPI 0.115, httpx 0.28, SQLite, pytest (run inside Docker — the operator PC has no Python), `jeepney` for systemd over D-Bus.

**Spec:** `docs/superpowers/specs/2026-09-24-office-fleet-redesign-design.md` §5, §6, §7, §12, §13.1. Spike results: `…-spike-results.md`.

## Global Constraints

- Roles: `supervisor`, `lab-host`, `ingress` (was `edge`), `llm` (was `llm-edge`), `cluster-gpu`, `vector`, `obs`, plus `approver` (console only, never mounted into a Hermes container).
- Token env names: `OFFICE_GATEWAY_TOKEN_{SUPERVISOR,LAB_HOST,INGRESS,LLM,CLUSTER,VECTOR,OBS,APPROVER}`; all must be non-empty and distinct.
- Writes: `lab-host` → `restart_service` (any existing Lab container); `ingress` → `apply_edge_routes`, `rollback_edge_routes`; `obs` → none in Phase 1 (`create_dashboard` is Phase 2). Nothing executes without `POST /v1/approvals/{id}/approve` by the `approver` role with an `X-Approver` header.
- Action states: `pending` → `executing` → `succeeded` | `failed`; `pending` → `rejected` | `expired` (TTL `OFFICE_ACTION_TTL_SECONDS`, default 600).
- Tool result envelope: `{"ok": true, "data": {...}}` or `{"ok": false, "error": {"category", "detail", "valid"?}}`; categories `unreachable`, `timeout`, `forbidden`, `invalid_argument`, `no_metrics`, `not_configured`; hard cap 2,000 characters per serialized result.
- Every upstream call inside a tool: timeout 10 s, no retry. Whole tool call: 12 s.
- Logs and inspect output are redacted (bearer tokens, `sk-…`, JWTs, AWS keys, `user:pass@` URLs, `password=`/`token=`/`api_key=`… values); env values are never returned, only names.
- `route backups`: keep the newest 10; auto-restore when an apply fails after writing.
- Error text and code comments in English; existing Indonesian `ValueError` messages in `config.py` stay as they are.
- Commit with `git -c user.name=fachrifer -c user.email=fferdianachmad@gmail.com commit …`. Never commit `.env*` (except `*.example`), `ca/*`, `certs/*`, kubeconfigs.

## Test command (used by every task)

From `D:\Tim AI - Project\hermes-fleet-sync` in PowerShell:

```powershell
docker run --rm -v "${PWD}:/src" -w /src office-gw-test python -m pytest -q -p no:cacheprovider <paths>
```

`<paths>` is given per step. The image is built in Task 1.

## File map

| File | Responsibility |
|---|---|
| `Dockerfile.office-gateway-test` (new) | Test runner image: gateway requirements + pytest |
| `office_gateway/roles.py` | Roles, REST read routes, per-role write actions |
| `office_gateway/config.py` | Env parsing: renamed tokens, service URLs on `:8642/health`, vector instances, console URL |
| `office_gateway/store.py` | SQLite actions + audit, new state machine |
| `office_gateway/edge_ops.py` | Route parse/render + backup, verified apply, auto-restore, restore, diff |
| `office_gateway/actions.py` | Propose / approve / reject / status; executes approved actions |
| `office_gateway/redact.py` (new) | Secret redaction for log lines |
| `office_gateway/reports/queries.py` (new) | Named PromQL registry |
| `office_gateway/tools/core.py` (new) | `Tool`, `Param`, `ToolContext`, envelope, arg validation, `call_tool` |
| `office_gateway/tools/{common,status,lab_host,ingress,llm,cluster,vector,obs}.py` (new) | Tool definitions per domain |
| `office_gateway/tools/__init__.py` (new) | Registry: `ALL_TOOLS`, `tools_for_role`, `REGISTRY` |
| `office_gateway/mcp_server.py` (new) | `/mcp` JSON-RPC endpoint |
| `office_gateway/app.py` | Wiring; approvals endpoints; removals |
| `office_gateway/docker_ops.py` | `LIST_CAP`, `compact_listing`, inspect env names |
| `office_gateway/autoheal.py` | Deleted |
| `office_gateway/llm_ops.py` | Drop `proxy_litellm_get` |
| `office_gateway/brief.py` | Agent ids `ingress`, `llm` |
| `tests/gw_helpers.py` (new) | `TOKENS`, `make_config`, `auth`, `FakeDockerOps` |
| `tests/integration/mcp_contract.py` (new) | Real MCP SDK client (inside the Hermes image) against a running gateway |

---

### Task 1: Test runner image and baseline

**Files:**
- Create: `Dockerfile.office-gateway-test`
- Modify: `office_gateway/requirements.txt`

**Interfaces:**
- Produces: image `office-gw-test` used by every later task.

- [ ] **Step 1: Add `jeepney` to requirements**

<!-- file: office_gateway/requirements.txt -->
```text
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
pydantic==2.10.4
docker==7.1.0
kubernetes==35.0.0
cryptography==44.0.0
jeepney==0.8.0
```

- [ ] **Step 2: Create the test image**

<!-- file: Dockerfile.office-gateway-test -->
```dockerfile
FROM python:3.11-slim
COPY office_gateway/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt pytest==8.3.4
WORKDIR /src
```

- [ ] **Step 3: Build and record the baseline**

```powershell
docker build -f Dockerfile.office-gateway-test -t office-gw-test .
docker run --rm -v "${PWD}:/src" -w /src office-gw-test python -m pytest -q -p no:cacheprovider tests 2>&1 | Select-Object -Last 15
```

Expected: the suite runs. Record the pass/fail counts in the commit message; tests that need `bash` or `docker` may fail or skip in this image — note which.

- [ ] **Step 4: Commit**

```powershell
git add Dockerfile.office-gateway-test office_gateway/requirements.txt
git commit -m "test(gateway): docker test runner image; add jeepney"
```

---

### Task 2: Roles, config, and test helpers

**Files:**
- Modify: `office_gateway/roles.py` (full rewrite)
- Modify: `office_gateway/config.py`
- Create: `tests/gw_helpers.py`
- Modify: `tests/test_office_gateway_roles.py` (full rewrite)

**Interfaces:**
- Produces: `roles.AGENT_ROLES`, `roles.APPROVER_ROLE`, `roles.ROLES`, `roles.READ_ROUTES`, `roles.WRITE_ACTIONS`, `roles.WRITE_ROLES`, `role_for_token(config, token) -> str | None`, `can_read(role, prefix) -> bool`, `can_write(role) -> bool`, `can_propose(role, action) -> bool`.
- Produces: `GatewayConfig` fields `tokens`, `service_urls`, `vector_instances: dict[str, str]`, `console_url: str`, `edge_backup_dir: str`, and the existing Grafana/metrics/LiteLLM/edge/TLS/db fields. Removed fields: `write_targets`, `log_targets`, `vector_env`, `autoheal_deny`.
- Produces (tests): `gw_helpers.TOKENS`, `make_config(tmp_path, **overrides) -> GatewayConfig`, `auth(role) -> dict`, `FakeDockerOps`.

- [ ] **Step 1: Write the helpers**

<!-- file: tests/gw_helpers.py -->
```python
from __future__ import annotations

from office_gateway.config import GatewayConfig
from office_gateway.docker_ops import DockerOps

TOKENS = {
    "supervisor": "tok-sup",
    "lab-host": "tok-lab",
    "ingress": "tok-ing",
    "llm": "tok-llm",
    "cluster-gpu": "tok-gpu",
    "vector": "tok-vec",
    "obs": "tok-obs",
    "approver": "tok-app",
}


def make_config(tmp_path, **overrides) -> GatewayConfig:
    base = {
        "tokens": dict(TOKENS),
        "service_urls": {"gateway": "http://office-gateway:8080/health"},
        "db_path": str(tmp_path / "gw.db"),
        "console_url": "https://console.test",
    }
    base.update(overrides)
    return GatewayConfig(**base)


def auth(role: str, approver: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {TOKENS[role]}"}
    if approver is not None:
        headers["X-Approver"] = approver
    return headers


def container(name: str, status: str = "running", service: str = "", health=None) -> dict:
    return {
        "name": name,
        "status": status,
        "health": health,
        "image": f"img/{name}:1",
        "compose_service": service,
        "ports": [],
        "networks": [],
        "env_names": ["PATH", "SECRET_TOKEN"],
        "restart_count": 0,
        "started_at": "2026-09-24T00:00:00Z",
    }


class FakeDockerOps(DockerOps):
    def __init__(self, containers=None, logs=None, fail_restart: bool = False):
        super().__init__(client=object())
        self._containers = containers if containers is not None else [
            container("aiplatform-api"),
            container("office-office-edge-1", service="office-edge"),
            container("broken-worker", status="exited"),
        ]
        self._logs = logs or {}
        self._fail_restart = fail_restart
        self.restarted: list[str] = []

    def list_containers(self, all: bool = True) -> dict:
        rows = [dict(c) for c in self._containers]
        running = sum(1 for c in rows if c["status"] == "running")
        return {"containers": rows, "running": running, "total": len(rows), "truncated": False}

    def inspect(self, name: str) -> dict:
        for c in self._containers:
            if c["name"] == name:
                return dict(c)
        raise ValueError(f"container not found: {name}")

    def logs(self, name: str, tail: int = 80) -> dict:
        self.inspect(name)
        lines = list(self._logs.get(name, []))[-tail:]
        return {"name": name, "tail": len(lines), "lines": lines}

    def restart(self, name: str) -> None:
        if self._fail_restart:
            raise RuntimeError("docker down")
        self.inspect(name)
        self.restarted.append(name)
```

- [ ] **Step 2: Write the failing roles/config tests**

<!-- file: tests/test_office_gateway_roles.py -->
```python
import pytest

from gw_helpers import TOKENS, make_config
from office_gateway.config import GatewayConfig
from office_gateway.roles import (
    AGENT_ROLES,
    ROLES,
    WRITE_ACTIONS,
    can_propose,
    can_read,
    can_write,
    role_for_token,
)

ENV = {
    "OFFICE_GATEWAY_TOKEN_SUPERVISOR": "t-sup",
    "OFFICE_GATEWAY_TOKEN_LAB_HOST": "t-lab",
    "OFFICE_GATEWAY_TOKEN_INGRESS": "t-ing",
    "OFFICE_GATEWAY_TOKEN_LLM": "t-llm",
    "OFFICE_GATEWAY_TOKEN_CLUSTER": "t-gpu",
    "OFFICE_GATEWAY_TOKEN_VECTOR": "t-vec",
    "OFFICE_GATEWAY_TOKEN_OBS": "t-obs",
    "OFFICE_GATEWAY_TOKEN_APPROVER": "t-app",
}


def _env(monkeypatch, tmp_path, **extra):
    for key, value in {**ENV, **extra}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OFFICE_GATEWAY_DB_PATH", str(tmp_path / "gw.db"))


def test_roles_are_renamed_and_approver_is_not_an_agent():
    assert {"ingress", "llm", "approver"} <= ROLES
    assert not {"edge", "llm-edge"} & ROLES
    assert "approver" not in AGENT_ROLES
    assert len(AGENT_ROLES) == 7


def test_write_actions_per_role():
    assert can_propose("lab-host", "restart_service")
    assert can_propose("ingress", "apply_edge_routes")
    assert can_propose("ingress", "rollback_edge_routes")
    assert not can_propose("ingress", "restart_service")
    for role in ("vector", "supervisor", "approver", "llm", "cluster-gpu"):
        assert not can_propose(role, "restart_service")
        assert not can_write(role)
    assert WRITE_ACTIONS["obs"] == frozenset()


def test_supervisor_reads_only_status_and_fleet():
    assert can_read("supervisor", "/v1/fleet")
    assert can_read("supervisor", "/v1/status")
    for prefix in ("/v1/litellm", "/v1/metrics/query", "/v1/watch", "/v1/audit", "/v1/docker"):
        assert not can_read("supervisor", prefix)


def test_ingress_reads_logs_but_not_other_docker_routes():
    assert can_read("ingress", "/v1/docker/logs")
    assert not can_read("ingress", "/v1/docker")
    assert can_read("lab-host", "/v1/docker/logs")


def test_approver_reads_only_approvals_audit_status():
    assert can_read("approver", "/v1/approvals")
    assert can_read("approver", "/v1/audit")
    assert not can_read("approver", "/v1/docker")


def test_role_for_token(tmp_path):
    cfg = make_config(tmp_path)
    assert role_for_token(cfg, TOKENS["ingress"]) == "ingress"
    assert role_for_token(cfg, TOKENS["approver"]) == "approver"
    assert role_for_token(cfg, "nope") is None
    assert role_for_token(cfg, "") is None


def test_from_env_reads_renamed_tokens(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    cfg = GatewayConfig.from_env()
    assert cfg.tokens["ingress"] == "t-ing"
    assert cfg.tokens["llm"] == "t-llm"
    assert cfg.tokens["approver"] == "t-app"
    assert set(cfg.tokens) == ROLES


def test_from_env_requires_approver_token(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, OFFICE_GATEWAY_TOKEN_APPROVER="")
    with pytest.raises(ValueError, match="OFFICE_GATEWAY_TOKEN_APPROVER"):
        GatewayConfig.from_env()


def test_from_env_rejects_duplicate_tokens(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, OFFICE_GATEWAY_TOKEN_OBS="t-sup")
    with pytest.raises(ValueError, match="distinct"):
        GatewayConfig.from_env()


def test_default_service_urls_use_api_server_health(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, OFFICE_SERVICE_URLS="")
    cfg = GatewayConfig.from_env()
    assert cfg.service_urls["gateway"] == "http://office-gateway:8080/health"
    for name in (
        "hermes-agent",
        "hermes-lab-host",
        "hermes-ingress",
        "hermes-llm",
        "hermes-cluster-gpu",
        "hermes-vector",
        "hermes-obs",
    ):
        assert cfg.service_urls[name] == f"http://{name}:8642/health"
    assert not any(url.endswith("agent.json") for url in cfg.service_urls.values())


def test_vector_instances_default_and_override(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    cfg = GatewayConfig.from_env()
    assert set(cfg.vector_instances) == {"milvus-dev", "milvus-prod", "qdrant-dev"}
    _env(monkeypatch, tmp_path, OFFICE_VECTOR_INSTANCES="milvus-dev=http://m:9091/healthz")
    cfg = GatewayConfig.from_env()
    assert cfg.vector_instances == {"milvus-dev": "http://m:9091/healthz"}


def test_console_url_and_backup_dir(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, OFFICE_CONSOLE_URL="https://10.216.4.80/", OFFICE_EDGE_BACKUP_DIR="/edge/backups")
    cfg = GatewayConfig.from_env()
    assert cfg.console_url == "https://10.216.4.80"
    assert cfg.edge_backup_dir == "/edge/backups"
```

- [ ] **Step 3: Run to verify failure**

Run the test command with `tests/test_office_gateway_roles.py`. Expected: import errors (`AGENT_ROLES`, `can_propose` missing).

- [ ] **Step 4: Rewrite `roles.py`**

<!-- file: office_gateway/roles.py -->
```python
from __future__ import annotations

import hmac

AGENT_ROLES = frozenset(
    {"supervisor", "lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs"}
)
APPROVER_ROLE = "approver"
ROLES = AGENT_ROLES | {APPROVER_ROLE}
SPECIALIST_ROLES = AGENT_ROLES - {"supervisor"}

_COMMON = frozenset({"/v1/status", "/v1/services"})

READ_ROUTES: dict[str, frozenset[str]] = {
    "supervisor": _COMMON | {"/v1/fleet"},
    "lab-host": _COMMON | {"/v1/docker", "/v1/actions"},
    "ingress": _COMMON | {"/v1/edge", "/v1/docker/logs", "/v1/actions"},
    "llm": _COMMON | {"/v1/llm"},
    "cluster-gpu": _COMMON | {"/v1/k8s/resources", "/v1/gpu/mig", "/v1/host/gpu"},
    "vector": _COMMON,
    "obs": _COMMON | {"/v1/metrics/query", "/v1/grafana/links", "/v1/actions"},
    APPROVER_ROLE: frozenset({"/v1/status", "/v1/approvals", "/v1/audit"}),
}

WRITE_ACTIONS: dict[str, frozenset[str]] = {
    "lab-host": frozenset({"restart_service"}),
    "ingress": frozenset({"apply_edge_routes", "rollback_edge_routes"}),
    "obs": frozenset(),
}

WRITE_ROLES = frozenset(role for role, actions in WRITE_ACTIONS.items() if actions)


def role_for_token(config, token: str) -> str | None:
    if not token:
        return None
    for role, expected in config.tokens.items():
        if expected and hmac.compare_digest(token.encode(), expected.encode()):
            return role
    return None


def can_read(role: str, route_prefix: str) -> bool:
    allowed = READ_ROUTES.get(role, frozenset())
    return any(route_prefix == p or route_prefix.startswith(p + "/") for p in allowed)


def can_write(role: str) -> bool:
    return role in WRITE_ROLES


def can_propose(role: str, action: str) -> bool:
    return action in WRITE_ACTIONS.get(role, frozenset())
```

- [ ] **Step 5: Update `config.py`**

Replace `_TOKEN_ENV`, `_DEFAULT_SERVICE_URLS`, remove `_vector_env`, and replace the `GatewayConfig` dataclass and `from_env` with the code below. `_required`, `_parse_service_urls`, `_service_urls`, `_csv_set`, `_grafana_dashboards`, `_mig_expected` stay unchanged.

<!-- file: office_gateway/config.py (replace from `_TOKEN_ENV` through `_DEFAULT_SERVICE_URLS`) -->
```python
_TOKEN_ENV = {
    "supervisor": "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "lab-host": "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "ingress": "OFFICE_GATEWAY_TOKEN_INGRESS",
    "llm": "OFFICE_GATEWAY_TOKEN_LLM",
    "cluster-gpu": "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "vector": "OFFICE_GATEWAY_TOKEN_VECTOR",
    "obs": "OFFICE_GATEWAY_TOKEN_OBS",
    "approver": "OFFICE_GATEWAY_TOKEN_APPROVER",
}

_AGENT_SERVICES = (
    "hermes-agent",
    "hermes-lab-host",
    "hermes-ingress",
    "hermes-llm",
    "hermes-cluster-gpu",
    "hermes-vector",
    "hermes-obs",
)

_DEFAULT_SERVICE_URLS = ",".join(
    ["gateway=http://office-gateway:8080/health"]
    + [f"{name}=http://{name}:8642/health" for name in _AGENT_SERVICES]
)

_DEFAULT_VECTOR_INSTANCES = (
    "milvus-dev=http://host.docker.internal:9091/healthz,"
    "milvus-prod=http://10.216.203.132:9091/healthz,"
    "qdrant-dev=http://host.docker.internal:6333/readyz"
)
```

<!-- file: office_gateway/config.py (replace `_vector_env` with) -->
```python
def _vector_instances(value: str) -> dict[str, str]:
    """Parse `name=url,...` health endpoints for vector instances."""
    return _parse_service_urls(value) if value.strip() else {}
```

<!-- file: office_gateway/config.py (replace the dataclass and from_env) -->
```python
@dataclass(frozen=True)
class GatewayConfig:
    tokens: dict[str, str]
    service_urls: dict[str, str]
    vector_instances: dict[str, str] = field(default_factory=dict)
    grafana_base_url: str = ""
    grafana_dashboards: dict[str, str] = field(default_factory=dict)
    grafana_panel_ids: dict[str, int] = field(default_factory=dict)
    mig_expected: dict[str, int] = field(default_factory=dict)
    metrics_url: str = ""
    db_path: str = "/var/lib/hermes-office-gateway/gateway.db"
    action_ttl_seconds: int = 600
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080
    hermes_dashboard_url: str = ""
    hermes_dashboard_username: str = ""
    hermes_dashboard_password: str = ""
    edge_routes_path: str = ""
    edge_locations_path: str = ""
    edge_backup_dir: str = ""
    edge_tls_cert_path: str = ""
    litellm_url: str = ""
    litellm_master_key: str = ""
    console_url: str = ""

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        tokens = {role: _required(env) for role, env in _TOKEN_ENV.items()}
        if len(set(tokens.values())) != len(tokens):
            raise ValueError("OFFICE_GATEWAY_TOKEN_* values must be distinct")
        extra = os.getenv("OFFICE_GRAFANA_PANELS", "")
        panels: dict[str, int] = {}
        for item in (part.strip() for part in extra.split(",") if part.strip()):
            name, sep, pid = item.partition("=")
            if not sep:
                raise ValueError("OFFICE_GRAFANA_PANELS format name=id tidak valid")
            panels[name] = int(pid)
        return cls(
            tokens=tokens,
            service_urls=_service_urls(os.getenv("OFFICE_SERVICE_URLS", "").strip()),
            vector_instances=_vector_instances(
                os.getenv("OFFICE_VECTOR_INSTANCES", "").strip() or _DEFAULT_VECTOR_INSTANCES
            ),
            grafana_base_url=os.getenv("OFFICE_GRAFANA_BASE_URL", "").rstrip("/"),
            grafana_dashboards=_grafana_dashboards(os.getenv("OFFICE_GRAFANA_DASHBOARDS", "")),
            grafana_panel_ids=panels,
            mig_expected=_mig_expected(os.getenv("OFFICE_MIG_EXPECTED", "")),
            metrics_url=os.getenv("OFFICE_METRICS_URL", "").rstrip("/"),
            db_path=os.getenv(
                "OFFICE_GATEWAY_DB_PATH", "/var/lib/hermes-office-gateway/gateway.db"
            ),
            action_ttl_seconds=max(60, int(os.getenv("OFFICE_ACTION_TTL_SECONDS", "600"))),
            bind_host=os.getenv("OFFICE_GATEWAY_BIND_HOST", "0.0.0.0"),
            bind_port=int(os.getenv("OFFICE_GATEWAY_PORT", "8080")),
            hermes_dashboard_url=os.getenv("HERMES_DASHBOARD_URL", "").rstrip("/"),
            hermes_dashboard_username=os.getenv("HERMES_DASHBOARD_USERNAME", "").strip(),
            hermes_dashboard_password=os.getenv("HERMES_DASHBOARD_PASSWORD", "").strip(),
            edge_routes_path=os.getenv("OFFICE_EDGE_ROUTES_PATH", "").strip(),
            edge_locations_path=(
                os.getenv("OFFICE_EDGE_DYNAMIC_PATH", "").strip()
                or os.getenv("OFFICE_EDGE_LOCATIONS_PATH", "").strip()
            ),
            edge_backup_dir=os.getenv("OFFICE_EDGE_BACKUP_DIR", "").strip(),
            edge_tls_cert_path=os.getenv("OFFICE_EDGE_TLS_CERT_PATH", "/certs/tls.crt").strip(),
            litellm_url=(
                os.getenv("OFFICE_LITELLM_URL", "").strip()
                or os.getenv("LITELLM_URL", "").strip()
            ),
            litellm_master_key=(
                os.getenv("OFFICE_LITELLM_MASTER_KEY", "").strip()
                or os.getenv("LITELLM_MASTER_KEY", "").strip()
            ),
            console_url=os.getenv("OFFICE_CONSOLE_URL", "").strip().rstrip("/"),
        )
```

- [ ] **Step 6: Run the roles tests**

Run the test command with `tests/test_office_gateway_roles.py`. Expected: all PASS. (`app.py`/`actions.py` still reference removed names; they are fixed in Tasks 5–6, so do not run the full suite yet.)

- [ ] **Step 7: Commit**

```powershell
git add office_gateway/roles.py office_gateway/config.py tests/gw_helpers.py tests/test_office_gateway_roles.py
git commit -m "feat(gateway): rename roles to ingress/llm, add approver role and per-role write actions"
```

---

### Task 3: Store — approval state machine

**Files:**
- Modify: `office_gateway/store.py` (full rewrite)
- Create: `tests/test_office_gateway_store.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `PendingAction` (fields `action_id, action, target, role, params_json, summary, created_at, expires_at, status, approver, decided_at, detail, result_json`; properties `params -> dict`, `result -> dict`); `GatewayStore(db_path, action_ttl_seconds)` with `propose(action, target, role, params, summary) -> PendingAction`, `get_action(id) -> PendingAction | None` (expires on read), `list_actions(statuses: tuple[str, ...] | None, limit=50) -> list[PendingAction]`, `claim_pending(id, approver) -> PendingAction | None`, `reject(id, approver) -> PendingAction | None`, `mark_executed(id, ok, detail, result=None) -> PendingAction | None`, `list_audit(limit) -> list[dict]`; constants `STATUSES`, `ALLOWED_AUDIT_DETAIL_CODES`.

- [ ] **Step 1: Write the failing tests**

<!-- file: tests/test_office_gateway_store.py -->
```python
import sqlite3
from datetime import datetime, timedelta, timezone

from office_gateway.store import GatewayStore


def _store(tmp_path, ttl=600):
    return GatewayStore(str(tmp_path / "gw.db"), ttl)


def _expire(store, action_id):
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE actions SET expires_at = ? WHERE action_id = ?", (past, action_id))


def test_propose_then_approve_then_succeed(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {"reason": "hung"}, "restart api")
    assert a.status == "pending" and a.params == {"reason": "hung"}
    claimed = store.claim_pending(a.action_id, "tim")
    assert claimed.status == "executing" and claimed.approver == "tim"
    assert store.claim_pending(a.action_id, "tim") is None
    done = store.mark_executed(a.action_id, ok=True, detail="ok", result={"x": 1})
    assert done.status == "succeeded" and done.result == {"x": 1}
    events = [row["event"] for row in store.list_audit(10)]
    assert events == ["succeeded", "approved", "proposed"]
    assert store.list_audit(10)[1]["actor"] == "tim"


def test_failed_execution_records_detail(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {}, "restart api")
    store.claim_pending(a.action_id, "tim")
    done = store.mark_executed(a.action_id, ok=False, detail="failed:rolled_back", result={"rolled_back": True})
    assert done.status == "failed" and done.detail == "failed:rolled_back"


def test_reject_only_pending(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {}, "restart api")
    rejected = store.reject(a.action_id, "tim")
    assert rejected.status == "rejected" and rejected.approver == "tim"
    assert store.reject(a.action_id, "tim") is None
    assert store.claim_pending(a.action_id, "tim") is None


def test_expired_actions_cannot_be_claimed_and_show_expired(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {}, "restart api")
    _expire(store, a.action_id)
    assert store.claim_pending(a.action_id, "tim") is None
    assert store.get_action(a.action_id).status == "expired"
    assert [x.status for x in store.list_actions(None)] == ["expired"]
    assert store.list_actions(("pending",)) == []


def test_list_actions_filters_and_orders_newest_first(tmp_path):
    store = _store(tmp_path)
    first = store.propose("restart_service", "a", "lab-host", {}, "restart a")
    second = store.propose("restart_service", "b", "lab-host", {}, "restart b")
    store.reject(first.action_id, "tim")
    pending = store.list_actions(("pending",))
    assert [x.action_id for x in pending] == [second.action_id]
    assert [x.action_id for x in store.list_actions(None)] == [second.action_id, first.action_id]


def test_migrates_old_executed_status(tmp_path):
    db = tmp_path / "gw.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE actions (action_id TEXT PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL,
              role TEXT NOT NULL, params_json TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL, status TEXT NOT NULL, executing_at TEXT);
            CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT NOT NULL,
              event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO actions VALUES ('old','restart_service','x','lab-host','{}','restart x',
              '2026-01-01T00:00:00Z','2026-01-01T00:10:00Z','executed',NULL);
            """
        )
    store = GatewayStore(str(db), 600)
    assert store.get_action("old").status == "succeeded"
    assert store.list_audit(5) == []


def test_unknown_audit_detail_is_normalized(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {}, "restart api")
    store.claim_pending(a.action_id, "tim")
    store.mark_executed(a.action_id, ok=False, detail="stack trace with secrets")
    assert store.list_audit(1)[0]["detail"] == "failed:not_found"
```

- [ ] **Step 2: Run to verify failure**

Run the test command with `tests/test_office_gateway_store.py`. Expected: FAIL (`claim_pending()` takes 2 positional arguments, `list_actions` missing).

- [ ] **Step 3: Rewrite `store.py`**

<!-- file: office_gateway/store.py -->
```python
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUSES = ("pending", "executing", "succeeded", "failed", "rejected", "expired")

ALLOWED_AUDIT_DETAIL_CODES = frozenset(
    {
        "ok",
        "rejected",
        "failed:adapter_unavailable",
        "failed:not_found",
        "failed:invalid",
        "failed:rolled_back",
        "failed:claim_conflict",
        "failed:expired",
    }
)

_ACTION_COLUMNS = {
    "role": "TEXT NOT NULL DEFAULT ''",
    "executing_at": "TEXT",
    "approver": "TEXT NOT NULL DEFAULT ''",
    "decided_at": "TEXT NOT NULL DEFAULT ''",
    "detail": "TEXT NOT NULL DEFAULT ''",
    "result_json": "TEXT NOT NULL DEFAULT '{}'",
}


def normalize_audit_detail(detail: str) -> str:
    if detail in ALLOWED_AUDIT_DETAIL_CODES:
        return detail
    return "failed:not_found"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class PendingAction:
    action_id: str
    action: str
    target: str
    role: str
    params_json: str
    summary: str
    created_at: str
    expires_at: str
    status: str
    approver: str = ""
    decided_at: str = ""
    detail: str = ""
    result_json: str = "{}"

    @property
    def params(self) -> dict:
        return json.loads(self.params_json or "{}")

    @property
    def result(self) -> dict:
        return json.loads(self.result_json or "{}")


class GatewayStore:
    def __init__(self, db_path: str, action_ttl_seconds: int = 600) -> None:
        self.db_path = db_path
        self.action_ttl_seconds = action_ttl_seconds
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT '',
                    params_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            have = {row["name"] for row in conn.execute("PRAGMA table_info(actions)")}
            for name, decl in _ACTION_COLUMNS.items():
                if name not in have:
                    conn.execute(f"ALTER TABLE actions ADD COLUMN {name} {decl}")
            audit_cols = {row["name"] for row in conn.execute("PRAGMA table_info(audit)")}
            if "actor" not in audit_cols:
                conn.execute("ALTER TABLE audit ADD COLUMN actor TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE actions SET status = 'succeeded' WHERE status = 'executed'")
            self._recover_stale_executing(conn)

    def _audit(self, conn, action_id: str, event: str, detail: str, actor: str = "") -> None:
        conn.execute(
            "INSERT INTO audit (action_id, event, detail, created_at, actor) VALUES (?, ?, ?, ?, ?)",
            (action_id, event, normalize_audit_detail(detail), _iso_z(_utc_now()), actor),
        )

    def _recover_stale_executing(self, conn: sqlite3.Connection) -> None:
        cutoff = _iso_z(_utc_now() - timedelta(seconds=self.action_ttl_seconds))
        rows = conn.execute(
            "SELECT action_id FROM actions WHERE status = 'executing' "
            "AND COALESCE(executing_at, created_at) <= ?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            cur = conn.execute(
                "UPDATE actions SET status = 'failed', detail = 'failed:adapter_unavailable' "
                "WHERE action_id = ? AND status = 'executing'",
                (row["action_id"],),
            )
            if cur.rowcount:
                self._audit(conn, row["action_id"], "failed", "failed:adapter_unavailable")

    def _expire_due(self, conn: sqlite3.Connection) -> None:
        now = _iso_z(_utc_now())
        rows = conn.execute(
            "SELECT action_id FROM actions WHERE status = 'pending' AND expires_at < ?", (now,)
        ).fetchall()
        for row in rows:
            cur = conn.execute(
                "UPDATE actions SET status = 'expired' WHERE action_id = ? AND status = 'pending'",
                (row["action_id"],),
            )
            if cur.rowcount:
                self._audit(conn, row["action_id"], "expired", "failed:expired")

    @staticmethod
    def _row(row: sqlite3.Row) -> PendingAction:
        return PendingAction(
            action_id=row["action_id"],
            action=row["action"],
            target=row["target"],
            role=row["role"],
            params_json=row["params_json"],
            summary=row["summary"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            status=row["status"],
            approver=row["approver"],
            decided_at=row["decided_at"],
            detail=row["detail"],
            result_json=row["result_json"],
        )

    def propose(
        self,
        action: str,
        target: str,
        role: str,
        params: dict | None = None,
        summary: str = "",
    ) -> PendingAction:
        action_id = str(uuid.uuid4())
        created = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO actions (action_id, action, target, role, params_json, summary, "
                "created_at, expires_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (
                    action_id,
                    action,
                    target,
                    role,
                    json.dumps(params or {}),
                    summary,
                    _iso_z(created),
                    _iso_z(created + timedelta(seconds=self.action_ttl_seconds)),
                ),
            )
            self._audit(conn, action_id, "proposed", "ok", role)
        found = self.get_action(action_id)
        assert found is not None
        return found

    def get_action(self, action_id: str) -> PendingAction | None:
        with self._connect() as conn:
            self._expire_due(conn)
            row = conn.execute("SELECT * FROM actions WHERE action_id = ?", (action_id,)).fetchone()
        return self._row(row) if row else None

    def list_actions(
        self, statuses: tuple[str, ...] | None = None, limit: int = 50
    ) -> list[PendingAction]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            self._expire_due(conn)
            if statuses:
                marks = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    f"SELECT * FROM actions WHERE status IN ({marks}) "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (*statuses, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM actions ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._row(row) for row in rows]

    def claim_pending(self, action_id: str, approver: str) -> PendingAction | None:
        now = _iso_z(_utc_now())
        with self._connect() as conn:
            self._expire_due(conn)
            cur = conn.execute(
                "UPDATE actions SET status = 'executing', executing_at = ?, approver = ?, "
                "decided_at = ? WHERE action_id = ? AND status = 'pending' AND expires_at >= ?",
                (now, approver, now, action_id, now),
            )
            if cur.rowcount == 0:
                return None
            self._audit(conn, action_id, "approved", "ok", approver)
        return self.get_action(action_id)

    def reject(self, action_id: str, approver: str) -> PendingAction | None:
        now = _iso_z(_utc_now())
        with self._connect() as conn:
            self._expire_due(conn)
            cur = conn.execute(
                "UPDATE actions SET status = 'rejected', approver = ?, decided_at = ? "
                "WHERE action_id = ? AND status = 'pending'",
                (approver, now, action_id),
            )
            if cur.rowcount == 0:
                return None
            self._audit(conn, action_id, "rejected", "rejected", approver)
        return self.get_action(action_id)

    def mark_executed(
        self, action_id: str, ok: bool, detail: str, result: dict | None = None
    ) -> PendingAction | None:
        status = "succeeded" if ok else "failed"
        safe = normalize_audit_detail(detail)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE actions SET status = ?, detail = ?, result_json = ? "
                "WHERE action_id = ? AND status = 'executing'",
                (status, safe, json.dumps(result or {}), action_id),
            )
            if cur.rowcount == 0:
                return None
            self._audit(conn, action_id, status, safe)
        return self.get_action(action_id)

    def list_audit(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, action_id, event, detail, created_at, actor FROM audit "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "action_id": row["action_id"],
                "event": row["event"],
                "detail": normalize_audit_detail(row["detail"]),
                "actor": row["actor"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
```

- [ ] **Step 4: Run the store tests**

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add office_gateway/store.py tests/test_office_gateway_store.py
git commit -m "feat(gateway): approval state machine with approver audit"
```

---

### Task 4: Route backup, verified apply, auto-restore, rollback

**Files:**
- Modify: `office_gateway/edge_ops.py`
- Create: `tests/test_office_gateway_edge_backup.py`

**Interfaces:**
- Produces: `BACKUP_KEEP = 10`; `RouteApplyError(message, rolled_back: bool)`; `route_diff(old: str, new: str, limit=60) -> list[str]`; `EdgeRoutesOps(routes_path, locations_path, host_alias="host.docker.internal", backup_dir=None, clock=None)` with `read_text_or_empty() -> str`, `list_backups() -> list[str]` (newest first), `read_backup(name) -> str`, `apply(content) -> {"routes": int, "backup": str | None}`, `restore(name=None) -> {"restored": str, "routes": int, "backup": str | None}`. Existing `configured`, `read_text`, `list_routes`, `validate`, `parse_edge_routes`, `render_locations`, `render_traefik_dynamic`, `AdapterNotConfigured`, `EdgeRoute` unchanged.

- [ ] **Step 1: Write the failing tests**

<!-- file: tests/test_office_gateway_edge_backup.py -->
```python
from datetime import datetime, timedelta, timezone

import pytest

import office_gateway.edge_ops as edge_ops
from office_gateway.edge_ops import EdgeRoutesOps, RouteApplyError, route_diff

OLD = "grafana /grafana/ 10.0.0.1:3000 0\n"
NEW = "grafana /grafana/ 10.0.0.1:3000 0\nattu /attu/ 10.0.0.2:8000 1\n"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 24, 8, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


def _ops(tmp_path, clock=None):
    routes = tmp_path / "edge-routes"
    dynamic = tmp_path / "dynamic" / "routes.yml"
    routes.write_text(OLD, encoding="utf-8")
    return EdgeRoutesOps(str(routes), str(dynamic), clock=clock or Clock()), routes, dynamic


def test_apply_backs_up_previous_file_and_writes_both(tmp_path):
    ops, routes, dynamic = _ops(tmp_path)
    result = ops.apply(NEW)
    assert result["routes"] == 2
    assert result["backup"] == ops.list_backups()[0]
    assert ops.read_backup(result["backup"]) == OLD
    assert routes.read_text(encoding="utf-8") == NEW
    assert "attu" in dynamic.read_text(encoding="utf-8")


def test_keeps_only_newest_ten_backups(tmp_path):
    ops, _, _ = _ops(tmp_path)
    for i in range(12):
        ops.apply(OLD if i % 2 else NEW)
    backups = ops.list_backups()
    assert len(backups) == 10
    assert backups == sorted(backups, reverse=True)


def test_failed_render_restores_previous_routes(tmp_path, monkeypatch):
    ops, routes, _ = _ops(tmp_path)
    original = edge_ops.render_locations

    def render(route_list, **kwargs):
        if any(r.name == "boom" for r in route_list):
            raise ValueError("render failed")
        return original(route_list, **kwargs)

    monkeypatch.setattr(edge_ops, "render_locations", render)
    with pytest.raises(RouteApplyError) as info:
        ops.apply("boom /boom/ 10.0.0.9:80 0\n")
    assert info.value.rolled_back is True
    assert routes.read_text(encoding="utf-8") == OLD


def test_restore_newest_or_named_backup(tmp_path):
    ops, routes, _ = _ops(tmp_path)
    ops.apply(NEW)
    newest = ops.list_backups()[0]
    result = ops.restore()
    assert result["restored"] == newest
    assert routes.read_text(encoding="utf-8") == OLD
    with pytest.raises(ValueError, match="unknown backup"):
        ops.restore("edge-routes.nope")


def test_restore_without_backups_fails(tmp_path):
    ops, _, _ = _ops(tmp_path)
    with pytest.raises(ValueError, match="no backups"):
        ops.restore()


def test_read_backup_rejects_path_tricks(tmp_path):
    ops, _, _ = _ops(tmp_path)
    ops.apply(NEW)
    with pytest.raises(ValueError, match="unknown backup"):
        ops.read_backup("../edge-routes")


def test_route_diff_is_compact():
    diff = route_diff(OLD, NEW)
    assert "+attu /attu/ 10.0.0.2:8000 1" in diff
    assert route_diff(OLD, OLD) == []
```

- [ ] **Step 2: Run to verify failure**

Expected: import error (`RouteApplyError`, `route_diff`).

- [ ] **Step 3: Implement in `edge_ops.py`**

Add imports at the top (`import difflib`, `from datetime import datetime, timezone`, `from typing import Callable, Optional`) and constants after `RESERVED_PREFIXES`:

<!-- file: office_gateway/edge_ops.py (after RESERVED_PREFIXES) -->
```python
BACKUP_KEEP = 10
_BACKUP_PREFIX = "edge-routes."


class RouteApplyError(Exception):
    def __init__(self, message: str, rolled_back: bool) -> None:
        super().__init__(message)
        self.rolled_back = rolled_back


def route_diff(old: str, new: str, limit: int = 60) -> list[str]:
    lines = [
        line
        for line in difflib.unified_diff(
            old.splitlines(), new.splitlines(), "current", "proposed", n=0, lineterm=""
        )
        if not line.startswith("@@")
    ]
    if len(lines) > limit:
        return lines[:limit] + [f"... {len(lines) - limit} more lines"]
    return lines
```

Replace the `EdgeRoutesOps` class with:

<!-- file: office_gateway/edge_ops.py (replace class EdgeRoutesOps) -->
```python
class EdgeRoutesOps:
    def __init__(
        self,
        routes_path: str | None,
        locations_path: str | None,
        host_alias: str = "host.docker.internal",
        backup_dir: str | None = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self._routes_path = Path(routes_path) if routes_path else None
        self._locations_path = Path(locations_path) if locations_path else None
        self._host_alias = host_alias
        if backup_dir:
            self._backup_dir: Path | None = Path(backup_dir)
        elif self._routes_path is not None:
            self._backup_dir = self._routes_path.parent / "backups"
        else:
            self._backup_dir = None
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def configured(self) -> bool:
        return self._routes_path is not None and self._locations_path is not None

    def _require_configured(self) -> tuple[Path, Path]:
        if not self.configured():
            raise AdapterNotConfigured()
        assert self._routes_path is not None
        assert self._locations_path is not None
        return self._routes_path, self._locations_path

    def read_text(self) -> str:
        routes_path, _ = self._require_configured()
        return routes_path.read_text(encoding="utf-8")

    def read_text_or_empty(self) -> str:
        routes_path, _ = self._require_configured()
        return routes_path.read_text(encoding="utf-8") if routes_path.exists() else ""

    def list_routes(self) -> list[EdgeRoute]:
        return parse_edge_routes(self.read_text())

    def validate(self, content: str) -> list[EdgeRoute]:
        return parse_edge_routes(content)

    def list_backups(self) -> list[str]:
        if self._backup_dir is None or not self._backup_dir.is_dir():
            return []
        return sorted(
            (p.name for p in self._backup_dir.iterdir() if p.name.startswith(_BACKUP_PREFIX)),
            reverse=True,
        )

    def read_backup(self, name: str) -> str:
        if name not in self.list_backups():
            raise ValueError("unknown backup")
        assert self._backup_dir is not None
        return (self._backup_dir / name).read_text(encoding="utf-8")

    def _backup_current(self) -> str | None:
        routes_path, _ = self._require_configured()
        if not routes_path.exists() or self._backup_dir is None:
            return None
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        name = _BACKUP_PREFIX + self._clock().strftime("%Y%m%dT%H%M%S%fZ")
        (self._backup_dir / name).write_text(routes_path.read_text(encoding="utf-8"), encoding="utf-8")
        for old in self.list_backups()[BACKUP_KEEP:]:
            (self._backup_dir / old).unlink(missing_ok=True)
        return name

    def _write_pair(self, content: str) -> list[EdgeRoute]:
        routes_path, locations_path = self._require_configured()
        routes = parse_edge_routes(content)
        _atomic_write(routes_path, content)
        rendered = render_locations(routes, host_alias=self._host_alias)
        _atomic_write(locations_path, rendered)
        if parse_edge_routes(routes_path.read_text(encoding="utf-8")) != routes:
            raise ValueError("routes file verification failed")
        if locations_path.read_text(encoding="utf-8") != rendered:
            raise ValueError("dynamic config verification failed")
        return routes

    def apply(self, content: str) -> dict:
        self.validate(content)
        previous = self.read_text_or_empty()
        backup = self._backup_current()
        try:
            routes = self._write_pair(content)
        except Exception as exc:
            rolled_back = False
            if previous:
                try:
                    self._write_pair(previous)
                    rolled_back = True
                except Exception:
                    rolled_back = False
            raise RouteApplyError(str(exc), rolled_back=rolled_back) from exc
        return {"routes": len(routes), "backup": backup}

    def restore(self, name: str | None = None) -> dict:
        backups = self.list_backups()
        if not backups:
            raise ValueError("no backups")
        chosen = name or backups[0]
        content = self.read_backup(chosen)
        return {"restored": chosen, **self.apply(content)}
```

- [ ] **Step 4: Run the new tests plus the existing edge-related tests**

Run the test command with `tests/test_office_gateway_edge_backup.py`. Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add office_gateway/edge_ops.py tests/test_office_gateway_edge_backup.py
git commit -m "feat(gateway): route backups, verified apply with auto-restore, restore"
```

---

### Task 5: Actions service and approver endpoints; remove auto-execute

**Files:**
- Modify: `office_gateway/actions.py` (full rewrite)
- Modify: `office_gateway/docker_ops.py` (add `LIST_CAP`, `compact_listing`, inspect `env_names`/`restart_count`/`started_at`)
- Delete: `office_gateway/autoheal.py`
- Modify: `office_gateway/app.py` (actions/approvals endpoints, docker compact listing, logs RBAC)
- Create: `tests/test_office_gateway_approvals.py`
- Modify: `tests/test_office_gateway_docker.py` (expect the new inspect keys)

**Interfaces:**
- Consumes: `store` from Task 3, `edge_ops` from Task 4, `roles.can_propose`.
- Produces: `ActionError(message, category="forbidden", valid=None)` with `.category`, `.valid`; `action_view(PendingAction) -> dict`; `ActionService(config, store, docker_ops=None, edge_ops=None)` with `container_names() -> list[str]`, `propose(role, action, target, params) -> PendingAction`, `status(role, action_id) -> PendingAction`, `approve(action_id, approver) -> PendingAction`, `reject(action_id, approver) -> PendingAction`. REST: `POST /v1/actions/propose`, `GET /v1/actions/{id}`, `GET /v1/approvals?status=pending|recent&limit=`, `POST /v1/approvals/{id}/approve`, `POST /v1/approvals/{id}/reject`. `docker_ops.LIST_CAP`, `docker_ops.compact_listing(containers) -> dict`.

- [ ] **Step 1: Write the failing tests**

<!-- file: tests/test_office_gateway_approvals.py -->
```python
from fastapi.testclient import TestClient

from gw_helpers import FakeDockerOps, auth, make_config
from office_gateway.app import create_app
from office_gateway.edge_ops import EdgeRoutesOps

ROUTES = "grafana /grafana/ 10.0.0.1:3000 0\n"


def _client(tmp_path, docker=None):
    routes = tmp_path / "edge" / "edge-routes"
    routes.parent.mkdir()
    routes.write_text(ROUTES, encoding="utf-8")
    edge = EdgeRoutesOps(str(routes), str(tmp_path / "edge" / "routes.yml"))
    docker = docker or FakeDockerOps()
    app = create_app(make_config(tmp_path), docker_ops=docker, edge_ops=edge)
    return TestClient(app), docker, routes


def _propose_restart(client, target="aiplatform-api"):
    return client.post(
        "/v1/actions/propose",
        headers=auth("lab-host"),
        json={"action": "restart_service", "target": target, "params": {"reason": "hung"}},
    )


def test_propose_restart_is_pending_and_not_executed(tmp_path):
    client, docker, _ = _client(tmp_path)
    r = _propose_restart(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending" and body["reason"] == "hung"
    assert docker.restarted == []


def test_propose_unknown_container_lists_valid_names(tmp_path):
    client, _, _ = _client(tmp_path)
    r = _propose_restart(client, "nope")
    assert r.status_code == 400
    assert "aiplatform-api" in r.json()["detail"]["valid"]


def test_auto_execute_and_execute_endpoint_are_gone(tmp_path):
    client, docker, _ = _client(tmp_path)
    r = client.post(
        "/v1/actions/propose",
        headers=auth("lab-host"),
        json={"action": "restart_service", "target": "aiplatform-api", "auto_execute": True},
    )
    assert r.status_code == 200 and r.json()["status"] == "pending"
    assert docker.restarted == []
    r = client.post("/v1/actions/execute", headers=auth("lab-host"), json={"action_id": "x"})
    assert r.status_code in (404, 405)


def test_agents_cannot_approve(tmp_path):
    client, docker, _ = _client(tmp_path)
    action_id = _propose_restart(client).json()["action_id"]
    for role in ("lab-host", "supervisor"):
        r = client.post(f"/v1/approvals/{action_id}/approve", headers=auth(role, approver="x"))
        assert r.status_code == 403
    assert docker.restarted == []


def test_approve_requires_x_approver(tmp_path):
    client, docker, _ = _client(tmp_path)
    action_id = _propose_restart(client).json()["action_id"]
    r = client.post(f"/v1/approvals/{action_id}/approve", headers=auth("approver"))
    assert r.status_code == 400
    assert docker.restarted == []


def test_approve_executes_once_and_records_approver(tmp_path):
    client, docker, _ = _client(tmp_path)
    action_id = _propose_restart(client).json()["action_id"]
    r = client.post(f"/v1/approvals/{action_id}/approve", headers=auth("approver", approver="tim"))
    assert r.status_code == 200
    assert r.json()["status"] == "succeeded" and r.json()["approver"] == "tim"
    assert docker.restarted == ["aiplatform-api"]
    again = client.post(f"/v1/approvals/{action_id}/approve", headers=auth("approver", approver="tim"))
    assert again.status_code == 409
    assert docker.restarted == ["aiplatform-api"]
    audit = client.get("/v1/audit", headers=auth("approver")).json()["entries"]
    assert [e["event"] for e in audit][:3] == ["succeeded", "approved", "proposed"]


def test_restart_failure_is_recorded(tmp_path):
    client, _, _ = _client(tmp_path, docker=FakeDockerOps(fail_restart=True))
    action_id = _propose_restart(client).json()["action_id"]
    r = client.post(f"/v1/approvals/{action_id}/approve", headers=auth("approver", approver="tim"))
    assert r.json()["status"] == "failed"
    assert r.json()["detail"] == "failed:adapter_unavailable"


def test_reject_then_status_visible_to_proposer_only(tmp_path):
    client, docker, _ = _client(tmp_path)
    action_id = _propose_restart(client).json()["action_id"]
    r = client.post(f"/v1/approvals/{action_id}/reject", headers=auth("approver", approver="tim"))
    assert r.json()["status"] == "rejected"
    assert client.get(f"/v1/actions/{action_id}", headers=auth("lab-host")).json()["status"] == "rejected"
    assert client.get(f"/v1/actions/{action_id}", headers=auth("ingress")).status_code == 404
    assert docker.restarted == []


def test_list_pending_and_recent(tmp_path):
    client, _, _ = _client(tmp_path)
    first = _propose_restart(client).json()["action_id"]
    second = _propose_restart(client, "broken-worker").json()["action_id"]
    client.post(f"/v1/approvals/{first}/reject", headers=auth("approver", approver="tim"))
    pending = client.get("/v1/approvals", headers=auth("approver")).json()["actions"]
    assert [a["action_id"] for a in pending] == [second]
    recent = client.get("/v1/approvals?status=recent", headers=auth("approver")).json()["actions"]
    assert {a["action_id"] for a in recent} == {first, second}
    assert client.get("/v1/approvals", headers=auth("lab-host")).status_code == 403


def test_route_change_flow_with_diff_backup_and_rollback(tmp_path):
    client, _, routes = _client(tmp_path)
    new = ROUTES + "attu /attu/ 10.0.0.2:8000 1\n"
    r = client.post(
        "/v1/actions/propose",
        headers=auth("ingress"),
        json={"action": "apply_edge_routes", "target": "edge-routes", "params": {"content": new, "reason": "add attu"}},
    )
    assert r.status_code == 200
    assert "+attu /attu/ 10.0.0.2:8000 1" in r.json()["diff"]
    applied = client.post(
        f"/v1/approvals/{r.json()['action_id']}/approve", headers=auth("approver", approver="tim")
    ).json()
    assert applied["status"] == "succeeded" and applied["result"]["backup"]
    assert routes.read_text(encoding="utf-8") == new
    rb = client.post(
        "/v1/actions/propose",
        headers=auth("ingress"),
        json={"action": "rollback_edge_routes", "target": "edge-routes", "params": {"reason": "undo"}},
    )
    assert rb.status_code == 200
    done = client.post(
        f"/v1/approvals/{rb.json()['action_id']}/approve", headers=auth("approver", approver="tim")
    ).json()
    assert done["status"] == "succeeded"
    assert routes.read_text(encoding="utf-8") == ROUTES


def test_ingress_cannot_restart_and_vector_cannot_write(tmp_path):
    client, _, _ = _client(tmp_path)
    r = client.post(
        "/v1/actions/propose",
        headers=auth("ingress"),
        json={"action": "restart_service", "target": "office-office-edge-1"},
    )
    assert r.status_code == 403
    r = client.post(
        "/v1/actions/propose",
        headers=auth("vector"),
        json={"action": "restart_service", "target": "aiplatform-api"},
    )
    assert r.status_code == 403


def test_docker_logs_rbac(tmp_path):
    docker = FakeDockerOps(logs={"office-office-edge-1": ["GET / 200"], "aiplatform-api": ["token=abc123456789"]})
    client, _, _ = _client(tmp_path, docker=docker)
    assert client.get("/v1/docker/logs/office-office-edge-1", headers=auth("ingress")).status_code == 200
    assert client.get("/v1/docker/logs/aiplatform-api", headers=auth("ingress")).status_code == 403
    lines = client.get("/v1/docker/logs/aiplatform-api", headers=auth("lab-host")).json()["lines"]
    assert lines == ["token=[redacted]"]
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL/ERROR (import of `autoheal` in `app.py`, missing endpoints).

- [ ] **Step 3: Redaction module (needed by the logs endpoint)**

<!-- file: office_gateway/redact.py -->
```python
from __future__ import annotations

import re

_SECRET_WORDS = (
    r"password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|"
    r"private[_-]?key|authorization|credential|client[_-]?secret"
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[redacted]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "[redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}"), "[redacted]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[redacted]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s/]+@"), r"\1[redacted]@"),
    (
        re.compile(rf"(?i)((?:{_SECRET_WORDS})[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&]+"),
        r"\1[redacted]",
    ),
)


def redact_text(text: str) -> str:
    out = str(text)
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out
```

- [ ] **Step 4: `docker_ops.py` additions**

Add after `_LOG_LINE_MAX = 500`:

<!-- file: office_gateway/docker_ops.py (after _LOG_LINE_MAX) -->
```python
LIST_CAP = 500
_NOT_RUNNING_CAP = 20
_NAMES_CAP = 80
```

In `strip_inspect`, add three keys to the returned dict (after `"networks"`):

<!-- file: office_gateway/docker_ops.py (strip_inspect return additions) -->
```python
        "env_names": sorted(
            {str(item).split("=", 1)[0] for item in ((raw.get("Config") or {}).get("Env") or [])}
        )[:60],
        "restart_count": int(raw.get("RestartCount") or 0),
        "started_at": str(state.get("StartedAt") or ""),
```

Add at the end of the module:

<!-- file: office_gateway/docker_ops.py (append) -->
```python
def compact_listing(containers: list[dict]) -> dict:
    running = 0
    exited = 0
    unhealthy: list[str] = []
    not_running: list[str] = []
    for row in containers:
        name = str(row.get("name") or "")
        status, parsed_health = normalize_container_status(str(row.get("status") or ""))
        health = row.get("health") or parsed_health
        if status == "running":
            running += 1
        else:
            not_running.append(f"{name} ({status})")
        if status in {"exited", "dead"}:
            exited += 1
        if "unhealthy" in str(health or "").lower():
            unhealthy.append(name)
    names = sorted(str(row.get("name") or "") for row in containers)
    return {
        "running": running,
        "total": len(containers),
        "exited": exited,
        "unhealthy": unhealthy[:_NOT_RUNNING_CAP],
        "not_running": not_running[:_NOT_RUNNING_CAP],
        "names": names[:_NAMES_CAP],
        "names_truncated": len(names) > _NAMES_CAP,
    }
```

In `tests/test_office_gateway_docker.py`, update the `strip_inspect` expectation to include `env_names`, `restart_count`, `started_at` (values from the fake payload; `[]`, `0`, `""` when absent).

- [ ] **Step 5: Rewrite `actions.py`**

<!-- file: office_gateway/actions.py -->
```python
from __future__ import annotations

import os
import re

import httpx

from office_gateway.docker_ops import DockerOps
from office_gateway.edge_ops import EdgeRoutesOps, RouteApplyError, route_diff
from office_gateway.roles import can_propose
from office_gateway.store import PendingAction

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_OFFICE_GATEWAY_TARGET_RE = re.compile(r"(?:^|[-_.])office-gateway(?:[-_.]\d+)?$")
_APPROVER_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
_REASON_MAX = 200
_EDGE_TARGET = "edge-routes"


class ActionError(Exception):
    def __init__(self, message: str, category: str = "forbidden", valid=None) -> None:
        super().__init__(message)
        self.category = category
        self.valid = list(valid or [])


def action_view(action: PendingAction) -> dict:
    params = action.params
    view = {
        "action_id": action.action_id,
        "action": action.action,
        "target": action.target,
        "role": action.role,
        "summary": action.summary,
        "reason": params.get("reason", ""),
        "status": action.status,
        "created_at": action.created_at,
        "expires_at": action.expires_at,
        "approver": action.approver,
        "detail": action.detail,
        "result": action.result,
    }
    if action.action in ("apply_edge_routes", "rollback_edge_routes"):
        view["diff"] = params.get("diff", [])
    return view


def _adapter_endpoints() -> dict[str, str]:
    raw = os.getenv("OFFICE_ADAPTER_ENDPOINTS", "").strip()
    endpoints: dict[str, str] = {}
    for item in (part.strip() for part in raw.split(",") if part.strip()):
        name, separator, url = item.partition("=")
        if separator and _SERVICE_NAME_RE.fullmatch(name) and url.startswith(("http://", "https://")):
            endpoints[name] = url
    return endpoints


def _is_office_gateway_target(target: str) -> bool:
    return bool(_OFFICE_GATEWAY_TARGET_RE.search(target))


def _check_approver(approver: str) -> None:
    if not _APPROVER_RE.fullmatch(approver or ""):
        raise ActionError("X-Approver header required", "invalid_argument")


class ActionService:
    def __init__(
        self,
        config,
        store,
        docker_ops: DockerOps | None = None,
        edge_ops: EdgeRoutesOps | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.docker_ops = docker_ops or DockerOps()
        self.edge_ops = edge_ops or EdgeRoutesOps(None, None)

    def container_names(self) -> list[str]:
        listing = self.docker_ops.list_containers()
        return sorted(str(c.get("name") or "") for c in listing.get("containers", []))

    def propose(self, role: str, action: str, target: str, params: dict | None = None) -> PendingAction:
        if not can_propose(role, action):
            raise ActionError("action not allowed for role", "forbidden")
        params = dict(params or {})
        reason = str(params.get("reason") or "").strip()[:_REASON_MAX]
        if action == "restart_service":
            return self._propose_restart(role, target, reason)
        if action == "apply_edge_routes":
            return self._propose_apply(role, target, params.get("content"), reason)
        if action == "rollback_edge_routes":
            return self._propose_rollback(role, target, params.get("backup"), reason)
        raise ActionError("unknown action", "invalid_argument")

    def _propose_restart(self, role: str, target: str, reason: str) -> PendingAction:
        names = self.container_names()
        if target not in names:
            raise ActionError("unknown container", "invalid_argument", names)
        return self.store.propose(
            action="restart_service",
            target=target,
            role=role,
            params={"reason": reason},
            summary=f"restart {target}",
        )

    def _require_edge(self, target: str) -> None:
        if target != _EDGE_TARGET:
            raise ActionError("unknown target", "invalid_argument", [_EDGE_TARGET])
        if not self.edge_ops.configured():
            raise ActionError("edge adapter not configured", "not_configured")

    def _propose_apply(self, role: str, target: str, content, reason: str) -> PendingAction:
        self._require_edge(target)
        if not isinstance(content, str) or not content.strip():
            raise ActionError("content required", "invalid_argument")
        try:
            routes = self.edge_ops.validate(content)
        except ValueError as exc:
            raise ActionError(str(exc), "invalid_argument") from exc
        diff = route_diff(self.edge_ops.read_text_or_empty(), content)
        if not diff:
            raise ActionError("no change", "invalid_argument")
        return self.store.propose(
            action="apply_edge_routes",
            target=target,
            role=role,
            params={"content": content, "diff": diff, "reason": reason},
            summary=f"apply edge-routes ({len(routes)} routes)",
        )

    def _propose_rollback(self, role: str, target: str, backup, reason: str) -> PendingAction:
        self._require_edge(target)
        backups = self.edge_ops.list_backups()
        if not backups:
            raise ActionError("no backups", "invalid_argument")
        chosen = str(backup or backups[0])
        if chosen not in backups:
            raise ActionError("unknown backup", "invalid_argument", backups)
        diff = route_diff(self.edge_ops.read_text_or_empty(), self.edge_ops.read_backup(chosen))
        return self.store.propose(
            action="rollback_edge_routes",
            target=target,
            role=role,
            params={"backup": chosen, "diff": diff, "reason": reason},
            summary=f"rollback edge-routes to {chosen}",
        )

    def status(self, role: str, action_id: str) -> PendingAction:
        action = self.store.get_action(action_id)
        if action is None or action.role != role:
            raise ActionError("action not found", "invalid_argument")
        return action

    def _not_open_reason(self, action_id: str) -> ActionError:
        action = self.store.get_action(action_id)
        if action is None:
            return ActionError("action not found", "not_found")
        return ActionError(f"action already {action.status}", "conflict")

    def reject(self, action_id: str, approver: str) -> PendingAction:
        _check_approver(approver)
        action = self.store.reject(action_id, approver)
        if action is None:
            raise self._not_open_reason(action_id)
        return action

    def approve(self, action_id: str, approver: str) -> PendingAction:
        _check_approver(approver)
        claimed = self.store.claim_pending(action_id, approver)
        if claimed is None:
            raise self._not_open_reason(action_id)
        if claimed.action == "restart_service":
            return self._execute_restart(claimed)
        if claimed.action == "apply_edge_routes":
            return self._execute_edge(claimed, lambda: self.edge_ops.apply(claimed.params.get("content", "")))
        if claimed.action == "rollback_edge_routes":
            return self._execute_edge(claimed, lambda: self.edge_ops.restore(claimed.params.get("backup")))
        return self._finish(claimed, False, "failed:invalid")

    def _finish(self, claimed: PendingAction, ok: bool, detail: str, result: dict | None = None) -> PendingAction:
        done = self.store.mark_executed(claimed.action_id, ok=ok, detail=detail, result=result)
        if done is None:
            raise ActionError("action not executing", "conflict")
        return done

    def _execute_restart(self, claimed: PendingAction) -> PendingAction:
        if _is_office_gateway_target(claimed.target):
            done = self._finish(claimed, True, "ok", {"note": "gateway restarts itself"})
            self._restart_target(claimed.target)
            return done
        try:
            self._restart_target(claimed.target)
        except ValueError:
            return self._finish(claimed, False, "failed:not_found")
        except Exception:
            return self._finish(claimed, False, "failed:adapter_unavailable")
        return self._finish(claimed, True, "ok")

    def _execute_edge(self, claimed: PendingAction, run) -> PendingAction:
        try:
            result = run()
        except RouteApplyError as exc:
            detail = "failed:rolled_back" if exc.rolled_back else "failed:invalid"
            return self._finish(claimed, False, detail, {"rolled_back": exc.rolled_back})
        except ValueError:
            return self._finish(claimed, False, "failed:invalid", {"rolled_back": False})
        except Exception:
            return self._finish(claimed, False, "failed:adapter_unavailable", {"rolled_back": False})
        return self._finish(claimed, True, "ok", result)

    def _restart_target(self, target: str) -> None:
        endpoints = _adapter_endpoints()
        if target in endpoints:
            response = httpx.post(endpoints[target], timeout=10.0)
            if response.status_code == 404:
                raise ValueError(f"container not found: {target}")
            response.raise_for_status()
            return
        self.docker_ops.restart(target)
```

- [ ] **Step 6: Wire `app.py`**

1. Imports: delete `from office_gateway.autoheal import compact_listing`; add `from fastapi import Header`; add `from office_gateway.actions import ActionError, ActionService, action_view`; add `from office_gateway.docker_ops import DockerOps, compact_listing`; add `from office_gateway.redact import redact_text`; change the roles import to `from office_gateway.roles import APPROVER_ROLE, can_read, can_write, role_for_token`.
2. `ProposeRequest`: remove `auto_execute`. Delete `ExecuteRequest`.
3. `create_app`: build `EdgeRoutesOps(config.edge_routes_path or None, config.edge_locations_path or None, backup_dir=config.edge_backup_dir or None)`.
4. `/v1/docker/containers`: when `compact`, return `compact_listing(listing.get("containers") or [])`.
5. Replace the `/v1/docker/logs/{container}` handler, the `/v1/actions/propose` handler, and the `/v1/actions/execute` handler with:

<!-- file: office_gateway/app.py (inside create_app; replaces logs, propose, execute handlers) -->
```python
    _HTTP_FOR_CATEGORY = {
        "forbidden": 403,
        "invalid_argument": 400,
        "not_configured": 503,
        "not_found": 404,
        "conflict": 409,
    }

    def _action_http_error(exc: ActionError) -> HTTPException:
        status = _HTTP_FOR_CATEGORY.get(exc.category, 400)
        detail = {"error": str(exc)}
        if exc.valid:
            detail["valid"] = exc.valid[:40]
        return HTTPException(status_code=status, detail=detail)

    def _edge_container_names() -> set[str]:
        listing = ops.list_containers()
        return {
            str(c.get("name"))
            for c in listing.get("containers", [])
            if c.get("compose_service") == "office-edge"
        }

    @app.get("/v1/docker/logs/{container}")
    async def docker_logs(
        container: str,
        role: str = Depends(_role_from_request),
        tail: int = 80,
    ) -> dict:
        _require_read(role, "/v1/docker/logs")
        if role == "ingress" and container not in _edge_container_names():
            raise HTTPException(status_code=403, detail="forbidden")
        tail = max(1, min(int(tail), 200))
        try:
            result = ops.logs(container, tail=tail)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="container not found") from exc
        result["lines"] = [redact_text(line) for line in result.get("lines", [])]
        return result

    @app.post("/v1/actions/propose")
    async def propose_action(body: ProposeRequest, role: str = Depends(_role_from_request)) -> dict:
        if not can_write(role):
            raise HTTPException(status_code=403, detail="forbidden")
        try:
            pending = actions.propose(role, body.action, body.target, body.params)
        except ActionError as exc:
            raise _action_http_error(exc) from exc
        return action_view(pending)

    @app.get("/v1/actions/{action_id}")
    async def action_status(action_id: str, role: str = Depends(_role_from_request)) -> dict:
        _require_read(role, "/v1/actions")
        try:
            return action_view(actions.status(role, action_id))
        except ActionError as exc:
            raise HTTPException(status_code=404, detail="action not found") from exc

    def _require_approver(role: str) -> None:
        if role != APPROVER_ROLE:
            raise HTTPException(status_code=403, detail="forbidden")

    @app.get("/v1/approvals")
    async def list_approvals(
        role: str = Depends(_role_from_request), status: str = "pending", limit: int = 50
    ) -> dict:
        _require_approver(role)
        statuses = ("pending",) if status == "pending" else None
        return {"actions": [action_view(a) for a in store.list_actions(statuses, limit)]}

    @app.post("/v1/approvals/{action_id}/approve")
    async def approve_action(
        action_id: str,
        role: str = Depends(_role_from_request),
        x_approver: str = Header(default=""),
    ) -> dict:
        _require_approver(role)
        try:
            return action_view(actions.approve(action_id, x_approver.strip()))
        except ActionError as exc:
            raise _action_http_error(exc) from exc

    @app.post("/v1/approvals/{action_id}/reject")
    async def reject_action(
        action_id: str,
        role: str = Depends(_role_from_request),
        x_approver: str = Header(default=""),
    ) -> dict:
        _require_approver(role)
        try:
            return action_view(actions.reject(action_id, x_approver.strip()))
        except ActionError as exc:
            raise _action_http_error(exc) from exc
```

6. `git rm office_gateway/autoheal.py`.

- [ ] **Step 7: Run the new and touched tests**

Run the test command with `tests/test_office_gateway_approvals.py tests/test_office_gateway_store.py tests/test_office_gateway_edge_backup.py tests/test_office_gateway_roles.py tests/test_office_gateway_docker.py`. Expected: all PASS.

- [ ] **Step 8: Commit**

```powershell
git add -A office_gateway tests/test_office_gateway_approvals.py tests/test_office_gateway_docker.py
git commit -m "feat(gateway): human-only approvals; remove auto_execute, AUTOHEAL and agent execute"
```

---

### Task 6: Remove passthroughs and retired endpoints; fix remaining tests

**Files:**
- Modify: `office_gateway/app.py` (delete `/v1/litellm/{path}`, `/v1/watch/snapshot`, `/v1/watch/summary`; metrics REST takes a named query — done in Task 9; `/v1/k8s/resources` drop the `llm-edge` kind filter)
- Modify: `office_gateway/llm_ops.py` (delete `proxy_litellm_get`, `_safe_subpath`, `_PROXY_TIMEOUT`)
- Modify: `office_gateway/brief.py` (ids `ingress`/`llm`, ready services `hermes-ingress`/`hermes-llm`, purposes "Ingress + TLS", "LLM API")
- Modify: `tests/test_office_gateway_brief.py`, `tests/test_office_gateway_metrics.py`, `tests/test_office_gateway_mig.py`, `tests/test_office_gateway_chat.py` — replace each local config builder with `gw_helpers.make_config(tmp_path, ...)` (keep their extra kwargs such as `grafana_*`, `metrics_url`, `mig_expected`), replace role keys `llm-edge`→`llm`, `edge`→`ingress`, and replace ids `edge`/`llm-edge` in brief expectations with `ingress`/`llm`.
- Create: `tests/test_office_gateway_removed.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces: a gateway whose full unit suite passes.

- [ ] **Step 1: Write the failing removal test**

<!-- file: tests/test_office_gateway_removed.py -->
```python
from fastapi.testclient import TestClient

from gw_helpers import FakeDockerOps, auth, make_config
from office_gateway.app import create_app


def test_retired_endpoints_are_gone(tmp_path):
    client = TestClient(create_app(make_config(tmp_path), docker_ops=FakeDockerOps()))
    assert client.get("/v1/litellm/models", headers=auth("llm")).status_code == 404
    assert client.post("/v1/watch/snapshot", headers=auth("lab-host"), json={}).status_code == 404
    assert client.get("/v1/watch/summary", headers=auth("supervisor")).status_code == 404


def test_no_autoheal_module():
    import importlib.util

    assert importlib.util.find_spec("office_gateway.autoheal") is None
```

- [ ] **Step 2: Run to verify failure**

Expected: FAIL on the litellm/watch routes.

- [ ] **Step 3: Apply the removals listed under Files**, plus remove `upsert_watch`/`list_watch_summary` callers and the `WatchSnapshotRequest` model from `app.py`.

- [ ] **Step 4: Update the four existing test modules as listed under Files**

- [ ] **Step 5: Run the whole gateway suite**

Run the test command with `tests/test_office_gateway_*.py`. Expected: all PASS.

- [ ] **Step 6: Commit**

```powershell
git add -A office_gateway tests
git commit -m "refactor(gateway): drop LiteLLM passthrough and watch endpoints; ingress/llm ids in brief"
```

---

### Task 7: Tool core — envelope, arguments, dispatch

**Files:**
- Create: `office_gateway/tools/core.py`
- Create: `office_gateway/tools/__init__.py` (registry, filled by later tasks)
- Create: `tests/test_office_gateway_tools_core.py`
- Create: `tests/test_office_gateway_redact.py`

**Interfaces:**
- Produces: `RESULT_CAP = 2000`, `TOOL_TIMEOUT_SECONDS = 12.0`, `ToolError(category, detail, valid=None)`, `Param(type, description, required=False, enum=(), minimum=None, maximum=None, max_length=None, default=None)`, `Tool(name, roles, description, params, handler)` with `input_schema()`, `ToolContext` (fields `config, store, actions, docker, edge, k8s, collector, proc, systemd`), `ok(data)`, `err(category, detail, valid=None)`, `cap(envelope)`, `validate_args(tool, args) -> dict`, `fit_items(items, budget=1500) -> tuple[list, int]`, `async call_tool(ctx, registry, role, name, args) -> dict`. Handler signature: `async def handler(ctx: ToolContext, args: dict, role: str) -> dict`.
- Produces: `tools.ALL_TOOLS: tuple[Tool, ...]`, `tools.REGISTRY: dict[str, Tool]`, `tools.tools_for_role(role) -> list[Tool]`.

- [ ] **Step 1: Write the failing tests**

<!-- file: tests/test_office_gateway_redact.py -->
```python
from office_gateway.redact import redact_text


def test_redacts_common_secret_shapes():
    cases = {
        "Authorization: Bearer abcdefghijklmnop": "Authorization: Bearer [redacted]",
        "key sk-live_1234567890abcdef used": "key [redacted] used",
        "password=hunter22 ok": "password=[redacted] ok",
        'api_key: "zzzzzzzz"': 'api_key: "[redacted]"',
        "postgres://app:s3cret@db:5432/x": "postgres://app:[redacted]@db:5432/x",
        "AKIAABCDEFGHIJKLMNOP": "[redacted]",
    }
    for raw, expected in cases.items():
        assert redact_text(raw) == expected


def test_leaves_normal_lines():
    line = "GET /grafana/api/health 200 12ms"
    assert redact_text(line) == line
```

<!-- file: tests/test_office_gateway_tools_core.py -->
```python
import asyncio
import json

from office_gateway.actions import ActionError
from office_gateway.tools.core import (
    RESULT_CAP,
    Param,
    Tool,
    ToolError,
    call_tool,
    fit_items,
    validate_args,
)


async def _echo(ctx, args, role):
    return {"args": args, "role": role}


async def _boom(ctx, args, role):
    raise ToolError("no_metrics", "empty", valid=["a"])


async def _action_error(ctx, args, role):
    raise ActionError("unknown container", "invalid_argument", ["x", "y"])


async def _huge(ctx, args, role):
    return {"blob": "x" * 5000}


async def _slow(ctx, args, role):
    await asyncio.sleep(1)
    return {}


def _tool(name, handler, **params):
    return Tool(name=name, roles=frozenset({"obs"}), description=name, params=params, handler=handler)


REG = {
    t.name: t
    for t in (
        _tool(
            "echo",
            _echo,
            window=Param("string", "window", enum=("5m", "1h"), default="5m"),
            lines=Param("integer", "lines", minimum=1, maximum=100, default=30),
            name=Param("string", "name", required=True, max_length=10),
        ),
        _tool("boom", _boom),
        _tool("act", _action_error),
        _tool("huge", _huge),
        _tool("slow", _slow),
    )
}


def _call(name, args=None, role="obs"):
    return asyncio.run(call_tool(None, REG, role, name, args or {}))


def test_ok_envelope_with_defaults():
    out = _call("echo", {"name": "a"})
    assert out == {"ok": True, "data": {"args": {"name": "a", "window": "5m", "lines": 30}, "role": "obs"}}


def test_invalid_arguments():
    assert _call("echo", {})["error"]["category"] == "invalid_argument"
    out = _call("echo", {"name": "a", "window": "2d"})
    assert out["error"]["valid"] == ["5m", "1h"]
    assert _call("echo", {"name": "a", "lines": 500})["error"]["category"] == "invalid_argument"
    assert _call("echo", {"name": "a", "extra": 1})["error"]["category"] == "invalid_argument"
    assert _call("echo", {"name": "x" * 11})["error"]["category"] == "invalid_argument"


def test_unknown_tool_and_role_forbidden():
    assert _call("nope")["error"]["category"] == "invalid_argument"
    assert _call("echo", {"name": "a"}, role="vector")["error"]["category"] == "forbidden"


def test_tool_and_action_errors_map_to_categories():
    assert _call("boom")["error"] == {"category": "no_metrics", "detail": "empty", "valid": ["a"]}
    assert _call("act")["error"]["valid"] == ["x", "y"]


def test_results_are_capped():
    out = _call("huge")
    assert len(json.dumps(out)) <= RESULT_CAP
    assert out["ok"] is True and out["data"]["truncated"] is True


def test_timeout(monkeypatch):
    import office_gateway.tools.core as core

    monkeypatch.setattr(core, "TOOL_TIMEOUT_SECONDS", 0.05)
    assert _call("slow")["error"]["category"] == "timeout"


def test_input_schema_shape():
    schema = REG["echo"].input_schema()
    assert schema["type"] == "object" and schema["additionalProperties"] is False
    assert schema["required"] == ["name"]
    assert schema["properties"]["window"]["enum"] == ["5m", "1h"]


def test_fit_items_respects_budget():
    kept, omitted = fit_items([{"n": "x" * 100} for _ in range(50)], budget=500)
    assert len(kept) < 50 and omitted == 50 - len(kept)
```

- [ ] **Step 2: Run to verify failure**

Expected: `ModuleNotFoundError: office_gateway.tools`.

- [ ] **Step 3: Implement**

<!-- file: office_gateway/tools/core.py -->
```python
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from office_gateway.actions import ActionError
from office_gateway.edge_ops import AdapterNotConfigured as EdgeAdapterNotConfigured
from office_gateway.k8s_ops import AdapterNotConfigured

RESULT_CAP = 2000
TOOL_TIMEOUT_SECONDS = 12.0
UPSTREAM_TIMEOUT_SECONDS = 10.0
ERROR_CATEGORIES = frozenset(
    {"unreachable", "timeout", "forbidden", "invalid_argument", "no_metrics", "not_configured"}
)


class ToolError(Exception):
    def __init__(self, category: str, detail: str, valid=None) -> None:
        super().__init__(detail)
        self.category = category if category in ERROR_CATEGORIES else "invalid_argument"
        self.detail = detail
        self.valid = list(valid or [])


@dataclass(frozen=True)
class Param:
    type: str
    description: str
    required: bool = False
    enum: tuple = ()
    minimum: int | None = None
    maximum: int | None = None
    max_length: int | None = None
    default: Any = None

    def schema(self) -> dict:
        out: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum:
            out["enum"] = list(self.enum)
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.max_length is not None:
            out["maxLength"] = self.max_length
        if self.default is not None:
            out["default"] = self.default
        return out


Handler = Callable[["ToolContext", dict, str], Awaitable[dict]]


@dataclass(frozen=True)
class Tool:
    name: str
    roles: frozenset[str]
    description: str
    params: dict[str, Param]
    handler: Handler

    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {name: p.schema() for name, p in self.params.items()},
            "required": [name for name, p in self.params.items() if p.required],
            "additionalProperties": False,
        }


@dataclass
class ToolContext:
    config: Any
    store: Any
    actions: Any
    docker: Any
    edge: Any
    k8s: Any
    collector: Any
    proc: Any
    systemd: Any
    extras: dict = field(default_factory=dict)


def ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def err(category: str, detail: str, valid=None) -> dict:
    error: dict[str, Any] = {"category": category, "detail": str(detail)[:300]}
    if valid:
        error["valid"] = [str(v) for v in list(valid)[:40]]
    return {"ok": False, "error": error}


def _size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))


def cap(envelope: dict) -> dict:
    if _size(envelope) <= RESULT_CAP:
        return envelope
    if not envelope.get("ok"):
        error = envelope.get("error", {})
        return err(error.get("category", "unreachable"), str(error.get("detail", ""))[:200])
    text = json.dumps(envelope.get("data"), ensure_ascii=False, separators=(",", ":"), default=str)
    return ok({"truncated": True, "text": text[: RESULT_CAP - 120]})


def fit_items(items: list, budget: int = 1500) -> tuple[list, int]:
    kept: list = []
    used = 0
    for item in items:
        size = _size(item) + 1
        if used + size > budget:
            break
        kept.append(item)
        used += size
    return kept, len(items) - len(kept)


def validate_args(tool: Tool, args: Any) -> dict:
    if not isinstance(args, dict):
        raise ToolError("invalid_argument", "arguments must be an object")
    unknown = sorted(set(args) - set(tool.params))
    if unknown:
        raise ToolError("invalid_argument", f"unknown argument(s): {', '.join(unknown)}", sorted(tool.params))
    out: dict[str, Any] = {}
    for name, p in tool.params.items():
        value = args.get(name)
        if value is None or (isinstance(value, str) and not value.strip() and not p.required):
            if p.required:
                raise ToolError("invalid_argument", f"{name} is required")
            if p.default is not None:
                out[name] = p.default
            continue
        if p.type == "string":
            if not isinstance(value, str) or not value.strip():
                raise ToolError("invalid_argument", f"{name} must be a non-empty string")
            value = value.strip()
            if p.max_length is not None and len(value) > p.max_length:
                raise ToolError("invalid_argument", f"{name} longer than {p.max_length} characters")
        elif p.type == "integer":
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise ToolError("invalid_argument", f"{name} must be an integer")
            try:
                value = int(value)
            except ValueError as exc:
                raise ToolError("invalid_argument", f"{name} must be an integer") from exc
            if (p.minimum is not None and value < p.minimum) or (p.maximum is not None and value > p.maximum):
                raise ToolError("invalid_argument", f"{name} must be between {p.minimum} and {p.maximum}")
        elif p.type == "boolean":
            if not isinstance(value, bool):
                raise ToolError("invalid_argument", f"{name} must be true or false")
        if p.enum and value not in p.enum:
            raise ToolError("invalid_argument", f"{name} must be one of the valid values", p.enum)
        out[name] = value
    return out


async def call_tool(ctx: ToolContext, registry: dict[str, Tool], role: str, name: str, args: Any) -> dict:
    tool = registry.get(name)
    if tool is None:
        valid = sorted(t.name for t in registry.values() if role in t.roles)
        return err("invalid_argument", f"unknown tool {name}", valid)
    if role not in tool.roles:
        return err("forbidden", f"{name} is not available to {role}")
    try:
        clean = validate_args(tool, args if args is not None else {})
        data = await asyncio.wait_for(tool.handler(ctx, clean, role), TOOL_TIMEOUT_SECONDS)
        return cap(ok(data))
    except ToolError as exc:
        return cap(err(exc.category, exc.detail, exc.valid))
    except ActionError as exc:
        category = exc.category if exc.category in ERROR_CATEGORIES else "invalid_argument"
        if exc.category == "forbidden":
            category = "forbidden"
        return cap(err(category, str(exc), exc.valid))
    except (AdapterNotConfigured, EdgeAdapterNotConfigured):
        return err("not_configured", f"{name}: adapter not configured on the gateway")
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return err("timeout", f"{name} did not finish in time")
    except (httpx.HTTPError, OSError, ConnectionError) as exc:
        return err("unreachable", f"{name}: {type(exc).__name__}")
    except ValueError as exc:
        return err("invalid_argument", str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool must always answer with an envelope
        return err("unreachable", f"{name}: {type(exc).__name__}")
```

<!-- file: office_gateway/tools/__init__.py -->
```python
from __future__ import annotations

from office_gateway.tools.core import Tool

ALL_TOOLS: tuple[Tool, ...] = ()

REGISTRY: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}


def tools_for_role(role: str) -> list[Tool]:
    return [tool for tool in ALL_TOOLS if role in tool.roles]
```

- [ ] **Step 4: Run the two new test files**

Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add office_gateway/tools office_gateway/redact.py tests/test_office_gateway_tools_core.py tests/test_office_gateway_redact.py
git commit -m "feat(gateway): tool core with envelope, bounded arguments and error categories"
```

---

### Task 8: lab-host and ingress tools

**Files:**
- Create: `office_gateway/tools/common.py`, `office_gateway/tools/lab_host.py`, `office_gateway/tools/ingress.py`
- Modify: `office_gateway/tools/__init__.py`
- Create: `tests/test_office_gateway_tools_lab_ingress.py`

**Interfaces:**
- Consumes: `ToolContext` fields `docker` (DockerOps), `actions` (ActionService), `edge` (EdgeRoutesOps), `proc` (ProcOps), `systemd` (SystemdOps), `config.console_url`, `config.edge_tls_cert_path`.
- Produces tools: `action_status` (roles lab-host, ingress, obs); lab-host: `list_containers`, `inspect_container`, `tail_logs`, `host_resources`, `list_host_services`, `propose_restart`; ingress: `list_routes`, `edge_status`, `tls_status`, `tail_traefik_logs`, `validate_route_change`, `propose_route_change`, `propose_route_rollback`. Helper `common.logs_window(lines, limit, contains, budget=1500) -> {"lines", "omitted"}`.

- [ ] **Step 1: Write the failing tests**

<!-- file: tests/test_office_gateway_tools_lab_ingress.py -->
```python
import asyncio
import json

from gw_helpers import FakeDockerOps, make_config
from office_gateway.actions import ActionService
from office_gateway.edge_ops import EdgeRoutesOps
from office_gateway.store import GatewayStore
from office_gateway.tools import REGISTRY, tools_for_role
from office_gateway.tools.core import RESULT_CAP, ToolContext, call_tool

ROUTES = "grafana /grafana/ 10.0.0.1:3000 0\n"


class FakeProc:
    def host_usage(self):
        gib = 1024**3
        return {
            "loadavg": [4.0, 3.0, 2.0],
            "cpu_count": 8,
            "memory_bytes": {"total": 16 * gib, "available": 4 * gib, "free": 1 * gib},
            "swap_bytes": {"total": 2 * gib, "used": 1 * gib},
            "disk_bytes": {"total": 100 * gib, "used": 90 * gib, "free": 10 * gib, "path": "/"},
            "uptime_seconds": 7200.0,
        }


class FakeSystemd:
    def list_units(self, unit_type="service"):
        return {
            "units": [
                {"id": "docker.service", "description": "", "load_state": "loaded", "active_state": "active", "sub_state": "running"},
                {"id": "nginx.service", "description": "", "load_state": "loaded", "active_state": "failed", "sub_state": "failed"},
            ],
            "truncated": False,
        }


def _ctx(tmp_path, docker=None):
    config = make_config(tmp_path)
    store = GatewayStore(config.db_path)
    routes = tmp_path / "edge-routes"
    routes.write_text(ROUTES, encoding="utf-8")
    edge = EdgeRoutesOps(str(routes), str(tmp_path / "routes.yml"))
    docker = docker or FakeDockerOps(
        logs={
            "aiplatform-api": [f"line {i} ok" for i in range(300)] + ["ERROR token=abcdef123456"],
            "office-office-edge-1": ["GET /grafana 200", "GET /attu 502"],
        }
    )
    actions = ActionService(config, store, docker_ops=docker, edge_ops=edge)
    return ToolContext(config, store, actions, docker, edge, None, None, FakeProc(), FakeSystemd())


def _call(ctx, role, name, args=None):
    out = asyncio.run(call_tool(ctx, REGISTRY, role, name, args or {}))
    assert len(json.dumps(out)) <= RESULT_CAP
    return out


def test_role_catalogs():
    lab = {t.name for t in tools_for_role("lab-host")}
    assert lab == {
        "list_containers", "inspect_container", "tail_logs", "host_resources",
        "list_host_services", "propose_restart", "action_status",
    }
    ingress = {t.name for t in tools_for_role("ingress")}
    assert ingress == {
        "list_routes", "edge_status", "tls_status", "tail_traefik_logs",
        "validate_route_change", "propose_route_change", "propose_route_rollback", "action_status",
    }


def test_list_and_inspect_containers(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "lab-host", "list_containers")["data"]
    assert data["total"] == 3 and data["not_running"] == ["broken-worker (exited)"]
    bad = _call(ctx, "lab-host", "inspect_container", {"name": "nope"})
    assert bad["error"]["category"] == "invalid_argument" and "aiplatform-api" in bad["error"]["valid"]
    info = _call(ctx, "lab-host", "inspect_container", {"name": "aiplatform-api"})["data"]
    assert info["env_names"] == ["PATH", "SECRET_TOKEN"] and "env" not in info


def test_tail_logs_is_bounded_redacted_and_filterable(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "lab-host", "tail_logs", {"name": "aiplatform-api", "lines": 100})["data"]
    assert data["lines"][-1] == "ERROR token=[redacted]"
    assert data["omitted"] > 0
    only = _call(ctx, "lab-host", "tail_logs", {"name": "aiplatform-api", "contains": "error"})["data"]
    assert only["lines"] == ["ERROR token=[redacted]"]


def test_host_resources_and_services(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "lab-host", "host_resources")["data"]
    assert data["mem_used_pct"] == 75.0 and data["disk_used_pct"] == 90.0 and data["load_per_core"] == 0.5
    services = _call(ctx, "lab-host", "list_host_services")["data"]
    assert services["failed"] == 1 and services["units"] == ["nginx.service (failed/failed)"]


def test_propose_restart_and_action_status(tmp_path):
    ctx = _ctx(tmp_path)
    missing = _call(ctx, "lab-host", "propose_restart", {"container": "aiplatform-api"})
    assert missing["error"]["category"] == "invalid_argument"
    data = _call(ctx, "lab-host", "propose_restart", {"container": "aiplatform-api", "reason": "hung"})["data"]
    assert data["status"] == "pending" and data["approve_at"] == "https://console.test/approvals/"
    status = _call(ctx, "lab-host", "action_status", {"action_id": data["action_id"]})["data"]
    assert status["status"] == "pending"
    assert _call(ctx, "ingress", "action_status", {"action_id": data["action_id"]})["ok"] is False
    assert ctx.docker.restarted == []


def test_ingress_routes_validate_and_propose(tmp_path):
    ctx = _ctx(tmp_path)
    assert _call(ctx, "ingress", "list_routes")["data"]["routes"][0]["name"] == "grafana"
    bad = _call(ctx, "ingress", "validate_route_change", {"content": "x /x 1 0\n"})
    assert bad["error"]["category"] == "invalid_argument"
    new = ROUTES + "attu /attu/ 10.0.0.2:8000 1\n"
    ok = _call(ctx, "ingress", "validate_route_change", {"content": new})["data"]
    assert ok["valid"] is True and "+attu /attu/ 10.0.0.2:8000 1" in ok["diff"]
    proposed = _call(ctx, "ingress", "propose_route_change", {"content": new, "reason": "add attu"})["data"]
    assert proposed["status"] == "pending"
    none = _call(ctx, "ingress", "propose_route_rollback", {"reason": "undo"})
    assert none["error"]["category"] == "invalid_argument"


def test_traefik_logs_only_edge_container(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "ingress", "tail_traefik_logs", {"contains": "502"})["data"]
    assert data["lines"] == ["GET /attu 502"]
```

- [ ] **Step 2: Run to verify failure**

Expected: catalog assertions fail (empty registry).

- [ ] **Step 3: Implement the modules**

<!-- file: office_gateway/tools/common.py -->
```python
from __future__ import annotations

from office_gateway.actions import action_view
from office_gateway.redact import redact_text
from office_gateway.tools.core import Param, Tool

LINE_MAX = 200


def logs_window(lines: list[str], limit: int, contains: str | None, budget: int = 1500) -> dict:
    rows = [redact_text(line)[:LINE_MAX] for line in lines]
    if contains:
        needle = contains.lower()
        rows = [row for row in rows if needle in row.lower()]
    rows = rows[-limit:]
    kept: list[str] = []
    used = 0
    for row in reversed(rows):
        if used + len(row) + 4 > budget:
            break
        kept.append(row)
        used += len(row) + 4
    kept.reverse()
    return {"lines": kept, "omitted": len(rows) - len(kept)}


def pending_view(action, console_url: str) -> dict:
    return {
        "action_id": action.action_id,
        "status": action.status,
        "summary": action.summary,
        "expires_at": action.expires_at,
        "approve_at": f"{console_url}/approvals/" if console_url else "/approvals/",
    }


async def _action_status(ctx, args, role):
    view = action_view(ctx.actions.status(role, args["action_id"]))
    return {k: view[k] for k in ("action_id", "status", "summary", "approver", "detail", "result")}


ACTION_STATUS = Tool(
    name="action_status",
    roles=frozenset({"lab-host", "ingress", "obs"}),
    description=(
        "Status of an action you proposed: pending, executing, succeeded, failed, rejected or expired. "
        "Only a human can approve; call this at most once per user request."
    ),
    params={"action_id": Param("string", "action_id returned by a propose_* tool", required=True, max_length=64)},
    handler=_action_status,
)

TOOLS = (ACTION_STATUS,)
```

<!-- file: office_gateway/tools/lab_host.py -->
```python
from __future__ import annotations

import asyncio

from office_gateway.docker_ops import compact_listing
from office_gateway.tools.common import logs_window, pending_view
from office_gateway.tools.core import Param, Tool, ToolError

LAB = frozenset({"lab-host"})
_LINES = Param("integer", "number of newest log lines (1-100)", minimum=1, maximum=100, default=30)
_CONTAINS = Param("string", "optional case-insensitive substring filter", max_length=60)


async def _names(ctx) -> list[str]:
    return await asyncio.to_thread(ctx.actions.container_names)


async def _require_container(ctx, name: str) -> None:
    names = await _names(ctx)
    if name not in names:
        raise ToolError("invalid_argument", "unknown container", names)


async def _list_containers(ctx, args, role):
    listing = await asyncio.to_thread(ctx.docker.list_containers)
    return compact_listing(listing.get("containers") or [])


async def _inspect_container(ctx, args, role):
    await _require_container(ctx, args["name"])
    info = await asyncio.to_thread(ctx.docker.inspect, args["name"])
    ports = [
        f"{p.get('host_ip') or '*'}:{p.get('host_port')}->{p.get('container_port')}/{p.get('protocol')}"
        if p.get("host_port")
        else f"{p.get('container_port')}/{p.get('protocol')}"
        for p in info.get("ports", [])
    ][:10]
    return {
        "name": info.get("name"),
        "status": info.get("status"),
        "health": info.get("health"),
        "image": info.get("image"),
        "compose_service": info.get("compose_service"),
        "restart_count": info.get("restart_count", 0),
        "started_at": info.get("started_at", ""),
        "ports": ports,
        "networks": [n.get("name") for n in info.get("networks", [])][:5],
        "env_names": info.get("env_names", []),
    }


async def _tail_logs(ctx, args, role):
    await _require_container(ctx, args["name"])
    fetch = 500 if args.get("contains") else args["lines"]
    raw = await asyncio.to_thread(ctx.docker.logs, args["name"], fetch)
    return {"name": args["name"], **logs_window(raw.get("lines", []), args["lines"], args.get("contains"))}


def _pct(used: float, total: float) -> float:
    return round(used / total * 100, 1) if total else 0.0


async def _host_resources(ctx, args, role):
    usage = await asyncio.to_thread(ctx.proc.host_usage)
    mem = usage["memory_bytes"]
    swap = usage["swap_bytes"]
    disk = usage["disk_bytes"]
    cores = max(1, int(usage.get("cpu_count") or 1))
    load = usage.get("loadavg", [0, 0, 0])
    return {
        "load": load,
        "cores": cores,
        "load_per_core": round(float(load[0]) / cores, 2),
        "mem_used_pct": _pct(mem["total"] - mem["available"], mem["total"]),
        "swap_used_pct": _pct(swap["used"], swap["total"]),
        "disk_used_pct": _pct(disk["used"], disk["total"]),
        "disk_path": disk.get("path", ""),
        "uptime_hours": round(float(usage.get("uptime_seconds") or 0) / 3600, 1),
    }


async def _list_host_services(ctx, args, role):
    listing = await asyncio.to_thread(ctx.systemd.list_units, "service")
    units = listing.get("units", [])
    failed = [u for u in units if u.get("active_state") == "failed"]
    state = args["state"]
    if state == "failed":
        chosen = failed
    elif state == "running":
        chosen = [u for u in units if u.get("sub_state") == "running"]
    else:
        chosen = units
    return {
        "total": len(units),
        "failed": len(failed),
        "units": [f"{u['id']} ({u.get('active_state')}/{u.get('sub_state')})" for u in chosen][:40],
    }


async def _propose_restart(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose, role, "restart_service", args["container"], {"reason": args["reason"]}
    )
    return pending_view(action, ctx.config.console_url)


TOOLS = (
    Tool("list_containers", LAB, "Counts of Lab Docker containers, the ones not running or unhealthy, and all names.", {}, _list_containers),
    Tool(
        "inspect_container",
        LAB,
        "Summary of one Lab container (status, health, image, ports, restart count, env variable names only).",
        {"name": Param("string", "exact container name from list_containers", required=True, max_length=128)},
        _inspect_container,
    ),
    Tool(
        "tail_logs",
        LAB,
        "Newest redacted log lines of one Lab container. Use contains= to find errors.",
        {"name": Param("string", "exact container name", required=True, max_length=128), "lines": _LINES, "contains": _CONTAINS},
        _tail_logs,
    ),
    Tool("host_resources", LAB, "Lab host load, RAM, swap, disk and uptime.", {}, _host_resources),
    Tool(
        "list_host_services",
        LAB,
        "systemd services on the Lab host (default: failed only).",
        {"state": Param("string", "which units to list", enum=("failed", "running", "all"), default="failed")},
        _list_host_services,
    ),
    Tool(
        "propose_restart",
        LAB,
        "Propose restarting one Lab container. Creates a pending action for a human to approve in the console; "
        "it does NOT restart anything. Report the action_id to the supervisor.",
        {
            "container": Param("string", "exact container name", required=True, max_length=128),
            "reason": Param("string", "one-line reason shown to the approver", required=True, max_length=200),
        },
        _propose_restart,
    ),
)
```

<!-- file: office_gateway/tools/ingress.py -->
```python
from __future__ import annotations

import asyncio

from office_gateway.edge_ops import route_diff
from office_gateway.tls_ops import tls_cert_status
from office_gateway.tools.common import logs_window, pending_view
from office_gateway.tools.core import Param, Tool, ToolError

INGRESS = frozenset({"ingress"})
_CONTENT = Param(
    "string",
    "full edge-routes file: one route per line `name /path/ host:port websocket(0|1) [strip_prefix(0|1)]`",
    required=True,
    max_length=8000,
)
_REASON = Param("string", "one-line reason shown to the approver", required=True, max_length=200)


def _edge_containers(ctx) -> list[dict]:
    listing = ctx.docker.list_containers()
    return [c for c in listing.get("containers", []) if c.get("compose_service") == "office-edge"]


async def _list_routes(ctx, args, role):
    routes = await asyncio.to_thread(ctx.edge.list_routes)
    return {
        "count": len(routes),
        "routes": [
            {"name": r.name, "path": r.path, "upstream": r.upstream, "websocket": r.websocket}
            for r in routes
        ][:40],
        "backups": ctx.edge.list_backups()[:10],
    }


async def _edge_status(ctx, args, role):
    containers = await asyncio.to_thread(_edge_containers, ctx)
    tls = tls_cert_status(ctx.config.edge_tls_cert_path)
    return {
        "configured": ctx.edge.configured(),
        "edge": [{"name": c["name"], "status": c["status"], "health": c.get("health")} for c in containers],
        "tls_days_left": tls.get("days_left"),
        "tls_error": tls.get("error"),
    }


async def _tls_status(ctx, args, role):
    return tls_cert_status(ctx.config.edge_tls_cert_path)


async def _tail_traefik_logs(ctx, args, role):
    containers = await asyncio.to_thread(_edge_containers, ctx)
    if not containers:
        raise ToolError("not_configured", "office-edge container not found")
    name = containers[0]["name"]
    fetch = 500 if args.get("contains") else args["lines"]
    raw = await asyncio.to_thread(ctx.docker.logs, name, fetch)
    return {"name": name, **logs_window(raw.get("lines", []), args["lines"], args.get("contains"))}


async def _validate_route_change(ctx, args, role):
    try:
        routes = ctx.edge.validate(args["content"])
    except ValueError as exc:
        raise ToolError("invalid_argument", str(exc)) from exc
    return {
        "valid": True,
        "routes": len(routes),
        "diff": route_diff(ctx.edge.read_text_or_empty(), args["content"], limit=30),
    }


async def _propose_route_change(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose,
        role,
        "apply_edge_routes",
        "edge-routes",
        {"content": args["content"], "reason": args["reason"]},
    )
    return {**pending_view(action, ctx.config.console_url), "diff": action.params.get("diff", [])[:30]}


async def _propose_route_rollback(ctx, args, role):
    action = await asyncio.to_thread(
        ctx.actions.propose,
        role,
        "rollback_edge_routes",
        "edge-routes",
        {"backup": args.get("backup"), "reason": args["reason"]},
    )
    return {**pending_view(action, ctx.config.console_url), "diff": action.params.get("diff", [])[:30]}


_LINES = Param("integer", "number of newest log lines (1-100)", minimum=1, maximum=100, default=30)

TOOLS = (
    Tool("list_routes", INGRESS, "Parsed Lab-VM Traefik edge routes and the newest route backups.", {}, _list_routes),
    Tool("edge_status", INGRESS, "office-edge (Traefik) container state and TLS days left.", {}, _edge_status),
    Tool("tls_status", INGRESS, "Lab edge TLS certificate metadata (subject, expiry). No private key.", {}, _tls_status),
    Tool(
        "tail_traefik_logs",
        INGRESS,
        "Newest redacted office-edge (Traefik) log lines. Use contains= (e.g. 502) to filter.",
        {"lines": _LINES, "contains": Param("string", "optional substring filter", max_length=60)},
        _tail_traefik_logs,
    ),
    Tool(
        "validate_route_change",
        INGRESS,
        "Check a full edge-routes file and show the diff against the current file. Changes nothing.",
        {"content": _CONTENT},
        _validate_route_change,
    ),
    Tool(
        "propose_route_change",
        INGRESS,
        "Propose replacing edge-routes with this content. Creates a pending action for a human to approve; "
        "the current file is backed up and restored automatically if the apply fails.",
        {"content": _CONTENT, "reason": _REASON},
        _propose_route_change,
    ),
    Tool(
        "propose_route_rollback",
        INGRESS,
        "Propose restoring the newest (or a named) edge-routes backup. Needs human approval.",
        {"backup": Param("string", "backup name from list_routes; default newest", max_length=64), "reason": _REASON},
        _propose_route_rollback,
    ),
)
```

<!-- file: office_gateway/tools/__init__.py -->
```python
from __future__ import annotations

from office_gateway.tools import common, ingress, lab_host
from office_gateway.tools.core import Tool

ALL_TOOLS: tuple[Tool, ...] = (*common.TOOLS, *lab_host.TOOLS, *ingress.TOOLS)

REGISTRY: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}
assert len(REGISTRY) == len(ALL_TOOLS), "duplicate tool names"


def tools_for_role(role: str) -> list[Tool]:
    return [tool for tool in ALL_TOOLS if role in tool.roles]
```

Also fix `office_gateway/systemd_ops.py`: its `from office_gateway.docker_ops import LIST_CAP` now resolves (added in Task 5). Add a one-line import test to the new test file if the module was never imported before:

```python
def test_systemd_ops_imports():
    import office_gateway.systemd_ops  # noqa: F401
```

- [ ] **Step 4: Run the tests**

Run the test command with `tests/test_office_gateway_tools_lab_ingress.py tests/test_office_gateway_tools_core.py`. Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add office_gateway/tools tests/test_office_gateway_tools_lab_ingress.py
git commit -m "feat(gateway): lab-host and ingress MCP tools"
```

---

### Task 9: Supervisor, llm, cluster-gpu, vector, obs tools; named queries

**Files:**
- Create: `office_gateway/reports/__init__.py` (empty), `office_gateway/reports/queries.py`, `office_gateway/tools/status.py`, `office_gateway/tools/llm.py`, `office_gateway/tools/cluster.py`, `office_gateway/tools/vector.py`, `office_gateway/tools/obs.py`
- Modify: `office_gateway/tools/__init__.py`, `office_gateway/app.py` (`/v1/metrics/query` takes `name`, `instance`, `window`)
- Create: `tests/test_office_gateway_tools_other.py`
- Modify: `tests/test_office_gateway_metrics.py` (REST now uses `?name=`)

**Interfaces:**
- Produces: `queries.WINDOWS`, `queries.QUERIES: dict[str, NamedQuery]`, `queries.render(name, instance=None, window=None) -> str` (raises `ValueError`). Tools: supervisor `fleet_status`; llm `llm_status`, `list_models`; cluster-gpu `k8s_get`, `mig_map`, `gpu_usage`; vector `vector_status`; obs `metrics_query`, `grafana_links`.
- Consumes: `ctx.collector.collect()`, `ctx.store.list_actions`, `llm_ops.fetch_litellm_status`, `ctx.k8s.list_resources/get_mig_actual/get_gpu_metrics`, `mig.compare_mig`, `metrics.instant_query`, `grafana_links.build_links`, `brief.FLEET_AGENTS`.

- [ ] **Step 1: Write the failing tests**

<!-- file: tests/test_office_gateway_tools_other.py -->
```python
import asyncio
import json

import httpx
import pytest

from gw_helpers import make_config
from office_gateway.reports import queries
from office_gateway.store import GatewayStore
from office_gateway.tools import REGISTRY, tools_for_role
from office_gateway.tools.core import RESULT_CAP, ToolContext, call_tool


class FakeCollector:
    def __init__(self, services):
        self.services = services

    async def collect(self):
        return self.services


class FakeK8s:
    async def list_resources(self, kind, namespace):
        return {"kind": kind, "items": [{"name": f"pod-{i}", "phase": "Running"} for i in range(200)]}

    async def get_mig_actual(self):
        return {"actual": {"1g.18gb": 7}}

    async def get_gpu_metrics(self):
        return {"gpus": [{"index": 0, "util": 40}]}


def _ctx(tmp_path, services=None, **cfg):
    config = make_config(tmp_path, **cfg)
    store = GatewayStore(config.db_path)
    collector = FakeCollector(services or [])
    return ToolContext(config, store, None, None, None, FakeK8s(), collector, None, None)


def _call(ctx, role, name, args=None):
    out = asyncio.run(call_tool(ctx, REGISTRY, role, name, args or {}))
    assert len(json.dumps(out)) <= RESULT_CAP
    return out


def test_role_catalogs():
    assert {t.name for t in tools_for_role("supervisor")} == {"fleet_status"}
    assert {t.name for t in tools_for_role("llm")} == {"llm_status", "list_models"}
    assert {t.name for t in tools_for_role("cluster-gpu")} == {"k8s_get", "mig_map", "gpu_usage"}
    assert {t.name for t in tools_for_role("vector")} == {"vector_status"}
    assert {t.name for t in tools_for_role("obs")} == {"metrics_query", "grafana_links", "action_status"}
    assert tools_for_role("approver") == []


def test_fleet_status_one_line_per_domain(tmp_path):
    services = [
        {"name": "gateway", "status": "ok"},
        {"name": "hermes-lab-host", "status": "ok"},
        {"name": "aiplatform", "status": "critical"},
        {"name": "hermes-ingress", "status": "unknown"},
    ]
    data = _call(_ctx(tmp_path, services), "supervisor", "fleet_status")["data"]
    lines = data["domains"]
    assert len(lines) == 7
    assert any(line.startswith("lab-host: agent ok") and "aiplatform=critical" in line for line in lines)
    assert any(line.startswith("ingress: agent unknown") for line in lines)
    assert data["pending_approvals"] == 0


def test_k8s_get_is_bounded(tmp_path):
    ctx = _ctx(tmp_path)
    data = _call(ctx, "cluster-gpu", "k8s_get", {"kind": "pods", "namespace": "litellm"})["data"]
    assert data["count"] == 200 and data["omitted"] > 0
    bad = _call(ctx, "cluster-gpu", "k8s_get", {"kind": "secrets-all"})
    assert bad["error"]["category"] == "invalid_argument"


def test_mig_map_and_gpu_usage(tmp_path):
    ctx = _ctx(tmp_path, mig_expected={"1g.18gb": 7})
    assert _call(ctx, "cluster-gpu", "mig_map")["data"]["ok"] is True
    assert _call(ctx, "cluster-gpu", "gpu_usage")["data"]["gpus"][0]["util"] == 40


def test_vector_status_reports_per_instance(tmp_path, monkeypatch):
    def handler(request):
        if "down" in str(request.url):
            raise httpx.ConnectError("refused")
        return httpx.Response(200, text="ok")

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))
    ctx = _ctx(tmp_path, vector_instances={"milvus-dev": "http://up/healthz", "milvus-prod": "http://down/healthz"})
    rows = {r["instance"]: r for r in _call(ctx, "vector", "vector_status")["data"]["instances"]}
    assert rows["milvus-dev"]["status"] == "up" and rows["milvus-prod"]["status"] == "unreachable"
    one = _call(ctx, "vector", "vector_status", {"instance": "milvus-dev"})["data"]["instances"]
    assert [r["instance"] for r in one] == ["milvus-dev"]


def test_named_query_render():
    q = queries.render("cpu_busy_pct", instance="10.216.4.80", window="1h")
    assert 'instance=~"^10\\\\.216\\\\.4\\\\.80(:[0-9]+)?$"' in q and "[1h]" in q
    assert queries.render("targets_down") == "up == 0"
    with pytest.raises(ValueError):
        queries.render("cpu_busy_pct", instance='x"}) or vector(1')
    with pytest.raises(ValueError):
        queries.render("nope")


def test_metrics_query_no_metrics(tmp_path, monkeypatch):
    import office_gateway.tools.obs as obs

    async def empty(url, query):
        return {"status": "success", "data": {"resultType": "vector", "result": []}}

    monkeypatch.setattr(obs, "instant_query", empty)
    ctx = _ctx(tmp_path, metrics_url="http://vm:8428")
    out = _call(ctx, "obs", "metrics_query", {"name": "targets_down"})
    assert out["error"]["category"] == "no_metrics"
    bad = _call(ctx, "obs", "metrics_query", {"name": "up{job=~'.*'}"})
    assert bad["error"]["category"] == "invalid_argument" and "targets_down" in bad["error"]["valid"]


def test_metrics_query_values(tmp_path, monkeypatch):
    import office_gateway.tools.obs as obs

    async def some(url, query):
        return {
            "status": "success",
            "data": {"resultType": "vector", "result": [{"metric": {"instance": "a:9100", "job": "node"}, "value": [0, "12.5"]}]},
        }

    monkeypatch.setattr(obs, "instant_query", some)
    ctx = _ctx(tmp_path, metrics_url="http://vm:8428")
    data = _call(ctx, "obs", "metrics_query", {"name": "cpu_busy_pct"})["data"]
    assert data["series"] == 1 and data["values"][0] == {"labels": {"instance": "a:9100", "job": "node"}, "value": 12.5}
```

- [ ] **Step 2: Run to verify failure**

Expected: catalog and import failures.

- [ ] **Step 3: Implement**

<!-- file: office_gateway/reports/queries.py -->
```python
from __future__ import annotations

import re
from dataclasses import dataclass

WINDOWS = ("5m", "1h", "24h", "7d", "30d")
_INSTANCE_RE = re.compile(r"^[A-Za-z0-9.:_-]{1,64}$")
_FS = 'fstype!~"tmpfs|overlay|squashfs|devtmpfs"'


@dataclass(frozen=True)
class NamedQuery:
    description: str
    template: str
    uses_instance: bool = False
    default_window: str | None = None


QUERIES: dict[str, NamedQuery] = {
    "targets_up": NamedQuery("Scrape targets up, per job", "sum by (job) (up)"),
    "targets_down": NamedQuery("Scrape targets currently down", "up == 0"),
    "cpu_busy_pct": NamedQuery(
        "CPU busy percent per instance",
        '100 * (1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"{sel}}[{window}])))',
        uses_instance=True,
        default_window="5m",
    ),
    "mem_used_pct": NamedQuery(
        "RAM used percent per instance",
        "100 * (1 - node_memory_MemAvailable_bytes{isel} / node_memory_MemTotal_bytes{isel})",
        uses_instance=True,
    ),
    "disk_used_pct": NamedQuery(
        "Filesystem used percent per instance and mountpoint",
        f"100 * (1 - node_filesystem_avail_bytes{{{_FS}{{sel}}}} / node_filesystem_size_bytes{{{_FS}{{sel}}}})",
        uses_instance=True,
    ),
    "load_per_core": NamedQuery(
        "1-minute load divided by CPU cores",
        'node_load1{isel} / on (instance) count by (instance) (node_cpu_seconds_total{mode="idle"{sel}})',
        uses_instance=True,
    ),
    "probe_success": NamedQuery("Blackbox probe success (1 up, 0 down)", "probe_success{isel}", uses_instance=True),
    "availability_pct": NamedQuery(
        "Probe availability percent over a window",
        "100 * avg_over_time(probe_success{isel}[{window}])",
        uses_instance=True,
        default_window="24h",
    ),
}


def render(name: str, instance: str | None = None, window: str | None = None) -> str:
    query = QUERIES.get(name)
    if query is None:
        raise ValueError("unknown query")
    sel = isel = ""
    if instance:
        if not query.uses_instance:
            raise ValueError("query takes no instance")
        if not _INSTANCE_RE.fullmatch(instance):
            raise ValueError("instance has invalid characters")
        pattern = instance.replace(".", "\\\\.")
        sel = f',instance=~"^{pattern}(:[0-9]+)?$"'
        isel = "{" + sel[1:] + "}"
    chosen_window = window or query.default_window or "5m"
    if chosen_window not in WINDOWS:
        raise ValueError("invalid window")
    return (
        query.template.replace("{sel}", sel).replace("{isel}", isel).replace("{window}", chosen_window)
    )
```

<!-- file: office_gateway/tools/status.py -->
```python
from __future__ import annotations

from office_gateway.brief import FLEET_AGENTS
from office_gateway.tools.core import Tool

_RANK = {"ok": 0, "unknown": 1, "warn": 2, "critical": 3}


async def _fleet_status(ctx, args, role):
    services = await ctx.collector.collect()
    by_name = {s.get("name"): s for s in services}
    lines = []
    for agent in FLEET_AGENTS:
        ready = by_name.get(agent["ready_service"], {}).get("status", "unknown")
        found = [by_name[n] for n in agent["service_names"] if n in by_name]
        worst = max((s.get("status", "unknown") for s in found), key=lambda s: _RANK.get(s, 1), default="unknown")
        bad = [f"{s['name']}={s.get('status')}" for s in found if s.get("status") != "ok"][:4]
        line = f"{agent['id']}: agent {ready}, services {worst if found else 'n/a'}"
        if bad:
            line += f" ({', '.join(bad)})"
        lines.append(line)
    pending = ctx.store.list_actions(("pending",), 50)
    return {"domains": lines, "pending_approvals": len(pending)}


TOOLS = (
    Tool(
        "fleet_status",
        frozenset({"supervisor"}),
        "One line per domain (agent health + domain services) and the number of pending approvals. "
        "Answer status questions from this without messaging specialists.",
        {},
        _fleet_status,
    ),
)
```

<!-- file: office_gateway/tools/llm.py -->
```python
from __future__ import annotations

from office_gateway.llm_ops import fetch_litellm_status
from office_gateway.tools.core import Tool, ToolError

LLM = frozenset({"llm"})


async def _status(ctx):
    data = await fetch_litellm_status(ctx.config.litellm_url, ctx.config.litellm_master_key)
    if not data.get("configured"):
        raise ToolError("not_configured", "LiteLLM URL or master key not set on the gateway")
    return data


async def _llm_status(ctx, args, role):
    data = await _status(ctx)
    return {k: data.get(k) for k in ("ok", "auth", "models", "keys", "error") if k in data}


async def _list_models(ctx, args, role):
    data = await _status(ctx)
    return {"count": data.get("models", 0), "models": data.get("model_ids", [])[:20]}


TOOLS = (
    Tool("llm_status", LLM, "LiteLLM prod API health: auth, model count, virtual key count.", {}, _llm_status),
    Tool("list_models", LLM, "Model aliases served by LiteLLM prod (max 20).", {}, _list_models),
)
```

<!-- file: office_gateway/tools/cluster.py -->
```python
from __future__ import annotations

import re

from office_gateway.k8s_ops import ALLOWED_KINDS
from office_gateway.mig import compare_mig
from office_gateway.tools.core import Param, Tool, ToolError, fit_items

GPU = frozenset({"cluster-gpu"})
_NS_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


async def _k8s_get(ctx, args, role):
    namespace = args.get("namespace")
    if namespace and not _NS_RE.fullmatch(namespace):
        raise ToolError("invalid_argument", "namespace is not a valid Kubernetes name")
    result = await ctx.k8s.list_resources(args["kind"], namespace)
    items = result.get("items", [])
    kept, omitted = fit_items(items, budget=1500)
    return {"kind": result.get("kind"), "count": len(items), "items": kept, "omitted": omitted}


async def _mig_map(ctx, args, role):
    mig = await ctx.k8s.get_mig_actual()
    result = compare_mig(ctx.config.mig_expected, mig.get("actual", {}))
    if mig.get("error"):
        result["ok"] = False
        result["error"] = mig["error"]
    return result


async def _gpu_usage(ctx, args, role):
    return await ctx.k8s.get_gpu_metrics()


TOOLS = (
    Tool(
        "k8s_get",
        GPU,
        "List RKE2 resources of one kind (sanitised, bounded). Includes K8s Traefik HTTPRoute/Gateway/IngressRoute.",
        {
            "kind": Param("string", "resource kind", required=True, enum=tuple(sorted(ALLOWED_KINDS))),
            "namespace": Param("string", "namespace; omit for all namespaces", max_length=63),
        },
        _k8s_get,
    ),
    Tool("mig_map", GPU, "MIG profiles found on the GPU nodes compared with the expected layout.", {}, _mig_map),
    Tool("gpu_usage", GPU, "GPU utilisation and memory from DCGM.", {}, _gpu_usage),
)
```

<!-- file: office_gateway/tools/vector.py -->
```python
from __future__ import annotations

import asyncio
import time

import httpx

from office_gateway.tools.core import UPSTREAM_TIMEOUT_SECONDS, Param, Tool, ToolError

VECTOR = frozenset({"vector"})


async def _probe(client: httpx.AsyncClient, name: str, url: str) -> dict:
    start = time.monotonic()
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        return {"instance": name, "status": "timeout"}
    except httpx.HTTPError:
        return {"instance": name, "status": "unreachable"}
    return {
        "instance": name,
        "status": "up" if response.status_code < 400 else "down",
        "http": response.status_code,
        "latency_ms": int((time.monotonic() - start) * 1000),
    }


async def _vector_status(ctx, args, role):
    instances = ctx.config.vector_instances
    if not instances:
        raise ToolError("not_configured", "OFFICE_VECTOR_INSTANCES is empty")
    wanted = args.get("instance")
    if wanted and wanted not in instances:
        raise ToolError("invalid_argument", "unknown instance", sorted(instances))
    chosen = {wanted: instances[wanted]} if wanted else instances
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        rows = await asyncio.gather(*(_probe(client, n, u) for n, u in chosen.items()))
    return {"instances": list(rows)}


TOOLS = (
    Tool(
        "vector_status",
        VECTOR,
        "Health of the vector databases: milvus-dev (Lab VM), milvus-prod (10.216.203.132, read-only), qdrant-dev.",
        {"instance": Param("string", "one instance; omit for all", max_length=32)},
        _vector_status,
    ),
)
```

<!-- file: office_gateway/tools/obs.py -->
```python
from __future__ import annotations

from office_gateway.reports import queries
from office_gateway.grafana_links import build_links
from office_gateway.metrics import instant_query
from office_gateway.tools.core import Param, Tool, ToolError, fit_items

OBS = frozenset({"obs"})
_KEEP_LABELS = ("instance", "job", "mountpoint", "device", "service", "namespace", "pod")


async def _metrics_query(ctx, args, role):
    if not ctx.config.metrics_url:
        raise ToolError("not_configured", "OFFICE_METRICS_URL is not set")
    try:
        promql = queries.render(args["name"], args.get("instance"), args.get("window"))
    except ValueError as exc:
        raise ToolError("invalid_argument", str(exc)) from exc
    payload = await instant_query(ctx.config.metrics_url, promql)
    result = (payload.get("data") or {}).get("result") or []
    if not result:
        raise ToolError("no_metrics", f"{args['name']} returned no series")
    rows = []
    for series in result:
        labels = {k: v for k, v in (series.get("metric") or {}).items() if k in _KEEP_LABELS}
        value = series.get("value", [None, None])[1]
        try:
            value = round(float(value), 2)
        except (TypeError, ValueError):
            pass
        rows.append({"labels": labels, "value": value})
    kept, omitted = fit_items(rows, budget=1500)
    return {"query": args["name"], "series": len(rows), "values": kept, "omitted": omitted}


async def _grafana_links(ctx, args, role):
    links = build_links(
        base=ctx.config.grafana_base_url,
        dashboards=ctx.config.grafana_dashboards,
        panels=ctx.config.grafana_panel_ids,
    )
    kept, omitted = fit_items(links, budget=1500)
    return {"links": kept, "omitted": omitted}


TOOLS = (
    Tool(
        "metrics_query",
        OBS,
        "Run a named VictoriaMetrics query. Names: "
        + "; ".join(f"{k} = {v.description}" for k, v in queries.QUERIES.items()),
        {
            "name": Param("string", "named query", required=True, enum=tuple(queries.QUERIES)),
            "instance": Param("string", "optional host or host:port filter", max_length=64),
            "window": Param("string", "time window where the query uses one", enum=queries.WINDOWS),
        },
        _metrics_query,
    ),
    Tool("grafana_links", OBS, "Deep links to the configured Grafana dashboards and panels.", {}, _grafana_links),
)
```

<!-- file: office_gateway/tools/__init__.py -->
```python
from __future__ import annotations

from office_gateway.tools import cluster, common, ingress, lab_host, llm, obs, status, vector
from office_gateway.tools.core import Tool

ALL_TOOLS: tuple[Tool, ...] = (
    *status.TOOLS,
    *common.TOOLS,
    *lab_host.TOOLS,
    *ingress.TOOLS,
    *llm.TOOLS,
    *cluster.TOOLS,
    *vector.TOOLS,
    *obs.TOOLS,
)

REGISTRY: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}
assert len(REGISTRY) == len(ALL_TOOLS), "duplicate tool names"


def tools_for_role(role: str) -> list[Tool]:
    return [tool for tool in ALL_TOOLS if role in tool.roles]
```

In `office_gateway/metrics.py`, lower the `instant_query` client timeout from 15.0 to 10.0 s (upstream budget; the tool call budget is 12 s).

Change the REST `/v1/metrics/query` handler to take `name: str = "", instance: str = "", window: str = ""`, render with `queries.render(name, instance or None, window or None)` (400 on `ValueError`) and call `instant_query` with the rendered PromQL. Update `tests/test_office_gateway_metrics.py` so its query calls use `?name=targets_down` and its "not allowlisted" case uses `?name=bogus` expecting 400.

- [ ] **Step 4: Run the gateway suite**

Run the test command with `tests/test_office_gateway_*.py`. Expected: all PASS.

- [ ] **Step 5: Commit**

```powershell
git add -A office_gateway tests
git commit -m "feat(gateway): supervisor/llm/cluster/vector/obs tools and named query registry"
```

---

### Task 10: MCP endpoint at `/mcp` and contract test

**Files:**
- Create: `office_gateway/mcp_server.py`
- Modify: `office_gateway/app.py` (build `ToolContext`, include the MCP router)
- Create: `tests/test_office_gateway_mcp.py`
- Create: `tests/integration/mcp_contract.py`

**Interfaces:**
- Consumes: `tools.REGISTRY`, `tools.tools_for_role`, `tools.core.call_tool`, `ToolContext`.
- Produces: `mcp_server.build_router(ctx, role_dependency) -> APIRouter`; `mcp_server.SUPPORTED_PROTOCOL_VERSIONS`. `POST /mcp` JSON-RPC (`initialize`, `notifications/*` → 202, `ping`, `tools/list`, `tools/call`), `GET /mcp` and `DELETE /mcp` → 405. Approver token → 403.

- [ ] **Step 1: Write the failing unit tests**

<!-- file: tests/test_office_gateway_mcp.py -->
```python
import json

from fastapi.testclient import TestClient

from gw_helpers import FakeDockerOps, auth, make_config
from office_gateway.app import create_app


def _client(tmp_path):
    return TestClient(create_app(make_config(tmp_path), docker_ops=FakeDockerOps()))


def _rpc(client, role, method, params=None, id_=1):
    body = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    headers = {**auth(role), "Accept": "application/json, text/event-stream"}
    return client.post("/mcp", headers=headers, json=body)


def test_initialize_echoes_supported_version(tmp_path):
    client = _client(tmp_path)
    r = _rpc(client, "lab-host", "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    result = r.json()["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    r = _rpc(client, "lab-host", "initialize", {"protocolVersion": "1999-01-01"})
    assert r.json()["result"]["protocolVersion"] == "2025-06-18"


def test_notifications_get_202(tmp_path):
    r = _rpc(_client(tmp_path), "lab-host", "notifications/initialized", id_=None)
    assert r.status_code == 202 and r.content == b""


def test_tools_list_is_role_filtered(tmp_path):
    client = _client(tmp_path)
    sup = {t["name"] for t in _rpc(client, "supervisor", "tools/list").json()["result"]["tools"]}
    assert sup == {"fleet_status"}
    lab = _rpc(client, "lab-host", "tools/list").json()["result"]["tools"]
    names = {t["name"] for t in lab}
    assert "propose_restart" in names and "fleet_status" not in names
    tool = next(t for t in lab if t["name"] == "tail_logs")
    assert tool["inputSchema"]["properties"]["lines"]["maximum"] == 100


def test_tools_call_returns_envelope_text(tmp_path):
    client = _client(tmp_path)
    r = _rpc(client, "lab-host", "tools/call", {"name": "list_containers", "arguments": {}})
    result = r.json()["result"]
    envelope = json.loads(result["content"][0]["text"])
    assert result["isError"] is False and envelope["data"]["total"] == 3


def test_tools_call_forbidden_tool_is_error_envelope(tmp_path):
    client = _client(tmp_path)
    r = _rpc(client, "supervisor", "tools/call", {"name": "propose_restart", "arguments": {"container": "x", "reason": "y"}})
    result = r.json()["result"]
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"]["category"] == "forbidden"


def test_auth_and_methods(tmp_path):
    client = _client(tmp_path)
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert _rpc(client, "approver", "tools/list").status_code == 403
    assert client.get("/mcp", headers=auth("lab-host")).status_code == 405
    assert _rpc(client, "lab-host", "ping").json()["result"] == {}
    assert _rpc(client, "lab-host", "resources/list").json()["error"]["code"] == -32601
    bad = client.post("/mcp", headers={**auth("lab-host"), "Content-Type": "application/json"}, content=b"{")
    assert bad.json()["error"]["code"] == -32700
```

- [ ] **Step 2: Run to verify failure**

Expected: 404 on `/mcp`.

- [ ] **Step 3: Implement the endpoint**

<!-- file: office_gateway/mcp_server.py -->
```python
from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from office_gateway.roles import AGENT_ROLES
from office_gateway.tools import REGISTRY, tools_for_role
from office_gateway.tools.core import ToolContext, call_tool

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-11-25", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "office-gateway", "version": "2026.9-phase1"}
INSTRUCTIONS = (
    "Office Lab tools. Every result is JSON {ok, data} or {ok:false, error:{category, detail, valid}}. "
    "Arguments are bounded; on invalid_argument pick from `valid`. propose_* tools never execute: "
    "a human approves in the console."
)


def _reply(id_: Any, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "result": result})


def _error(id_: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


def build_router(ctx: ToolContext, role_dependency: Callable[..., str]) -> APIRouter:
    router = APIRouter()

    @router.post("/mcp")
    async def mcp(request: Request, role: str = Depends(role_dependency)) -> Response:
        if role not in AGENT_ROLES:
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        try:
            message = json.loads(await request.body() or b"null")
        except ValueError:
            return _error(None, -32700, "parse error")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
            return _error(None, -32600, "invalid request")
        method = str(message["method"])
        id_ = message.get("id")
        params = message.get("params") or {}
        if id_ is None:
            return Response(status_code=202)
        if method == "initialize":
            requested = str(params.get("protocolVersion") or "")
            version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
            return _reply(
                id_,
                {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": INSTRUCTIONS,
                },
            )
        if method == "ping":
            return _reply(id_, {})
        if method == "tools/list":
            tools = [
                {"name": t.name, "description": t.description, "inputSchema": t.input_schema()}
                for t in tools_for_role(role)
            ]
            return _reply(id_, {"tools": tools})
        if method == "tools/call":
            name = str(params.get("name") or "")
            envelope = await call_tool(ctx, REGISTRY, role, name, params.get("arguments") or {})
            text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=str)
            return _reply(id_, {"content": [{"type": "text", "text": text}], "isError": not envelope["ok"]})
        return _error(id_, -32601, f"method not found: {method}")

    @router.get("/mcp")
    async def mcp_get() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    @router.delete("/mcp")
    async def mcp_delete() -> Response:
        return Response(status_code=405, headers={"Allow": "POST"})

    return router
```

In `create_app`, after building `actions` and `collector`, add:

<!-- file: office_gateway/app.py (inside create_app, after the dependency helpers are defined) -->
```python
    from office_gateway.mcp_server import build_router
    from office_gateway.proc_ops import ProcOps
    from office_gateway.systemd_ops import SystemdOps
    from office_gateway.tools.core import ToolContext

    tool_ctx = ToolContext(
        config=config,
        store=store,
        actions=actions,
        docker=ops,
        edge=edge,
        k8s=k8s,
        collector=collector,
        proc=ProcOps(),
        systemd=SystemdOps(),
    )
    app.include_router(build_router(tool_ctx, _role_from_request))
```

Place it after `_role_from_request` is defined. (`create_app` also accepts optional `proc_ops`/`systemd_ops` keyword args defaulting to `None` → `ProcOps()`/`SystemdOps()`, so tests can inject fakes.)

- [ ] **Step 4: Run the unit tests**

Run the test command with `tests/test_office_gateway_mcp.py`. Expected: all PASS.

- [ ] **Step 5: Write the contract client (runs inside the Hermes image with its MCP SDK)**

<!-- file: tests/integration/mcp_contract.py -->
```python
"""Run inside nousresearch/hermes-agent:v2026.9.21 against a running gateway.

Usage: /opt/hermes/.venv/bin/python mcp_contract.py <url> <token> <expected_tool> <call_tool>
"""
import asyncio
import json
import sys

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url: str, token: str, expected: str, call: str) -> int:
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=15) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                print("protocol", init.protocolVersion)
                listed = await session.list_tools()
                names = sorted(t.name for t in listed.tools)
                print("tools", names)
                assert expected in names, names
                result = await session.call_tool(call, {})
                envelope = json.loads(result.content[0].text)
                print("call", call, "ok" if envelope["ok"] else envelope["error"])
                assert "ok" in envelope
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(*sys.argv[1:5])))
```

- [ ] **Step 6: Run the contract test against a real gateway container**

```powershell
docker network create mcp-contract 2>$null
docker build -f Dockerfile.office-gateway -t office-gw:contract .
$envs = @("SUPERVISOR","LAB_HOST","INGRESS","LLM","CLUSTER","VECTOR","OBS","APPROVER") | ForEach-Object { "-e"; "OFFICE_GATEWAY_TOKEN_$_=contract-$_" }
docker run -d --rm --name gw-contract --network mcp-contract @envs -e OFFICE_GATEWAY_DB_PATH=/tmp/gw.db office-gw:contract
Start-Sleep 5
docker run --rm --network mcp-contract -v "${PWD}/tests/integration:/t:ro" --entrypoint /opt/hermes/.venv/bin/python nousresearch/hermes-agent:v2026.9.21 /t/mcp_contract.py http://gw-contract:8080/mcp contract-SUPERVISOR fleet_status fleet_status
docker run --rm --network mcp-contract -v "${PWD}/tests/integration:/t:ro" --entrypoint /opt/hermes/.venv/bin/python nousresearch/hermes-agent:v2026.9.21 /t/mcp_contract.py http://gw-contract:8080/mcp contract-LAB_HOST propose_restart host_resources
docker stop gw-contract; docker network rm mcp-contract
```

Expected: both runs print `protocol …`, the role's tool list, and `call … ok` or an error envelope (`not_configured`/`unreachable` are fine — the transport is what is being tested); exit code 0. If the SDK import path differs, adjust only the import line and note it in the commit.

- [ ] **Step 7: Commit**

```powershell
git add office_gateway/mcp_server.py office_gateway/app.py tests/test_office_gateway_mcp.py tests/integration/mcp_contract.py
git commit -m "feat(gateway): role-filtered MCP endpoint at /mcp, contract-tested with the Hermes MCP client"
```

---

### Task 11: Full suite, docs, and hand-off to Plan 1b-B

**Files:**
- Modify: `deploy/office-assistant/.env.example` (gateway section: renamed tokens, `OFFICE_GATEWAY_TOKEN_APPROVER`, `OFFICE_VECTOR_INSTANCES`, `OFFICE_CONSOLE_URL`, `OFFICE_EDGE_BACKUP_DIR`; remove `OFFICE_WRITE_VECTOR`, `OFFICE_WRITE_LAB_HOST`, `OFFICE_WRITE_EDGE`, `OFFICE_READ_LOGS_LAB_HOST`, `OFFICE_VECTOR_ENV`, `OFFICE_AUTOHEAL_DENY`)
- Delete: `deploy/office-assistant/hermes/office-gateway.openapi.json` (documented the retired curl surface)

- [ ] **Step 1: Run the whole gateway suite**

Run the test command with `tests/test_office_gateway_*.py`. Expected: all PASS. Then run `tests` (everything) and list failures that belong to Plan 1b-B (compose/scripts/config tests) in the commit message.

- [ ] **Step 2: Update `.env.example` as listed** (placeholders only, e.g. `OFFICE_GATEWAY_TOKEN_APPROVER=change-me-approver`).

- [ ] **Step 3: Commit**

```powershell
git add -A deploy/office-assistant/.env.example deploy/office-assistant/hermes
git commit -m "docs(gateway): env example for Phase 1b gateway"
```
