# Office Multi-Agent Fleet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Lab-VM office assistant into a supervisor + five specialist Hermes agents behind one office-gateway, so Dashboard chat can diagnose the fleet and APPROVE-restart only Lab-host and Vector-dev containers.

**Architecture:** One Compose file on the Lab VM. Supervisor Hermes (Dashboard `:9119`) talks A2A to five always-on specialist containers. All platform I/O goes through `office-gateway` with scoped bearer tokens. Writes are propose → `APPROVE <action-id>` → execute. Kanban on the supervisor runs a nightly fleet check. Grafana/Prometheus/Victoria URLs stay in gateway env.

**Tech Stack:** Python 3.11, FastAPI, httpx, pytest, Docker Compose, upstream `nousresearch/hermes-agent` (A2A + Kanban + Dashboard).

## Global Constraints

- Do not use Hermes `delegate_task` for specialists (children inherit parent tools).
- Human UI is Browser Dashboard only; no Desktop/Discord/Telegram/Mattermost.
- A2A binds on the Compose network only; specialists publish no host ports.
- Dashboard publish stays LAN (`HERMES_DASHBOARD_PUBLISH`, today `10.216.4.80:9119`) with basic auth.
- Platform credentials (`docker.sock`, kubeconfig, MinIO keys, Rancher token) live only in office-gateway.
- Responses must never include secrets, kubeconfig, raw env, or prompt logs.
- Vector prod / cluster-gpu / llm-edge / obs: no writes. Qdrant: read-only.
- Lab-host may restart AI Platform, `common-service-frontend`, and the Hermes stack containers. Vector may restart only `milvus-standalone` and `attu`.
- Skills never hardcode IPs; operators fill `OFFICE_SERVICE_URLS` and `OFFICE_GRAFANA_BASE_URL`.
- This checkout may only contain the fleet spec. Recreate `office_gateway` from this plan (2026-08-12 API plus fleet authz). Do not invent extra write actions (`scale_replicas`, `clear_queue`, `set_feature_flag` may exist as stubs; v1 execute path for new work is Docker `restart_service` only).
- After code changes, run `graphify update .` if `graphify` is available; do not fail the task if the CLI is missing.
- Commit each task as shown unless the human has forbidden git commits; then skip Step “Commit” and continue.

---

## File structure

| Path | Responsibility |
|---|---|
| `office_gateway/roles.py` | Role names, token lookup, permission matrix |
| `office_gateway/config.py` | Env parse: URLs, tokens, Docker allowlists, MIG expected, Grafana UIDs, target env tags |
| `office_gateway/collectors.py` | HTTP health collectors (no URLs in output) |
| `office_gateway/store.py` | SQLite propose/execute audit |
| `office_gateway/actions.py` | Propose/execute policy (role + env + allowlist) |
| `office_gateway/docker_ops.py` | Inspect (stripped) + restart by container name |
| `office_gateway/grafana_links.py` | Panel URL builder |
| `office_gateway/metrics.py` | MetricsQL/PromQL proxy to configured Victoria/Prometheus |
| `office_gateway/mig.py` | Expected vs actual MIG map |
| `office_gateway/k8s_ops.py` | Allowlisted read-only k8s gets + Traefik/OpenEBS/LiteLLM HTTP |
| `office_gateway/app.py` | FastAPI routes |
| `office_gateway/run.py` | uvicorn entry |
| `Dockerfile.office-gateway` | Gateway image |
| `deploy/office-assistant/docker-compose.yml` | Supervisor + 5 specialists + gateway + proxy |
| `deploy/office-assistant/.env.example` | Tokens, URLs, allowlists |
| `deploy/office-assistant/hermes/<role>/` | Per-agent config.yaml + .env.example |
| `deploy/office-assistant/skills/<role>/SKILL.md` | Supervisor + five specialist skills |
| `tests/test_office_gateway_*.py` | Unit/API tests |

---

### Task 1: Gateway core + scoped role tokens

**Files:**
- Create: `office_gateway/__init__.py`
- Create: `office_gateway/roles.py`
- Create: `office_gateway/config.py`
- Create: `office_gateway/collectors.py`
- Create: `office_gateway/store.py`
- Create: `office_gateway/actions.py`
- Create: `office_gateway/app.py`
- Create: `office_gateway/run.py`
- Create: `office_gateway/requirements.txt`
- Create: `tests/test_office_gateway_roles.py`
- Create: `Dockerfile.office-gateway`

**Interfaces:**
- Consumes: nothing (baseline)
- Produces: `ROLES`, `role_for_token(config, token) -> str | None`, `GatewayConfig.from_env()`, `create_app(config)`, `ActionService.propose/execute`, routes listed in the spec

- [ ] **Step 1: Write the failing role-token test**

```python
# tests/test_office_gateway_roles.py
from office_gateway.config import GatewayConfig
from office_gateway.roles import ROLES, role_for_token


def test_roles_are_the_six_fleet_roles():
    assert ROLES == frozenset(
        {"supervisor", "lab-host", "vector", "cluster-gpu", "llm-edge", "obs"}
    )


def test_role_for_token_maps_each_bearer():
    config = GatewayConfig(
        tokens={
            "supervisor": "tok-sup",
            "lab-host": "tok-lab",
            "vector": "tok-vec",
            "cluster-gpu": "tok-gpu",
            "llm-edge": "tok-llm",
            "obs": "tok-obs",
        },
        service_urls={"grafana": "http://grafana.internal/api/health"},
        write_targets={"lab-host": frozenset({"aiplatform-dashboard"})},
        vector_env={"milvus-standalone": "dev", "milvus-prod": "prod"},
    )
    assert role_for_token(config, "tok-lab") == "lab-host"
    assert role_for_token(config, "nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_office_gateway_roles.py::test_roles_are_the_six_fleet_roles -v`

Expected: FAIL with `ModuleNotFoundError` or `ImportError` for `office_gateway`

- [ ] **Step 3: Write minimal roles + config**

```python
# office_gateway/roles.py
from __future__ import annotations

ROLES = frozenset(
    {"supervisor", "lab-host", "vector", "cluster-gpu", "llm-edge", "obs"}
)

READ_ROUTES = {
    "supervisor": frozenset({"/v1/status", "/v1/services", "/v1/audit"}),
    "lab-host": frozenset({"/v1/status", "/v1/services", "/v1/docker/inspect"}),
    "vector": frozenset({"/v1/status", "/v1/services"}),
    "cluster-gpu": frozenset(
        {"/v1/status", "/v1/services", "/v1/k8s/resources", "/v1/gpu/mig"}
    ),
    "llm-edge": frozenset({"/v1/status", "/v1/services", "/v1/k8s/resources"}),
    "obs": frozenset(
        {"/v1/status", "/v1/services", "/v1/metrics/query", "/v1/grafana/links"}
    ),
}

WRITE_ROLES = frozenset({"lab-host", "vector"})


def role_for_token(config, token: str) -> str | None:
    for role, expected in config.tokens.items():
        if expected and token == expected:
            return role
    return None


def can_read(role: str, route_prefix: str) -> bool:
    allowed = READ_ROUTES.get(role, frozenset())
    return any(route_prefix == p or route_prefix.startswith(p + "/") for p in allowed)


def can_write(role: str) -> bool:
    return role in WRITE_ROLES
```

```python
# office_gateway/config.py
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from office_gateway.roles import ROLES

_SERVICE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_TOKEN_ENV = {
    "supervisor": "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "lab-host": "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "vector": "OFFICE_GATEWAY_TOKEN_VECTOR",
    "cluster-gpu": "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "llm-edge": "OFFICE_GATEWAY_TOKEN_LLM",
    "obs": "OFFICE_GATEWAY_TOKEN_OBS",
}


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} wajib diisi")
    return value


def _service_urls(value: str) -> dict[str, str]:
    endpoints: dict[str, str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, separator, url = item.partition("=")
        if not separator or not _SERVICE_NAME_RE.fullmatch(name) or not url.startswith(
            ("http://", "https://")
        ):
            raise ValueError("OFFICE_SERVICE_URLS berisi service atau URL tidak valid")
        endpoints[name] = url
    if not endpoints:
        raise ValueError("OFFICE_SERVICE_URLS wajib memiliki minimal satu service")
    return endpoints


def _csv_set(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _vector_env(value: str) -> dict[str, str]:
    """Parse `name:dev|name:prod`."""
    if not value.strip():
        return {}
    out: dict[str, str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, sep, env = item.partition(":")
        if not sep or env not in {"dev", "prod"} or not _SERVICE_NAME_RE.fullmatch(name):
            raise ValueError("OFFICE_VECTOR_ENV format name:dev|prod tidak valid")
        out[name] = env
    return out


def _grafana_dashboards(value: str) -> dict[str, str]:
    """Parse `name=uid,...`."""
    if not value.strip():
        return {}
    out: dict[str, str] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        name, sep, uid = item.partition("=")
        if not sep or not name or not uid:
            raise ValueError("OFFICE_GRAFANA_DASHBOARDS format name=uid tidak valid")
        out[name] = uid
    return out


def _mig_expected(value: str) -> dict[str, int]:
    """Parse `1g.18gb:7,2g.35gb:2,3g.71gb:2,4g.71gb:1,7g.141gb:1`."""
    if not value.strip():
        return {
            "1g.18gb": 7,
            "2g.35gb": 2,
            "3g.71gb": 2,
            "4g.71gb": 1,
            "7g.141gb": 1,
        }
    out: dict[str, int] = {}
    for item in (part.strip() for part in value.split(",") if part.strip()):
        profile, sep, count = item.partition(":")
        if not sep:
            raise ValueError("OFFICE_MIG_EXPECTED format profile:count tidak valid")
        out[profile] = int(count)
    return out


@dataclass(frozen=True)
class GatewayConfig:
    tokens: dict[str, str]
    service_urls: dict[str, str]
    write_targets: dict[str, frozenset[str]] = field(default_factory=dict)
    vector_env: dict[str, str] = field(default_factory=dict)
    grafana_base_url: str = ""
    grafana_dashboards: dict[str, str] = field(default_factory=dict)
    grafana_panel_ids: dict[str, int] = field(default_factory=dict)
    mig_expected: dict[str, int] = field(default_factory=dict)
    metrics_url: str = ""
    db_path: str = "/var/lib/hermes-office-gateway/gateway.db"
    action_ttl_seconds: int = 600
    bind_host: str = "0.0.0.0"
    bind_port: int = 8080

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        tokens = {role: _required(env) for role, env in _TOKEN_ENV.items()}
        extra = os.getenv("OFFICE_GRAFANA_PANELS", "")
        panels: dict[str, int] = {}
        for item in (part.strip() for part in extra.split(",") if part.strip()):
            name, sep, pid = item.partition("=")
            if not sep:
                raise ValueError("OFFICE_GRAFANA_PANELS format name=id tidak valid")
            panels[name] = int(pid)
        return cls(
            tokens=tokens,
            service_urls=_service_urls(_required("OFFICE_SERVICE_URLS")),
            write_targets={
                "lab-host": _csv_set(os.getenv("OFFICE_WRITE_LAB_HOST", "")),
                "vector": _csv_set(os.getenv("OFFICE_WRITE_VECTOR", "milvus-standalone,attu")),
            },
            vector_env=_vector_env(
                os.getenv(
                    "OFFICE_VECTOR_ENV",
                    "milvus-standalone:dev,attu:dev,milvus-prod:prod",
                )
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
        )
```

Implement `collectors.py` and `store.py` in this task (do not copy from another checkout).

`HttpServiceCollector`: take `endpoints: dict[str, str]`; `async collect()` / `collect_one(name)` GET each URL with httpx timeout 10s; return `{"name", "status": "ok"|"critical"|"unknown", "latency_ms"?}` — never include the URL.

`GatewayStore`: SQLite at `db_path`; tables `actions` (`action_id`, `action`, `target`, `params_json`, `summary`, `created_at`, `expires_at`, `status`) and `audit` (`id`, `action_id`, `event`, `detail`, `created_at`); methods `propose(...) -> PendingAction`, `get_action`, `mark_expired_if_needed` (pending past `expires_at` → status `expired`), `mark_executed(action_id, ok, detail)`, `list_audit(limit=50)`. `action_id` is `uuid4()`. Timestamps ISO-8601 UTC ending in `Z`.

`actions.py` for this task: `restart_service` only; `validate_propose` must:

1. `can_write(role)` else `ActionError`
2. `target in config.write_targets[role]` else `ActionError`
3. if `config.vector_env.get(target) == "prod"`: `ActionError("writes disabled for prod")`

`app.py`: Bearer token → `role_for_token`; 401 if unknown; `GET /health` unauthenticated; `GET /v1/status` role-filtered to services the role may see (supervisor and obs see all names; lab-host sees docker-related names plus configured health; start simple: **all roles may GET /v1/status for all configured health names** — writes stay role-scoped). Include `X-Office-Role` is **not** returned. `POST /v1/actions/propose` and `/execute` require `can_write(role)`.

`requirements.txt`:

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
httpx==0.28.1
pydantic==2.10.4
```

`Dockerfile.office-gateway`: python:3.11-slim, user 10001, `CMD ["python", "-m", "office_gateway.run"]`.

- [ ] **Step 4: Run tests and make sure they pass**

Run: `python -m pytest tests/test_office_gateway_roles.py -v`

Expected: PASS (add a propose-reject test: cluster-gpu token cannot propose; vector token cannot propose `aiplatform-dashboard`; vector token cannot propose a target with `env=prod`)

- [ ] **Step 5: Commit**

```bash
git add office_gateway tests/test_office_gateway_roles.py Dockerfile.office-gateway
git commit -m "$(cat <<'EOF'
feat: office-gateway role tokens and write authz

EOF
)"
```

---

### Task 2: Docker inspect (stripped) + restart adapter

**Files:**
- Create: `office_gateway/docker_ops.py`
- Modify: `office_gateway/app.py`
- Modify: `office_gateway/actions.py`
- Test: `tests/test_office_gateway_docker.py`

**Interfaces:**
- Consumes: `GatewayConfig.write_targets`, `ActionService`
- Produces: `DockerOps.inspect(name: str) -> dict`, `DockerOps.restart(name: str) -> None`, `GET /v1/docker/inspect/{container}`, execute `restart_service` calls `DockerOps.restart`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_office_gateway_docker.py
from office_gateway.docker_ops import strip_inspect, DockerOps


def test_strip_inspect_drops_env_and_mounts():
    raw = {
        "Name": "/aiplatform-dashboard",
        "State": {"Status": "running", "Health": {"Status": "unhealthy"}},
        "Config": {"Image": "dashboard:latest", "Env": ["SECRET=1"]},
        "Mounts": [{"Source": "/var/run/secrets", "Destination": "/secrets"}],
    }
    out = strip_inspect(raw)
    assert out["name"] == "aiplatform-dashboard"
    assert out["status"] == "running"
    assert out["health"] == "unhealthy"
    assert "Env" not in str(out)
    assert "SECRET" not in str(out)
    assert "Mounts" not in out


def test_restart_unknown_name_rejected(tmp_path):
    class Fake:
        def inspect_container(self, name):
            raise KeyError(name)

        def restart(self, name):
            raise AssertionError("must not restart")

    ops = DockerOps(client=Fake())
    try:
        ops.restart("nope")
        raise AssertionError("expected error")
    except ValueError as exc:
        assert "unknown" in str(exc).lower() or "not found" in str(exc).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_office_gateway_docker.py -v`

Expected: FAIL `ModuleNotFoundError: office_gateway.docker_ops`

- [ ] **Step 3: Implement docker_ops + wire execute**

```python
# office_gateway/docker_ops.py
from __future__ import annotations


def strip_inspect(raw: dict) -> dict:
    name = str(raw.get("Name", "")).lstrip("/")
    state = raw.get("State") or {}
    health = (state.get("Health") or {}).get("Status")
    image = (raw.get("Config") or {}).get("Image") or (raw.get("Image") or "")
    return {
        "name": name,
        "status": state.get("Status", "unknown"),
        "health": health,
        "image": image,
    }


class DockerOps:
    def __init__(self, client=None):
        self._client = client

    def _client_or_docker(self):
        if self._client is not None:
            return self._client
        import docker  # optional runtime dep

        return docker.from_env()

    def inspect(self, name: str) -> dict:
        client = self._client_or_docker()
        try:
            if hasattr(client, "inspect_container"):
                raw = client.inspect_container(name)
            else:
                raw = client.api.inspect_container(name)
        except Exception as exc:
            raise ValueError(f"container not found: {name}") from exc
        return strip_inspect(raw)

    def restart(self, name: str) -> None:
        client = self._client_or_docker()
        try:
            if hasattr(client, "restart"):
                client.restart(name)
            else:
                container = client.containers.get(name)
                container.restart()
        except Exception as exc:
            raise ValueError(f"container not found: {name}") from exc
```

On execute of `restart_service`, call `DockerOps.restart(target)` instead of HTTP adapter POST. Keep HTTP adapter only if `OFFICE_ADAPTER_ENDPOINTS` is set for tests; production Lab uses DockerOps.

`GET /v1/docker/inspect/{container}`: require role `lab-host` (or supervisor read — spec says lab-host). 404 if inspect fails. Never return raw inspect.

Add `docker` extra to requirements only if using docker SDK; otherwise talk to `unix:///var/run/docker.sock` via httpx. Prefer docker SDK: add `docker==7.1.0` to `office_gateway/requirements.txt`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_office_gateway_docker.py tests/test_office_gateway_roles.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add office_gateway/docker_ops.py office_gateway/app.py office_gateway/actions.py office_gateway/requirements.txt tests/test_office_gateway_docker.py
git commit -m "$(cat <<'EOF'
feat: stripped docker inspect and allowlisted restart

EOF
)"
```

---

### Task 3: Grafana panel links + MetricsQL proxy

**Files:**
- Create: `office_gateway/grafana_links.py`
- Create: `office_gateway/metrics.py`
- Modify: `office_gateway/app.py`
- Test: `tests/test_office_gateway_metrics.py`

**Interfaces:**
- Consumes: `GatewayConfig.grafana_base_url`, `grafana_dashboards`, `grafana_panel_ids`, `metrics_url`
- Produces: `panel_url(base, uid, panel_id) -> str`, `GET /v1/grafana/links`, `GET /v1/metrics/query?query=`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_office_gateway_metrics.py
from office_gateway.grafana_links import panel_url, build_links


def test_panel_url_joins_base_uid_and_id():
    url = panel_url("http://10.1.2.3:3000/", "abcUID", 12)
    assert url == "http://10.1.2.3:3000/d/abcUID?viewPanel=12"


def test_build_links_from_config_map():
    links = build_links(
        base="http://grafana.internal",
        dashboards={"gpu": "migdash"},
        panels={"gpu": 4},
    )
    assert links == [
        {"name": "gpu", "url": "http://grafana.internal/d/migdash?viewPanel=4"}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_office_gateway_metrics.py -v`

Expected: FAIL import `office_gateway.grafana_links`

- [ ] **Step 3: Implement builders + routes**

```python
# office_gateway/grafana_links.py
from __future__ import annotations


def panel_url(base: str, dashboard_uid: str, panel_id: int) -> str:
    return f"{base.rstrip('/')}/d/{dashboard_uid}?viewPanel={panel_id}"


def build_links(*, base: str, dashboards: dict[str, str], panels: dict[str, int]) -> list[dict]:
    if not base:
        return []
    links = []
    for name, uid in sorted(dashboards.items()):
        pid = panels.get(name)
        if pid is None:
            links.append({"name": name, "url": f"{base.rstrip('/')}/d/{uid}"})
        else:
            links.append({"name": name, "url": panel_url(base, uid, pid)})
    return links
```

```python
# office_gateway/metrics.py
from __future__ import annotations

import httpx

MAX_QUERY_CHARS = 500


async def instant_query(metrics_url: str, query: str) -> dict:
    if not metrics_url:
        raise ValueError("OFFICE_METRICS_URL tidak diisi")
    q = (query or "").strip()
    if not q or len(q) > MAX_QUERY_CHARS:
        raise ValueError("query tidak valid")
    # Deny obvious secret-scraping label selectors by refusing `password|token|api_key`
    lowered = q.lower()
    for banned in ("password", "token", "api_key", "secret", "authorization"):
        if banned in lowered:
            raise ValueError("query not allowlisted")
    url = f"{metrics_url.rstrip('/')}/api/v1/query"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, params={"query": q})
    response.raise_for_status()
    payload = response.json()
    # Pass through Prometheus instant-query JSON only (status/data)
    return {"status": payload.get("status"), "data": payload.get("data")}
```

`GET /v1/grafana/links`: role `obs` or `supervisor`.  
`GET /v1/metrics/query`: role `obs` or `supervisor`; 400 on validation error.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_office_gateway_metrics.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add office_gateway/grafana_links.py office_gateway/metrics.py office_gateway/app.py tests/test_office_gateway_metrics.py
git commit -m "$(cat <<'EOF'
feat: grafana panel links and metrics query proxy

EOF
)"
```

---

### Task 4: MIG map + allowlisted k8s reads

**Files:**
- Create: `office_gateway/mig.py`
- Create: `office_gateway/k8s_ops.py`
- Modify: `office_gateway/app.py`
- Test: `tests/test_office_gateway_mig.py`

**Interfaces:**
- Consumes: `GatewayConfig.mig_expected`
- Produces: `compare_mig(expected, actual) -> dict`, `GET /v1/gpu/mig`, `GET /v1/k8s/resources?kind=&namespace=`

- [ ] **Step 1: Write the failing MIG test**

```python
# tests/test_office_gateway_mig.py
from office_gateway.mig import compare_mig

EXPECTED = {
    "1g.18gb": 7,
    "2g.35gb": 2,
    "3g.71gb": 2,
    "4g.71gb": 1,
    "7g.141gb": 1,
}


def test_mig_map_ok_when_counts_match():
    result = compare_mig(EXPECTED, dict(EXPECTED))
    assert result["ok"] is True
    assert result["missing"] == {}
    assert result["extra"] == {}


def test_mig_map_reports_missing_slice():
    actual = dict(EXPECTED)
    actual["1g.18gb"] = 6
    result = compare_mig(EXPECTED, actual)
    assert result["ok"] is False
    assert result["missing"] == {"1g.18gb": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_office_gateway_mig.py -v`

Expected: FAIL import `office_gateway.mig`

- [ ] **Step 3: Implement compare_mig + k8s read facade**

```python
# office_gateway/mig.py
from __future__ import annotations


def compare_mig(expected: dict[str, int], actual: dict[str, int]) -> dict:
    missing = {}
    extra = {}
    for profile, count in expected.items():
        have = actual.get(profile, 0)
        if have < count:
            missing[profile] = count - have
        elif have > count:
            extra[profile] = have - count
    for profile, have in actual.items():
        if profile not in expected and have:
            extra[profile] = have
    return {
        "expected": dict(expected),
        "actual": dict(actual),
        "missing": missing,
        "extra": extra,
        "ok": not missing and not extra,
    }
```

`k8s_ops.py`:

- `ALLOWED_KINDS = frozenset({"nodes", "pods", "deployments", "gateway", "httproute", "storageclass", "migpolicy", "configmap"})` — configmaps: **names only**, never `data`
- `async def list_resources(kind: str, namespace: str | None) -> dict` using kubernetes client if present, else httpx to `OFFICE_K8S_API` with bearer from env `OFFICE_K8S_TOKEN` (in-cluster or kubeconfig loaded **only inside this module**)
- `count_mig_profiles(items) -> dict[str, int]` reading labels such as `nvidia.com/mig.profile` or `nvidia.com/gpu.product` from node/pod allocatable; if the API is missing, return `actual={}` and `error="adapter not configured"` without leaking the API URL
- Traefik/LiteLLM: GET configured health URLs from `service_urls` keys `traefik`, `litellm` (already in collectors). `GET /v1/k8s/resources?kind=httproute` is the extra.

For unit tests, inject `list_fn`.

`GET /v1/gpu/mig`: role `cluster-gpu` or `supervisor`; body is `compare_mig(config.mig_expected, actual)`.

`GET /v1/k8s/resources`: reject unknown kind with 400; strip `data`, `secret`, `token`, `kubeconfig` keys from items.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_office_gateway_mig.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add office_gateway/mig.py office_gateway/k8s_ops.py office_gateway/app.py tests/test_office_gateway_mig.py
git commit -m "$(cat <<'EOF'
feat: MIG expected-vs-actual map and read-only k8s gets

EOF
)"
```

---

### Task 5: Compose fleet (supervisor + five specialists + gateway)

**Files:**
- Create: `deploy/office-assistant/docker-compose.yml`
- Create: `deploy/office-assistant/.env.example`
- Create: `deploy/office-assistant/README.md`
- Create: `deploy/office-assistant/hermes/supervisor/config.yaml`
- Create: `deploy/office-assistant/hermes/lab-host/config.yaml`
- Create: `deploy/office-assistant/hermes/vector/config.yaml`
- Create: `deploy/office-assistant/hermes/cluster-gpu/config.yaml`
- Create: `deploy/office-assistant/hermes/llm-edge/config.yaml`
- Create: `deploy/office-assistant/hermes/obs/config.yaml`
- Create: `deploy/office-assistant/hermes/supervisor/.env.example`
- Test: `tests/test_office_fleet_compose.py`

**Interfaces:**
- Consumes: gateway image from `Dockerfile.office-gateway`
- Produces: one Compose project `office` network; Dashboard published; specialists internal A2A `:9900`

- [ ] **Step 1: Write the failing compose contract test**

```python
# tests/test_office_fleet_compose.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_office_fleet_compose.py -v`

Expected: FAIL missing `docker-compose.yml` or missing services

- [ ] **Step 3: Write compose + env example + Hermes configs**

`docker-compose.yml` services:

- `office-gateway`: build `Dockerfile.office-gateway`; env from `.env`; volume `office_gateway_data`; **mount `/var/run/docker.sock:ro`**; optional `OFFICE_KUBECONFIG` file mount (ro); `expose: ["8080"]`; no `ports`
- `hermes-agent` (supervisor): image `${HERMES_IMAGE:-nousresearch/hermes-agent:latest}`; `ports: ["${HERMES_DASHBOARD_PUBLISH:-10.216.4.80:9119}:9119"]`; env `A2A` peer URLs `http://hermes-lab-host:9900` etc.; `OFFICE_GATEWAY_URL=http://office-gateway:8080`; `OFFICE_GATEWAY_TOKEN=${OFFICE_GATEWAY_TOKEN_SUPERVISOR}`; volumes: supervisor config, skills/supervisor, `kanban_data:/opt/data/kanban` (or Hermes home kanban path documented in README)
- Each specialist: same image; **no ports**; `A2A_HOST=0.0.0.0`; `A2A_PORT=9900`; unique `A2A` inbound token; `OFFICE_GATEWAY_TOKEN_*` matching role; skill mount only that role; own volume `hermes_<role>_data` for `HERMES_HOME` (not shared with supervisor)

Supervisor `config.yaml` `a2a_agents:` entries for the five peers (bearer tokens via env). Specialists do **not** list each other as A2A peers (star topology).

`.env.example` includes empty `OFFICE_SERVICE_URLS` with comments for grafana/victoria/prometheus/litellm/milvus-dev/milvus-prod/minio/qdrant/rancher, all six tokens, `OFFICE_WRITE_LAB_HOST=aiplatform-dashboard,aiplatform-agent-inference,aiplatform-workflow,common-service-frontend,hermes-assistant-hermes-agent-1,hermes-assistant-office-gateway-1,hermes-assistant-dashboard-proxy-1` plus a comment to replace Hermes names after first `compose up` (`docker compose ps -a`), `OFFICE_WRITE_VECTOR=milvus-standalone,attu`, `OFFICE_VECTOR_ENV=milvus-standalone:dev,attu:dev,milvus-prod:prod`, `OFFICE_GRAFANA_BASE_URL=`, `OFFICE_GRAFANA_DASHBOARDS=`, `OFFICE_GRAFANA_PANELS=`, `OFFICE_METRICS_URL=`, `OFFICE_MIG_EXPECTED=1g.18gb:7,2g.35gb:2,3g.71gb:2,4g.71gb:1,7g.141gb:1`.

README: air-gap load image; fill `.env`; `docker compose up -d --build`; open `http://10.216.4.80:9119`.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_office_fleet_compose.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add deploy/office-assistant tests/test_office_fleet_compose.py
git commit -m "$(cat <<'EOF'
feat: compose fleet of supervisor and specialist hermes agents

EOF
)"
```

---

### Task 6: Skills (supervisor + five specialists)

**Files:**
- Create: `deploy/office-assistant/skills/supervisor/SKILL.md`
- Create: `deploy/office-assistant/skills/lab-host/SKILL.md`
- Create: `deploy/office-assistant/skills/vector/SKILL.md`
- Create: `deploy/office-assistant/skills/cluster-gpu/SKILL.md`
- Create: `deploy/office-assistant/skills/llm-edge/SKILL.md`
- Create: `deploy/office-assistant/skills/obs/SKILL.md`
- Modify: `tests/test_office_fleet_compose.py` (assert skill files exist and contain guardrail phrases)

**Interfaces:**
- Consumes: gateway routes and A2A peer names from Task 5
- Produces: skill text mounted into each container

- [ ] **Step 1: Write the failing skill contract test**

```python
from pathlib import Path

ROOT = Path("deploy/office-assistant/skills")


def test_supervisor_skill_forbids_direct_platform_calls_and_uses_a2a():
    text = (ROOT / "supervisor" / "SKILL.md").read_text()
    assert "a2a_call" in text
    assert "APPROVE" in text
    assert "delegate_task" in text and "do not" in text.lower()
    assert "office-gateway write" not in text.lower() or "never call write APIs" in text.lower()
    assert "never call write" in text.lower() or "specialist" in text.lower()


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_office_fleet_compose.py -k skill -v`

Expected: FAIL missing SKILL.md

- [ ] **Step 3: Write the six SKILL.md files**

Supervisor skill must include:

- You are the office supervisor. Talk to the user in Dashboard only.
- For Lab Docker questions call A2A agent `lab-host`. Vector: `vector`. GPU/RKE2/Traefik/OpenEBS: `cluster-gpu`. LiteLLM/routes: `llm-edge`. Grafana/Victoria: `obs`.
- Fan-out with `a2a_orchestrate` or multiple `a2a_call` for “how’s the fleet?”. If a peer errors, still return the others and name the failed domain.
- Never use `delegate_task` for platform work.
- Never `POST /v1/actions/propose` or `execute` yourself. Specialists own writes.
- When a specialist returns `approval_phrase`, show it verbatim and wait for the user to reply exactly that phrase, then A2A the same specialist to execute.
- Include MIG map and Grafana links when those specialists answered.

Lab-host skill: `GET /v1/docker/inspect/{container}`, `GET /v1/status`; propose `restart_service` only for allowlisted names; warn that restarting hermes-agent/office-gateway/dashboard-proxy may drop the Dashboard session; wait for user APPROVE relayed by supervisor (specialist still only execute when asked with the action_id).

Vector skill: inspect/health for lab + prod; restart only `milvus-standalone` and `attu`; never restart prod; never drop collections.

Cluster-gpu: always call `/v1/gpu/mig` in answers about GPU; include Grafana links from obs or `/v1/grafana/links` if the gateway allows cluster-gpu — **cluster-gpu cannot call grafana route** (obs-only). Instruct cluster-gpu to return MIG JSON; supervisor asks obs for links. Put that split in both skills.

llm-edge: LiteLLM + httproute reads; no writes.

obs: metrics query + grafana links; no writes; no log/prompt scrape.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_office_fleet_compose.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add deploy/office-assistant/skills tests/test_office_fleet_compose.py
git commit -m "$(cat <<'EOF'
feat: supervisor and specialist office skills

EOF
)"
```

---

### Task 7: Nightly Kanban fleet check + docs

**Files:**
- Create: `deploy/office-assistant/hermes/supervisor/cron.fleet-check.md` (instructions embedded in supervisor skill + config snippet)
- Modify: `deploy/office-assistant/hermes/supervisor/config.yaml` — enable kanban dispatch in gateway; cron job text
- Modify: `deploy/office-assistant/README.md` — manual acceptance checklist
- Modify: `README.md` — point office section at the fleet if the file exists; if README is missing, only update `deploy/office-assistant/README.md`
- Test: `tests/test_office_fleet_compose.py` — assert cron/kanban strings exist

**Interfaces:**
- Consumes: supervisor A2A peers from Task 5, skills from Task 6
- Produces: documented nightly job the operator enables once Dashboard is up

Hermes cron is configured via Dashboard or `config.yaml` `cronjobs`. Put this exact job body in `deploy/office-assistant/hermes/supervisor/cron.fleet-check.md` and reference it from README (operator pastes into Dashboard Cron if YAML cron is version-dependent):

```text
Every day at 01:00 (Asia/Jakarta): create a Kanban card titled "nightly fleet check".
A2A call lab-host, vector, cluster-gpu, llm-edge, obs asking for a short health+analysis summary.
cluster-gpu must include MIG map. obs must include Grafana panel links.
Comment each specialist reply on the card. Complete the card, or block it naming any failed peer.
Do not propose writes during the nightly check.
```

- [ ] **Step 1: Write the failing test**

```python
def test_nightly_fleet_check_instructions_exist():
    text = Path("deploy/office-assistant/hermes/supervisor/cron.fleet-check.md").read_text()
    assert "nightly fleet check" in text
    assert "MIG" in text
    assert "Grafana" in text
    assert "block" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_office_fleet_compose.py::test_nightly_fleet_check_instructions_exist -v`

Expected: FAIL file not found

- [ ] **Step 3: Add cron instructions + README acceptance checklist**

README checklist (must match spec §6):

1. Open `http://10.216.4.80:9119` (or `HERMES_DASHBOARD_PUBLISH`), login.
2. Ask “how’s the fleet?” — all five domains represented; MIG map present; Grafana links present.
3. Propose restart `common-service-frontend` → reply `APPROVE <id>` → container restarts; audit row exists.
4. Propose restart `milvus-standalone` via vector path → APPROVE works.
5. Ask to restart prod Milvus → gateway/skill refuses.
6. Next morning: Kanban card “nightly fleet check” is `done` or `blocked` with peer name.

Air-gap: build/load `office-gateway` and pin `nousresearch/hermes-agent` on a networked machine; `docker save` / `docker load` on the Lab VM.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_office_gateway_roles.py tests/test_office_gateway_docker.py tests/test_office_gateway_metrics.py tests/test_office_gateway_mig.py tests/test_office_fleet_compose.py -v`

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add deploy/office-assistant README.md tests/test_office_fleet_compose.py
git commit -m "$(cat <<'EOF'
docs: nightly kanban fleet check and v1 acceptance

EOF
)"
```

---

## Spec coverage (self-review)

| Spec requirement | Task |
|---|---|
| Supervisor + 5 specialists, all Lab VM, one Compose | 5 |
| Browser Dashboard only, LAN bind | 5 |
| A2A live, not `delegate_task` | 5, 6 |
| Kanban nightly fleet check | 7 |
| Scoped gateway tokens | 1 |
| Docker inspect + Lab/Vector-dev restart | 2 |
| Prod Vector write reject | 1 |
| Grafana URLs in env + panel links | 3, 5 |
| MetricsQL/PromQL | 3 |
| MIG expected vs actual | 4 |
| kubectl get / Traefik / OpenEBS / LiteLLM reads | 4 |
| APPROVE in supervisor thread; specialist execute | 6 |
| Self-restart session warning | 6 |
| Secrets never in responses | 2, 4 |
| Qdrant read-only | 5 `.env.example` health URL only |
| Air-gap image ship | 7 README |
| v1 acceptance checklist | 7 |

**Not in this plan (spec out of scope):** Desktop/Discord, prod writes, MIG reconfigure, log scrape, two-compose split, specialists on H200/Milvus VM, deleting legacy observer.
