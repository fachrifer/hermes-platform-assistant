# Office Fleet Phase 1a — Verification Spikes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove or disprove, on Hermes `v2026.9.21`, the assumptions the redesign spec (`docs/superpowers/specs/2026-09-24-office-fleet-redesign-design.md` §14) depends on, before any Phase 1 production code is written.

**Architecture:** A throwaway two-agent fleet (supervisor + lab-host) runs in Docker Desktop on the operator PC, configured the way the spec prescribes (no `terminal`, `bot_peers` delegation, `key_env` without `OPENAI_API_KEY`). A tiny capture proxy sits between Hermes and LiteLLM and logs request shape (never secrets) so provider and `extra_body` behaviour can be checked objectively. Each spike records a PASS/FAIL row in a results document; a FAIL on a blocking spike stops the work and goes back to the user.

**Tech Stack:** Docker Desktop (Compose v2), `nousresearch/hermes-agent:v2026.9.21`, Python 3.11 stdlib (capture proxy), PowerShell on the host, Hermes Desktop (installed on the PC).

## Global Constraints

- Hermes image: `nousresearch/hermes-agent:v2026.9.21`.
- Model: `qwen3.8-fast` via LiteLLM `http://10.216.221.100/llm/v1`; never `qwen3.8-reasoning*`.
- Do not touch the Lab VM (`10.216.4.80`) in this plan.
- Never commit secrets: `.env.spike`, `capture/` stay git-ignored; the capture log stores no header values or message contents.
- Host ports bind to `127.0.0.1` only.
- Do not re-enable `terminal` on any agent to make a spike pass.
- Blocking spikes (Tasks 3, 4, 5): on FAIL, stop, record, and report to the user — do not continue to later tasks.
- All commands run from `D:\Tim AI - Project\hermes-fleet-sync\deploy\office-assistant\spike` in PowerShell unless stated. At the start of every task (fresh shells included) run:
  ```powershell
  cd "D:\Tim AI - Project\hermes-fleet-sync\deploy\office-assistant\spike"
  $dc = "docker compose -f docker-compose.spike.yml --env-file .env.spike"
  ```
- Out of scope: the Phase 2 spikes from spec §14 (Grafana service account, `POST /v1/runs` dedicated sessions); they belong to the Phase 2 plan.

---

## File Structure

| File | Responsibility |
|---|---|
| `deploy/office-assistant/spike/docker-compose.spike.yml` | Mini-fleet: `llm-capture`, `spike-supervisor`, `spike-lab-host` |
| `deploy/office-assistant/spike/capture_proxy.py` | Forward `/v1/*` to LiteLLM, log request shape to `capture/requests.jsonl` |
| `deploy/office-assistant/spike/supervisor.config.yaml` | Supervisor config per spec §8 (no terminal, `bot_peers`) |
| `deploy/office-assistant/spike/lab-host.config.yaml` | Specialist config per spec §8 (`bot_mode_protocol: false`) |
| `deploy/office-assistant/spike/lab-host.thinking.config.yaml` | Same as above plus `extra_body.chat_template_kwargs.enable_thinking: true` |
| `deploy/office-assistant/spike/bot-mode-marker-cont-init.sh` | Writes the Bot-Mode-managed marker into `/opt/data/profile.yaml` |
| `deploy/office-assistant/spike/.env.spike.example` | Variable names for the harness (no values) |
| `docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md` | PASS/FAIL table + evidence per spike |
| `.gitignore` | Ignore `.env.spike` and `capture/` |

---

### Task 1: Spike harness

**Files:**
- Create: all `deploy/office-assistant/spike/*` files listed above
- Create: `docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md`
- Modify: `.gitignore` (append)

**Interfaces:**
- Produces: running services `spike-supervisor` (Dashboard `127.0.0.1:19119`), `spike-lab-host` (Dashboard `127.0.0.1:19121`), `llm-capture` (`http://llm-capture:8000/v1` inside the network); capture log `capture/requests.jsonl`; results doc with one row per spike (S1–S7).

- [ ] **Step 1: Write the capture proxy**

`deploy/office-assistant/spike/capture_proxy.py`:

```python
import json
import os
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ["UPSTREAM_BASE"].rstrip("/")
LOG = os.environ.get("CAPTURE_LOG", "/capture/requests.jsonl")
PORT = int(os.environ.get("CAPTURE_PORT", "8000"))
_DROP = {"host", "content-length", "connection", "accept-encoding"}


def summarize(method: str, path: str, has_auth: bool, body: bytes) -> dict:
    entry = {"ts": time.time(), "method": method, "path": path, "has_auth": has_auth}
    try:
        data = json.loads(body) if body else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        entry["keys"] = sorted(data)
        entry["model"] = data.get("model")
        entry["stream"] = data.get("stream")
        entry["messages"] = len(data.get("messages") or [])
        entry["tools"] = len(data.get("tools") or [])
        entry["chat_template_kwargs"] = data.get("chat_template_kwargs")
    return entry


class Handler(BaseHTTPRequestHandler):
    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(summarize(
                self.command, self.path, bool(self.headers.get("Authorization")), body)) + "\n")
        suffix = self.path[3:] if self.path.startswith("/v1") else self.path
        req = urllib.request.Request(UPSTREAM + suffix, data=body or None, method=self.command)
        for key, value in self.headers.items():
            if key.lower() not in _DROP:
                req.add_header(key, value)
        try:
            resp = urllib.request.urlopen(req, timeout=600)
            status = resp.status
        except urllib.error.HTTPError as exc:
            resp, status = exc, exc.code
        except urllib.error.URLError as exc:
            self.send_error(502, f"upstream unreachable: {exc.reason}")
            return
        self.send_response(status)
        self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
        self.end_headers()
        while True:
            chunk = resp.read(4096)
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()

    do_GET = _forward
    do_POST = _forward

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
```

- [ ] **Step 2: Write the Bot Mode marker init script**

`deploy/office-assistant/spike/bot-mode-marker-cont-init.sh`:

```sh
#!/command/with-contenv sh
set -eu
MARKER=/opt/data/profile.yaml
if [ ! -f "$MARKER" ]; then
  printf 'ui_meta:\n  hermes-bots: {}\n' > "$MARKER"
elif ! grep -q 'hermes-bots' "$MARKER"; then
  echo "bot-mode-marker: $MARKER exists without hermes-bots; refusing to edit" >&2
  exit 1
fi
chown "$(stat -c %u:%g /opt/data)" "$MARKER"
```

- [ ] **Step 3: Write the supervisor config**

`deploy/office-assistant/spike/supervisor.config.yaml`:

```yaml
agent:
  name: supervisor
  build_wait_timeout: 30
  api_max_retries: 1
  max_turns: 6
  disabled_toolsets: [terminal, file, web, browser, code_execution, delegation, memory, session_search, todo, kanban, clarify]

model:
  default: qwen3.8-fast
  provider: office-litellm

providers:
  office-litellm:
    api: http://llm-capture:8000/v1
    key_env: OFFICE_LLM_API_KEY
    transport: chat_completions
    request_timeout_seconds: 60

platform_toolsets:
  cli: [skills]
  tui: [skills]
  api_server: [skills]

tool_loop_guardrails:
  warnings_enabled: true
  hard_stop_enabled: true
  warn_after: {exact_failure: 1, same_tool_failure: 1, idempotent_no_progress: 1}
  hard_stop_after: {exact_failure: 2, same_tool_failure: 2, idempotent_no_progress: 2}

browser:
  backend: "off"
security:
  allow_lazy_installs: false
mcp_servers: {}
model_catalog:
  enabled: false
network:
  force_ipv4: true
updates:
  check: false

dashboard:
  enabled: true
  host: 0.0.0.0
  port: 9119
  public_url: ${HERMES_DASHBOARD_PUBLIC_URL}
  basic_auth:
    username: ${SPIKE_DASHBOARD_USER}
    password: ${SPIKE_DASHBOARD_PASSWORD}

gateway:
  platforms:
    api_server:
      enabled: true

bot_peers:
  lab-host:
    url: http://spike-lab-host:8642
```

- [ ] **Step 4: Write the two lab-host configs**

`deploy/office-assistant/spike/lab-host.config.yaml`:

```yaml
agent:
  name: lab-host
  build_wait_timeout: 30
  api_max_retries: 1
  max_turns: 6
  run_budget_seconds: 180
  bot_mode_protocol: false
  disabled_toolsets: [terminal, file, web, browser, code_execution, delegation, memory, session_search, todo, kanban, clarify]

model:
  default: qwen3.8-fast
  provider: office-litellm

providers:
  office-litellm:
    api: http://llm-capture:8000/v1
    key_env: OFFICE_LLM_API_KEY
    transport: chat_completions
    request_timeout_seconds: 60

platform_toolsets:
  cli: [skills]
  tui: [skills]
  api_server: [skills]

tool_loop_guardrails:
  warnings_enabled: true
  hard_stop_enabled: true
  warn_after: {exact_failure: 1, same_tool_failure: 1, idempotent_no_progress: 1}
  hard_stop_after: {exact_failure: 2, same_tool_failure: 2, idempotent_no_progress: 2}

browser:
  backend: "off"
security:
  allow_lazy_installs: false
mcp_servers: {}
model_catalog:
  enabled: false
network:
  force_ipv4: true
updates:
  check: false

dashboard:
  enabled: true
  host: 0.0.0.0
  port: 9119
  public_url: ${HERMES_DASHBOARD_PUBLIC_URL}
  basic_auth:
    username: ${SPIKE_DASHBOARD_USER}
    password: ${SPIKE_DASHBOARD_PASSWORD}

gateway:
  platforms:
    api_server:
      enabled: true
```

`deploy/office-assistant/spike/lab-host.thinking.config.yaml`: identical to `lab-host.config.yaml` except the `providers` block:

```yaml
providers:
  office-litellm:
    api: http://llm-capture:8000/v1
    key_env: OFFICE_LLM_API_KEY
    transport: chat_completions
    request_timeout_seconds: 60
    extra_body:
      chat_template_kwargs:
        enable_thinking: true
```

(Write the full file: copy `lab-host.config.yaml` and replace its `providers` block with the one above.)

- [ ] **Step 5: Write the compose file and env example**

`deploy/office-assistant/spike/docker-compose.spike.yml`:

```yaml
name: fleet-spike

x-airgap-hosts: &airgap-hosts
  - "models.dev:127.0.0.1"
  - "api.github.com:127.0.0.1"
  - "github.com:127.0.0.1"
  - "raw.githubusercontent.com:127.0.0.1"
  - "registry.npmjs.org:127.0.0.1"
  - "portal.nousresearch.com:127.0.0.1"
  - "openrouter.ai:127.0.0.1"
  - "pypi.org:127.0.0.1"
  - "files.pythonhosted.org:127.0.0.1"

x-hermes-env: &hermes-env
  HERMES_HOME: /opt/data
  TZ: Asia/Jakarta
  OFFICE_LLM_API_KEY: ${OFFICE_LLM_API_KEY}
  API_SERVER_ENABLED: "true"
  API_SERVER_HOST: "0.0.0.0"
  API_SERVER_PORT: "8642"
  HERMES_DASHBOARD: "1"
  HERMES_DASHBOARD_HOST: "0.0.0.0"
  HERMES_DASHBOARD_PORT: "9119"
  HERMES_DASHBOARD_TUI: "1"
  HERMES_DASHBOARD_TRUST_LAN: "1"
  HERMES_DASHBOARD_BASIC_AUTH_USERNAME: ${SPIKE_DASHBOARD_USER}
  HERMES_DASHBOARD_BASIC_AUTH_PASSWORD: ${SPIKE_DASHBOARD_PASSWORD}
  HERMES_DASHBOARD_BASIC_AUTH_SECRET: ${SPIKE_DASHBOARD_SECRET}
  SPIKE_DASHBOARD_USER: ${SPIKE_DASHBOARD_USER}
  SPIKE_DASHBOARD_PASSWORD: ${SPIKE_DASHBOARD_PASSWORD}

networks:
  spike:
    driver: bridge

volumes:
  spike_supervisor_data:
  spike_lab_host_data:

services:
  llm-capture:
    image: python:3.11-slim
    networks: [spike]
    environment:
      UPSTREAM_BASE: ${LITELLM_BASE_URL}
      CAPTURE_LOG: /capture/requests.jsonl
    volumes:
      - ./capture_proxy.py:/app/capture_proxy.py:ro
      - ./capture:/capture
    command: ["python", "/app/capture_proxy.py"]

  spike-lab-host:
    image: nousresearch/hermes-agent:v2026.9.21
    networks: [spike]
    depends_on: [llm-capture]
    extra_hosts: *airgap-hosts
    ports:
      - "127.0.0.1:19121:9119"
    environment:
      <<: *hermes-env
      API_SERVER_KEY: ${SPIKE_LAB_HOST_API_KEY}
      HERMES_DASHBOARD_PUBLIC_URL: http://127.0.0.1:19121
    volumes:
      - spike_lab_host_data:/opt/data
      - ./${LAB_HOST_CONFIG:-lab-host.config.yaml}:/opt/data/config.yaml:ro
      - ../scripts/hermes-cli-symlink-cont-init.sh:/etc/cont-init.d/08-hermes-cli:ro
      - ./bot-mode-marker-cont-init.sh:/etc/cont-init.d/30-bot-mode-marker:ro
    command: ["gateway", "run"]

  spike-supervisor:
    image: nousresearch/hermes-agent:v2026.9.21
    networks: [spike]
    depends_on: [llm-capture, spike-lab-host]
    extra_hosts: *airgap-hosts
    ports:
      - "127.0.0.1:19119:9119"
    environment:
      <<: *hermes-env
      API_SERVER_KEY: ${SPIKE_SUPERVISOR_API_KEY}
      HERMES_PEER_LAB_HOST_KEY: ${SPIKE_LAB_HOST_API_KEY}
      HERMES_DASHBOARD_PUBLIC_URL: http://127.0.0.1:19119
    volumes:
      - spike_supervisor_data:/opt/data
      - ./supervisor.config.yaml:/opt/data/config.yaml:ro
      - ../scripts/hermes-cli-symlink-cont-init.sh:/etc/cont-init.d/08-hermes-cli:ro
      - ./bot-mode-marker-cont-init.sh:/etc/cont-init.d/30-bot-mode-marker:ro
    command: ["gateway", "run"]
```

`deploy/office-assistant/spike/.env.spike.example`:

```
OFFICE_LLM_API_KEY=
LITELLM_BASE_URL=http://10.216.221.100/llm/v1
SPIKE_SUPERVISOR_API_KEY=
SPIKE_LAB_HOST_API_KEY=
SPIKE_DASHBOARD_USER=spike
SPIKE_DASHBOARD_PASSWORD=
SPIKE_DASHBOARD_SECRET=
LAB_HOST_CONFIG=lab-host.config.yaml
```

Append to repo-root `.gitignore`:

```
deploy/office-assistant/spike/.env.spike
deploy/office-assistant/spike/capture/
```

- [ ] **Step 6: Write the results document skeleton**

`docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md`:

```markdown
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
```

- [ ] **Step 7: Create `.env.spike` and start the harness**

The operator supplies a LiteLLM key (do not use the snapshot `.env.bak` key; that key is due for rotation). Generate the other secrets:

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync\deploy\office-assistant\spike"
function New-Secret { -join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) }) }
Copy-Item .env.spike.example .env.spike
(Get-Content .env.spike) `
  -replace '^SPIKE_SUPERVISOR_API_KEY=$', "SPIKE_SUPERVISOR_API_KEY=$(New-Secret)" `
  -replace '^SPIKE_LAB_HOST_API_KEY=$', "SPIKE_LAB_HOST_API_KEY=$(New-Secret)" `
  -replace '^SPIKE_DASHBOARD_PASSWORD=$', "SPIKE_DASHBOARD_PASSWORD=$(New-Secret)" `
  -replace '^SPIKE_DASHBOARD_SECRET=$', "SPIKE_DASHBOARD_SECRET=$(New-Secret)" |
  Set-Content -Encoding ascii .env.spike
New-Item -ItemType Directory -Force capture | Out-Null
notepad .env.spike   # operator pastes OFFICE_LLM_API_KEY, saves, closes
```

Start (Docker Desktop must be running):

```powershell
$dc = "docker compose -f docker-compose.spike.yml --env-file .env.spike"
docker pull nousresearch/hermes-agent:v2026.9.21
Invoke-Expression "$dc config --quiet"
Invoke-Expression "$dc up -d"
Start-Sleep -Seconds 45
Invoke-Expression "$dc ps"
```

Expected: `config --quiet` prints nothing; `ps` shows three services `running`.

- [ ] **Step 8: Verify peer reachability and the marker**

```powershell
Invoke-Expression "$dc exec -T spike-supervisor sh -c 'curl -s -o /dev/null -w %{http_code} -H ""Authorization: Bearer `$HERMES_PEER_LAB_HOST_KEY"" http://spike-lab-host:8642/v1/capabilities'"
Invoke-Expression "$dc exec -T spike-supervisor cat /opt/data/profile.yaml"
Invoke-Expression "$dc exec -T -u hermes spike-supervisor hermes tools --summary"
```

Expected: `200`; the marker prints `ui_meta:` / `hermes-bots: {}`; the tools summary does not list `terminal` (record the full summary in the results doc under S1 evidence).

- [ ] **Step 9: Commit**

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync"
git add .gitignore deploy/office-assistant/spike docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md
git update-index --chmod=+x deploy/office-assistant/spike/bot-mode-marker-cont-init.sh
git status --short   # must NOT list .env.spike or capture/
git commit -m "chore(spike): add v2026.9.21 two-agent spike harness and results skeleton"
```

---

### Task 2: S4 — provider via `key_env` without `OPENAI_API_KEY`

**Files:**
- Modify: `docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md` (row S4)

**Interfaces:**
- Consumes: harness from Task 1.
- Produces: S4 result; first-build timing baseline for spec §13.4.

- [ ] **Step 1: Confirm no `OPENAI_API_KEY` in the container**

```powershell
Invoke-Expression "$dc exec -T spike-lab-host sh -c 'env | grep -c ^OPENAI_API_KEY= || true'"
```

Expected: `0`.

- [ ] **Step 2: One-shot query through the provider**

```powershell
Invoke-Expression "$dc exec -T -u hermes spike-lab-host hermes chat -q 'Balas persis dengan teks: OK' --oneshot --format stream-json" | Tee-Object -Variable s4
$s4 | Select-String '"type": *"result"'
Get-Content capture\requests.jsonl -Tail 3
```

Expected: the `result` line has `"exit_code": 0` and text containing `OK`; the last capture entries show `"path": "/v1/chat/completions"`, `"has_auth": true`, `"model": "qwen3.8-fast"`. (OpenRouter is loopback-mapped, so a success here proves the custom provider was used.)

- [ ] **Step 3: Record startup evidence**

```powershell
$s4 | Select-String '"duration_ms"'
Invoke-Expression "$dc logs --no-color spike-lab-host" | Select-String -Pattern 'models.dev|github|openrouter|nousresearch.com|timed out|timeout' | Select-Object -First 20
```

Record: first-query `duration_ms`, and every matching log line (expected: none that show waiting on an outbound connection).

- [ ] **Step 4: Record S4 and commit**

Fill row S4: `PASS` if Steps 1–2 met expectations, else `FAIL` with the error text. If FAIL, add under "Decisions triggered": `S4 failed → Phase 1b ports patch-hermes-airgap-provider.py to v2026.9.21 (spec §13.5)`.

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync"
git add docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md
git commit -m "docs(spike): record S4 provider key_env result"
```

---

### Task 3: S1 — `message_agent` with `terminal` disabled (blocking)

**Files:**
- Modify: results doc (row S1)

**Interfaces:**
- Consumes: harness; supervisor tools summary from Task 1 Step 8.
- Produces: S1 result; the supervisor "Bot Chat" session used by Tasks 4–6.

- [ ] **Step 1: Create the supervisor's canonical Bot Chat and delegate once**

```powershell
Invoke-Expression "$dc exec -T -u hermes spike-supervisor hermes chat -c 'Bot Chat' --create-if-missing -q 'Gunakan tool message_agent untuk mengirim pesan ke target lab-host dengan isi: Balas persis dengan teks SPIKE-PONG-1 dan tidak ada yang lain. Setelah mengirim, akhiri giliranmu.' --oneshot --format stream-json" | Tee-Object -Variable s1
$s1 | Select-String '"tool_use"|"tool_result"'
```

Expected: a `tool_use` with `"name": "message_agent"` and a `tool_result` for it with `"is_error": false` whose output contains `queued`. No `tool_use` named `terminal`.

- [ ] **Step 2: Confirm the specialist actually ran the turn**

```powershell
Start-Sleep -Seconds 60
Invoke-Expression "$dc exec -T -u hermes spike-lab-host hermes sessions list"
Get-Content capture\requests.jsonl -Tail 5
```

Expected: lab-host lists a session titled `Bot Chat`; the capture log has a new `/v1/chat/completions` entry after the supervisor's.

- [ ] **Step 3: Record S1 and commit**

Row S1: `PASS` only if Steps 1–2 both met expectations. Evidence: the `tool_result` output (trim to 300 chars) and the lab-host session line.

If FAIL: add under "Decisions triggered": `S1 failed → STOP. Re-open the communication decision with the user (spec §14).` Commit, then report to the user and do not start Task 4.

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync"
git add docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md
git commit -m "docs(spike): record S1 message_agent without terminal"
```

---

### Task 4: S2 — web Dashboard "Bot Chat" end to end (blocking)

**Files:**
- Modify: results doc (row S2)

**Interfaces:**
- Consumes: supervisor "Bot Chat" from Task 3.
- Produces: S2 result.

- [ ] **Step 1: Open the supervisor Dashboard and resume Bot Chat**

Operator: close Hermes Desktop completely. Open `http://127.0.0.1:19119` in a browser, log in with `SPIKE_DASHBOARD_USER` / `SPIKE_DASHBOARD_PASSWORD` from `.env.spike`, go to **Chat**, and in the right-rail session list click the session titled **Bot Chat**.

- [ ] **Step 2: Delegate and wait for the notification**

Send:

```
Gunakan tool message_agent untuk mengirim pesan ke target lab-host dengan isi: Balas persis dengan teks SPIKE-PONG-2 dan tidak ada yang lain. Setelah mengirim, akhiri giliranmu.
```

Expected, within 120 s and without typing anything else: the supervisor's turn ends after a `message_agent` call reporting queued, then a completion notification arrives in the same chat containing `SPIKE-PONG-2`, and the supervisor relays it.

- [ ] **Step 3: Record S2 and commit**

Row S2: `PASS` if the notification with `SPIKE-PONG-2` arrived without manual polling. Evidence: a screenshot path or the copied notification text and the elapsed time.

If FAIL: "Decisions triggered": `S2 failed → STOP. Remote users cannot delegate; re-open the communication decision with the user.` Commit, report, stop.

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync"
git add docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md
git commit -m "docs(spike): record S2 web dashboard delegation"
```

---

### Task 5: S3 — Desktop connected to all gateways (blocking)

**Files:**
- Modify: results doc (row S3)

**Interfaces:**
- Consumes: harness, supervisor "Bot Chat".
- Produces: S3 result; decision whether peers must be renamed `peer-<role>`.

- [ ] **Step 1: Connect Hermes Desktop to both agents**

Operator: open Hermes Desktop → **Settings → Connections** → add `http://127.0.0.1:19119` (name `spike-supervisor`) and `http://127.0.0.1:19121` (name `spike-lab-host`) with the spike dashboard credentials. Open the **Bots** tab and wait until both appear in the roster (this is when the relay roster is pushed to the supervisor gateway).

- [ ] **Step 2: Check which route `message_agent` takes while Desktop is connected**

```powershell
Invoke-Expression "$dc exec -T -u hermes spike-supervisor hermes chat -c 'Bot Chat' -q 'Gunakan tool message_agent untuk mengirim pesan ke target lab-host dengan isi: Balas persis dengan teks SPIKE-PONG-3 dan tidak ada yang lain. Setelah mengirim, akhiri giliranmu.' --oneshot --format stream-json" | Tee-Object -Variable s3
$s3 | Select-String '"tool_result"'
```

Expected (peer route): the `message_agent` result contains `process_id` (background `hermes peer dm` delivery) and its `to` value is `lab-host`, not an `@...@<connection>` form. A result with `delivery_id` and a connection-qualified `to` means the Desktop relay was chosen.

- [ ] **Step 3: Close Desktop mid-delivery**

In Desktop, open the supervisor's Bot Chat and send the SPIKE-PONG message with `SPIKE-PONG-4`. Within 5 seconds of the `message_agent` call appearing, quit Hermes Desktop entirely (tray → Quit). Wait 90 s. Open `http://127.0.0.1:19119` in the browser, resume **Bot Chat**.

Expected: the completion notification containing `SPIKE-PONG-4` is present.

- [ ] **Step 4: Record S3 and commit**

Row S3: `PASS` if Step 2 shows the peer route and Step 3 delivered.

If Step 2 shows the relay route: add under "Decisions triggered": `S3 route collision → rename bot_peers to peer-<role> in Phase 1b and re-run Task 5 with target peer-lab-host.` Apply that rename now in `supervisor.config.yaml` (`bot_peers.peer-lab-host.url: http://spike-lab-host:8642`, env `HERMES_PEER_PEER_LAB_HOST_KEY` in the compose file), `$dc up -d spike-supervisor`, and repeat Steps 2–3 with target `peer-lab-host`. If it still fails, or if Step 3 fails: `S3 failed → STOP, re-open the communication decision with the user.`

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync"
git add docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md deploy/office-assistant/spike
git commit -m "docs(spike): record S3 desktop relay vs bot_peers"
```

---

### Task 6: S5 and S7 — restart persistence and specialist without protocol

**Files:**
- Modify: results doc (rows S5, S7)

**Interfaces:**
- Consumes: harness; sessions from Tasks 3–5.
- Produces: S5, S7 results.

- [ ] **Step 1: Restart and re-delegate (S5)**

```powershell
Invoke-Expression "$dc restart spike-supervisor spike-lab-host"
Start-Sleep -Seconds 45
Invoke-Expression "$dc exec -T -u hermes spike-supervisor hermes sessions list"
Invoke-Expression "$dc exec -T -u hermes spike-supervisor hermes chat -c 'Bot Chat' -q 'Gunakan tool message_agent untuk mengirim pesan ke target lab-host dengan isi: Balas persis dengan teks SPIKE-PONG-5 dan tidak ada yang lain. Setelah mengirim, akhiri giliranmu.' --oneshot --format stream-json" | Select-String '"tool_result"'
```

Expected: `Bot Chat` still listed; `message_agent` result `is_error: false`, contains `queued`.

- [ ] **Step 2: Specialist has no `message_agent` (S7)**

```powershell
Invoke-Expression "$dc exec -T -u hermes spike-lab-host hermes chat -c 'Bot Chat' -q 'Kirim pesan ke supervisor memakai tool message_agent yang berisi: TEST.' --oneshot --format stream-json" | Select-String '"tool_use"|"result"'
Invoke-Expression "$dc exec -T -u hermes spike-lab-host hermes tools --summary"
```

Expected: no `tool_use` named `message_agent`; the tools summary lists only skills-related tools. Task 3 Step 2 already showed the specialist receives peer messages with the protocol off.

- [ ] **Step 3: Record S5, S7 and commit**

S5 `PASS` per Step 1. S7 `PASS` per Step 2 plus Task 3 Step 2. If S7 fails: "Decisions triggered": `S7 failed → specialists keep bot_mode_protocol on, with empty bot_peers and a skill rule never to message (spec §14 fallback).`

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync"
git add docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md
git commit -m "docs(spike): record S5 restart persistence and S7 specialist protocol off"
```

---

### Task 7: S6 — `extra_body` thinking passthrough

**Files:**
- Modify: `deploy/office-assistant/spike/.env.spike` (local only, not committed)
- Modify: results doc (row S6)

**Interfaces:**
- Consumes: `lab-host.thinking.config.yaml`.
- Produces: S6 result (decides whether spec §13.3 can run).

- [ ] **Step 1: Switch lab-host to the thinking config**

```powershell
(Get-Content .env.spike) -replace '^LAB_HOST_CONFIG=.*$', 'LAB_HOST_CONFIG=lab-host.thinking.config.yaml' | Set-Content -Encoding ascii .env.spike
Invoke-Expression "$dc up -d spike-lab-host"
Start-Sleep -Seconds 45
```

- [ ] **Step 2: Query and inspect the captured request**

```powershell
Invoke-Expression "$dc exec -T -u hermes spike-lab-host hermes chat -q 'Berapa 17 x 23? Jawab angkanya saja.' --oneshot --format stream-json" | Select-String '"result"'
Get-Content capture\requests.jsonl -Tail 1
```

Expected: the last capture entry has `"chat_template_kwargs": {"enable_thinking": true}`; the result text contains `391`.

- [ ] **Step 3: Restore config, record S6, commit**

```powershell
(Get-Content .env.spike) -replace '^LAB_HOST_CONFIG=.*$', 'LAB_HOST_CONFIG=lab-host.config.yaml' | Set-Content -Encoding ascii .env.spike
Invoke-Expression "$dc up -d spike-lab-host"
```

S6 `PASS` if the captured entry shows the kwargs. If FAIL: "Decisions triggered": `S6 failed → skip spec §13.3; thinking stays off.`

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync"
git add docs/superpowers/specs/2026-09-24-office-fleet-redesign-spike-results.md
git commit -m "docs(spike): record S6 extra_body thinking passthrough"
```

---

### Task 8: Close out — spec update, teardown, hand-off to Phase 1b

**Files:**
- Modify: `docs/superpowers/specs/2026-09-24-office-fleet-redesign-design.md` (§10, §14)
- Modify: results doc ("Decisions triggered")

**Interfaces:**
- Consumes: all spike rows.
- Produces: an updated spec that Phase 1b's plan is written against.

- [ ] **Step 1: Update spec §10 with the confirmed startup facts**

In §10, replace the sentence beginning `Findings in \`v2026.9.21\` source:` with:

```markdown
Findings in `v2026.9.21` source: `agent.offline` and the `HERMES_OFFLINE` env var (both set on today's fleet) are not read anywhere and have no effect. Network touch points at startup/background are the remote model catalog, the models.dev registry, IPv6-first resolution, and the update check (already skipped on Docker installs). The existing Compose `extra_hosts` map that points known internet hosts at `127.0.0.1` stays; the proxy safety net below covers hosts not on that list.
```

- [ ] **Step 2: Apply triggered decisions to the spec**

For each line under "Decisions triggered" in the results doc, edit the spec section it names (§9 peer names for S3, §8.1/§8.2 specialist protocol for S7, §8.4/§13.3 for S6, §13.5 for S4). If no decisions were triggered, add to §14 the line `All Phase 1 spikes passed on <date> (see 2026-09-24-office-fleet-redesign-spike-results.md).`, writing the calendar date the spikes ran in place of `<date>` (format `YYYY-MM-DD`).

- [ ] **Step 3: Tear down the harness**

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync\deploy\office-assistant\spike"
Invoke-Expression "$dc down -v"
Remove-Item .env.spike
Remove-Item -Recurse -Force capture
```

Operator: remove the two spike connections from Hermes Desktop → Settings → Connections.

- [ ] **Step 4: Commit**

```powershell
cd "D:\Tim AI - Project\hermes-fleet-sync"
git add docs/superpowers/specs
git commit -m "docs(spec): apply Phase 1a spike outcomes"
```

- [ ] **Step 5: Hand-off**

Report the results table to the user. If every blocking spike passed, the next step is writing the Phase 1b plan (gateway MCP + role catalog, approvals, agent configs and skills, removals, startup hardening, VM rollout) against the updated spec.
