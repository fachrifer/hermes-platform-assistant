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

- S4: every session makes one extra auxiliary LLM call (`response_format`, no tools) after the first turn. Check whether title generation can be disabled or pointed at a cheap path; on a 35 tok/s model it adds latency and load per delegation.
- S1: every agent process start (CLI one-shot and each api_server run) fires ~12 model metadata probes (`/v1/models`, `/v1/models/<id>`, `/api/show`, `/props`, `/version`, `/api/tags`) before the first chat call. Check whether `model_overrides` with `context_window` (spec §10) skips them.
- S1: the specialist's "Bot Chat" session keeps accumulating peer turns (lab-host request carried prior PONG turns). Compression threshold in spec §8 must cover long-lived specialist Bot Chats.
- S1: the LiteLLM route caches identical requests (repeat calls returned in 0.2 s with identical output). Benchmarks and spike measurements must use a nonce in the prompt.
- S4: ~5k input tokens for a trivial prompt with only the `skills` toolset. Bundled skills are seeded into every profile; Phase 1b should prune them to the office skills only.
