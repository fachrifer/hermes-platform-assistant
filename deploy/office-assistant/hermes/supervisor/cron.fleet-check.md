# Nightly Kanban fleet check

Enable once the Dashboard is up. Hermes cron is configured via Dashboard **Cron** or `config.yaml` `cronjobs` (version-dependent). Paste the job body below verbatim.

**Schedule:** `0 1 * * *` at `01:00` **Asia/Jakarta** (set `TZ=Asia/Jakarta` on the supervisor container or in the Dashboard cron UI).

## Job body

```text
Every day at 01:00 (Asia/Jakarta): create a Kanban card titled "nightly fleet check".
A2A call lab-host, vector, cluster-gpu, llm-edge, obs asking for a short health+analysis summary.
cluster-gpu must include MIG map. obs must include Grafana panel links.
Comment each specialist reply on the card. Complete the card, or block it naming any failed peer.
Do not propose writes during the nightly check.
```

## Supervisor config

The supervisor `config.yaml` sets `kanban.dispatch_in_gateway: true` so this gateway owns Kanban dispatch. Specialist gateways must not dispatch (default Hermes multi-gateway posture).
