from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class PendingAction:
    action_id: str
    action: str
    target: str
    params_json: str
    summary: str
    created_at: str
    expires_at: str
    status: str


class GatewayStore:
    def __init__(self, db_path: str, action_ttl_seconds: int = 600) -> None:
        self.db_path = db_path
        self.action_ttl_seconds = action_ttl_seconds
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
        action: str,
        target: str,
        params: dict | None = None,
        summary: str = "",
    ) -> PendingAction:
        action_id = str(uuid.uuid4())
        created = _utc_now()
        expires = created + timedelta(seconds=self.action_ttl_seconds)
        params_json = json.dumps(params or {})
        created_at = _iso_z(created)
        expires_at = _iso_z(expires)
        status = "pending"

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO actions (
                    action_id, action, target, params_json, summary,
                    created_at, expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (action_id, action, target, params_json, summary, created_at, expires_at, status),
            )
            conn.execute(
                """
                INSERT INTO audit (action_id, event, detail, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (action_id, "proposed", summary, created_at),
            )

        return PendingAction(
            action_id=action_id,
            action=action,
            target=target,
            params_json=params_json,
            summary=summary,
            created_at=created_at,
            expires_at=expires_at,
            status=status,
        )

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
            params_json=row["params_json"],
            summary=row["summary"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            status=row["status"],
        )

    def mark_expired_if_needed(self, action: PendingAction) -> PendingAction:
        if action.status != "pending":
            return action
        expires = datetime.fromisoformat(action.expires_at.replace("Z", "+00:00"))
        if _utc_now() <= expires:
            return action
        with self._connect() as conn:
            conn.execute(
                "UPDATE actions SET status = ? WHERE action_id = ?",
                ("expired", action.action_id),
            )
            conn.execute(
                """
                INSERT INTO audit (action_id, event, detail, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (action.action_id, "expired", "action expired", _iso_z(_utc_now())),
            )
        return PendingAction(
            action_id=action.action_id,
            action=action.action,
            target=action.target,
            params_json=action.params_json,
            summary=action.summary,
            created_at=action.created_at,
            expires_at=action.expires_at,
            status="expired",
        )

    def mark_executed(self, action_id: str, ok: bool, detail: str) -> PendingAction | None:
        action = self.get_action(action_id)
        if action is None:
            return None
        status = "executed" if ok else "failed"
        now = _iso_z(_utc_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE actions SET status = ? WHERE action_id = ?",
                (status, action_id),
            )
            conn.execute(
                """
                INSERT INTO audit (action_id, event, detail, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (action_id, "executed" if ok else "failed", detail, now),
            )
        return PendingAction(
            action_id=action.action_id,
            action=action.action,
            target=action.target,
            params_json=action.params_json,
            summary=action.summary,
            created_at=action.created_at,
            expires_at=action.expires_at,
            status=status,
        )

    def list_audit(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, action_id, event, detail, created_at
                FROM audit
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "action_id": row["action_id"],
                "event": row["event"],
                "detail": row["detail"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
