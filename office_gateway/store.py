"""SQLite store for pending actions and append-only audit events."""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class PendingAction:
    action_id: str
    action: str
    target: str
    params: dict
    summary: str
    created_at: str
    expires_at: str
    status: str


class GatewayStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def propose(
        self,
        *,
        action: str,
        target: str,
        params: dict,
        summary: str,
        ttl_seconds: int,
    ) -> PendingAction:
        now = _utcnow()
        pending = PendingAction(
            action_id=str(uuid.uuid4()),
            action=action,
            target=target,
            params=params,
            summary=summary,
            created_at=_iso(now),
            expires_at=_iso(now + timedelta(seconds=ttl_seconds)),
            status="pending",
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO actions (
                    action_id, action, target, params_json, summary,
                    created_at, expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pending.action_id,
                    pending.action,
                    pending.target,
                    json.dumps(pending.params, sort_keys=True),
                    pending.summary,
                    pending.created_at,
                    pending.expires_at,
                    pending.status,
                ),
            )
            conn.execute(
                "INSERT INTO audit (action_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
                (pending.action_id, "propose", pending.summary, pending.created_at),
            )
        return pending

    def get_action(self, action_id: str) -> PendingAction | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        if row is None:
            return None
        return PendingAction(
            action_id=row["action_id"],
            action=row["action"],
            target=row["target"],
            params=json.loads(row["params_json"]),
            summary=row["summary"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            status=row["status"],
        )

    def mark_expired_if_needed(self, action: PendingAction) -> PendingAction:
        if action.status != "pending":
            return action
        if action.expires_at >= _iso(_utcnow()):
            return action
        with self._connect() as conn:
            conn.execute(
                "UPDATE actions SET status = ? WHERE action_id = ? AND status = ?",
                ("expired", action.action_id, "pending"),
            )
            conn.execute(
                "INSERT INTO audit (action_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
                (action.action_id, "expired", "action TTL elapsed", _iso(_utcnow())),
            )
        updated = self.get_action(action.action_id)
        assert updated is not None
        return updated

    def mark_executed(self, action_id: str, *, ok: bool, detail: str) -> None:
        status = "executed" if ok else "failed"
        event = "execute_success" if ok else "execute_failure"
        with self._connect() as conn:
            conn.execute(
                "UPDATE actions SET status = ? WHERE action_id = ?",
                (status, action_id),
            )
            conn.execute(
                "INSERT INTO audit (action_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
                (action_id, event, detail, _iso(_utcnow())),
            )

    def list_audit(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT action_id, event, detail, created_at
                FROM audit
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
