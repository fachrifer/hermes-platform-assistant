# Office Fleet Redesign — Phase 1a Spike Results

Spec: `2026-09-24-office-fleet-redesign-design.md` §14 · Hermes `v2026.9.21` · run on operator PC (Docker Desktop)

| ID | Spike | Blocking | Result | Evidence |
|---|---|---|---|---|
| S1 | `message_agent` works with `terminal` disabled | yes | PASS | Supervisor `hermes tools --summary`: CLI 1/29, Skills only (no terminal). Bot Chat one-shot → `tool_use message_agent {target: lab-host}` → `tool_result is_error: false`, `{"status": "queued", "to": "@lab-host on peer 'lab-host'", "reply_delivery": "notification", "process_id": "proc_c9fb4d7c0ef1", ...}`; no `terminal` tool_use. Completion notification arrived inside the same one-shot run: final text `lab-host membalas: SPIKE-PONG-1`. lab-host `sessions list`: `Bot Chat  api_1790237327_d779313f`. Timing with default reasoning: 111 s (first supervisor call 47 s, queue → lab-host LLM call 34 s). Re-run with `enable_thinking: false` on both agents: `SPIKE-PONG-1B` round trip **16.1 s**, queue → lab-host LLM call ≈4 s, reasoning_tokens 0; same supervisor session id `20260924_150710_fd714a` resumed. |
| S2 | Web Dashboard "Bot Chat" delegates and receives the reply | yes | DEFERRED | Operator chose on 2026-09-24 to skip the manual browser/Desktop spikes on the PC and verify them on the Lab VM during the Phase 1b rollout (rollout acceptance check). Source check: the Dashboard/Desktop chat runs through `tui_gateway`; platform keys `gui`, `dashboard`, `web` resolve to composite toolsets not covered by today's `platform_toolsets` block → Phase 1b pins every chat surface (see decisions). |
| S3 | Desktop connected to all gateways still routes via `bot_peers`; Desktop close does not break delivery | yes | DEFERRED | Same operator decision. Route collision is removed up front instead of tested: `bot_peers` keys are renamed `peer-<role>` (see decisions), so a Desktop relay roster entry named `<role>` can never shadow them. The Desktop-close-mid-delivery check runs on the VM rollout. |
| S4 | `key_env: OFFICE_LLM_API_KEY` without `OPENAI_API_KEY`, no airgap patch | no | PASS | No `OPENAI_API_KEY` in container (count 0). One-shot `-q 'Balas persis dengan teks: OK'` → `text: "OK"`, `exit_code: 0`, `duration_ms: 12267` (29.4 s wall incl. `docker exec` + CLI start), tokens in 4966 / out 37. Capture: `POST /api/show` (provider probe), then `/v1/chat/completions` `has_auth: true`, `model: qwen3.8-fast`, `stream: true`, `tools: 2`, keys include `reasoning_effort`; then a second non-stream call with `response_format`, `tools: 0` (auxiliary, likely session title). Logs: no models.dev/openrouter/nousresearch/timeout lines (only bundled skill name `github`). |
| S5 | Bot Mode marker + "Bot Chat" survive restart | no | PASS | `compose restart spike-supervisor spike-lab-host`, 45 s → `/opt/data/profile.yaml` still `ui_meta: hermes-bots: {}`; `sessions list` still shows `Bot Chat 20260924_150710_fd714a`; re-delegation `SPIKE-PONG-5` → `status: queued`, final `lab-host membalas: SPIKE-PONG-5`, 13.4 s. |
| S6 | `extra_body.chat_template_kwargs` reaches LiteLLM | no | PASS | `lab-host.thinking.config.yaml` → capture shows top-level `"chat_template_kwargs": {"enable_thinking": true}` (with `reasoning_effort: "medium"`), response `reasoning_tokens: 195`, answer `391`, 13.6 s. The `false` direction is proven in the thinking decision below. |
| S7 | Specialist with `bot_mode_protocol: false` receives peer messages and has no `message_agent` | no | PASS | lab-host Bot Chat asked to use `message_agent` → no `tool_use`; reply: "I don't have a `message_agent` tool available in this session — my only tools are skill_view and skills_list". Request carried `tools: 2`. Receiving with protocol off proven by S1/S5 (lab-host answered PONG-1/1B/5). |

## Decisions triggered

- **Thinking is ON by default under Hermes and `reasoning_effort` cannot turn it off → every agent's provider sets `extra_body.chat_template_kwargs.enable_thinking: false` (spec §8 thinking, §10 provider block).**
  Evidence (lab-host, prompt `Sebutkan ibu kota Indonesia dalam satu kata.`, ~4.9k prompt tokens):
  - `agent.reasoning_effort` unset → Hermes' custom provider profile sends `reasoning_effort: "medium"` → 2242 reasoning tokens, 77.8 s.
  - `agent.reasoning_effort: none` → wire value `"none"`, still 975 reasoning tokens, 35.6 s.
  - provider `extra_body.chat_template_kwargs.enable_thinking: false` → forwarded as top-level `chat_template_kwargs`, 0 reasoning tokens, 3.7 s; a two-sentence answer 6.4 s.
  - Direct calls with a small system prompt do not think under any variant, so the model decides from context; Hermes' large system prompt tips it into thinking. This is why the Phase 0 benchmark saw no thinking.
  - Keep `agent.reasoning_effort: none` as well (harmless; LiteLLM accepts it, and Hermes already sends `none` on the title call).
  - This also proves `extra_body` passthrough for S6 in the `false` direction.
- **S3 deferred → `bot_peers` keys are `peer-<role>` from the start (spec §9).** Targets become `peer-lab-host`, `peer-vector`, …; env keys `HERMES_PEER_PEER_<ROLE>_KEY`. Removes the Desktop relay name collision without depending on the spike.
- **S2 deferred → `platform_toolsets` pins every chat surface (spec §8.2): `cli`, `tui`, `api_server`, `gui`, `desktop`, `dashboard`, `web`**, with the global `agent.disabled_toolsets` as backstop. S2 and the Desktop-close check become rollout acceptance checks on the VM; if S2 fails there, stop and re-open the communication decision (spec §14).

## Observations for Phase 1b

- S4: every session makes one extra auxiliary LLM call (`response_format`, no tools) for the title. Verified fix: `auxiliary.title_generation.model_upgrade_enabled: false` → no second call, title derived from the first message.
- The office virtual key gets 403 on `/v1/model/info` and `/v1/models/qwen3.8-fast`; `CONTEXT_WINDOW` must be read on the VM with the LiteLLM admin key. The metadata probe burst persists with `model_overrides` set (it is server-type detection), ~1–8 s per agent process start against LiteLLM.
- S1: the specialist's "Bot Chat" session keeps accumulating peer turns (lab-host request carried prior PONG turns). Compression threshold in spec §8 must cover long-lived specialist Bot Chats.
- S1: the LiteLLM route caches identical requests (repeat calls returned in 0.2 s with identical output). Benchmarks and spike measurements must use a nonce in the prompt.
- S4: ~5k input tokens for a trivial prompt with only the `skills` toolset. Bundled skills are seeded into every profile; Phase 1b should prune them to the office skills only.

## Phase 1b local smoke (2026-09-24, operator PC, Docker Desktop)

Full stack (7 agents, gateway, Traefik, www) from a `git archive` of the branch, flattened to the VM layout, with an LLM stub in place of LiteLLM (logs each request's tool list and system prompt).

| Check | Result |
|---|---|
| All 10 containers up, cont-inits OK | PASS |
| MCP contract per role (tool catalog filtered by role token) | PASS, all 7 |
| Approvals over Traefik HTTPS: no auth / wrong password / agent bearer → 401; forged `X-Approver` overwritten by the basic-auth user; agent calling `/v1/approvals` directly → 403; approve → `succeeded` and `office-www` really restarted; second approve → 409; `action_status` over MCP → `succeeded`; audit rows proposed/approved/succeeded/rejected | PASS |
| `fleet_status` shows all 7 agents ok | PASS |
| api_server turn per role: tools sent directly (no `tool_search` bridge), role skill auto-loaded in the system prompt, `chat_template_kwargs.enable_thinking: false` | PASS, all 7; tool counts supervisor 4, lab-host 10, ingress 11, llm 5, cluster-gpu 6, vector 4, obs 6 (MCP tools + `skills_list`/`skill_view`/`skill_manage`); system prompt 14–15.4k chars |
| Supervisor canonical "Bot Chat" one-shot | PASS: `fleet_status`, skill tools, `message_agent` |
| Warm-up with the LLM reachable | 3.6–4.6 s |

Findings fixed during the smoke:

- Configs lacked `_config_version`; the image's config migration warned "predates version 12". All configs now carry `_config_version: 45`.
- `tools.tool_search.enabled` defaults to `auto`, which always defers MCP tools behind `tool_search`/`tool_describe`/`tool_call`; the model saw only the bridge. Set to `off`; `tools.connectors.enabled: false`.
- `skills.auto_load` is only injected when the agent has a skills tool, so the `skills` toolset stays (there is no read-only variant). To keep `skill_manage` from becoming a write path or an extra LLM loop: `skills.write_approval: true`, `creation_nudge_interval: 0`, `project_discovery: false`, `auxiliary.background_review.enabled: false` (post-turn fork replaying the conversation), `curator.enabled: false`, memory off.
- `/opt/data/skills` was created root-owned by the nested read-only skill mount, breaking Hermes skill operations (`PermissionError` on `skills/.hub`). The bot-mode cont-init now chowns that directory (not recursive).
- The bundled-skill opt-out marker takes effect from the second boot; only the builtin `hermes-agent` skill is listed next to the role skill.

Local-only artifact: after an in-place `docker compose restart`, `docker exec` into some containers failed with "unable to find user" while the agent kept serving `/health`; recreating the container cleared it. Recheck `docker exec` after an approved restart on the VM.

## Phase 1b Lab VM rollout (2026-09-28)

Shipped with `ship-phase1b.ps1` (SHA-256 of tree, gateway and image archives matched on both ends), then on the VM: `docker compose down --remove-orphans`, `unpack-phase1b.sh` (backup `~/hermes-assistant-pre-phase1b-20260928-091038.tgz`), `migrate-env-phase1b.sh` (env backups `*.pre-phase1b-20260928-091530`; supervisor gateway token rotated; approver login created), `deploy-and-start.sh`. The previous gateway image is kept as `office-gw:pre-phase1b`; the old `office_hermes_*` volumes are untouched.

| Check | Result |
|---|---|
| 10 containers up on `v2026.9.21`; no config-migration warning; `/opt/data/skills` owned by hermes; no errors | PASS |
| Warm-up (LLM reachable) | 3.5–3.6 s on all 7 agents |
| MCP contract per role with each agent's own token (tool list + one read tool) | PASS, all 7 |
| Specialist turns on production LiteLLM | lab-host 4 tool calls / 16.2 s; ingress 2 / 8.7 s; llm 2 / 8.2 s; cluster-gpu 3 / 11.7 s (1 tool error, see below); vector 1 / 5.7 s; obs 2 / 9.0 s. Every run ended with a structured answer, no repeats |
| Supervisor "Bot Chat" delegation | 1 `message_agent` to lab-host, reply folded into the final answer, 31.9 s; lab-host shows the peer session as `Bot Chat` |
| Broad prompt "cek semua sistem" (looped before) | 1 `fleet_status` call, 6.4 s |
| Restart request to lab-host | 1 `propose_restart`, action `pending`, nothing restarted |
| Approvals page | HTTP → HTTPS redirect; no auth 401; approver login 200 |
| Operator approve in the browser | PASS: action `succeeded`, approver `timai`, `office-www` restarted, `docker exec` still works afterwards |
| Operator web Dashboard Bot Chat (S2), Desktop connected (S3) | pending |

Findings:

- `context_window`: LiteLLM `/model/info` has no `max_input_tokens` for `qwen3.8-fast` and the vLLM upstream is a cluster-internal service. A 250,019-token prompt was accepted (47–103 s prefill), so the real limit is at least ~250k. The configs keep `131072`: below the proven limit, and earlier compression keeps turns fast.
- vector-dev (Milvus, Qdrant on the Lab host) was unreachable: the gateway had no `host.docker.internal` mapping (also missing in the old compose). Fixed in compose; after recreating the gateway all three instances report up.
- cluster-gpu cannot read GPU, MIG or k8s: `OFFICE_KUBECONFIG` points to the empty `kubeconfig.absent`, the gateway still holds an old kubeconfig copy in its data volume, and Rancher (`c-w86wr`) treats that token, and the one in `~/.kube/config`, as `system:unauthenticated`. Needs a fresh read-only Rancher token.
- The supervisor summarised `fleet_status` as "all systems healthy"; `fleet_status` is agent health only, not domain health. The skill now says "agent ok".
- The operator's first chat was a new session, not "Bot Chat"; Hermes only grants `message_agent` when the session title is exactly "Bot Chat" (`message_agent_authorized`, no config switch). The supervisor told the user to "enable Bot Mode" and run `docker ps`. The skill now answers from `fleet_status` and points to the "Bot Chat" session; re-tested: normal session → pointer, Bot Chat → 1 `message_agent`, 19.3 s.

### Follow-up hotfixes (2026-09-28)

Gateway code-only ships (bind-mounted `office_gateway/`, restart `office-gateway`); backup `~/hermes-assistant-pre-hotfix-20260928-100448.tar`.

- Kubeconfig: the operator added `.kubeconfig.local`, but `.env` said `./kubeconfig.local`. Fixed the path (file mode 600). cluster-gpu now reads the cluster: `mig_map` ok (7/2/2/1 as expected), `gpu_usage` 4 GPUs.
- `k8s_get nodes` returned `items: []`, `omitted: 1`: the generic projection kept ~100 node-feature labels, over the 1500-char item budget. Nodes now get a compact summary (roles, Ready, pressure, IP, kubelet, cpu/memory, `nvidia.com/*` allocatable, GPU product, MIG config, taints). Pod `node`/`podIP`/`restartCount` were empty on the kubernetes-client path (snake_case `to_dict()`); both key styles are read now.
- milvus-dev admin visibility (read-only): `milvus_databases`, `milvus_collections`, `milvus_collection` (rows, load state, fields, indexes), `milvus_users` (with roles), `milvus_roles` (grants across all DBs), vector role only, over Milvus REST v2 (`OFFICE_MILVUS_DEV_URL`, optional `OFFICE_MILVUS_DEV_TOKEN`). No write tools; access changes would need a propose/approve action.
- Approver password: the operator changed `approver_password` in `.local-login` only. `ensure-approver.sh` now re-hashes into `edge/approvers.htpasswd` in place (same inode, bind mount) when the password or user differs, leaves hand-made non-apr1 hashes alone, never prints the password. Applied, then `office-edge` restarted.

| Real turn after the hotfixes | Result |
|---|---|
| cluster-gpu: GPU nodes, Ready, MIG layout | `k8s_get` + `mig_map`, 0 errors, 16.8 s |
| vector: milvus-dev users and roles | 1 `milvus_users`, 13.8 s |
| vector: `uat_gpu.rag_docs` rows, load, index | 1 `milvus_collection`, 13.0 s |
