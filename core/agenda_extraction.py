"""Normalization helpers for multimodal agenda extraction."""

from __future__ import annotations

import datetime as dt
import re

from core.reminders import parse_reminders


def normalize_agenda_items(data: dict | list) -> list[dict]:
    raw_items = data if isinstance(data, list) else data.get("agendas", []) if isinstance(data, dict) else []
    normalized: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title", "") or "").strip()
        if not title:
            continue
        date = str(raw.get("date", "") or "").strip()
        start_time = str(raw.get("start_time", "") or "").strip()
        end_time = str(raw.get("end_time", "") or "").strip()
        needs_review = bool(raw.get("needs_review", False))
        if date:
            try:
                date = dt.date.fromisoformat(date).isoformat()
            except ValueError:
                needs_review = True
        if not date or not start_time:
            needs_review = True
        normalized.append({
            "title": title,
            "date": date,
            "start_time": start_time,
            "end_time": end_time,
            "location": str(raw.get("location", "") or "").strip(),
            "notes": str(raw.get("notes", "") or "").strip(),
            "reminders": raw.get("reminders", []) if isinstance(raw.get("reminders", []), list) else [],
            "needs_review": needs_review,
        })
    return normalized


def parse_agenda_text(text: str) -> dict:
    """Parse the explicit `/agenda tambah YYYY-MM-DD HH:MM title` form."""
    match = re.match(
        r"^(?P<date>\d{4}-\d{2}-\d{2})(?:\s+(?P<time>\d{2}:\d{2}))?\s+(?P<title>.+)$",
        text.strip(),
    )
    if not match:
        return {
            "title": text.strip(),
            "date": "",
            "start_time": "",
            "end_time": "",
            "location": "",
            "notes": "",
            "reminders": parse_reminders(text),
            "needs_review": False,
        }
    return {
        "title": match.group("title").strip(),
        "date": match.group("date"),
        "start_time": match.group("time") or "",
        "end_time": "",
        "location": "",
        "notes": "",
        "reminders": parse_reminders(text),
        "needs_review": False,
    }
