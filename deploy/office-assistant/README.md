# Office Assistant Fleet

Supervisor Hermes (Athena) plus six specialist agents, one `office-gateway`, and a
Traefik front door on the Lab VM.

Every agent talks to the lab only through the gateway's MCP endpoint (`/mcp`,
toolset `mcp-office`), filtered by its role token. Agents have no terminal, file,
web or browser tools. They read, and they **propose** writes. A person approves
or rejects each proposal on the Approvals page; nothing restarts or changes
before that click.

The fleet has been running on the Lab VM (`10.216.4.80`) since 2026-09-28.
Day-to-day use is the Dashboard, Desktop, and Approvals sections below.
The first-time ship at the bottom already ran; do not run it again.

Architecture and the rollout record live in
`docs/superpowers/specs/2026-09-24-office-fleet-redesign-design.md` and
`docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md`.

## Prerequisites

- Docker Compose v2
- Pinned `nousresearch/hermes-agent:v2026.9.21` image (air-gap: pull on a connected machine, `docker save`, `docker load` on the Lab VM)
- LiteLLM reachable from every container; its host listed in `NO_PROXY` in `docker-compose.yml` (httpx ignores CIDR entries, so list IPs)

## Roles

| Persona | Service | Role id | Desktop port | Writes (after approval) |
|---|---|---|---|---|
| Athena | `hermes-agent` | `supervisor` | 9119 | none; asks specialists via Bot Chat peers |
| Hephaestus | `hermes-lab-host` | `lab-host` | 9121 | restart a lab container or host service |
| Mnemosyne | `hermes-vector` | `vector` | 9122 | none; can inspect milvus-dev (databases, collections, users, roles) |
| Surtr | `hermes-cluster-gpu` | `cluster-gpu` | 9123 | none |
| Iris | `hermes-llm` | `llm` | 9124 | none |
| Argus | `hermes-obs` | `obs` | 9125 | inspect Grafana panels and report their current values; propose a new dashboard (created only after approval) |
| Janus | `hermes-ingress` | `ingress` | 9126 | apply or roll back HTTPS routes |

Configs: `hermes/<role>/config.yaml` (mounted read-only). Skills: `skills/<role>/SKILL.md`
(one short skill per role). Secrets: gitignored `hermes/<role>/.env`.

## First rollout (already done on 2026-09-28)

The PC ships committed files only, so env files, certs and rendered secrets never
leave it. Install the SSH key on the VM once (you type your own password):

```powershell
type $env:USERPROFILE\.ssh\id_ed25519_hermes_lab.pub | ssh timai@10.216.4.80 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

Then, with everything committed:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\office-assistant\scripts\ship-phase1b.ps1
```

On the VM, in `/home/timai/hermes-assistant`:

```bash
docker compose down --remove-orphans
bash images/unpack-phase1b.sh          # backup, retire old files, unpack, docker load
./scripts/migrate-env-phase1b.sh       # first Phase 1b rollout only; idempotent
./scripts/deploy-and-start.sh images/does-not-exist
```

`unpack-phase1b.sh` writes `~/hermes-assistant-pre-phase1b-<ts>.tgz` (mode 600)
and never overwrites `.env` files, certs, or the gateway-owned route files
(`edge/edge-routes`, `edge/traefik-dynamic/routes.yml`, `edge/edge-locations.conf`).
`migrate-env-phase1b.sh` backs up every env file as `<file>.pre-phase1b-<ts>`,
renames old keys, pairs tokens and creates the approver login. It prints key
names only.

`deploy-and-start.sh` never overwrites env files. It renders Traefik
(`render-edge-traefik.py`, `render-traefik-core.sh`), makes sure the approver
login exists, then runs `docker compose down` / `up -d`.

Do not use `scripts/deploy.sh` for the Lab: it copies laptop env files over the Lab's.
It now refuses to run unless `OFFICE_ALLOW_LAB_OVERWRITE=1` (throwaway hosts only).
The old `ship-to-vm.sh` / `build-and-ship.sh` (`rsync --delete` onto the Lab dir) were
removed after they overwrote the fleet layout from a stale checkout. Agent rules live
in the repo root `AGENTS.md` and `.cursor/rules/lab-vm-protected.mdc`.

**Rollback:** `docker compose down`, restore the `~/hermes-assistant-pre-phase1b-<ts>.tgz`
backup over the directory, and run `./scripts/deploy-and-start.sh images/does-not-exist`.
The old agent volumes are untouched (the new fleet uses `*_v2` volumes).

## Environment

- `.env` (operator, loaded only by `office-gateway`): one `OFFICE_GATEWAY_TOKEN_*`
  per role plus `OFFICE_GATEWAY_TOKEN_APPROVER`, `OFFICE_LITELLM_URL` /
  `OFFICE_LITELLM_MASTER_KEY`, Grafana and metrics URLs, `OFFICE_KUBECONFIG`,
  `DOCKER_GID`. The approver token is injected by Traefik on the Approvals page
  and is never given to an agent.
- `models.env`: `OFFICE_LLM_BASE_URL` only.
- `hermes/<role>/.env`: `OFFICE_GATEWAY_TOKEN`, `OFFICE_LLM_API_KEY` (that
  agent's LiteLLM virtual key), `API_SERVER_KEY`, `HERMES_DASHBOARD_SESSION_TOKEN`.
  The supervisor also holds `HERMES_PEER_PEER_<ROLE>_KEY` for each specialist
  (equal to that specialist's `API_SERVER_KEY`) and the dashboard login.

`./scripts/copy-env.sh` creates missing env files from the examples.
`./scripts/pair-env.sh` copies gateway tokens, peer keys and the session token
into the role files and rewrites `.local-login`.

## Approvals

`https://10.216.4.80/approvals/` (HTTPS only, basic auth from
`edge/approvers.htpasswd`). Traefik sets `X-Approver` to the login name and adds
the approver bearer, so the audit log records who clicked. The login is created
once by `./scripts/ensure-approver.sh` (user `timai` unless `OFFICE_APPROVER_USER`
is set); the password is in `.local-login` as `approver_password`. The page lists
pending requests with their diff, asks for confirmation, and shows recent
decisions. Requests expire after `OFFICE_ACTION_TTL_SECONDS` (default 600).

**Release Bot Chat lock** on that page frees Athena's Bot Chat from whatever
holds it. A stuck dashboard TUI is a `tui_gateway` child process and is
stopped. A Desktop connection lives inside the dashboard process itself, which
is never killed: its lease entry is removed from the active-session registry
instead. The session and its history stay. Use it from the VPN when the chat
says it is open in another window. Close Bot Chat in the other window first if
you are still using it there, otherwise two windows can write to one chat. It
does not create a missing Bot Chat session. Specialists are reachable only from
the session titled exactly "Bot Chat", so open that one rather than New chat or
the last session.

To change the approver password: edit `approver_password` in `.local-login`,
run `./scripts/ensure-approver.sh`, then `docker compose restart office-edge`.

Typing "approve" in chat does nothing.

## Hermes Desktop and the web Dashboard

```bash
./scripts/desktop-connect.sh --lab
```

Then open gitignored `.local-login`. Hermes Desktop â†’ Remote gateway â†’ **Gateway URL**
= `url=` (`http://10.216.4.80:9119`) and **Session token** = `session_token=`.
Extra headers empty; do not use OAuth. Use HTTP `:9119`, not `https://â€¦/dash`
(Electron rejects the Lab Internal CA). Specialists are extra Remote gateways on
`:9121`â€“`:9126` (table above) with the same session token. Do not use
`http://10.216.4.80/bots/â€¦` in Desktop; `/api/ws` fails behind Traefik there.

Outside the office (VPN): Bot Chat does not appear in the Dashboard's session
list, because Hermes keeps the canonical Bot Chat hidden (`hidden = 1`; the
Desktop reaches it through the bot row). Bookmark
`https://10.216.4.80/approvals/api/bot-chat/open`: after the approver login it
redirects to the Dashboard chat for the current Bot Chat id (the Dashboard login
keeps the target). The Approvals page has the same door as **Open Bot Chat
(web)**. Not listing it in the Sessions panel is deliberate: Hermes uses the
`hidden` flag to recognise the canonical chat. To open it by hand,
`https://10.216.4.80/dash/chat?resume=<Bot Chat session id>` works; find the id with:

```bash
docker exec -u hermes office-hermes-agent-1 /opt/hermes/.venv/bin/python3 -c \
 "import sqlite3;print(sqlite3.connect('/opt/data/state.db').execute(\"select id from sessions where title='Bot Chat'\").fetchone()[0])"
```

Inside the Dashboard chat, typing `/resume Bot Chat` also works and needs no link: `session.resume` accepts an exact session title (hidden sessions included) and follows Bot Chat to its newest continuation. Use it after moving between Dashboard menus, because the sidebar Chat entry opens `/chat` without `resume` and so starts a new chat.

Only the session titled exactly **Bot Chat** can ask specialists. Any other
session, including New chat and the Dashboard's last session, can only say which
agents are up. If Bot Chat says it is open in another window, release the lock on
the Approvals page (above). Do not delete the session or unhide it.

## Laptop functionality check

```bash
./scripts/local-up.sh
```

Athena console: `https://127.0.0.1:9443` (import `ca/ca.crt` once;
`http://127.0.0.1:9120` redirects). Raw Dashboard: `http://127.0.0.1:9119`.
Approvals: `https://127.0.0.1:9443/approvals/`. Logins are in `.local-login`.
Set `OFFICE_LLM_BASE_URL` in `models.env` and `OFFICE_LLM_API_KEY` in each
`hermes/<role>/.env` (or export `OFFICE_LLM_API_KEY` before running to fill empty ones).

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
LAB_FIREWALL_APPLY=1 ./scripts/lab-firewall-https-edge.sh   # Lab only (optional)
```

## HTTPS edge (Traefik)

`office-edge` (Traefik) terminates TLS on `:443` with a **Lab Internal CA** leaf
(IP SAN `10.216.4.80` and `127.0.0.1`, 90 days). HTTP `:80` redirects to HTTPS.
Static files are served by internal `office-www` (no host ports). The CA private
key stays on the host in `ca/` and is **not** mounted into Traefik.

```bash
./scripts/init-lab-ca.sh          # once; creates ca/ca.crt + ca/ca.key
./scripts/issue-edge-cert.sh      # leaf + chain â†’ certs/tls.crt (FORCE=1 to reissue)
./scripts/render-traefik-core.sh  # injects supervisor + approver tokens into core.yml
./scripts/render-edge-traefik.py  # edge-routes â†’ traefik-dynamic/routes.yml
```

`edge/traefik-dynamic/core.yml` is rendered on the host and gitignored because it
contains tokens. Edit `core.yml.template` instead.

Import **`ca/ca.crt`** (public) into each operator machine once:

- macOS: `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ca/ca.crt`
- Linux: copy to `/usr/local/share/ca-certificates/lab-internal-ca.crt` then `sudo update-ca-certificates`
- Windows: import `ca.crt` into Trusted Root Certification Authorities
- Firefox uses its own store if it does not follow the OS

Lab HTTP UIs/APIs behind path prefixes (`edge/edge-routes`):

| Path | Upstream (host) | Notes |
|---|---|---|
| `/attu/` | `127.0.0.1:8000` | prefix stripped |
| `/toolbox/` | `127.0.0.1:555` (`common-service-frontend`) | keep prefix â€” rebuild with `BASE_PATH=/toolbox` |

Optional 5th column on `edge/edge-routes`: `strip_prefix` (`1` default; `0` keeps
path for Next.js `basePath`). Milvus gRPC (`:19530`) is not an HTTP path on Traefik.

### Route changes through Janus

Janus lists routes, validates a change, and proposes it (`propose_route_change`
or `propose_route_rollback`). You approve it on the Approvals page; the gateway
backs up `edge-routes`, writes it and `routes.yml`, and Traefik reloads by file
watch without restarting `office-edge`. The newest 10 backups are kept.

Manual fallback (no approval gate):

```bash
# edit edge/edge-routes, then:
python3 ./scripts/render-edge-traefik.py
docker compose up -d office-edge
```

## Air-gap image shipping (bash hosts)

On a networked Linux/macOS machine, `./scripts/save-images.sh` pulls the pinned
`HERMES_IMAGE`, `busybox:1.36`, `nginx`, `traefik:v3.3`, and builds
`office-gw:local` for **linux/amd64** into `images/office-fleet-images.tar.gz`.
`./scripts/deploy-and-start.sh` loads that tarball when present. The Lab cannot
reach Docker Hub, so missing images must be shipped.
