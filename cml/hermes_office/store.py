"""Local CML storage for logs, health history, reports, and cloud outbox."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OutboxItem:
    id: int
    payload: dict
    signature: str


class OfficeStore:
    """SQLite-backed store kept entirely inside the CML project filesystem."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self.data_dir / "hermes_office.db"
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS service_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    service TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    line TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_service_logs_service_ts
                    ON service_logs(service, ts);

                CREATE TABLE IF NOT EXISTS health_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cloud_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload_json TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            self._conn.commit()

    def append_logs(self, service: str, entries: list[dict]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT INTO service_logs (service, ts, line) VALUES (?, ?, ?)",
                [(service, item["ts"], item["line"]) for item in entries],
            )
            self._conn.commit()

    def list_recent_logs(self, service: str | None = None, *, limit: int = 100) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            if service:
                rows = self._conn.execute(
                    """
                    SELECT service, ts, line FROM service_logs
                    WHERE service = ?
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (service, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT service, ts, line FROM service_logs
                    ORDER BY ts DESC, id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        items = [dict(row) for row in rows]
        items.reverse()
        return items

    def purge_logs(self, *, now: dt.datetime | None = None, days: int = 14) -> None:
        now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        cutoff = (now - dt.timedelta(days=days)).isoformat()
        with self._lock:
            self._conn.execute("DELETE FROM service_logs WHERE ts < ?", (cutoff,))
            self._conn.commit()

    def save_health(self, observed_at: str, services: list[dict], metrics: dict) -> None:
        payload = {"services": services, "metrics": metrics}
        with self._lock:
            self._conn.execute(
                "INSERT INTO health_samples (observed_at, payload_json) VALUES (?, ?)",
                (observed_at, json.dumps(payload, ensure_ascii=True)),
            )
            self._conn.commit()

    def latest_health(self) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT observed_at, payload_json FROM health_samples
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        return {"observed_at": row["observed_at"], **payload}

    def save_report(self, period: str, content: str, *, created_at: str | None = None) -> None:
        created_at = created_at or dt.datetime.now(dt.timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO reports (period, content, created_at) VALUES (?, ?, ?)",
                (period, content, created_at),
            )
            self._conn.commit()

    def list_reports(self, *, limit: int = 20) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT period, content, created_at FROM reports
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_report(self, period: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT period, content, created_at FROM reports
                WHERE period = ?
                ORDER BY id DESC LIMIT 1
                """,
                (period,),
            ).fetchone()
        return dict(row) if row else None

    def enqueue_cloud_payload(self, payload: dict, signature: str) -> int:
        created_at = dt.datetime.now(dt.timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO cloud_outbox (payload_json, signature, created_at)
                VALUES (?, ?, ?)
                """,
                (json.dumps(payload, ensure_ascii=True), signature, created_at),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def dequeue_cloud_payloads(self, *, limit: int = 20) -> list[OutboxItem]:
        limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, payload_json, signature FROM cloud_outbox
                WHERE delivered = 0
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            OutboxItem(id=row["id"], payload=json.loads(row["payload_json"]), signature=row["signature"])
            for row in rows
        ]

    def acknowledge_cloud_payload(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE cloud_outbox SET delivered = 1 WHERE id = ?",
                (item_id,),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
