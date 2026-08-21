# Office Assistant Fleet

Supervisor Hermes (Dashboard) plus five specialist agents and one `office-gateway` on the Lab VM.

## Prerequisites

- Docker Compose v2
- Pinned `nousresearch/hermes-agent` image (air-gap: build or pull on a connected machine, then `docker save` / `docker load` on the Lab VM)
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
```

## Laptop functionality check

On this Mac (not the Lab VM):

```bash
./scripts/local-up.sh
```

Athena console: `http://127.0.0.1:9120` (VTuber portrait, specialist wall, and Ops chat in one page). Raw Hermes Dashboard: `http://127.0.0.1:9119`. Login is in `.local-login`. The first visit to the console opens a **How to use** guide (who Athena and the specialists are, plus copyable starter prompts). Reopen it from the header.

Chat uses `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` from gitignored `models.env` (see `models.env.example`). Compose loads each role `.env` first, then `models.env`, so LiteLLM/OpenAI settings in `models.env` apply to Athena and every specialist. `local-up.sh` also copies those three values into every `hermes/*/.env`. The hostname `litellm.lab` does not resolve inside Docker on a laptop unless you replace it with a reachable URL.

## Setup

On the Lab VM itself (or after `deploy.sh` has copied the tree):

From `deploy/office-assistant`:

1. Copy environment templates (skips files that already exist):

   ```bash
   ./scripts/copy-env.sh
   ```

2. Fill the operator `.env` with all six `OFFICE_GATEWAY_TOKEN_*` values,
   `OFFICE_SERVICE_URLS`, Grafana placeholders, write allowlists,
   `OFFICE_KUBECONFIG`, and `DOCKER_GID`. This file is loaded only by
   `office-gateway`, never by a Hermes agent.

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

5. Open the Athena console at `http://10.216.4.80` (or the host in
   `HERMES_CONSOLE_PUBLISH`) and sign in with the supervisor's
   `HERMES_DASHBOARD_USERNAME` / `HERMES_DASHBOARD_PASSWORD`. Raw Dashboard
   remains at `http://10.216.4.80:9119`.

Other scripts:

```bash
./scripts/status.sh
./scripts/restart.sh          # optional service names as extra args
./scripts/stop.sh
./scripts/doctor.sh           # hermes doctor in every Hermes container
```

## Topology

- **office-gateway** — platform reads/writes; mounts Docker socket (read-only); exposes port 8080 on the Compose network only (no host publish).
- **hermes-agent** — supervisor; publishes Dashboard on `HERMES_DASHBOARD_PUBLISH` (default `10.216.4.80:9119`); star A2A to five specialists.
- **office-console** — Athena overlay on `HERMES_CONSOLE_PUBLISH` (default `10.216.4.80:80`); specialist wall + VTuber portrait + iframe to Dashboard chat. Stop any old `dashboard-proxy` that still binds Lab `:80`.
- **hermes-lab-host**, **hermes-vector**, **hermes-cluster-gpu**, **hermes-llm-edge**, **hermes-obs** — specialists; A2A on `:9900` internally; no host ports; each has its own `HERMES_HOME` volume.

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

After the Dashboard is up, enable the nightly Kanban job:

1. Open Dashboard **Cron** (or add to `config.yaml` `cronjobs` if your Hermes version supports it).
2. Schedule `0 1 * * *` with timezone **Asia/Jakarta**.
3. Paste the job body from [`hermes/supervisor/cron.fleet-check.md`](hermes/supervisor/cron.fleet-check.md) verbatim.

The supervisor `config.yaml` enables `kanban.dispatch_in_gateway` so Kanban dispatch runs on the supervisor gateway.

## v1 acceptance checklist

Manual verification once the fleet is running:

1. Open `http://10.216.4.80` (Athena console) or `http://10.216.4.80:9119` (raw Dashboard), login. First console visit shows **How to use**; reopen from the header.
2. Ask “how’s the fleet?” — all five domains represented; MIG map present; Grafana links present.
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

`save-images.sh` pulls the pinned `HERMES_IMAGE`, `busybox:1.36`, `nginx`,
and builds `office-gw:local` for **linux/amd64** (the Lab VM). A Mac laptop
must still ship amd64 images; native arm64 tarballs fail on the Lab with
`exec format error` / init exit 255. The Lab host cannot reach Docker Hub, so
`deploy-and-start.sh` will not pull replacements. Rebuild the tarball on a
networked machine, copy it, then `docker load`. After load it runs
`docker compose up -d` (or `up -d --build` only if the gateway image is
missing entirely).
