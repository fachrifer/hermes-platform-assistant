#!/usr/bin/env bash
set -euo pipefail

# One-time env migration for the Phase 1b fleet (MCP-only agents, approvals page).
# Run on the Lab VM after the new tree is unpacked, before compose up.
# Idempotent. Backs up every env file first. Prints key names only, never values.

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

cd "$DEPLOY_DIR"
if ! grep -q 'hermes-ingress' docker-compose.yml 2>/dev/null; then
  echo "error: docker-compose.yml has no hermes-ingress; unpack the Phase 1b tree first" >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  echo "error: $DEPLOY_DIR/.env missing; this script migrates an existing Lab install" >&2
  exit 1
fi

TS="$(date +%Y%m%d-%H%M%S)"
python3 - "$DEPLOY_DIR" "$TS" <<'PY'
import os
import re
import secrets
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

deploy, ts = Path(sys.argv[1]), sys.argv[2]
HERMES = deploy / "hermes"
AGENTS = ("supervisor", "lab-host", "ingress", "llm", "cluster-gpu", "vector", "obs")
RENAMED_DIRS = {"edge": "ingress", "llm-edge": "llm"}
IMAGE = "nousresearch/hermes-agent:v2026.9.21"
GATEWAY_TOKENS = (
    "OFFICE_GATEWAY_TOKEN_SUPERVISOR",
    "OFFICE_GATEWAY_TOKEN_LAB_HOST",
    "OFFICE_GATEWAY_TOKEN_INGRESS",
    "OFFICE_GATEWAY_TOKEN_LLM",
    "OFFICE_GATEWAY_TOKEN_CLUSTER",
    "OFFICE_GATEWAY_TOKEN_VECTOR",
    "OFFICE_GATEWAY_TOKEN_OBS",
)
ROOT_DROP = re.compile(
    r"^(OFFICE_WRITE_.*|OFFICE_READ_LOGS_.*|OFFICE_VECTOR_ENV|OFFICE_AUTOHEAL.*|A2A_.*)$"
)
AGENT_DROP = re.compile(r"^(OPENAI_.*|A2A_.*)$")
OLD_PEER = re.compile(r"^HERMES_PEER_(?!PEER_)[A-Z0-9_]+_KEY$")
changes: dict[str, list[str]] = {}


class EnvFile:
    def __init__(self, path: Path):
        self.path = path
        self.lines = path.read_text().splitlines() if path.exists() else []
        self.dirty = False

    def get(self, key: str) -> str:
        value = ""
        for line in self.lines:
            if line.startswith(key + "="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
        return value

    def keys(self) -> list[str]:
        return [l.split("=", 1)[0] for l in self.lines if "=" in l and not l.lstrip().startswith("#")]

    def set(self, key: str, value: str) -> None:
        out, seen = [], False
        for line in self.lines:
            if line.startswith(key + "="):
                if not seen:
                    out.append(f"{key}={value}")
                seen = True
            else:
                out.append(line)
        if not seen:
            out.append(f"{key}={value}")
        if out != self.lines:
            self.lines = out
            self.dirty = True
            self.note(f"set {key}")

    def drop(self, pattern: re.Pattern) -> None:
        for key in [k for k in self.keys() if pattern.match(k)]:
            self.lines = [l for l in self.lines if not l.startswith(key + "=")]
            self.dirty = True
            self.note(f"removed {key}")

    def note(self, text: str) -> None:
        changes.setdefault(str(self.path.relative_to(deploy)), []).append(text)

    def save(self) -> None:
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("\n".join(self.lines) + "\n")
            os.chmod(self.path, 0o600)


# 2. Backups next to each file.
env_files = [deploy / ".env", deploy / "models.env", deploy / ".local-login"]
env_files += sorted(HERMES.glob("*/.env"))
for path in env_files:
    if path.is_file():
        backup = path.with_name(f"{path.name}.pre-phase1b-{ts}")
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
print(f"backed up env files with suffix .pre-phase1b-{ts}")

# 3. Renamed role directories.
for old, new in RENAMED_DIRS.items():
    old_dir, new_env = HERMES / old, HERMES / new / ".env"
    if not old_dir.is_dir():
        continue
    if (old_dir / ".env").is_file() and not new_env.exists():
        new_env.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_dir / ".env"), new_env)
        changes.setdefault(f"hermes/{new}/.env", []).append(f"moved from hermes/{old}/.env")
    old_dir.rename(HERMES / f"{old}.pre-phase1b-{ts}")
    print(f"retired hermes/{old}/ (kept as hermes/{old}.pre-phase1b-{ts}/)")

# 4. Root .env.
root = EnvFile(deploy / ".env")
if root.get("OFFICE_GATEWAY_TOKEN_EDGE") and not root.get("OFFICE_GATEWAY_TOKEN_INGRESS"):
    root.set("OFFICE_GATEWAY_TOKEN_INGRESS", root.get("OFFICE_GATEWAY_TOKEN_EDGE"))
root.drop(re.compile(r"^OFFICE_GATEWAY_TOKEN_EDGE$"))
first_run = not root.get("OFFICE_GATEWAY_TOKEN_APPROVER")
if first_run:
    root.set("OFFICE_GATEWAY_TOKEN_APPROVER", secrets.token_urlsafe(32))
    # The supervisor token was committed in a rendered core.yml; rotate it once.
    root.set("OFFICE_GATEWAY_TOKEN_SUPERVISOR", secrets.token_urlsafe(32))
for key in GATEWAY_TOKENS:
    if not root.get(key):
        root.set(key, secrets.token_urlsafe(32))
if root.get("HERMES_IMAGE") != IMAGE:
    root.set("HERMES_IMAGE", IMAGE)
root.drop(ROOT_DROP)
urls = root.get("OFFICE_SERVICE_URLS")
if urls:
    kept = [
        item.strip() for item in urls.split(",")
        if item.strip() and not re.search(r"hermes-|:9900|agent\.json", item)
    ]
    new_urls = ",".join(kept) or "gateway=http://office-gateway:8080/health"
    if new_urls != urls:
        root.set("OFFICE_SERVICE_URLS", new_urls)
root.save()

# 5. models.env.
models = EnvFile(deploy / "models.env")
base_url = models.get("OFFICE_LLM_BASE_URL") or models.get("OPENAI_BASE_URL")
if not base_url:
    for role in AGENTS:
        base_url = EnvFile(HERMES / role / ".env").get("OPENAI_BASE_URL")
        if base_url:
            break
if not base_url:
    raise SystemExit("migrate: no OFFICE_LLM_BASE_URL / OPENAI_BASE_URL found; set OFFICE_LLM_BASE_URL in models.env")
models.set("OFFICE_LLM_BASE_URL", base_url)
models.drop(re.compile(r"^OPENAI_.*$"))
models.save()

# 6-7. Agent env files.
missing_keys = []
for role in AGENTS:
    env = EnvFile(HERMES / role / ".env")
    if not env.get("OFFICE_LLM_API_KEY"):
        if env.get("OPENAI_API_KEY"):
            env.set("OFFICE_LLM_API_KEY", env.get("OPENAI_API_KEY"))
        else:
            missing_keys.append(role)
    env.drop(AGENT_DROP)
    if not env.get("API_SERVER_KEY"):
        env.set("API_SERVER_KEY", secrets.token_urlsafe(32))
    if role == "supervisor":
        env.drop(OLD_PEER)
    env.save()
if missing_keys:
    raise SystemExit("migrate: OFFICE_LLM_API_KEY / OPENAI_API_KEY empty for: " + ", ".join(missing_keys))

# NO_PROXY in compose must list the LiteLLM host (httpx ignores CIDR entries).
host = urlsplit(base_url).hostname or ""
compose = (deploy / "docker-compose.yml").read_text()
match = re.search(r"NO_PROXY:\s*&no-proxy\s*\"?([^\"\n]+)", compose)
no_proxy = [h.strip() for h in (match.group(1) if match else "").split(",")]
if host and host not in no_proxy:
    print(f"WARNING: LiteLLM host {host} is not in NO_PROXY in docker-compose.yml; LLM calls will fail")

for path, notes in sorted(changes.items()):
    print(f"{path}: " + ", ".join(notes))
if first_run:
    print("rotated OFFICE_GATEWAY_TOKEN_SUPERVISOR and created OFFICE_GATEWAY_TOKEN_APPROVER")
PY

# 8-9. Propagate tokens and create the approvals login.
"$SCRIPT_DIR/pair-env.sh"
"$SCRIPT_DIR/ensure-approver.sh"
echo "migration done; next: ./scripts/deploy-and-start.sh images/does-not-exist"
