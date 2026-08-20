# Office Assistant Fleet

Supervisor Hermes (Dashboard) plus five specialist agents and one `office-gateway` on the Lab VM.

## Prerequisites

- Docker Compose v2
- Pinned `nousresearch/hermes-agent` image (air-gap: build or pull on a connected machine, then `docker save` / `docker load` on the Lab VM)
- LiteLLM or other model endpoint reachable from the containers (configure in `hermes/supervisor/.env`)

## Setup

1. Copy environment templates:

   ```bash
   cp .env.example .env
   cp hermes/supervisor/.env.example hermes/supervisor/.env
   ```

2. Fill `.env`: all six `OFFICE_GATEWAY_TOKEN_*` values, five `A2A_TOKEN_*` values, `OFFICE_SERVICE_URLS`, Grafana placeholders, and write allowlists.

3. After the first `docker compose up -d --build`, run `docker compose ps -a` and update `OFFICE_WRITE_LAB_HOST` in `.env` with the actual Hermes container names if they differ from the placeholders.

4. Optional: set `OFFICE_KUBECONFIG` and uncomment the kubeconfig volume in `docker-compose.yml` for cluster-gpu reads.

5. Start the fleet:

   ```bash
   docker compose up -d --build
   ```

6. Open the Dashboard at `http://10.216.4.80:9119` (or the host in `HERMES_DASHBOARD_PUBLISH`).

## Topology

- **office-gateway** — platform reads/writes; mounts Docker socket (read-only); exposes port 8080 on the Compose network only (no host publish).
- **hermes-agent** — supervisor; publishes Dashboard on `HERMES_DASHBOARD_PUBLISH` (default `10.216.4.80:9119`); star A2A to five specialists.
- **hermes-lab-host**, **hermes-vector**, **hermes-cluster-gpu**, **hermes-llm-edge**, **hermes-obs** — specialists; A2A on `:9900` internally; no host ports; each has its own `HERMES_HOME` volume.

Kanban data lives on the supervisor at `/opt/data/kanban` (`kanban_data` volume). Specialists do not share the supervisor home directory.

Skills mount from `skills/<role>/` (may be empty until Task 6).

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
