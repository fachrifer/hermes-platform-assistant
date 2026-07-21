"""Manual agenda connector backed by SQLite.

Lets the user store agenda items directly via Telegram (/agenda). Always
available (local storage), so it also serves as a fallback when calendar
connectors are offline.
"""

from __future__ import annotations

import datetime as dt

from core.connector import Connector
from core.db import Database


class ManualAgendaConnector(Connector):
    name = "Agenda Manual"
    icon = "📝"

    def __init__(self, db: Database):
        self.db = db

    def is_available(self) -> bool:
        return True

    async def health_check(self) -> bool:
        return True

    # --- CRUD ---
    def add(self, title: str, when_at: str | None = None, notes: str | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO agenda (title, when_at, notes) VALUES (?, ?, ?)",
            (title, when_at, notes),
        )
        return cur.lastrowid

    def add_verified(self, title: str, when_at: str | None = None, notes: str | None = None) -> dict:
        agenda_id = self.add(title, when_at, notes)
        rows = self.db.query(
            "SELECT id, title, when_at, notes FROM agenda WHERE id = ?", (agenda_id,)
        )
        if not rows:
            raise RuntimeError("Agenda gagal dibaca kembali setelah disimpan.")
        return dict(rows[0])

    def list_all(self) -> list[dict]:
        rows = self.db.query(
            "SELECT id, title, when_at, notes FROM agenda ORDER BY COALESCE(when_at, created_at)"
        )
        return [dict(r) for r in rows]

    def delete(self, agenda_id: int) -> bool:
        cur = self.db.execute("DELETE FROM agenda WHERE id = ?", (agenda_id,))
        return cur.rowcount > 0

    async def today_events(self) -> list[dict]:
        """Return manual agenda items scheduled for today (or undated)."""
        today = dt.date.today().isoformat()
        rows = self.db.query(
            "SELECT id, title, when_at FROM agenda "
            "WHERE when_at IS NULL OR substr(when_at, 1, 10) = ? "
            "ORDER BY when_at",
            (today,),
        )
        out: list[dict] = []
        for r in rows:
            when = r["when_at"] or ""
            out.append(
                {
                    "time": when[11:16] if len(when) >= 16 else "kapan saja",
                    "title": r["title"],
                    "source": "Manual",
                }
            )
        return out
