# Fleet cron jobs (nightly + hourly)

Hermes cron ticks **inside the supervisor gateway** (`gateway run` + `HERMES_DASHBOARD=1`). Dashboard-only Athena will show **System Gateway Status: Off** and jobs never fire.

On first start, `scripts/hermes-supervisor-cron-init.sh` seeds **nightly fleet check** into `/opt/data/cron/jobs.json` (as user `hermes`) with `--continuity` (v0.21.0: previous run output is injected so the job can dedupe) and **removes** `hourly fleet` if it exists. Container `TZ=Asia/Jakarta`.

Hephaestus owns **Lab VM Docker/containers** (not Kubernetes). Surtr owns **GPU-cluster Kubernetes / node-gpu** (not Lab Docker). Janus owns HTTPS edge + TLS leaf expiry.

## nightly fleet check (`0 1 * * *`)

FAST orchestrate all six specialists. **Never** AUTOHEAL. TLS `expired` or `expires_soon` → block the card and include `./scripts/issue-edge-cert.sh`.

Prompt file: `cron.fleet-check.prompt.txt`

```text
Every day at 01:00 (Asia/Jakarta): create a Kanban card titled "nightly fleet check".
One a2a_orchestrate to Hephaestus, Mnemosyne, Surtr, Iris, Argus, Janus with this exact payload to each peer:

FAST: run bash /opt/office/office-gw-fast.sh and reply with its stdout only. No extra probes. Do not propose writes.

Comment each specialist reply on the card. Complete the card, or block it naming any failed peer.
Never send automated Lab restarts during the nightly check. Do not propose writes.

If Janus reports TLS leaf status expired or expires_soon, block the card and include the operator command: ./scripts/issue-edge-cert.sh

Synthesis must include: Hephaestus Lab Docker/container status (running vs exited, unhealthy, Compose names, host usage); Surtr MIG map plus Kubernetes inventory (namespaces, GPU pods and node placement, capacity vs allocatable); Janus HTTPS edge routes and TLS expiry; Argus Grafana panel links.
```

## hourly fleet (disabled)

The `hourly fleet` job (`5 * * * *`) is **disabled** — it duplicated the 60s specialist watch (FAST snapshots, no LLM). Cont-init **removes** it if a previous deploy seeded it (`hermes cron remove "hourly fleet"`). Do **not** re-create it. Hermes v0.21.0 `no_agent` / monitor-mode cron is the upstream equivalent; we already have per-specialist s6 watch.

Specialist watch (60s, no LLM) plus operator SNAPSHOT / `ada yang rusak?` replace the hourly FAST orchestrate. Chat restarts still use `PROPOSE:` + `APPROVE`. Arming Kanban `office-autoheal` does nothing while this job is absent.

Prompt file `cron.hourly-fleet.prompt.txt` is kept on disk for a future re-enable only.

To force a run from Dashboard Cron, use **Trigger now** on **nightly fleet check**. Nightly cards should be `done` or `blocked` the next morning.

## Supervisor config

The supervisor `config.yaml` sets `kanban.dispatch_in_gateway: true` so this gateway owns Kanban dispatch. Specialist gateways must not dispatch (default Hermes multi-gateway posture).
