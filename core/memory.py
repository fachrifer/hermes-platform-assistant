"""Conversational memory backed by SQLite.

Stores a rolling window of the dialogue per Telegram chat so follow-up messages
stay coherent. Kept intentionally small to remain lightweight.
"""

from __future__ import annotations

from core.db import Database

MAX_TURNS = 12  # number of recent messages to keep as context


class Memory:
    def __init__(self, db: Database):
        self.db = db

    def add(self, chat_id: str, role: str, content: str) -> None:
        self.db.execute(
            "INSERT INTO memory (chat_id, role, content) VALUES (?, ?, ?)",
            (str(chat_id), role, content),
        )

    def recent(self, chat_id: str, limit: int = MAX_TURNS) -> list[dict]:
        rows = self.db.query(
            "SELECT role, content FROM memory WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
            (str(chat_id), limit),
        )
        # Return chronological order (oldest first).
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def clear(self, chat_id: str) -> None:
        self.db.execute("DELETE FROM memory WHERE chat_id = ?", (str(chat_id),))
