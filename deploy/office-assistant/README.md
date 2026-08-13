# Office AI Assistant — Deploy (air-gapped VM)

**Target:** `timai@10.216.4.80:/home/timai/hermes-assistant`  
**Dashboard:** `http://10.216.4.80/` (port 80; also `http://10.216.4.80:9119`)

Nous Hermes Agent Web Dashboard + office-gateway, beside the apps already
running on this VM. The VM has **no internet**. Build on a PC, ship a tar,
`docker load`, then `docker compose up` with `pull_policy: never`.

Hermes calls the intranet **LiteLLM** gateway at
`http://10.216.221.100/llm/v1`. office-gateway probes local containers through
`host.docker.internal`. New services do **not** reuse existing host ports.

## Coexisting containers

| Container | Host port | Role |
|---|---|---|
| LiteLLM (external `10.216.221.100`) | `/llm/v1` | LLM for Hermes |
| `aiplatform-agent-inference` | 8010 | monitored (`/health`) |
| `aiplatform-workflow` | 8001 | monitored (`/health`) |
| `aiplatform-dashboard` | 3001 | monitored (`/`) |
| `common-service-frontend` | 555 | monitored (`/`) |
| `milvus-standalone` | 9091, 19530, 2379 | vector DB (probe `:9091/healthz`) |
| `attu` | 8000 | Milvus UI (probe `/`) |
| `qdrant_timai` | 6333–6334 | vector DB (probe `:6333/readyz`) |
| **hermes-agent** | **9119** | Web Dashboard |
| **dashboard-proxy** | **80** | nginx → dashboard (external HTTP test) |
| **office-gateway** | internal 8080 only | health tools + APPROVE gate |

If a probe returns 404, change that URL in `OFFICE_SERVICE_URLS`. A 401 on
inference still counts as up (key required, process alive).

## Architecture

```text
PC (internet)                     VM 10.216.4.80
├── docker pull hermes-agent      /home/timai/hermes-assistant
├── docker build office-gateway   ├── docker load  (no registry)
└── ship-to-vm.sh  ────────────►  ├── docker compose up -d
                                  └── :80 / :9119 → http://10.216.221.100/llm/v1
```

## 1. PC (has internet)

```bash
chmod +x deploy/office-assistant/scripts/*.sh
./deploy/office-assistant/scripts/build-and-ship.sh
```

Same as `export-images.sh` then `ship-to-vm.sh`. Builds Hermes + office-gateway for
**`linux/amd64`** (the VM). On Apple Silicon that is a cross-build — do not
ship `arm64` images to `10.216.4.80`.

`ship-to-vm.sh` rsyncs this folder to `/home/timai/hermes-assistant` and copies
the latest tar into `dist/`. It does **not** overwrite `.env` or `hermes/.env`.

Copy scripts and compose only (no image tar):

```bash
./deploy/office-assistant/scripts/ship-to-vm.sh --scripts-only
```

## 2. VM (no internet)

```bash
cd /home/timai/hermes-assistant
cp -n .env.example .env
cp -n hermes/.env.example hermes/.env
```

Edit `.env` and `hermes/.env`:

- `OPENAI_API_KEY` — LiteLLM key (must be in **both** `.env` and `hermes/.env`)
- `LITELLM_BASE_URL=http://10.216.221.100/llm/v1`
- `LITELLM_MODEL` — id from `curl -sS http://10.216.221.100/llm/v1/models` (e.g. `qwen3.5-fast`)
- `hermes/config.yaml` already points at `${LITELLM_MODEL}` / `${LITELLM_BASE_URL}`
- `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD`
- `OFFICE_GATEWAY_TOKEN`

Image tags must match `dist/*.txt`. Then:

```bash
chmod +x scripts/*.sh
./scripts/load-and-start.sh
# or, separately:
./scripts/load-images.sh dist/office-assistant-images-<timestamp>.tar
./scripts/start.sh
```

Do **not** use `docker compose up --build` on the VM.

Open `http://10.216.4.80/` (or `http://10.216.4.80:9119`) and sign in with the dashboard password.

Hermes must run `command: ["gateway", "run"]` (already in this compose). The
default image CMD is the interactive CLI; without a TTY it prints
`Input is not a terminal` and shuts down, which also stops the dashboard.

## Ask-before-execute

1. Ask Hermes to perform a write (restart, scale, clear queue, feature flag).
2. Hermes `POST /v1/actions/propose` and replies with `APPROVE <action_id>`.
3. Reply **exactly** that phrase.
4. Hermes `POST /v1/actions/execute` with that `action_id`.

Propose never mutates the platform. Pending actions expire after 10 minutes.
Restart adapters are unset until `OFFICE_ADAPTER_ENDPOINTS` points at a real
HTTP restart API — execute then returns `adapter not configured`.

## Environment

| Variable | Purpose |
|---|---|
| `OFFICE_VM_HOST` | `10.216.4.80` |
| `OFFICE_VM_USER` | `timai` |
| `OFFICE_VM_DIR` | `/home/timai/hermes-assistant` |
| `OFFICE_GATEWAY_IMAGE` | local tag from the PC bundle |
| `HERMES_IMAGE` | local tag from the PC bundle |
| `HERMES_DASHBOARD_PUBLISH` | `10.216.4.80:9119` |
| `HERMES_DASHBOARD_HTTP_PUBLISH` | `10.216.4.80:80` (nginx proxy for external HTTP) |
| `DASHBOARD_PROXY_IMAGE` | local nginx tag from the PC bundle |
| `HERMES_DASHBOARD` | Must be `1` so s6 starts the web dashboard |
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` | Dashboard login |
| `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD` | Dashboard password |
| `OPENAI_API_KEY` | agent-inference API key |
| `OFFICE_GATEWAY_TOKEN` | One Bearer token: Hermes → gateway, including `/v1/host` |
| `OFFICE_HOST_ENABLED` | `1` = lab VM Docker + CPU/memory/disk snapshot |
| `OFFICE_HOST_CPU_CRITICAL` | CPU load% at or above this is `critical` (default 90) |
| `OFFICE_HOST_MEMORY_CRITICAL` | Memory % critical threshold |
| `OFFICE_HOST_DISK_CRITICAL` | Disk % critical threshold |
| `OFFICE_SERVICE_URLS` | `name=url,...` health probes |
| `OFFICE_WRITE_TARGETS` | `name:action\|action,...` allowlist |
| `OFFICE_SCALE_BOUNDS` | `name:min-max,...` |
| `OFFICE_ADAPTER_ENDPOINTS` | `name.action=url,...` write backends |

## Updating later

PC: `./deploy/office-assistant/scripts/build-and-ship.sh`  
VM: `./scripts/load-and-start.sh`

A new **nginx** image (`dashboard-proxy` on host **:80**) is part of the tar.
Re-export and ship; `--scripts-only` is not enough the first time :80 is added.

## Legacy

`office_observer`, `office_relay`, and CML `hermes_office` are not the
supported office product. See
`docs/superpowers/specs/2026-08-12-office-hermes-assistant-design.md`.
