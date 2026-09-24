from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from office_gateway.roles import WATCH_SPECIALIST_ROLES, WATCH_STALE_SECONDS

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
    role: str
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
                    role TEXT NOT NULL,
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
                CREATE TABLE IF NOT EXISTS watch_snapshots (
                    role TEXT PRIMARY KEY,
                    snapshot TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    alert INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(actions)").fetchall()
            }
            if "role" not in columns:
                conn.execute(
                    "ALTER TABLE actions ADD COLUMN role TEXT NOT NULL DEFAULT ''"
                )
            if "executing_at" not in columns:
                conn.execute("ALTER TABLE actions ADD COLUMN executing_at TEXT")
            self._recover_stale_executing(conn)

    def _recover_stale_executing(self, conn: sqlite3.Connection) -> None:
        cutoff = _iso_z(
            _utc_now() - timedelta(seconds=self.action_ttl_seconds)
        )
        now = _iso_z(_utc_now())
        rows = conn.execute(
            """
            SELECT action_id
            FROM actions
            WHERE status = 'executing'
              AND COALESCE(executing_at, created_at) <= ?
            """,
            (cutoff,),
        ).fetchall()
        for row in rows:
            cur = conn.execute(
                """
                UPDATE actions
                SET status = 'failed'
                WHERE action_id = ? AND status = 'executing'
                """,
                (row["action_id"],),
            )
            if cur.rowcount:
                conn.execute(
                    """
                    INSERT INTO audit (action_id, event, detail, created_at)
                    VALUES (?, 'failed', 'failed:adapter_unavailable', ?)
                    """,
                    (row["action_id"], now),
                )

    def propose(
        self,
        action: str,
        target: str,
        role: str,
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
                    action_id, action, target, role, params_json, summary,
                    created_at, expires_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action_id,
                    action,
                    target,
                    role,
                    params_json,
                    summary,
                    created_at,
                    expires_at,
                    status,
                ),
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
            role=role,
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
            role=row["role"],
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
            role=action.role,
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
            role=row["role"],
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
                UPDATE actions SET status = 'executing', executing_at = ?
                WHERE action_id = ? AND status = 'pending' AND expires_at >= ?
                """,
                (now, action_id, now),
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
            role=action.role,
            params_json=action.params_json,
            summary=action.summary,
            created_at=action.created_at,
            expires_at=action.expires_at,
            status=status,
        )

    def upsert_watch(
        self,
        role: str,
        snapshot: str,
        ts: str,
        alert: bool,
        summary: str,
    ) -> None:
        updated_at = _iso_z(_utc_now())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO watch_snapshots (
                    role, snapshot, ts, alert, summary, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (role, snapshot, ts, int(alert), summary, updated_at),
            )

    def list_watch_summary(self, now: datetime) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, ts, alert, summary FROM watch_snapshots"
            ).fetchall()
        by_role = {row["role"]: row for row in rows}
        roles_out: list[dict] = []
        for role in sorted(WATCH_SPECIALIST_ROLES):
            row = by_role.get(role)
            if row is None:
                roles_out.append(
                    {
                        "role": role,
                        "alert": False,
                        "summary": "",
                        "ts": None,
                        "age_seconds": None,
                        "stale": True,
                    }
                )
                continue
            ts_str = row["ts"]
            ts_dt = datetime.fromisoformat(ts_str)
            age_seconds = int((now - ts_dt).total_seconds())
            stale = age_seconds > WATCH_STALE_SECONDS
            roles_out.append(
                {
                    "role": role,
                    "alert": bool(row["alert"]),
                    "summary": row["summary"],
                    "ts": ts_str,
                    "age_seconds": age_seconds,
                    "stale": stale,
                }
            )
        return {"roles": roles_out}

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
