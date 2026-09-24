#!/usr/bin/env python3
"""Upsert a single office-watch Kanban card from GET /v1/watch/summary JSON."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

CARD_ID = "office-watch"
CARD_TITLE = "office-watch"


def card_state_from_summary(summary: dict[str, Any]) -> tuple[bool, str]:
    roles = summary.get("roles") or []
    alerting: list[str] = []
    stale_roles: list[str] = []

    for row in roles:
        role = str(row.get("role", "")).strip()
        alert = bool(row.get("alert"))
        stale = bool(row.get("stale"))
        if alert and not stale:
            summary_text = str(row.get("summary", "")).strip()
            alerting.append(f"{role}: {summary_text}")
        elif stale:
            stale_roles.append(role)

    if alerting:
        return True, "\n".join(alerting)
    if stale_roles:
        return False, "stale: " + ",".join(stale_roles)
    return False, "all clear"


def upsert_office_watch_card(kanban_dir: Path, *, blocked: bool, body: str) -> None:
    kanban_dir.mkdir(parents=True, exist_ok=True)
    cards_path = kanban_dir / "cards.json"
    cards: list[dict[str, str]] = []
    if cards_path.is_file():
        cards = json.loads(cards_path.read_text())

    card = {
        "id": CARD_ID,
        "title": CARD_TITLE,
        "status": "blocked" if blocked else "done",
        "content": body,
    }

    updated = False
    for index, existing in enumerate(cards):
        if existing.get("id") == CARD_ID:
            cards[index] = card
            updated = True
            break
    if not updated:
        cards.append(card)

    tmp_path = cards_path.with_name(cards_path.name + ".tmp")
    tmp_path.write_text(json.dumps(cards, indent=2) + "\n")
    os.replace(tmp_path, cards_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Upsert office-watch Kanban card from summary JSON")
    parser.add_argument("--dir", required=True, type=Path, help="Kanban data directory")
    args = parser.parse_args()

    summary = json.load(sys.stdin)
    blocked, body = card_state_from_summary(summary)
    upsert_office_watch_card(args.dir, blocked=blocked, body=body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
