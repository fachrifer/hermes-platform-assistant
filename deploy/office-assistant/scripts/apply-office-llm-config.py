#!/usr/bin/env python3
"""Stamp Hermes HOME with compose LiteLLM credentials before gateway run.

Hermes v2026.8.3 prefers HERMES_HOME/.env over Docker env_file, and
unexpanded ${OPENAI_API_KEY} still looks like a usable secret. Write the
literal key into config.yaml and .env from the container environment.
Does not print secret values.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REQUIRED = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")
PLACEHOLDER = re.compile(r"\$\{(OPENAI_API_KEY|OPENAI_BASE_URL|OPENAI_MODEL)\}")
HERMES_UID = 10000
HERMES_GID = 10000


def expand_template(text: str, env: dict[str, str]) -> str:
    missing = [key for key in REQUIRED if not (env.get(key) or "").strip()]
    if missing:
        raise SystemExit("apply-office-llm-config: missing " + ", ".join(missing))

    def repl(match: re.Match[str]) -> str:
        return env[match.group(1)].strip()

    stamped = PLACEHOLDER.sub(repl, text)
    leftover = PLACEHOLDER.findall(stamped)
    if leftover:
        raise SystemExit(
            "apply-office-llm-config: unexpanded " + ", ".join(sorted(set(leftover)))
        )
    return stamped


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


def _chown_hermes(path: Path) -> None:
    try:
        os.chown(path, HERMES_UID, HERMES_GID)
    except OSError:
        return


def main() -> None:
    env = {key: os.environ.get(key, "") for key in REQUIRED}
    template = Path(
        os.environ.get(
            "OFFICE_HERMES_CONFIG_TEMPLATE",
            "/opt/hermes-config.template.yaml",
        )
    )
    home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
    stamped = expand_template(template.read_text(), env)
    dest = home / "config.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(stamped)
    os.chmod(dest, 0o640)
    _chown_hermes(dest)
    upsert_dotenv(home / ".env", {key: env[key].strip() for key in REQUIRED})
    os.chmod(home / ".env", 0o600)
    _chown_hermes(home / ".env")
    print("apply-office-llm-config: stamped Hermes config and .env from compose LiteLLM env")


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        print(f"apply-office-llm-config: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
