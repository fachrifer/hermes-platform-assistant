#!/usr/bin/env python3
"""Stamp Hermes HOME with compose LiteLLM credentials before gateway run.

Hermes v2026.8.31 prefers HERMES_HOME/.env over Docker env_file, and
unexpanded ${OPENAI_API_KEY} still looks like a usable secret. Write the
literal key into config.yaml and .env from the container environment.
Does not print secret values.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

REQUIRED = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
PLACEHOLDER = re.compile(r"\$\{(OPENAI_API_KEY|OPENAI_BASE_URL|OPENAI_MODEL)\}")
HERMES_UID = 10000
HERMES_GID = 10000
# Hermes prefers HERMES_HOME/.env over Docker env_file. Stamp Bot Mode
# credentials so specialist dashboards and hermes peer keep working after
# a volume already has a LiteLLM-only .env.
OPTIONAL_DOTENV_KEYS = (
    "API_SERVER_KEY",
    "HERMES_DASHBOARD_USERNAME",
    "HERMES_DASHBOARD_PASSWORD",
    "HERMES_DASHBOARD_BASIC_AUTH_SECRET",
    "HERMES_DASHBOARD_PUBLIC_URL",
    "HERMES_DASHBOARD_SESSION_TOKEN",
)


def llm_env(env: dict[str, str]) -> dict[str, str]:
    missing = [key for key in REQUIRED if not (env.get(key) or "").strip()]
    if missing:
        raise SystemExit("apply-office-llm-config: missing " + ", ".join(missing))
    out = {key: env[key].strip() for key in REQUIRED}
    key, url = out["OPENAI_API_KEY"], out["OPENAI_BASE_URL"]
    if key.lower().startswith(("http://", "https://")):
        raise SystemExit(
            "apply-office-llm-config: OPENAI_API_KEY looks like a URL; "
            "LiteLLM virtual keys must start with sk-"
        )
    if not key.startswith("sk-"):
        raise SystemExit(
            "apply-office-llm-config: OPENAI_API_KEY must be a LiteLLM virtual key starting with sk-"
        )
    if url.lower().startswith("sk-") or not url.lower().startswith(("http://", "https://")):
        raise SystemExit(
            "apply-office-llm-config: OPENAI_BASE_URL must be an http(s) endpoint, not an API key"
        )
    return out


def expand_template(text: str, env: dict[str, str]) -> str:
    env = llm_env(env)

    def repl(match: re.Match[str]) -> str:
        return env[match.group(1)]

    stamped = PLACEHOLDER.sub(repl, text)
    leftover = PLACEHOLDER.findall(stamped)
    if leftover:
        raise SystemExit(
            "apply-office-llm-config: unexpanded " + ", ".join(sorted(set(leftover)))
        )
    return stamped


def extra_dotenv(env: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in OPTIONAL_DOTENV_KEYS:
        val = (env.get(key) or "").strip()
        if val:
            out[key] = val
    for key, raw in env.items():
        if key.startswith("HERMES_PEER_") and key.endswith("_KEY"):
            val = (raw or "").strip()
            if val:
                out[key] = val
    return out


def upsert_dotenv(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, _current = line.split("=", 1)
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in values.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n")


_URL_SECRET_FIELDS = (
    "access_token",
    "api_key",
    "runtime_api_key",
    "token",
    "secret",
    "key",
)


def _is_http_url_secret(value: object) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def scrub_url_secrets_from_auth_json(path: Path) -> int:
    """Drop credential-pool rows that stored the LiteLLM URL as a Bearer token.

    Hermes persists that pool on the data volume, so it survives image pulls.
    """
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    pool = data.get("credential_pool")
    if not isinstance(pool, dict):
        return 0
    removed = 0
    for key in list(pool):
        entries = pool.get(key)
        if not isinstance(entries, list):
            continue
        kept: list[object] = []
        for entry in entries:
            if isinstance(entry, dict) and any(
                _is_http_url_secret(entry.get(field)) for field in _URL_SECRET_FIELDS
            ):
                removed += 1
                continue
            kept.append(entry)
        if kept:
            pool[key] = kept
        else:
            del pool[key]
    if not removed:
        return 0
    path.write_text(json.dumps(data, indent=2) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _chown_hermes(path)
    return removed


def _chown_hermes(path: Path) -> None:
    try:
        os.chown(path, HERMES_UID, HERMES_GID)
    except OSError:
        return


def main() -> None:
    raw = {key: os.environ.get(key, "") for key in REQUIRED}
    env = llm_env(raw)
    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
    stamp_config = os.environ.get("OFFICE_HERMES_STAMP_CONFIG", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    if stamp_config:
        template = Path(
            os.environ.get(
                "OFFICE_HERMES_CONFIG_TEMPLATE",
                "/opt/hermes-config.template.yaml",
            )
        )
        dest = home / "config.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(expand_template(template.read_text(), env))
        os.chmod(dest, 0o640)
        _chown_hermes(dest)
    dotenv_values = dict(env)
    dotenv_values.update(extra_dotenv(dict(os.environ)))
    upsert_dotenv(home / ".env", dotenv_values)
    os.chmod(home / ".env", 0o600)
    _chown_hermes(home / ".env")
    scrubbed = scrub_url_secrets_from_auth_json(home / "auth.json")
    what = "Hermes .env" if not stamp_config else "Hermes config and .env"
    key = env["OPENAI_API_KEY"]
    shape = "sk-" if key.startswith("sk-") else "not-sk"
    extra = f", scrubbed {scrubbed} url-token pool rows" if scrubbed else ""
    print(
        f"apply-office-llm-config: stamped {what} from compose LiteLLM env "
        f"({shape} len={len(key)}{extra})"
    )


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        print(f"apply-office-llm-config: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
