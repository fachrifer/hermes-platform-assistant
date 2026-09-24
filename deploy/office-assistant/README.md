# Office Assistant Fleet

Supervisor Hermes (Dashboard) plus six specialist agents and one `office-gateway` on the Lab VM.

## Prerequisites

- Docker Compose v2
- Pinned `nousresearch/hermes-agent:v2026.8.31` image (air-gap: build or pull on a connected machine, then `docker save` / `docker load` on the Lab VM)
- LiteLLM or another OpenAI-compatible endpoint reachable from every container

## Deploy from laptop

From `deploy/office-assistant` on a machine that can reach the Lab VM
(`timai@10.216.4.80`). SSH will prompt for the `timai` password; it does not
use BatchMode (which would cancel instead of asking). `/home/timai/hermes-assistant`
is assumed to already exist.

```bash
./scripts/deploy.sh
```

`deploy.sh` on the laptop only packages and copies. It never binds Lab ports.
If `images/office-fleet-images.tar.gz` is missing it runs `save-images.sh` locally
(the Lab cannot reach Docker Hub), rsyncs this Compose tree (including every
`scripts/*.sh`), `office_gateway`, and `Dockerfile.office-gateway` to
`/home/timai/hermes-assistant`. It overwrites Lab `.env`, `models.env`, and
each `hermes/<role>/.env` from this laptop. Before the copy it pairs each
role's `OFFICE_GATEWAY_TOKEN` and `A2A_BEARER_TOKEN` with the operator `.env`
and supervisor A2A tokens. Then it pins Lab bind addresses
(`10.216.4.80`, `OFFICE_GATEWAY_CONTEXT=.`) so laptop `local-up.sh` values
are not used on the server. Then it SSHs in and runs
`./scripts/deploy-and-start.sh`. That server script `docker load`s the tarball,
stops anything still holding Dashboard `:9119` or console `:80` (including the
old `hermes-assistant` stack), and `docker compose up -d`.
`scripts/load-and-start.sh` is a compatibility alias for the same entrypoint.
Do **not** run `deploy-and-start.sh` on the Mac; the tarball is linux/amd64.
On the laptop use `./scripts/local-up.sh` or `./scripts/deploy.sh`.

On the Lab VM the runnable layout is:

```text
/home/timai/hermes-assistant
  scripts/copy-env.sh
  scripts/deploy-and-start.sh
  scripts/load-and-start.sh
  scripts/stop.sh
  scripts/status.sh
  scripts/restart.sh
  docker-compose.yml
  office_gateway/
  Dockerfile.office-gateway
  images/office-fleet-images.tar.gz
```

Override host, user, or remote path if needed:

```bash
OFFICE_DEPLOY_HOST=10.216.4.80 OFFICE_DEPLOY_USER=timai \
  OFFICE_DEPLOY_DIR=/home/timai/hermes-assistant ./scripts/deploy.sh
OFFICE_DEPLOY_REBUILD=1 ./scripts/deploy.sh   # rebuild/save images first
OFFICE_DEPLOY_SKIP_IMAGES=1 ./scripts/deploy.sh  # code only; Docker Hub not needed
```

## Laptop functionality check

On this Mac (not the Lab VM):

```bash
./scripts/local-up.sh
```

Athena console: `https://127.0.0.1:9443` (import `ca/ca.crt` once; `http://127.0.0.1:9120` redirects). Raw Hermes Dashboard: `http://127.0.0.1:9119`. Login is in `.local-login`. The first visit to the console opens a **How to use** guide (who Athena and the specialists are, plus copyable natural-language starters). Reopen it from the header. Confirm fleet checks with YES/OK/ya/lanjut/go; reports show each agent's capabilities and which endpoints are up; writes still need `APPROVE <id>`; the hourly fleet cron (and scheduled autoheal) is disabled — specialist watch plus chat `APPROVE` cover status and restarts.

Chat uses `OPENAI_BASE_URL` and `OPENAI_MODEL` from gitignored `models.env` (see `models.env.example`). Per-agent LiteLLM virtual keys live in `hermes/<role>/.env` as `OPENAI_API_KEY`. Compose loads `models.env` first, then the role `.env`, so a leftover `OPENAI_API_KEY` in `models.env` cannot override the role key. `local-up.sh` copies non-empty `OPENAI_*` values from `models.env` into every `hermes/*/.env`. The hostname `litellm.lab` does not resolve inside Docker on a laptop unless you replace it with a reachable URL.

## Setup

On the Lab VM itself (or after `deploy.sh` has copied the tree):

From `deploy/office-assistant`:

1. Copy environment templates (skips files that already exist):

   ```bash
   ./scripts/copy-env.sh
   ```

2. Fill the operator `.env` with all six `OFFICE_GATEWAY_TOKEN_*` values,
   `OFFICE_SERVICE_URLS`, Grafana placeholders, write allowlists,
   `OFFICE_KUBECONFIG`, `DOCKER_GID`, `OFFICE_LITELLM_URL`, and
   `OFFICE_LITELLM_MASTER_KEY` (alias `LITELLM_MASTER_KEY`). This file is loaded only by
   `office-gateway`, never by a Hermes agent. Iris checks LiteLLM through
   `GET /v1/llm/status` and `GET /v1/litellm/*` so the master key never appears in a Hermes command.

   When `OFFICE_KUBECONFIG` points at a host file, `office-gateway-init`
   copies it into the gateway data volume at
   `/var/lib/hermes-office-gateway/kubeconfig` with owner `10001:10001` and
   mode `0640`. The gateway process reads that copy via `KUBECONFIG`. If the
   variable is unset or points at the empty `kubeconfig.absent` placeholder,
   the copy step is skipped.

3. Fill each `hermes/<role>/.env` with only that agent's gateway token, a
   role-scoped LiteLLM key, and its A2A credentials. Each specialist's
   `A2A_BEARER_TOKEN` must match the corresponding `A2A_TOKEN_*` in the
   supervisor env. Set supervisor dashboard username/password before binding
   to the Lab LAN.

4. Start the fleet (loads `images/office-fleet-images.tar.gz` when present):

   ```bash
   ./scripts/deploy-and-start.sh
   ```

   After the first start, run `./scripts/status.sh` and update
   `OFFICE_WRITE_LAB_HOST` in `.env` with the actual Hermes container names
   if they differ from the placeholders.

5. Open the Athena console at `https://10.216.4.80` after importing
   `ca/ca.crt` into the client trust store (see HTTPS section). Sign in
   with the supervisor's `HERMES_DASHBOARD_USERNAME` /
   `HERMES_DASHBOARD_PASSWORD`. Prefer `/dash/` over raw Dashboard
   `http://10.216.4.80:9119` (firewall should close raw ports from outside).

Other scripts:

```bash
./scripts/status.sh
./scripts/restart.sh          # optional service names as extra args
./scripts/stop.sh
./scripts/doctor.sh           # hermes doctor in every Hermes container
./scripts/init-lab-ca.sh
./scripts/issue-edge-cert.sh
./scripts/render-edge-traefik.py
./scripts/render-traefik-core.sh
LAB_FIREWALL_APPLY=1 ./scripts/lab-firewall-https-edge.sh   # Lab only (optional; not required for Traefik)
```

## HTTPS edge (Traefik)

`office-edge` (Traefik) terminates TLS on `:443` with a **Lab Internal CA** leaf
(IP SAN `10.216.4.80` and `127.0.0.1`, 90 days). HTTP `:80` redirects to HTTPS.
Athena static is served by internal `office-www` (no host ports). The CA private
key stays on the host in `ca/` and is **not** mounted into Traefik.

```bash
./scripts/init-lab-ca.sh          # once; creates ca/ca.crt + ca/ca.key
./scripts/issue-edge-cert.sh      # leaf + chain → certs/tls.crt (FORCE=1 to reissue)
./scripts/render-traefik-core.sh  # injects supervisor token into core routers
./scripts/render-edge-traefik.py  # edge-routes → traefik-dynamic/routes.yml
```

Import **`ca/ca.crt`** (public) into each operator machine once. After that,
`https://10.216.4.80` verifies without a browser warning; rotating the leaf
does not require another client change.

- macOS: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ca/ca.crt`
- Linux: copy to `/usr/local/share/ca-certificates/lab-internal-ca.crt` then `sudo update-ca-certificates`
- Windows: import `ca.crt` into Trusted Root Certification Authorities
- Firefox uses its own store if it does not follow the OS

Lab HTTP UIs/APIs behind path prefixes (edit `edge/edge-routes`, then render):

| Path | Upstream (host) | Notes |
|---|---|---|
| `/attu/` | `127.0.0.1:8000` | prefix stripped |
| `/toolbox/` | `127.0.0.1:555` (`common-service-frontend`) | keep prefix — rebuild with `BASE_PATH=/toolbox` |

Optional 5th column on `edge/edge-routes`: `strip_prefix` (`1` default; `0` keeps path for Next.js `basePath`).

```bash
# after editing edge/edge-routes
python3 ./scripts/render-edge-traefik.py
./scripts/render-traefik-core.sh
docker compose up -d office-edge office-www
```

**Toolbox (Next.js):** rebuild with `BASE_PATH=/toolbox` and `trailingSlash: true`; edge row  
`toolbox /toolbox/ 127.0.0.1:555 0 0`. Raw host `:555` / `:8000` are **unchanged** by this Traefik cutover (firewall not modified).

AI Platform / inference / workflow are **not** on the HTTPS edge (image-only Next/APIs without basePath). Use their host ports on the Lab if needed.

Optional Lab firewall (separate from Traefik):

```bash
./scripts/lab-firewall-https-edge.sh
LAB_FIREWALL_APPLY=1 ./scripts/lab-firewall-https-edge.sh
```

Milvus gRPC (`:19530`) is **not** an HTTP path on Traefik in this delivery.

## Janus (Traefik edge)

**Janus** (`hermes-edge`) owns HTTPS **edge** path routes: `edge/edge-routes`,
generated `edge/traefik-dynamic/routes.yml`. Apply uses Traefik **file watch**
(does **not** restart `office-edge`, which drops Dashboard chat WebSocket).

Athena routes edge work via A2A (`Janus` peer). Janus calls office-gateway with
the **edge** role token:

```text
GET  /v1/edge/routes      # list current routes (always first)
GET  /v1/edge/status      # paths + office-edge status + tls
GET  /v1/edge/tls         # leaf expiry
POST /v1/edge/validate    # dry-run parse (no write)
POST /v1/actions/propose  # apply_edge_routes | restart_service (office-edge)
POST /v1/actions/execute  # after operator replies APPROVE <action-id>
```

Workflow for a new path (e.g. `/foo/` → `127.0.0.1:9999`):

1. Janus lists routes with `GET /v1/edge/routes`.
2. Janus validates full file content with `POST /v1/edge/validate`.
3. Janus proposes `apply_edge_routes` with the complete `edge-routes` text.
4. Athena shows `APPROVE <action-id>`; you paste it back exactly.
5. Gateway writes `edge-routes` + `routes.yml`. Traefik reloads via file watch. Do **not** restart `office-edge` unless routes stay stale (that drop Dashboard chat — then refresh `/dash/`).

Restarting `office-edge` briefly drops Athena HTTPS and the Dashboard
iframe. Janus warns before propose/execute.

**In scope for Janus (read):** TLS leaf status/expiry via `GET /v1/edge/tls`, plus operator renew guidance.  
**Not in scope for Janus:** Traefik static core template, issuing TLS / holding `ca.key`, Lab firewall,
or deploying app containers. Manual fallback (no APPROVE gate):

```bash
# edit edge-routes, then:
python3 ./scripts/render-edge-traefik.py
./scripts/render-traefik-core.sh
docker compose up -d office-edge
```

Skill: `skills/edge/SKILL.md`. Console guide includes Janus starter prompts.

## Topology

- **office-gateway** — platform reads/writes; mounts Docker socket (read-only); exposes port 8080 on the Compose network only (no host publish).
- **hermes-agent** — supervisor; publishes Dashboard on `HERMES_DASHBOARD_PUBLISH` (default `10.216.4.80:9119`); star A2A to six specialists. Image pin: `nousresearch/hermes-agent:v2026.8.31` (v0.21.0). Keep separate containers — do not collapse to Hermes Bot Mode / in-container profiles; each specialist holds a scoped office-gateway token.
- **office-edge** — Traefik front door on `HERMES_CONSOLE_PUBLISH` (`:80`; HTTP `/bots/` for Desktop, everything else redirects to HTTPS) and `HERMES_CONSOLE_TLS_PUBLISH` (`:443` HTTPS); path proxy for Lab HTTP services via `edge-routes`.
- **office-www** — village/ops-map files kept on disk; Traefik **does not serve** `/` as village (`/` redirects to `/dash/`). Still used for `/avatars/` if needed.
- **hermes-lab-host**, **hermes-vector**, **hermes-cluster-gpu**, **hermes-llm-edge**, **hermes-obs**, **hermes-edge** — specialists; A2A on `:9900` internally; dashboard + API server on the Compose network only (no host ports). Desktop: `http://<lab>/bots/<role>/`. Browser with Lab CA: `https://<lab>/bots/<role>/`. Each has its own `HERMES_HOME` volume.

```bash
./scripts/desktop-connect.sh --lab
```

Then open gitignored `.local-login`. Hermes Desktop → Remote gateway → **Gateway URL** = `url=` (`http://10.216.4.80:9119`) and **Session token** = `session_token=`. Extra headers empty; **do not use OAuth**. Use HTTP `:9119`, not `https://…/dash` (Electron rejects the Lab Internal CA). Restart `hermes-agent` after pairing so `/api/ws` sees the same session token and `dashboard.public_url`. Desktop Test can pass HTTP (`/api/status` is public) while WebSocket fails if Origin does not match that URL or the token is stale.

Specialists are additional Remote gateways on published HTTP ports (same session token, not OAuth): `http://10.216.4.80:9121` lab-host, `:9122` vector, `:9123` cluster-gpu, `:9124` llm-edge, `:9125` obs, `:9126` edge. Same pattern as Athena `:9119`. Do **not** use `http://10.216.4.80/bots/…` in Desktop — `/api/status` can pass while `/api/ws` fails behind Traefik. Browser path `/bots/` remains for CA-trusting browsers.

`./scripts/desktop-connect.sh --local` writes the laptop card (`http://127.0.0.1:9119`). `./scripts/deploy.sh` runs the Lab variant before rsyncing env files.

Same dashboard user/pass still works in the browser. Village/ops-map at `/` stays deployed; it is not the new primary UX.

Kanban data lives on the supervisor at `/opt/data/kanban` (`kanban_data` volume). Specialists do not share the supervisor home directory.

Skills mount from `skills/<role>/`.

## Faster Dashboard Chat start

Opening Chat (or sending the first message) builds a new agent session.
With the default `hermes-cli` toolset that work includes browser/npx,
Playwright, MCP discovery, and a 300s A2A reply wait — often ~5 minutes
on an air-gapped Lab VM. The fleet configs drop `hermes-cli`, turn
browser/MCP off, cap LiteLLM at 60s, and cap A2A at 45s/90s.

On the Lab VM, after Compose is up:

```bash
./scripts/doctor.sh
```

`hermes doctor` should report no MCP servers, browser backend unused, and
a working `office-litellm` key. Then restart so Chat picks up the bind-mounted
`config.yaml`:

```bash
./scripts/restart.sh
```

From this laptop, `./scripts/deploy.sh` copies the tuned configs and restarts
the stack. After restart, a new Dashboard Chat session should be ready in
seconds, not minutes. If doctor still warns about `agent-browser` / npx or
Playwright Chromium, those tools are unused for office Chat — ignore them
unless you re-enable `hermes-cli`.

## Nightly fleet check

Athena’s Cron ticker only runs when the supervisor Hermes **gateway** is up
(`command: gateway run` plus `HERMES_DASHBOARD=1`). If the sidebar shows
**System Gateway Status: Off**, jobs will never fire — restart `hermes-agent`.

Deploy seeds job **nightly fleet check** (`0 1 * * *`, `TZ=Asia/Jakarta`, `--continuity`) from
[`hermes/supervisor/cron.fleet-check.prompt.txt`](hermes/supervisor/cron.fleet-check.prompt.txt).
Confirm it in Dashboard **Cron**. Use **Trigger now** to run once without waiting
until 01:00. Next morning the Kanban card should be `done` or `blocked`.
The hourly fleet cron is **not** seeded — 60s specialist watch already covers FAST
snapshots, so a second LLM cron would duplicate that function.

The supervisor `config.yaml` enables `kanban.dispatch_in_gateway` so Kanban dispatch runs on the supervisor gateway.

## v1 acceptance checklist

Manual verification once the fleet is running:

1. Open `http://10.216.4.80` (Athena console) or `http://10.216.4.80:9119` (raw Dashboard), login. First console visit shows **How to use**; reopen from the header.
2. Ask “how’s the fleet?” — all six domains represented; MIG map present; Grafana links present.
3. Propose restart `common-service-frontend` → reply `APPROVE <id>` → container restarts; audit row exists.
4. Propose restart `milvus-standalone` via vector path → APPROVE works.
5. Ask to restart prod Milvus → gateway/skill refuses.
6. Next morning: Kanban card “nightly fleet check” is `done` or `blocked` with peer name.

## Air-gap image shipping

On a networked machine (from `deploy/office-assistant`):

```bash
./scripts/save-images.sh
# default output: images/office-fleet-images.tar.gz
# optional: ./scripts/save-images.sh /tmp/office-fleet-images.tar.gz
```

Copy the tarball (and this repo) to the Lab VM. Then:

```bash
./scripts/deploy-and-start.sh
# or: ./scripts/deploy-and-start.sh /path/to/office-fleet-images.tar.gz
# alias: ./scripts/load-and-start.sh
```

`save-images.sh` pulls the pinned `HERMES_IMAGE`, `busybox:1.36`, `nginx`
(for `office-www`), **`traefik:v3.3`** (for `office-edge`), and builds
`office-gw:local` for **linux/amd64** (the Lab VM). A Mac laptop
must still ship amd64 images; native arm64 tarballs fail on the Lab with
`exec format error` / init exit 255. The Lab host cannot reach Docker Hub, so
`deploy-and-start.sh` will not pull replacements. Rebuild the tarball on a
networked machine, copy it, then `docker load`. After load it runs
`docker compose up -d` (or `up -d --build` only if the gateway image is
missing entirely).
