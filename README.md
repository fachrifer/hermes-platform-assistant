# Office AI Assistant

Intranet **Hermes Agent Web Dashboard** plus **office-gateway**. This repo is
the office platform assistant only — not the personal Telegram finance bot
and not CML Qwen serving.

**Target:** `timai@10.216.4.80:/home/timai/hermes-assistant`  
**Dashboard:** `http://10.216.4.80/` (also `:9119`)

The VM has **no internet**. Build images on a PC, ship a tar, `docker load`,
then `docker compose up` with `pull_policy: never`.

Hermes uses the intranet **LiteLLM** gateway at `http://10.216.221.100/llm/v1`.
office-gateway probes the other apps on this VM via `host.docker.internal`.

```text
Browser → http://10.216.4.80/      (dashboard-proxy → Hermes)
       or http://10.216.4.80:9119  (Hermes Dashboard direct)
              ├─ LLM  → http://10.216.221.100/llm/v1
              └─ tools → office-gateway :8080 (internal)
```

| Container | Host port | Role |
|---|---|---|
| LiteLLM `10.216.221.100/llm/v1` | — | LLM for Hermes |
| `aiplatform-agent-inference` | 8010 | monitored |
| `aiplatform-workflow` | 8001 | monitored |
| `aiplatform-dashboard` | 3001 | monitored |
| `common-service-frontend` | 555 | monitored |
| `milvus-standalone` | 9091 / 19530 / 2379 | vector DB |
| `attu` | 8000 | Milvus UI |
| `qdrant_timai` | 6333–6334 | vector DB |
| `hermes-agent` | **9119** | Web Dashboard |
| `dashboard-proxy` | **80** | nginx → dashboard |
| `office-gateway` | internal 8080 | monitor + APPROVE writes |

Design: `docs/superpowers/specs/2026-08-12-office-hermes-assistant-design.md`.  
Runbook: `deploy/office-assistant/README.md`.

```bash
# On PC (internet):
chmod +x deploy/office-assistant/scripts/*.sh
./deploy/office-assistant/scripts/build-and-ship.sh
# scripts/configs only: ./deploy/office-assistant/scripts/ship-to-vm.sh --scripts-only

# On VM:
cd /home/timai/hermes-assistant
cp -n .env.example .env && cp -n hermes/.env.example hermes/.env
# set OPENAI_API_KEY, LITELLM_BASE_URL, LITELLM_MODEL, dashboard password
chmod +x scripts/*.sh
./scripts/load-and-start.sh
# equivalent: ./scripts/load-images.sh && docker compose --env-file .env -f docker-compose.yml up -d
```

Writes: propose → reply exactly `APPROVE <action_id>` → execute.
