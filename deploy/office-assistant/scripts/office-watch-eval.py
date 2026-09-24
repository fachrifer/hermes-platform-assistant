#!/usr/bin/env python3
"""Evaluate specialist watch alert predicates from FAST stdout (stdlib only)."""
from __future__ import annotations

import json
import re
import sys

_DAYS_RE = re.compile(r"days=(\d+)")


def evaluate_watch_alert(role: str, text: str) -> tuple[bool, str]:
    lines = text.splitlines()
    role = role.strip()

    for line in lines:
        if line.startswith("error:"):
            return True, "watch failed"

    if role == "lab-host":
        for line in lines:
            if line.startswith("heal_candidates:"):
                value = line.split(":", 1)[1].strip()
                if value.casefold() != "none":
                    return True, line.strip()
                break

    elif role in ("vector", "obs"):
        for line in lines:
            if line.startswith("endpoints:"):
                value = line.split(":", 1)[1].strip()
                if value.casefold() == "none":
                    break
                for part in value.split(","):
                    part = part.strip()
                    if "=" not in part:
                        continue
                    _name, status = part.split("=", 1)
                    if status.strip().casefold() != "ok":
                        return True, line.strip()
                break

    elif role == "cluster-gpu":
        for line in lines:
            stripped = line.strip()
            if stripped == "mig: mismatch":
                return True, stripped
        for line in lines:
            stripped = line.strip()
            if stripped == "k8s=unavailable":
                return True, stripped

    elif role == "llm-edge":
        for line in lines:
            stripped = line.strip()
            if stripped == "k8s=unavailable":
                return True, stripped

    elif role == "edge":
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("tls:"):
                continue
            if "expired" in stripped.casefold():
                return True, stripped
            match = _DAYS_RE.search(stripped)
            if match and int(match.group(1)) < 14:
                return True, stripped

    return False, "ok"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: office-watch-eval.py <role>", file=sys.stderr)
        return 2
    role = sys.argv[1]
    text = sys.stdin.read()
    alert, summary = evaluate_watch_alert(role, text)
    print(json.dumps({"alert": alert, "summary": summary}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
