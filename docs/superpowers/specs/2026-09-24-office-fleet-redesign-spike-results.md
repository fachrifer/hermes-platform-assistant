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
