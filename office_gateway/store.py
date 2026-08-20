from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ALLOWED_AUDIT_DETAIL_CODES = frozenset(
    {
        "ok",
        "failed:adapter_unavailable",
        "failed:not_found",
        "failed:claim_conflict",
        "failed:expired",
    }
)


def normalize_audit_detail(detail: str) -> str:
    if detail in ALLOWED_AUDIT_DETAIL_CODES:
        return detail
    return "failed:not_found"


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
                (action_id, "proposed", "ok", created_at),
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

    def _is_expired(self, action: PendingAction) -> bool:
        expires = datetime.fromisoformat(action.expires_at.replace("Z", "+00:00"))
        return _utc_now() > expires

    def mark_expired_if_needed(self, action: PendingAction) -> PendingAction:
        if action.status != "pending" or not self._is_expired(action):
            return action
        now = _iso_z(_utc_now())
        with self._connect() as conn:
            conn.execute(
                "UPDATE actions SET status = ? WHERE action_id = ? AND status = 'pending'",
                ("expired", action.action_id),
            )
            conn.execute(
                """
                INSERT INTO audit (action_id, event, detail, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (action.action_id, "expired", "failed:expired", now),
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

    def _row_to_pending(self, row: sqlite3.Row) -> PendingAction:
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

    def claim_pending(self, action_id: str) -> PendingAction | None:
        now = _iso_z(_utc_now())
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE actions SET status = 'executing'
                WHERE action_id = ? AND status = 'pending' AND expires_at >= ?
                """,
                (action_id, now),
            )
            if cur.rowcount == 0:
                return None
            row = conn.execute(
                "SELECT * FROM actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_pending(row)

    def mark_executed(self, action_id: str, ok: bool, detail: str) -> PendingAction | None:
        action = self.get_action(action_id)
        if action is None:
            return None
        status = "executed" if ok else "failed"
        safe_detail = normalize_audit_detail(detail)
        now = _iso_z(_utc_now())
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE actions SET status = ?
                WHERE action_id = ? AND status = 'executing'
                """,
                (status, action_id),
            )
            if cur.rowcount == 0:
                return None
            conn.execute(
                """
                INSERT INTO audit (action_id, event, detail, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (action_id, "executed" if ok else "failed", safe_detail, now),
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
                "detail": normalize_audit_detail(row["detail"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
