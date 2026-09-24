# Office Fleet Redesign — Phase 1a Spike Results

Spec: `2026-09-24-office-fleet-redesign-design.md` §14 · Hermes `v2026.9.21` · run on operator PC (Docker Desktop)

| ID | Spike | Blocking | Result | Evidence |
|---|---|---|---|---|
| S1 | `message_agent` works with `terminal` disabled | yes | | |
| S2 | Web Dashboard "Bot Chat" delegates and receives the reply | yes | | |
| S3 | Desktop connected to all gateways still routes via `bot_peers`; Desktop close does not break delivery | yes | | |
| S4 | `key_env: OFFICE_LLM_API_KEY` without `OPENAI_API_KEY`, no airgap patch | no | PASS | No `OPENAI_API_KEY` in container (count 0). One-shot `-q 'Balas persis dengan teks: OK'` → `text: "OK"`, `exit_code: 0`, `duration_ms: 12267` (29.4 s wall incl. `docker exec` + CLI start), tokens in 4966 / out 37. Capture: `POST /api/show` (provider probe), then `/v1/chat/completions` `has_auth: true`, `model: qwen3.8-fast`, `stream: true`, `tools: 2`, keys include `reasoning_effort`; then a second non-stream call with `response_format`, `tools: 0` (auxiliary, likely session title). Logs: no models.dev/openrouter/nousresearch/timeout lines (only bundled skill name `github`). |
| S5 | Bot Mode marker + "Bot Chat" survive restart | no | | |
| S6 | `extra_body.chat_template_kwargs` reaches LiteLLM | no | | |
| S7 | Specialist with `bot_mode_protocol: false` receives peer messages and has no `message_agent` | no | | |

## Decisions triggered

(none yet)

## Observations for Phase 1b

- S4: every session makes one extra auxiliary LLM call (`response_format`, no tools) after the first turn. Check whether title generation can be disabled or pointed at a cheap path; on a 35 tok/s model it adds latency and load per delegation.
- S4: Hermes sends `reasoning_effort` on chat calls. Confirm in S6 how LiteLLM maps it for `qwen3.8-fast` (thinking must stay off unless `extra_body` turns it on).
- S4: ~5k input tokens for a trivial prompt with only the `skills` toolset. Bundled skills are seeded into every profile; Phase 1b should prune them to the office skills only.
