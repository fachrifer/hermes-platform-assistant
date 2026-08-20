# Office Multi-Agent Fleet — Supervisor + Specialists

Date: 2026-08-20  
Status: Approved  
Related: `docs/superpowers/specs/2026-08-12-office-hermes-assistant-design.md` is the existing single-agent office assistant. This spec **extends** that stack into a Lab-VM fleet. It does not delete office-gateway or Dashboard.

## 1. Context

The Lab VM already runs upstream Nous Hermes Agent (Dashboard `:9119`) plus `office-gateway` (health reads, propose/execute, `APPROVE <action-id>`). The estate to manage is larger than one agent should own:

| Plane | Reality |
|---|---|
| Vector **dev** | Lab `milvus-standalone` + `attu` |
| Vector **prod** | Dedicated Milvus VM (Docker) + MinIO + node exporter |
| Lab host | Mixed Compose: AI Platform, Hermes stack, `common-service-frontend` (unhealthy), leftover Qdrant |
| GPU cluster | Single-node H200 NVL, RHEL 9.6, RKE2 1.33.8, GPU Operator + MIG slices, Traefik Gateway API, OpenEBS ZFS |
| LLM edge | LiteLLM in-cluster + FastAPI/LLM routes via Traefik |
| Control / obs | Rancher (separate Docker); Grafana + VictoriaMetrics/Logs/Traces on a monitoring VM |

Native Hermes `delegate_task` is **not** used for specialists: children inherit the parent toolset, so credentials would collapse into one process.

## 2. Decisions

| Topic | Decision |
|---|---|
| Topology | Supervisor + five specialist Hermes agents |
| Specialist runtime | Always-on Hermes containers (own LLM loop + skills) |
| Host | All agents on the **Lab VM**, one Compose file |
| Human UI | **Browser Dashboard only** (today: `http://10.216.4.80:9119`, basic auth; bind via `HERMES_DASHBOARD_PUBLISH`). No Desktop/Discord/Telegram/Mattermost in v1 |
| Live handoff | Hermes **A2A** (Docker network only) |
| Durable/scheduled | Hermes **Kanban** (shared `kanban.db` volume, not a shared full `~/.hermes`) |
| Tool boundary | One **office-gateway**, **scoped bearer tokens** per role |
| Analysis | Health + Grafana/Victoria queries + read-only platform APIs. No prompt/log scrape, no secrets |
| Monitoring config | Grafana / Prometheus-or-Victoria **IPs and URLs in gateway env** (`OFFICE_SERVICE_URLS` + Grafana base URL). Skills never hardcode IPs |
| Writes | Lab-host allowlist + Vector **dev** Docker restarts only |
| Vector prod / Cluster / LLM-edge / Observability | Read + analysis only |
| Qdrant (`qdrant_timai`) | Read-only |
| Packaging | Approach 1: single Compose fleet |
| v1 done | Fleet report + APPROVE restarts + nightly Kanban check + **MIG slice map** + **Grafana panel links** on cluster/obs reports |

### Write allowlist (v1)

**Lab-host** (Docker restart after APPROVE):

- `aiplatform-dashboard`, `aiplatform-agent-inference`, `aiplatform-workflow`
- `common-service-frontend`
- Hermes stack as **deployed container names** (today: `hermes-assistant-hermes-agent-1`, `hermes-assistant-office-gateway-1`, `hermes-assistant-dashboard-proxy-1`). After the fleet Compose lands, the allowlist is updated to those service/container names so Lab-host can still bounce supervisor, gateway, and proxy.

Self-restart of Hermes is allowed; the skill **must warn** that the Dashboard session may drop. Completion is visible on Kanban/audit.

**Vector (dev only):** Docker restart `milvus-standalone` and/or `attu`.

**Rejected in v1:** prod Milvus/MinIO mutate, kubectl mutate, MIG reconfigure, collection drop, secret read, delete, cluster-admin.

## 3. Architecture

```text
You (intranet browser)
  → http://10.216.4.80:9119    supervisor Dashboard (basic auth; configurable)
         │
         │  A2A (Docker network only)
         ├── hermes-lab-host
         ├── hermes-vector          dev writes / prod read
         ├── hermes-cluster-gpu     read + MIG map
         ├── hermes-llm-edge        read
         └── hermes-obs             read + Grafana links
                    │
                    └── office-gateway   scoped tokens + APPROVE + audit
                              │
         Kanban (shared kanban.db volume) ← nightly fleet check

External (not in this Compose):
  Dedicated Milvus VM, MinIO, H200 RKE2, Rancher, Victoria stack, LiteLLM
```

**Boundaries**

- Specialists publish **no** host ports. A2A binds on the Compose network.
- Supervisor never holds Docker socket, kubeconfig, MinIO keys, or Rancher admin.
- `docker.sock`, kubeconfig / Rancher token, MinIO metrics creds, and LiteLLM admin URLs live **only** in office-gateway env/volumes. Specialists never mount the socket.
- LiteLLM stays external: every Hermes uses `custom` provider (base URL + API key only).
- Changing a monitoring IP is an env edit + compose recreate, not a skill change.

## 4. Components

### 4.1 Supervisor Hermes

- Same Compose service as today’s Dashboard (`hermes-agent` / published `:9119`). New specialists are additional services in that file.
- Tools: A2A client (`a2a_call`, `a2a_orchestrate`), Kanban orchestrator tools, optional office-gateway **supervisor** token for **read-only aggregate status** (no write adapters). Propose/execute are called by the **specialist** that owns the target, using that specialist’s token.
- Skills: classify domain, fan-out, synthesize; never call platform APIs directly; always print `APPROVE <action-id>` before execute.

### 4.2 Specialist Hermes (five containers)

| Service | Reads via gateway | Writes via gateway |
|---|---|---|
| `hermes-lab-host` | Docker inspect / Lab container health | Restart allowlisted Lab + Hermes containers |
| `hermes-vector` | Lab Milvus/Attu; prod Milvus + MinIO health/metrics | Restart `milvus-standalone`, `attu` only. Prod tagged `prod` → writes rejected |
| `hermes-cluster-gpu` | `kubectl get`, GPU Operator, **MIG slice map** (7×1g.18gb, 2×2g.35gb, 2×3g.71gb, 1×4g.71gb, 1×7g.141gb vs actual), Traefik Gateway, OpenEBS StorageClass | none |
| `hermes-llm-edge` | LiteLLM health/models, Traefik HTTPRoutes / FastAPI routes | none |
| `hermes-obs` | VictoriaMetrics/Logs/Traces queries, Grafana health, **panel deep links** from configured base URL + dashboard UIDs | none |

Each specialist: own `HERMES_HOME`, own A2A bearer token, own gateway token. Shared volume **only** for Kanban DB/workspaces/logs as required by Hermes, not the full supervisor home.

### 4.3 office-gateway

Extend the existing FastAPI service.

**Config (env, operator-filled later)**

- `OFFICE_SERVICE_URLS` — named health endpoints (`grafana=…`, `victoria=…` or `prometheus=…`, `litellm=…`, `milvus-dev=…`, `milvus-prod=…`, `minio=…`, …)
- `OFFICE_GRAFANA_BASE_URL` + dashboard UID map — for panel links (no admin API required if UIDs are configured)
- `OFFICE_WRITE_TARGETS` / `OFFICE_ADAPTER_ENDPOINTS` — Docker restart adapters for allowlisted names
- Per-role tokens: `OFFICE_GATEWAY_TOKEN_SUPERVISOR`, `_LAB_HOST`, `_VECTOR`, `_CLUSTER`, `_LLM`, `_OBS`
- Vector targets carry `env=dev|prod`

**API (unchanged shape, stricter authz)**

- `GET /health`
- `GET /v1/status` — role-filtered
- `GET /v1/services/{name}`
- `GET /v1/docker/inspect/{container}` — Lab Docker inspect (role lab-host)
- `GET /v1/metrics/query` — MetricsQL/PromQL against configured Victoria/Prometheus (role obs, others as needed)
- `GET /v1/k8s/resources` — allowlisted `kubectl get` equivalents (role cluster-gpu)
- `GET /v1/gpu/mig` — expected vs actual MIG slice map (role cluster-gpu)
- `POST /v1/actions/propose` / `POST /v1/actions/execute`
- `GET /v1/audit`

Role token cannot invoke another role’s write adapters. Cluster/LLM/obs tokens cannot propose writes.

### 4.4 Kanban

- Dispatcher in the **supervisor** gateway (Hermes default).
- Nightly cron (supervisor): create “fleet check” card → A2A all five specialists → comment summaries → `kanban_complete` (or `kanban_block` if a peer times out).
- Human reviews the card in the Dashboard Kanban tab the next morning.

### 4.5 Data flow

**Live:** User → Supervisor chat → A2A specialists → gateway reads → synthesized answer (MIG map and Grafana links when those domains participate).

**Write:** User asks restart → Supervisor A2A specialist → specialist `POST /v1/actions/propose` → Supervisor shows `APPROVE <action-id>` → user replies in supervisor thread → Supervisor A2A specialist to execute → specialist `POST /v1/actions/execute` → audit. Supervisor never calls write APIs with its own token.

## 5. Guardrails and failures

| Failure | Behavior |
|---|---|
| One specialist down | Supervisor reports that domain unknown; others still answer |
| Gateway down | No reads/writes; Dashboard still up |
| LiteLLM down | Agents report model unavailable |
| Victoria/Grafana down | Other health continues; obs notes metrics gap |
| APPROVE mismatch / expired | No-op; propose again |
| Docker restart error | Audit `failed`; no retry storm |
| Nightly timeout | Kanban card `blocked` with which peer failed |
| Model asks prod Vector restart | Gateway reject (`writes disabled` / env=prod) |
| Lab-host asks `milvus-standalone` | Gateway reject (wrong role allowlist) |

Self-restart of `hermes-agent` or `office-gateway`: warn in chat; do not claim success until audit/Kanban shows execute result.

## 6. Testing and acceptance

- Unit: scoped tokens, prod write reject, container allowlist, propose/expire, MIG map formatter, Grafana link builder from base URL + UID.
- API tests with fake Docker / MetricsQL / kubectl backends.
- Compose fixture: supervisor + two fake A2A peers + gateway.
- Manual: Dashboard “how’s the fleet?” → APPROVE restart (lab allowlist) → next-day Kanban card with MIG map + Grafana links.

**v1 acceptance**

1. Browser Dashboard (LAN) can answer a fleet question using all five specialists.
2. APPROVE restart works for an AI Platform or `common-service-frontend` container **and** for lab `milvus-standalone` or `attu`.
3. Prod Milvus restart is refused.
4. Nightly Kanban fleet check exists and is readable the next morning.
5. Cluster reports include the MIG slice map; obs/cluster answers include Grafana panel links.

## 7. Out of scope (v1)

- Hermes Desktop, Discord, Telegram, Slack, Mattermost
- Native `delegate_task` as the specialist mechanism
- Prod Vector writes, kubectl/MIG/Traefik/LiteLLM mutate
- Log ingestion / user prompt scraping
- Deleting legacy `office_observer` / `office_relay` / CML office
- Splitting Compose into two files
- Running specialists on the H200 node or Milvus VM

## 8. Implementation notes (non-binding)

- Extend `deploy/office-assistant/docker-compose.yml` rather than a new stack.
- Pin `nousresearch/hermes-agent` tags; air-gap: build/load images on a connected machine and ship to the Lab VM (existing practice).
- A2A ports internal only; reuse dashboard-proxy for `:9119` if already bound to `10.216.4.80`.
- Qdrant remains a read-only named health target if configured; no specialist writes.
