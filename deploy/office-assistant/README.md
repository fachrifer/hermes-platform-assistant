# Office Assistant Fleet

Supervisor Hermes (Dashboard) plus five specialist agents and one `office-gateway` on the Lab VM.

## Prerequisites

- Docker Compose v2
- Pinned `nousresearch/hermes-agent` image (air-gap: build or pull on a connected machine, then `docker save` / `docker load` on the Lab VM)
- LiteLLM or another OpenAI-compatible endpoint reachable from every container

## Setup

1. Copy environment templates:

   ```bash
   cp .env.example .env
   for role in supervisor lab-host vector cluster-gpu llm-edge obs; do
     cp "hermes/$role/.env.example" "hermes/$role/.env"
   done
   ```

2. Fill the operator `.env` with all six `OFFICE_GATEWAY_TOKEN_*` values,
   `OFFICE_SERVICE_URLS`, Grafana placeholders, write allowlists,
   `OFFICE_KUBECONFIG`, and `DOCKER_GID`. This file is loaded only by
   `office-gateway`, never by a Hermes agent.

   When `OFFICE_KUBECONFIG` points at a host file, `office-gateway-init`
   copies it into the gateway data volume at
   `/var/lib/hermes-office-gateway/kubeconfig` with owner `10001:10001` and
   mode `0640`. The gateway process reads that copy via `KUBECONFIG`. If no
   source kubeconfig is mounted, the copy step is skipped.

3. Fill each `hermes/<role>/.env` with only that agent's gateway token, a
   role-scoped LiteLLM key, and its A2A credentials. Each specialist's
   `A2A_BEARER_TOKEN` must match the corresponding `A2A_TOKEN_*` in the
   supervisor env. Set supervisor dashboard username/password before binding
   to the Lab LAN.

4. After the first `docker compose up -d --build`, run `docker compose ps -a`
   and update `OFFICE_WRITE_LAB_HOST` in `.env` with the actual Hermes
   container names if they differ from the placeholders.

5. Start the fleet:

   ```bash
   docker compose up -d --build
   ```

6. Open the Dashboard at `http://10.216.4.80:9119` (or the host in
   `HERMES_DASHBOARD_PUBLISH`) and sign in with the supervisor's
   `HERMES_DASHBOARD_USERNAME` / `HERMES_DASHBOARD_PASSWORD`.

## Topology

- **office-gateway** — platform reads/writes; mounts Docker socket (read-only); exposes port 8080 on the Compose network only (no host publish).
- **hermes-agent** — supervisor; publishes Dashboard on `HERMES_DASHBOARD_PUBLISH` (default `10.216.4.80:9119`); star A2A to five specialists.
- **hermes-lab-host**, **hermes-vector**, **hermes-cluster-gpu**, **hermes-llm-edge**, **hermes-obs** — specialists; A2A on `:9900` internally; no host ports; each has its own `HERMES_HOME` volume.

Kanban data lives on the supervisor at `/opt/data/kanban` (`kanban_data` volume). Specialists do not share the supervisor home directory.

Skills mount from `skills/<role>/`.

## Nightly fleet check

After the Dashboard is up, enable the nightly Kanban job:

1. Open Dashboard **Cron** (or add to `config.yaml` `cronjobs` if your Hermes version supports it).
2. Schedule `0 1 * * *` with timezone **Asia/Jakarta**.
3. Paste the job body from [`hermes/supervisor/cron.fleet-check.md`](hermes/supervisor/cron.fleet-check.md) verbatim.

The supervisor `config.yaml` enables `kanban.dispatch_in_gateway` so Kanban dispatch runs on the supervisor gateway.

## v1 acceptance checklist

Manual verification once the fleet is running:

1. Open `http://10.216.4.80:9119` (or `HERMES_DASHBOARD_PUBLISH`), login.
2. Ask “how’s the fleet?” — all five domains represented; MIG map present; Grafana links present.
3. Propose restart `common-service-frontend` → reply `APPROVE <id>` → container restarts; audit row exists.
4. Propose restart `milvus-standalone` via vector path → APPROVE works.
5. Ask to restart prod Milvus → gateway/skill refuses.
6. Next morning: Kanban card “nightly fleet check” is `done` or `blocked` with peer name.

## Air-gap image shipping

On a networked machine:

```bash
docker pull nousresearch/hermes-agent:<tag>
docker build -f Dockerfile.office-gateway -t office-gw:local ../..
docker save nousresearch/hermes-agent:<tag> office-gw:local | gzip > office-fleet-images.tar.gz
```

On the Lab VM:

```bash
docker load < office-fleet-images.tar.gz
```
