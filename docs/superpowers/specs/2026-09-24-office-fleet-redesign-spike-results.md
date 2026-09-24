# Office Fleet Redesign — Phase 1a Spike Results

Spec: `2026-09-24-office-fleet-redesign-design.md` §14 · Hermes `v2026.9.21` · run on operator PC (Docker Desktop)

| ID | Spike | Blocking | Result | Evidence |
|---|---|---|---|---|
| S1 | `message_agent` works with `terminal` disabled | yes | | |
| S2 | Web Dashboard "Bot Chat" delegates and receives the reply | yes | | |
| S3 | Desktop connected to all gateways still routes via `bot_peers`; Desktop close does not break delivery | yes | | |
| S4 | `key_env: OFFICE_LLM_API_KEY` without `OPENAI_API_KEY`, no airgap patch | no | | |
| S5 | Bot Mode marker + "Bot Chat" survive restart | no | | |
| S6 | `extra_body.chat_template_kwargs` reaches LiteLLM | no | | |
| S7 | Specialist with `bot_mode_protocol: false` receives peer messages and has no `message_agent` | no | | |

## Decisions triggered

(none yet)
