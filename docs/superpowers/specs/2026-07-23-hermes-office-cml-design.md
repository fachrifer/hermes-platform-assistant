# Hermes Office on CML + Qwen3 8B — Design Document

Date: 2026-07-23
Status: Approved

## 1. Context

The office AI platform runs on **Cloudera Machine Learning (CML)** with an
**NVIDIA V100 32GB** GPU. Hermes Office must monitor and report on that platform
from inside the same CML project, without the laptop relay path.

Hermes Cloud remains the Telegram delivery surface. It must **not** store raw
office logs — only finished **reports** and **recommendations** produced in CML.

The only local LLM in scope is **Qwen3 8B**. Hermes Cloud’s own agent brain
(Gemini) is unchanged.

## 2. Decisions (brainstorming)

| Topic | Decision |
|---|---|
| CML role | Host office LLM + Hermes Office (monitor/report) |
| Qwen3 8B role | Monitored office model **and** assist for log selection + recommendations |
| Scope | Monitor + report only (no infra write / restart / scale) |
| Relay | **Not used for CML** — CML posts directly to Hermes Cloud (legacy relay may remain for non-CML sites) |
| Log storage | **CML project storage only** |
| Cloud payload | **Report + recommendation only** (no raw log dump) |
| Delivery | **Telegram** (Hermes Cloud) **and** **CML Manager UI** |
| Model | **Qwen3 8B only** (FP16 on V100 32GB) |
| Gemini | Unchanged for Hermes personal-assistant features |

### Out of scope (v1)

- Infra management (pod restart, Rancher/K8s admin, secret access)
- Laptop Office Relay / office-hours mTLS bridge
- Replacing Gemini inside Hermes Cloud with Qwen
- Multi-model serving, OpenWebUI packaging, LiteLLM gateway
- Full cluster log aggregation (only configured service log sources)

## 3. Architecture

```text
CML Project
├── App: qwen3-8b
│     OpenAI-compatible API on CDSW_APP_PORT
│     Qwen3-8B FP16 on V100 32GB
│
└── App: hermes-office
      Observer  → health + log collection
      Store     → logs, health history, reports (CML disk)
      Qwen assist → log selection + recommendations
      Reporter  → POST report+recommendation → Hermes Cloud
      Manager UI → status, logs, reports, recommendations
              │
              │  payload: report + recommendation only
              ▼
Hermes Cloud
  verify auth, store report artifacts, Telegram /office
```

### Data boundary

- **Stays in CML:** raw logs, health time series, local report history, Qwen
  assist traces needed for UI.
- **Leaves CML:** signed JSON with metrics summary, selected-log highlights
  (short excerpts already chosen by Qwen), and recommendation text.
- **Never leaves CML:** full log files, credentials, K8s Secrets, prompt/chat
  contents from end users of Qwen (unless explicitly configured later).

## 4. Components

### 4.1 `qwen3-8b` (CML Application)

- Startup script binds to `os.environ["CDSW_APP_PORT"]` (and `127.0.0.1` or
  CML-required host).
- Exposes OpenAI-compatible `GET /v1/models` and `POST /v1/chat/completions`.
- Single model id: `Qwen/Qwen3-8B` (or pinned local path via env).
- Runtime: transformers / vLLM-compatible stack that supports Volta (`sm_70`),
  **FP16** (not BF16-native, not FP8).
- Health: `GET /health` used by Observer.

### 4.2 `hermes-office` (CML Application)

| Piece | Responsibility |
|---|---|
| **Observer** | Every 5 minutes: poll configured health URLs (Qwen3 required; optional k8s/prometheus/grafana/etc.); ingest bounded log windows from configured sources into local store |
| **Store** | Project-local SQLite + log files under a configurable data dir; retain logs ~14 days; retain reports ~90 days locally for UI |
| **Qwen assist** | Call local Qwen OpenAI API with health summary + capped log window; return selected log lines + recommendation |
| **Reporter** | Build deterministic metrics section; attach Qwen selection + recommendation; HMAC-sign; POST to Hermes Cloud; retry/queue on failure |
| **Manager UI** | Lightweight web UI on `CDSW_APP_PORT`: service status, browse logs, list reports, show latest recommendation |

### 4.3 Hermes Cloud (existing, slimmed office path)

- Adapt office ingest from “full snapshot telemetry” to **report + recommendation**
  artifacts (new or revised endpoint under `/api/v1/office/...`).
- Keep Telegram commands: `/office status`, `/office daily`, `/office weekly`,
  `/office monthly` (content sourced from last accepted report payloads).
- Recommendation text is **included inside** status/daily/weekly/monthly bodies
  (no separate `/office advise` command in v1).
- **Do not** persist raw CML logs.
- Laptop relay path becomes optional/legacy for non-CML sites; CML direct path
  is the supported office deployment for this design.

## 5. Report payload (Cloud contract)

Minimum fields (illustrative):

```json
{
  "observer_id": "cml-office-1",
  "generated_at": "2026-07-23T04:00:00Z",
  "period": "daily",
  "metrics": {
    "services": [{"name": "qwen3-8b", "healthy": true, "latency_ms": 12}],
    "coverage": {"expected_cycles": 288, "received_cycles": 287}
  },
  "selected_logs": [
    {"service": "qwen3-8b", "ts": "2026-07-23T03:11:00Z", "line": "..."}
  ],
  "recommendation": "…",
  "qwen_assist": {"available": true, "model": "Qwen3-8B"},
  "signature": "…"
}
```

Rules:

- `metrics` are deterministic from Observer measurements.
- `selected_logs` are short excerpts only (hard cap on count and bytes).
- If Qwen is down: still send `metrics`; set `qwen_assist.available=false` and
  a fixed recommendation string such as `"qwen unavailable"`.

## 6. CML deployment

Same CML **project**, two Applications:

1. **`qwen3-8b`** — GPU runtime with V100 32GB; model weights on project FS or
   configured model directory.
2. **`hermes-office`** — CPU sufficient; env vars for:
   - `QWEN_BASE_URL` (reachable URL of the `qwen3-8b` CML Application; apps do not share localhost)
   - `HERMES_CLOUD_REPORT_URL`
   - `OFFICE_OBSERVER_SHARED_SECRET` / HMAC secret
   - `OFFICE_SERVICE_URLS` (health endpoints)
   - `OFFICE_LOG_SOURCES` (paths or HTTP log endpoints)
   - `OFFICE_DATA_DIR` (persistent project path)

Startup follows CML Analytical Application pattern: Python launcher reading
`CDSW_APP_PORT`.

No requirement for Docker Compose inside CML for v1.

## 7. Security

- Observer is **read-only**.
- Qwen assist receives only a **bounded** log window; never secrets.
- Auth to Hermes Cloud via shared-secret HMAC (direct HTTPS from CML).
- Manager UI relies on CML application access control; do not bind a public
  unauthenticated internet port outside CML’s proxy model.
- Do not scrape user chat prompts from Qwen for reporting.

## 8. Failure behavior

| Failure | Behavior |
|---|---|
| Qwen3 down | Deterministic metrics still reported; recommendation = qwen unavailable; UI shows model unhealthy |
| Hermes Cloud unreachable | Queue report payloads in CML; retry with backoff; Manager UI remains available |
| Single health endpoint fails | Mark that service down; continue others |
| Log volume spike | Enforce per-cycle line/byte caps before Qwen assist |

## 9. Project layout (proposed additions)

```text
cml/
  qwen3_8b/
    run_app.py              # CML Application entry (CDSW_APP_PORT)
    serve.py                # OpenAI-compatible server
    requirements.txt
  hermes_office/
    run_app.py              # CML Application entry (UI + background loops)
    observer.py
    store.py
    qwen_assist.py
    reporter.py
    ui/                     # Manager UI (simple HTML/FastAPI templates or static)
    requirements.txt
  README.md                 # CML deploy steps
```

Existing `office_observer/` / `office_relay/` remain for legacy intranet+laptop
deployments; CML path is additive. Relay is not required when CML can reach
Hermes Cloud.

Cloud-side changes live under existing `core/office_monitor.py` (and related
API routes) to accept report+recommendation artifacts.

## 10. Testing & acceptance

### Automated

- Health collector up/down from fixtures
- Store retention trim and per-cycle caps
- Qwen assist with mocked OpenAI client
- Reporter payload contains only allowed fields (no raw log dump)
- Cloud endpoint rejects bad HMAC; accepts valid report
- UI/API can list reports and latest recommendation from local store

### Manual (CML)

1. Deploy `qwen3-8b` on V100 — models + one completion succeed
2. Deploy `hermes-office` — Manager UI reachable via CML app URL
3. Simulate Qwen health failure — UI + report reflect it; recommendation notes unavailability
4. Healthy path — report includes selected logs + recommendation
5. Block Cloud URL — local queue works; UI still shows reports; after unblock Telegram receives them
6. Telegram `/office daily` matches CML UI summary/recommendation

### Done when

- Logs stay in CML; Cloud never persists raw logs
- Telegram and CML UI both show report + recommendation
- Monitor/report only (no infra write actions)
- Only local model: Qwen3 8B

## 11. Migration note vs current Office path

Current path: `Observer → Laptop Relay → Hermes Cloud (snapshots)`.

This design: `CML Observer/Reporter → Hermes Cloud (report+recommendation)`,
with logs retained in CML and Qwen assist running beside the monitored model.

Legacy relay remains available for sites that cannot egress from the monitoring
host to Hermes Cloud; it is not part of the CML v1 happy path.
