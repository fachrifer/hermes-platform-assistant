# Office Fleet Redesign — Anti-Loop Core, Approvals, Reporting

Date: 2026-09-24  
Status: Draft (awaiting user review)  
Supersedes (partially): `docs/superpowers/specs/2026-08-20-office-multi-agent-fleet-design.md` — topology, tool boundary, live handoff, writes, and scheduled checks are replaced by this spec. Everything not mentioned here (Compose packaging, air-gapped image shipping, console avatars) stays as in v1.

Baseline: branch `fleet/sync-vm-20260924` (commit `cb3bbb3`), which mirrors the Lab VM (`10.216.4.80:/home/timai/hermes-assistant`, Compose project `office`) as of 2026-09-24.

## 1. Context

The fleet (supervisor "Athena" + six specialists) sometimes loops: an agent keeps calling tools or re-delegating without converging, occupying production LiteLLM for minutes to hours.

### 1.1 Root causes found in the synced tree

| # | Cause | Evidence |
|---|---|---|
| 1 | Free `terminal` tool on every agent | Skills drive the gateway through `curl` helper scripts (`office-gw-get.sh`, `office-gw-propose.sh`). Each command variation is a "new" call, so `tool_loop_guardrails` never trips; the model can also read `OFFICE_GATEWAY_TOKEN` from the environment |
| 2 | No turn cap anywhere | `agent.max_turns` unset (Hermes default: unlimited); guardrails configured on the supervisor only, soft-warning mode |
| 3 | Two delegation paths | Supervisor has both `a2a_agents` and `bot_peers`; `lab-host` even has itself as a peer (`bot_peers.default`). When one path is slow, the model tries the other |
| 4 | Timeout mismatch | Supervisor A2A timeout 90 s, specialist runs are unbounded → supervisor gives up and re-sends while the first run is still working, queueing duplicates |
| 5 | Hourly cron fan-out | `cron.hourly-fleet.prompt.txt` starts a Kanban card plus `a2a_orchestrate` to six peers every hour: seven LLM sessions per hour, sometimes overlapping the next hour |
| 6 | AUTOHEAL path | `lab-host` skill proposes restarts with `"auto_execute": true` and "do not wait for APPROVE" |
| 7 | Domain overlap | `edge` vs `llm-edge` vs `cluster-gpu` on Traefik; supervisor can read almost every route and diagnose by itself |
| 8 | Skills not guaranteed loaded; generic SOUL | Specialists must discover their skill each session |

### 1.2 Measured LLM performance (2026-09-24 ~14:15, `qwen3.8-fast` via LiteLLM prod)

| Test | Result |
|---|---|
| Short prompt, ~190 output tokens | ~35 tok/s decode, 5–6 s total |
| ~6k-token prompt | Prefill 0.7 s |
| ~37k-token prompt + 25 tool schemas | TTFT 5.8 s, 11 s total |
| 6 × 37k-token requests in parallel | TTFT 6–11 s, decode 25–34 tok/s, all done in 18.6 s |
| Thinking | Off by default for small prompts. `chat_template_kwargs.enable_thinking: true` adds ~540 reasoning tokens (~15 s) per turn, returned cleanly in `reasoning_content`. Under Hermes' ~5k-token system prompt the model thinks unless `enable_thinking: false` is sent (975–2242 reasoning tokens, 35–78 s for a one-word answer; see §8.4) |
| Aliases `qwen3.8-reasoning`, `qwen3.8-reasoning-xhigh` | Leak `<think>` text into `content` (no reasoning parser on those aliases) — **not usable** by the fleet |

Conclusion: the model is not the bottleneck. Perceived slowness comes from turn count, context growth, verbose tool output, and concurrent cron sessions.

## 2. Goals, non-goals, success criteria

Goals:

- No agent run can loop: every run is bounded by turn count, wall-clock, and guardrails, and failures end in an explicit answer.
- Every change to infrastructure happens only after a human clicks Approve in the console.
- Status questions answer in seconds without waking specialists.
- Daily/weekly/monthly reports always publish: numbers come from the gateway, analysis from each specialist, the executive summary from the supervisor; a missing analysis never blocks the report.
- Startup works cleanly in the air-gapped Lab VM (no waiting on internet timeouts).

Non-goals:

- Changing LiteLLM/vLLM production configuration (belongs to the cluster owners).
- Managing Kubernetes Traefik, Milvus prod runtime, or Rancher (read-only visibility only).
- Replacing Hermes with another agent runtime.

Success criteria (checked in §13):

1. Loop scenarios from §13.2 end within the bounds of §8 with no duplicate delegation.
2. No code path executes a restart, route change, or dashboard creation without an approver click recorded in the gateway audit.
3. `fleet_status` answers in < 5 s; a single-domain diagnosis in < 60 s typical, < 180 s worst case.
4. First agent build after container start completes in < 10 s with outbound internet blocked.

## 3. Decisions

| Topic | Decision |
|---|---|
| Topology | Supervisor + six specialists: `lab-host`, `ingress` (renamed from `edge`), `llm` (renamed from `llm-edge`), `cluster-gpu`, `vector`, `obs` |
| Supervisor scope | Router + status only. One gateway tool: `fleet_status` |
| Specialist mode | Single mode (no FAST/SNAPSHOT/deep variants). Specialists stay reachable from Hermes Desktop for debugging |
| Tool layer | office-gateway exposes an MCP server at `/mcp`; the bearer token picks the role and `tools/list` returns only that role's tools |
| Removed from all agents | `terminal`, `file`, `clarify`, `delegate_task`, `kanban`, A2A (`a2a_agents`, A2A platform on `:9900`), browser, web |
| Delegation channel | Bot Mode `message_agent` over `bot_peers` (api_server `:8642`), supervisor → specialist only. Async: supervisor answers immediately, specialist reply arrives as a completion notification |
| Writes | `lab-host`: restart any Lab Docker container. `ingress`: Lab-VM Traefik route change and rollback. `obs`: create dashboard in the "Hermes Fleet" Grafana folder. Everything else read-only (`vector` loses write) |
| Approval | Console Approvals page. Gateway executes only after a human clicks Approve. `auto_execute`, AUTOHEAL, and agent-side `execute` are removed |
| Scheduled work | Daily/weekly/monthly reports via a gateway-orchestrated pipeline: gateway collects numbers → specialists analyse their domain → supervisor compiles (§11.1). Hourly LLM cron and Kanban dispatch are removed |
| Hermes version | Upgrade `v2026.8.31` → `v2026.9.21` |
| Thinking | Off on all agents by default; evaluated for specialists during scenario tests (§13.3) |
| Area boundaries | `ingress` = Lab-VM Traefik only. K8s Traefik is visible to `cluster-gpu` via `k8s_get`. LiteLLM prod runs as a pod in RKE2: runtime state via `cluster-gpu`, API-level (spend/budget/models) via `llm`. Vector dev = Lab VM, prod = `10.216.203.132` (read-only) |

## 4. Architecture

```
            Human (Hermes Desktop / Dashboard, console)
                 │ chat                     │ Approve / Reports
                 ▼                          ▼
          ┌─────────────┐   MCP /mcp   ┌──────────────────────────────┐
          │  supervisor │─────────────▶│ office-gateway (FastAPI)     │
          │  (Athena)   │ fleet_status │  role tokens · MCP · audit   │
          └──────┬──────┘              │  approvals · report engine   │
  message_agent  │ (bot_peers :8642)   └──┬───────────┬───────────┬───┘
   async, 1 msg  ▼                        │           │           │
  ┌──────────┬────────┬─────┬───────────┬────────┬─────┐          │
  │ lab-host │ingress │ llm │cluster-gpu│ vector │ obs │──MCP─────┘
  └──────────┴────────┴─────┴───────────┴────────┴─────┘
        ▲ report runs: gateway → POST /v1/runs (specialists, then supervisor)
                                          │ upstream reads/writes (≤10 s)
     Docker API · Traefik files · LiteLLM API · RKE2 API · Milvus/Qdrant REST ·
     VictoriaMetrics · Grafana API
```

### 4.1 How the user reaches the fleet

| Where | Chat | Approvals and reports |
|---|---|---|
| Office | Hermes Desktop connected to all seven agents' api_servers (`:8642`): supervisor for normal use, specialists directly for debugging | Console in a browser |
| Outside the office (VPN) | Hermes web Dashboard, resuming the supervisor's canonical "Bot Chat" session | Console over VPN |

Consequences built into this design:

- The supervisor must be able to delegate from both surfaces, so its conversation always happens in the canonical "Bot Chat" session (Desktop opens it automatically; on the web Dashboard it is resumed from the session list). Verified in §14.
- Desktop connected to every gateway propagates a relay roster ("teammates on other connected machines") into the supervisor's Bot Chat, next to the `bot_peers` roster. That is a second route to the same specialist, and it only works while a Desktop is open. The supervisor must always use the `bot_peers` targets (§9); verified in §14.
- A specialist whose Bot Chat is busy with the user in Desktop queues a supervisor message for up to `bot_mode.turn_wait_seconds` (120 s), then returns `target_busy`.

### 4.2 Units

Units and their single purpose:

| Unit | Purpose | Depends on |
|---|---|---|
| `office_gateway/mcp_server.py` (new) | MCP transport at `/mcp`, role-filtered catalog, uniform result envelope | tool modules, `roles.py` |
| `office_gateway/tools/<domain>.py` (new) | One module per domain; each tool = typed args + compact output, built on existing ops modules | existing `*_ops.py` |
| `office_gateway/approvals.py` (new) | Approver endpoints, action state machine | `actions.py`, `store.py` |
| `office_gateway/reports/` (new) | Scheduler, data-pack builders, named queries, report pipeline (calls agents' `POST /v1/runs`), HTML rendering | `metrics.py`, `llm_ops.py`, `store.py`, agents' api_server |
| `console/www` | Adds Approvals and Reports pages | gateway approver + report endpoints via nginx |
| Hermes agent configs + skills | Brakes, toolsets, one skill per role | gateway MCP |

## 5. Tool catalog

### 5.1 Tool contract (all tools)

- Arguments are enums or bounded values drawn from gateway-known lists (container names, instances, namespaces, named queries, windows `5m|1h|24h|7d|30d`). Unknown values return `invalid_argument` listing valid choices.
- Output is pre-summarised: counts plus only the abnormal items; hard cap 2,000 characters per result.
- Result envelope: `{"ok": bool, "data": {...}}` or `{"ok": false, "error": {"category": ..., "detail": ..., "valid": [...]}}`.
- Error categories: `unreachable`, `timeout`, `forbidden`, `invalid_argument`, `no_metrics`, `not_configured`.
- Logs are redacted (tokens, keys, passwords, bearer headers, connection strings) before leaving the gateway.
- `metrics_query` no longer accepts free PromQL; it takes a named query + bounded parameters.

### 5.2 Catalog per role

Status: **existing** = wrap an existing endpoint; **change** = endpoint exists but needs modification; **new** = no endpoint yet.

| Role | Tool | R/W | Status | Notes |
|---|---|---|---|---|
| supervisor | `fleet_status` | R | change | From `/v1/status` + `/v1/fleet/brief`; one line per domain |
| lab-host | `list_containers` | R | existing | `/v1/docker/containers?compact=true` |
| | `inspect_container` | R | change | Summarised; env values redacted |
| | `tail_logs` | R | change | Max 100 lines, redacted; allowed for all Lab containers |
| | `host_resources` | R | new | Wire existing `proc_ops.py` (CPU, RAM, disk, load) |
| | `list_host_services` | R | new | Wire existing `systemd_ops.py` |
| | `propose_restart` | W | change | `/v1/actions/propose`, `auto_execute` removed |
| | `action_status` | R | new | Pending / approved / executing / succeeded / failed / rejected / expired |
| ingress | `list_routes` | R | change | Parsed routes only (drop raw file text) |
| | `edge_status`, `tls_status` | R | existing | `/v1/edge/status`, `/v1/edge/tls` |
| | `tail_traefik_logs` | R | change | `/v1/docker/logs` with ingress log permission, redacted |
| | `validate_route_change` | R | existing | `POST /v1/edge/validate` |
| | `propose_route_change` | W | existing | Action `apply_edge_routes` |
| | `propose_route_rollback` | W | new | Requires backup-on-apply (§6.3) |
| | `action_status` | R | new | |
| llm | `llm_status`, `list_models` | R | change | From `/v1/llm/status`; key list reduced to a count |
| | `spend_report`, `budget_report`, `deployment_health` | R | new | Curated LiteLLM endpoints; `/v1/litellm/{path}` passthrough removed |
| cluster-gpu | `k8s_get` | R | existing | `/v1/k8s/resources` (includes K8s Traefik HTTPRoute/Gateway) |
| | `mig_map`, `gpu_usage` | R | existing | `/v1/gpu/mig`, `/v1/host/gpu` |
| | `pod_logs_tail` | R | new | Needs RBAC `get pods/log`; redacted, max 100 lines |
| vector | `vector_status` | R | change | Per instance: `milvus-dev`, `milvus-prod`, `qdrant-dev` |
| | `list_collections`, `collection_stats` | R | new | Milvus REST v2 (`:19530`), Qdrant REST; no `pymilvus` dependency |
| | `bucket_usage` | R | new | Filtered to Milvus buckets |
| | `server_health` | R | new | Filtered to vector servers |
| obs | `metrics_query` | R | change | Named queries only |
| | `grafana_links` | R | existing | |
| | `active_alerts` | R | new | Grafana Alerting API on `10.216.78.130:3000` (Viewer service account) |
| | `server_health` | R | new | All platform servers (§11.4) |
| | `bucket_usage` | R | new | MinIO dev + Milvus prod object storage |
| | `availability_report`, `usage_report` | R | new | Same numbers as scheduled reports, selectable period |
| | `propose_dashboard` | W | new | Compact spec → gateway-rendered JSON (§6.4) |
| | `action_status` | R | new | |

Per-role entries: 9 existing, 10 change, 20 new (a tool shared by several roles is counted once per role).

## 6. Gateway changes

### 6.1 Roles and access

- Rename roles `edge` → `ingress`, `llm-edge` → `llm` (tokens, `READ_ROUTES`, `WRITE_ROLES`, `log_targets`, the `llm-edge` K8s kind filter).
- `supervisor` reads only what `fleet_status` needs.
- `WRITE_ROLES = {"lab-host", "ingress", "obs"}`; per-role action allowlist: `lab-host` → `restart_service`; `ingress` → `apply_edge_routes`, `rollback_edge_routes`; `obs` → `create_dashboard`.
- New role `approver`, usable only by the console (§7). Its token is never mounted into any Hermes container.
- Remove: `auto_execute`, `autoheal.py` gate and `OFFICE_AUTOHEAL_DENY`, `POST /v1/actions/execute` for agent roles, `/v1/litellm/{path}`, `vector` write allowlist (`OFFICE_WRITE_VECTOR`).
- `fleet_status` is computed from gateway collectors; the in-container `office-watch-*` s6 services and `POST /v1/watch/snapshot` are retired.

### 6.2 MCP server

- Streamable HTTP MCP at `/mcp` on the existing port 8080, same bearer tokens as the REST API.
- `tools/list` is filtered by role; `tools/call` enforces role again (defence in depth).
- Every upstream call has a 10 s timeout; the gateway never retries upstream calls inside a tool call.
- REST endpoints stay for the console and for debugging with curl.
- `GET /static/models-dev.json` serves a minimal models.dev-shaped registry containing only the fleet model, so Hermes never reaches for the internet registry (§10).

### 6.3 Route change safety (ingress)

- `apply_edge_routes` writes a timestamped backup of the current routes file before the atomic write; keep the last 10.
- `rollback_edge_routes` restores the newest backup (or a named one from `list_routes` history).
- If validation of the rendered Traefik dynamic config fails after apply, the gateway restores the backup automatically and marks the action `failed` with `rolled_back: true`.

### 6.4 Dashboard creation (obs)

- `propose_dashboard(title, panels[])` where each panel is `{type: timeseries|stat|table|gauge, query: <named query>, params: {...}}`. No panel-count limit; every dashboard still passes through Approve.
- Gateway renders full Grafana JSON from templates, dry-runs each query once (empty result → `invalid_argument`), stores the rendered JSON in the pending action.
- On approval, gateway POSTs to Grafana with a service account that has Editor only on folder "Hermes Fleet". It never updates or deletes existing dashboards; name clash → suffix with date.

### 6.5 New credentials (least privilege, in gateway `.env` only)

| Credential | Scope |
|---|---|
| Grafana service account | Viewer (alerts, links) + Editor on folder "Hermes Fleet" only |
| Milvus prod user | Read-only (describe/list/stats) |
| Gateway kubeconfig | Add `get` on `pods/log` |
| Approver console credential | nginx basic auth (htpasswd), separate from Dashboard login |
| Agent api_server keys | Gateway holds each agent's `API_SERVER_KEY` to start report runs (`POST /v1/runs`); used only by the report pipeline |

## 7. Approval flow

State machine (stored in gateway SQLite, audited):

```
proposed ──approve──▶ approved ──▶ executing ──▶ succeeded
    │                                   └──────▶ failed (rolled_back?)
    ├──reject──▶ rejected
    └──TTL (OFFICE_ACTION_TTL_SECONDS, default 600)──▶ expired
```

- Specialist proposes → gets `action_id` → replies to supervisor with `PROPOSED <action_id>` and a one-line summary. The supervisor tells the user to open Approvals. No agent can execute.
- Console Approvals page (`/approvals`) lists pending actions with role, target, summary, and a diff (routes, dashboard panel list). Approve/Reject buttons call `POST /v1/approvals/{id}/approve|reject` through nginx, which enforces basic auth and injects the approver token and `X-Approver: $remote_user`.
- `/approvals` and the approver API are served only over the console's HTTPS listener (`:443`); plain HTTP redirects. This matters because approvals are also clicked from outside the office over VPN.
- The gateway executes immediately on approve and records approver, timestamps, and result in the audit log. Expired or already-decided actions reject further clicks.
- Agents observe outcomes only through `action_status`.

## 8. Agent configuration

### 8.1 Brakes

| Layer | Setting | Value |
|---|---|---|
| Gateway → upstream | per call | 10 s |
| MCP tool call | `mcp_servers.office.timeout` / `connect_timeout` | 15 s / 5 s |
| One LLM call | `providers.office-litellm.request_timeout_seconds` | 60 s |
| Specialist turns | `agent.max_turns` | 6 (typical 2–3) |
| Specialist wall-clock | `agent.run_budget_seconds` | 180 s (wrap-up notice at 80%) |
| Supervisor turns | `agent.max_turns` | 6 |
| Guardrails (all agents) | `tool_loop_guardrails.hard_stop_enabled: true`; `warn_after` 1; `hard_stop_after` `exact_failure`/`same_tool_failure`/`idempotent_no_progress` | 2 |
| API retries | `agent.api_max_retries` | 1 |

Rules enforced by skill text and verified by tests:

- Supervisor sends at most one message per specialist per user request, to at most two specialists, and never re-sends — including after a failed notification.
- Specialists have `agent.bot_mode_protocol: false` (no `message_agent`, no roster) and no `bot_peers`.
- Each role has exactly one skill, < ~1,500 tokens, pinned with `skills.auto_load`.

### 8.2 Toolsets

| Agent | Enabled |
|---|---|
| supervisor | `mcp-office` (only `fleet_status`), `skills`, Bot Mode `message_agent` (canonical "Bot Chat" session) |
| specialists | `mcp-office` (role catalog), `skills` |

`memory`, `session_search`, `todo`, `kanban`, `terminal`, `file`, `clarify`, `delegate`, `a2a`, browser, and web toolsets are disabled everywhere.

Both mechanisms are set on every agent: `agent.disabled_toolsets` (global backstop) and `platform_toolsets` pinned to the enabled list for every chat surface — `cli`, `tui`, `api_server`, `gui`, `desktop`, `dashboard`, `web`. Unpinned surfaces fall back to Hermes composite defaults, and the web Dashboard / Desktop chat runs through `tui_gateway`.

### 8.3 Context growth

Specialist canonical Bot Chats accumulate every supervisor message. Set `compression.threshold_tokens: 32000` on specialists (tuned in §13.2 tests) so compaction keeps turns small; supervisor keeps Hermes defaults.

### 8.4 Thinking

Off, explicitly. Every agent sets:

```yaml
agent:
  reasoning_effort: none
providers:
  office-litellm:
    extra_body:
      chat_template_kwargs:
        enable_thinking: false
```

Leaving `agent.reasoning_effort` unset makes Hermes send `reasoning_effort: "medium"`; `none` alone does not stop the model thinking under Hermes' system prompt. Only `enable_thinking: false` does (spike results: 78 s → 3.7 s for a one-word answer; supervisor → specialist round trip 111 s → 16 s).

For the §13.3 comparison, specialists get `enable_thinking: true` instead (passthrough verified). If adopted, specialist `run_budget_seconds` becomes 270. The `qwen3.8-reasoning*` aliases are not used.

## 9. Communication protocol

- Channel: `message_agent(target="<peer>", message=...)` from the supervisor's canonical "Bot Chat" over `bot_peers` (`http://hermes-<role>:8642`, `HERMES_PEER_<NAME>_KEY`). This route is gateway-to-gateway and does not need Hermes Desktop running.
- `bot_peers` keys are `peer-<role>` so they can never collide with a Desktop relay-roster handle named `<role>`. Targets are exactly those names, listed in the supervisor skill: `peer-lab-host`, `peer-ingress`, `peer-llm`, `peer-cluster-gpu`, `peer-vector`, `peer-obs`; env keys `HERMES_PEER_PEER_<ROLE>_KEY` (e.g. `HERMES_PEER_PEER_LAB_HOST_KEY`). Relay-roster entries injected by a connected Desktop (`@name@<connection>` forms) are never used.
- `target_busy` (user is mid-conversation with that specialist) is reported like any other failure: domain `unknown`, no resend.
- The supervisor never waits: after dispatching it tells the user which specialist is checking, then ends the turn. The completion notification starts a new supervisor turn that relays the result.
- Status-only questions are answered from `fleet_status` without messaging anyone.

Specialist → supervisor reply format (plain text, ≤ 12 lines):

```
STATUS: ok | degraded | down | unknown
FINDINGS:
- <fact> (<tool>: <value>)            # max 5
CAUSE: <most likely cause | unknown>
NEXT: <recommended step>
PROPOSED: <action_id> <one-line summary>   # only when a write was proposed
```

Supervisor → user (Indonesian): one headline sentence, then one line per involved domain, then pending approvals with the console link. Failed/timeout specialists are reported as `unknown` with the error category.

## 10. Startup and air-gap hardening

Findings in `v2026.9.21` source: `agent.offline` and the `HERMES_OFFLINE` env var (both set on today's fleet) are not read anywhere and have no effect. Network touch points at startup/background are the remote model catalog, the models.dev registry, IPv6-first resolution, and the update check (already skipped on Docker installs). The existing Compose `extra_hosts` map that points known internet hosts at `127.0.0.1` stays; the proxy safety net below covers hosts not on that list.

Spike-confirmed (PC, 2026-09-24): with `key_env` and the settings below no startup log line waits on an outbound connection. Each agent process start still sends ~12 server-type detection probes to the LiteLLM base URL (`/v1/models`, `/api/show`, `/props`, `/api/tags`, …, ~1–8 s); `model_overrides` does not skip them, and they hit LiteLLM, not the internet. Title generation makes one extra LLM call per new session; `auxiliary.title_generation.model_upgrade_enabled: false` removes it (verified: titles are derived locally from the first message).

The office virtual key gets 403 on `/model/info` and `/v1/models/<id>`, so `CONTEXT_WINDOW` is read on the VM with the gateway's LiteLLM admin key.

Config applied to every agent:

```yaml
model_catalog:
  enabled: false
models_dev:
  url: http://office-gateway:8080/static/models-dev.json   # minimal registry served by the gateway
model_overrides:
  custom:office-litellm:
    qwen3.8-fast: {context_window: CONTEXT_WINDOW, supports_tools: true, supports_reasoning: false}
auxiliary:
  title_generation:
    model_upgrade_enabled: false
network:
  force_ipv4: true
updates:
  check: false
security:
  allow_lazy_installs: false
browser:
  backend: "off"
agent:
  build_wait_timeout: 30
mcp:
  discovery_concurrency: 1
```

`CONTEXT_WINDOW` is the literal `max_input_tokens` that LiteLLM `/model/info` reports for `qwen3.8-fast`, read once in Phase 1 and written into the config as a number.

Plus a fail-fast safety net: `HTTPS_PROXY=http://127.0.0.1:9` and `HTTP_PROXY` likewise, with `NO_PROXY=localhost,127.0.0.1,10.0.0.0/8,office-gateway,hermes-supervisor,hermes-lab-host,hermes-ingress,hermes-llm,hermes-cluster-gpu,hermes-vector,hermes-obs`. Any internet attempt fails in milliseconds instead of waiting for a TCP timeout. `agent.offline` is removed from configs.

## 11. Reporting and monitoring

### 11.1 Scheduled reports (gateway-orchestrated, specialists analyse, supervisor compiles)

| Report | Schedule | Data pack content (numbers from the gateway) |
|---|---|---|
| Daily | 07:00 | Per-domain status, incidents last 24 h, active Grafana alerts, certificates expiring < 30 days, servers/buckets over thresholds, monitoring coverage gaps, LiteLLM reachability |
| Weekly | Monday 07:00 | Availability trend, top token/spend consumers, average GPU/MIG usage, capacity trend + projected full date for disks and buckets, actions proposed/approved/rejected |
| Monthly | 1st, 07:00 | Availability % per service and per server, usage (tokens, spend, GPU), bucket growth, incident list |

Pipeline (the same for all three; the orchestration is gateway code, never an LLM fan-out):

1. **Collect.** The gateway builds one data pack per domain for the period. All numbers in the final report come from these packs.
2. **Specialist analysis.** The gateway starts one run per specialist in parallel via the agent's api_server `POST /v1/runs` with idempotency key `<report>-<period>-<role>` (e.g. `daily-2026-09-25-vector`), so a restart of the scheduler never starts a second run. Each run gets its data pack and may use its read tools to investigate anomalies. Normal specialist brakes apply (6 turns, 180 s); the gateway polls `GET /v1/runs/{id}` and stops the run at 190 s. Output format:
   ```
   STATUS: ok | degraded | down | unknown
   FINDINGS:
   - <fact with number from the pack or a tool>      # max 5
   RISKS: <what may break next and when | none>
   RECOMMENDATIONS: <max 3 steps>
   ```
3. **Supervisor compile.** One supervisor run receives the six sections (plus "analysis unavailable" markers) and writes the executive summary: overall condition, top risks, cross-domain links (e.g. MinIO disk full explaining Milvus restarts), recommended actions. The supervisor gets no extra tools for this; deadline 120 s.
4. **Publish.** Stored in gateway SQLite and rendered as HTML under a gateway volume: executive summary, then each specialist section, then the number tables. Shown on console `/reports` with HTML download (PDF via browser print).

- When reports coincide (e.g. Monday the 1st), they run one after another: daily, weekly, monthly.
- Report runs use a dedicated session per report (not the canonical Bot Chat), so they do not grow the chats used for interactive debugging.
- Load: six specialist runs plus one supervisor run per report, instead of seven sessions every hour today.

### 11.2 Availability

- Source: probe metrics already in VictoriaMetrics (`probe_success` / `up`; exact metric and label names confirmed during implementation via `metrics_query`).
- Availability % = successful probe samples / total samples in the period; missing samples count as `no_metrics`, not as up.

### 11.3 Named queries

A single allowlisted registry (`office_gateway/reports/queries.py`) shared by `metrics_query`, `server_health`, `bucket_usage`, reports, and dashboard panels. Parameters are bounded (server name from §11.4, bucket from MinIO metrics, window enum).

### 11.4 Platform servers and buckets

| Server | Role |
|---|---|
| 10.216.4.80 | Lab VM: Hermes fleet, gateway, Lab Traefik, Milvus dev, Qdrant dev, MinIO dev |
| 10.216.203.132 | Milvus prod + its object storage |
| 10.216.221.100 | RKE2 ingress + LiteLLM prod |
| 10.216.78.129 | Rancher |
| 10.216.78.130 | Grafana + VictoriaMetrics |

Buckets: MinIO dev (Lab VM) and the object storage behind Milvus prod.

`server_health`: up/down, CPU, RAM, disk per mount, load, uptime; flags over thresholds (disk ≥ 85%, RAM ≥ 90%, load/cores ≥ 2). `bucket_usage`: size, object count, growth per day, backing capacity.

### 11.5 Metric coverage

Some exporters are missing. Tools return `no_metrics` naming the missing exporter; the daily report lists coverage gaps. Rollout (Phase 3) ships offline: `node_exporter` where missing, VictoriaMetrics scrape of MinIO `/minio/v2/metrics/cluster` and `/minio/v2/metrics/bucket`, and blackbox probes for any server/service not yet probed.

## 12. Error handling

- Every failure maps to one category (§5.1); each skill defines one reaction per category: report it and stop. No retries by the model.
- Specialist failure or `delivery_timeout`/`target_busy`/`runtime_offline` notification → supervisor reports that domain as `unknown` with the reason; no resend.
- Wall-clock budget reached → Hermes wrap-up notice → specialist replies with partial findings and `STATUS: unknown`.
- Action failures are audited; route apply failures auto-restore the backup (§6.3).
- LiteLLM down → all agents unavailable; console status continues and scheduled reports publish numbers-only with the outage flagged.
- Report pipeline: a specialist run that fails or passes its deadline is stopped (`POST /v1/runs/{id}/stop`) and its section reads "analysis unavailable: <reason>"; a failed supervisor compile publishes the specialist sections without an executive summary. Nothing is retried within the same report.

## 13. Testing and acceptance

### 13.1 Gateway unit tests (pytest)

- Each MCP tool: argument validation with `valid` list, output cap, redaction, role filtering in `tools/list` and `tools/call`, every error category.
- Approvals: no execution without approver; expired/decided actions refuse; approver identity audited; `auto_execute` absent; agent roles get 403 on execute.
- Route backup/rollback and auto-restore on failed validation.
- Dashboard rendering from spec; folder restriction; no update/delete calls.
- Report builders against recorded metric fixtures, including `no_metrics` handling.
- Report pipeline with fake agent api_servers: idempotency (same key never starts a second run), specialist timeout → stop + "analysis unavailable", supervisor failure → report without summary, all agents down → numbers-only report.

### 13.2 Loop scenarios on the Lab VM

Prompts that looped before, a specialist forced to time out, an MCP tool forced to fail, LiteLLM cut off. Each run from both access paths: Desktop connected to all gateways, and web Dashboard with Desktop closed; plus one run while the user is chatting with the target specialist in Desktop (expect `target_busy` handling). Pass: no duplicate `message_agent`, turn counts ≤ §8.1, a final answer always produced, specialist runs ≤ 180 s.

### 13.3 Thinking comparison

Same scenario set plus one daily report run, with specialist thinking on vs off; compare answer quality (reviewed by the user) and time. Decision recorded in the plan; if on, apply §8.4.

### 13.4 Offline startup

Containers started with outbound internet blocked: first agent build < 10 s, no log lines showing outbound connection attempts waiting on timeouts.

### 13.5 Upgrade

`v2026.9.21` provider works with `key_env: OFFICE_LLM_API_KEY` and no `OPENAI_API_KEY` (so the OpenRouter auto-detect never triggers and the airgap patch can be dropped). If not, port `patch-hermes-airgap-provider.py` to the new source and test it against `hermes_cli/auth.py` + `runtime_provider.py` of `v2026.9.21` before use.

## 14. Verification spikes (first tasks of Phase 1)

Each is a go/no-go check with a fallback. The three `message_agent` spikes (terminal disabled, web Dashboard "Bot Chat", Desktop relay vs `bot_peers`) run first, on `v2026.9.21`, and block the rest of Phase 1: the user delegates through the supervisor from the web Dashboard when outside the office, so remote delegation is a hard requirement.

| Spike | Fallback if it fails |
|---|---|
| `message_agent` works with the `terminal` toolset disabled (delivery spawns `hermes peer dm` as a background process via the terminal machinery) | Stop and re-open the communication decision with the user (A2A remains the alternative); do not re-enable `terminal` silently |
| Headless Bot Mode markers (`ui_meta.hermes-bots` in a profile, canonical "Bot Chat") persist across container restarts | Create them in the container init script |
| Supervisor "Bot Chat" resumed from the web Dashboard (outside the office) exposes `message_agent` | Stop and re-open the communication decision with the user: remote users could not delegate otherwise |
| With Desktop connected to all seven gateways, `message_agent(target="<peer>")` resolves to the `bot_peers` route, not the Desktop relay; closing Desktop mid-request does not break delivery | Rename peers to `peer-<role>` so they never collide with relay handles, and re-test |
| Specialists with `agent.bot_mode_protocol: false` still receive peer messages and still work normally when opened from Desktop | Keep protocol on for specialists but give them an empty `bot_peers` and a skill rule never to message; re-test |
| `extra_body.chat_template_kwargs` reaches LiteLLM from Hermes custom provider | Skip §13.3; thinking stays off |
| `key_env` without `OPENAI_API_KEY` | Port airgap patch (§13.5) |

Outcome (2026-09-24, see `2026-09-24-office-fleet-redesign-spike-results.md`): terminal-disabled `message_agent`, marker/Bot Chat persistence, specialist protocol off, `extra_body` passthrough and `key_env` all PASS. The web Dashboard and Desktop-relay spikes were deferred by the operator to the Phase 1b VM rollout as acceptance checks; the peer rename fallback is applied up front (§9) and every chat surface is pinned (§8.2). If the web Dashboard check fails on the VM, stop and re-open the communication decision.
| Gateway can read Grafana alerting API and create in folder with the scoped service account | Alerts via VictoriaMetrics `ALERTS` series |
| `POST /v1/runs` on an agent api_server can run in a dedicated (non-Bot-Chat) session and honours the idempotency key (Phase 2 spike) | Runs land in the canonical Bot Chat and rely on §8.3 compression |

## 15. Phasing

Each phase gets its own implementation plan.

| Phase | Scope | Exit |
|---|---|---|
| 1 — Anti-loop core | Spikes, Hermes upgrade, gateway MCP + role catalog (all tools marked existing/change, plus `host_resources`, `list_host_services`, `action_status`), named query registry seeded with the queries skills use today, static models-dev registry, approvals + console Approvals page, route backup/rollback (`propose_route_rollback`), removal of terminal/A2A/AUTOHEAL/cron/Kanban/office-watch, agent configs + skills, startup hardening | §13.1 (Phase 1 tools, approvals, rollback), §13.2, §13.4, §13.5 pass |
| 2 — Reporting and monitoring | Extend named queries, `server_health`, `bucket_usage`, `active_alerts`, `availability_report`, `usage_report`, LLM spend/budget/deployment tools, vector collection tools, `pod_logs_tail`, report pipeline (data packs, specialist analysis, supervisor compile) + console Reports page, `propose_dashboard`, thinking comparison | §13.1 (remaining tools, dashboards, reports), §13.3 |
| 3 — Metric coverage | Offline exporter rollout (§11.5) | Daily report shows no coverage gaps for §11.4 targets |

## 16. Security and housekeeping

- No secrets in git: `.env`, `.env.bak`, `.local-login`, `.kubeconfig.local`, `ca/*`, `certs/*` stay ignored.
- The 2026-09-24 snapshot (`~/fleet-snapshot-*` on the VM; `fleet-snapshot-*.tgz` and `fleet-snapshot-extract/` on the PC) contains live credentials — the LiteLLM key in `.env.bak` was verified to still work. Delete all copies after this spec is approved and rotate that key.
- Gateway tokens reach agents only through MCP `headers: {Authorization: "Bearer ${OFFICE_GATEWAY_TOKEN}"}`; with `terminal` removed, the model cannot read them.
