"""Durable local queue for snapshots awaiting laptop-relay synchronization."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredSnapshot:
    snapshot: dict
    signature: str


class SnapshotSpool:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                sequence INTEGER PRIMARY KEY,
                snapshot_json TEXT NOT NULL,
                signature TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            """
        )
        self._conn.commit()

    def next_sequence(self) -> int:
        row = self._conn.execute("SELECT value FROM state WHERE key = 'sequence'").fetchone()
        sequence = int(row["value"] if row else 0) + 1
        self._conn.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES ('sequence', ?)", (sequence,)
        )
        self._conn.commit()
        return sequence

    def enqueue(self, snapshot: dict, signature: str) -> None:
        sequence = snapshot.get("sequence")
        if not isinstance(sequence, int):
            raise ValueError("snapshot harus memiliki sequence integer")
        self._conn.execute(
            "INSERT OR REPLACE INTO snapshots (sequence, snapshot_json, signature) VALUES (?, ?, ?)",
            (sequence, json.dumps(snapshot, sort_keys=True, separators=(",", ":")), signature),
        )
        self._conn.commit()

    def pending(self, limit: int = 500) -> list[StoredSnapshot]:
        rows = self._conn.execute(
            "SELECT snapshot_json, signature FROM snapshots ORDER BY sequence LIMIT ?", (limit,)
        ).fetchall()
        return [StoredSnapshot(json.loads(row["snapshot_json"]), row["signature"]) for row in rows]

    def acknowledge(self, sequence: int) -> None:
        self._conn.execute("DELETE FROM snapshots WHERE sequence = ?", (sequence,))
        self._conn.commit()

    def purge_before(self, cutoff_at: str) -> None:
        self._conn.execute("DELETE FROM snapshots WHERE created_at < ?", (cutoff_at,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
