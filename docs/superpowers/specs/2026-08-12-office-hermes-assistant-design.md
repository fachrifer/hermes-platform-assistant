# Office AI Assistant — Hermes Dashboard + Gateway

Date: 2026-08-12
Status: Approved

## 1. Context

The office AI platform needs a full **office AI assistant** that can monitor
platform health and, with hard guardrails, perform safe manage actions
(restart, scale, clear queue, feature flags). The control plane is the
**upstream Nous Research Hermes Agent Web Dashboard**. Inference uses an
**external on-prem LiteLLM** endpoint (base URL + API key only).

This design **replaces** the previous office path based on
`office_observer` / `office_relay` / CML `hermes_office`. Those packages
are removed. Personal Hermes (Telegram + Gemini finance) is unchanged.

## 2. Decisions

| Topic | Decision |
|---|---|
| Runtime host | Dedicated intranet VM `10.216.4.80`, dir `/home/timai/hermes-assistant` (air-gapped; images built on PC) |
| UI | Upstream Hermes Agent Web Dashboard (basic auth) |
| LLM | External LiteLLM; Hermes `model.provider: custom` |
| Tool boundary | Separate `office-gateway` service (Approach 2) |
| Image delivery | PC: `export-images.sh` + `ship-to-vm.sh` → `timai@10.216.4.80:/home/timai/hermes-assistant`; VM: `load-images.sh`; Compose `pull_policy: never` |
| Monitoring | Health endpoints: K8s/Rancher, Prometheus/VM, Grafana, OpenWebUI, GPU, vector DB, LiteLLM |
| Writes | Allowlisted only; propose → exact `APPROVE <action-id>` |
| Messaging | Dashboard first; Telegram later |
| Legacy stack | Removed; office-assistant is the only office product |

### Out of scope (v1)

- Telegram office channel
- Raw log ingestion / user prompt scraping
- Secret access, delete, cluster-admin
- Replacing personal Hermes finance bot

## 3. Architecture

```text
Intranet VM (Docker Compose)
├── hermes-agent          # upstream Nous image
│     Web Dashboard (basic auth)
│     custom provider → LiteLLM (base URL + API key)
│     office skills → office-gateway tools
│
└── office-gateway        # this repo
      read: configured health URLs
      write: allowlisted adapters + APPROVE gate
      audit: SQLite propose/execute log

External:
  LiteLLM / on-prem models
  Monitoring & platform APIs
```

Hermes never holds cluster-admin credentials. Platform credentials live only
in the gateway environment.

## 4. Components

### 4.1 Hermes Agent (upstream)

- Official image, pinned tag
- Dashboard bound to private IP; username/password provider
- `config.yaml`: custom provider pointing at LiteLLM `/v1`
- Skills instruct: analyze health; for writes always propose, then require
  the user to reply exactly `APPROVE <action-id>`

### 4.2 office-gateway

| Piece | Responsibility |
|---|---|
| Health collectors | Fixed configured HTTP health URLs (reuse observer pattern) |
| Service registry | Named targets; no free-form URLs/commands from the model |
| Propose/execute | Pending actions with TTL; execute only when pending + unexpired |
| Write adapters | HTTP/API adapters for restart, scale, clear_queue, set_feature_flag |
| Audit store | Append-only SQLite of propose / success / failure |

API (Bearer token):

- `GET /health`
- `GET /v1/status`
- `GET /v1/services/{name}`
- `POST /v1/actions/propose`
- `POST /v1/actions/execute`

Allowlisted actions: `restart_service`, `scale_replicas` (min/max bounds),
`clear_queue`, `set_feature_flag`. Missing adapter → execute returns
“adapter not configured” without mutating state.

### 4.3 Guardrails

- Propose never mutates platform state
- Execute requires existing pending `action_id`
- Default TTL 10 minutes
- Reject unknown action types, unknown targets, out-of-bounds scale
- Gateway rejects hallucinated executes with no pending proposal

## 5. Failure behavior

| Failure | Behavior |
|---|---|
| LiteLLM down | Dashboard up; agent reports model unavailable |
| One health endpoint down | Mark that service; continue others |
| Gateway down | Office tools unavailable; no writes |
| Approve mismatch / expired | No-op; re-propose |
| Write backend error | Error to caller; audit `failed` |

## 6. Testing & acceptance

- Unit: collectors, allowlist, propose/execute/expire, scale bounds
- API tests with fake backends
- Manual: Dashboard login → status → propose → `APPROVE` → audit row

## 7. Legacy

`office_observer/`, `office_relay/`, and `cml/hermes_office/` have been
removed. The supported office product is `deploy/office-assistant/`.
