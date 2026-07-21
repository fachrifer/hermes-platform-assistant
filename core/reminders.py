"""Parse user-requested Google Calendar reminders."""

from __future__ import annotations

import re


_DURATION_RE = re.compile(
    r"(?P<amount>\d+)\s*(?P<unit>menit|minute|minutes|jam|hour|hours|hari|day|days)"
    r"(?:\s+sebelumnya)?(?:\s+(?:lewat|via|dengan)\s*)?(?P<method>email|popup)?",
    re.IGNORECASE,
)


def parse_reminders(text: str) -> list[dict]:
    raw = text or ""
    lowered = raw.casefold()
    if not any(word in lowered for word in ("ingatkan", "reminder", "remind")):
        return []
    reminders: list[dict] = []
    for match in _DURATION_RE.finditer(raw):
        amount = int(match.group("amount"))
        unit = match.group("unit").casefold()
        if unit in {"jam", "hour", "hours"}:
            minutes = amount * 60
        elif unit in {"hari", "day", "days"}:
            minutes = amount * 24 * 60
        else:
            minutes = amount
        method = (match.group("method") or "popup").casefold()
        item = {"method": method, "minutes": minutes}
        if item not in reminders:
            reminders.append(item)
    return reminders


def build_reminders_payload(reminders: list[dict] | None) -> dict | None:
    if not reminders:
        return None
    return {"useDefault": False, "overrides": reminders}
