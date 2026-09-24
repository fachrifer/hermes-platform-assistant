from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATUSES = ("pending", "executing", "succeeded", "failed", "rejected", "expired")

ALLOWED_AUDIT_DETAIL_CODES = frozenset(
    {
        "ok",
        "rejected",
        "failed:adapter_unavailable",
        "failed:not_found",
        "failed:invalid",
        "failed:rolled_back",
        "failed:claim_conflict",
        "failed:expired",
    }
)

_ACTION_COLUMNS = {
    "role": "TEXT NOT NULL DEFAULT ''",
    "executing_at": "TEXT",
    "approver": "TEXT NOT NULL DEFAULT ''",
    "decided_at": "TEXT NOT NULL DEFAULT ''",
    "detail": "TEXT NOT NULL DEFAULT ''",
    "result_json": "TEXT NOT NULL DEFAULT '{}'",
}


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
    approver: str = ""
    decided_at: str = ""
    detail: str = ""
    result_json: str = "{}"

    @property
    def params(self) -> dict:
        return json.loads(self.params_json or "{}")

    @property
    def result(self) -> dict:
        return json.loads(self.result_json or "{}")


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
                    role TEXT NOT NULL DEFAULT '',
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
            have = {row["name"] for row in conn.execute("PRAGMA table_info(actions)")}
            for name, decl in _ACTION_COLUMNS.items():
                if name not in have:
                    conn.execute(f"ALTER TABLE actions ADD COLUMN {name} {decl}")
            audit_cols = {row["name"] for row in conn.execute("PRAGMA table_info(audit)")}
            if "actor" not in audit_cols:
                conn.execute("ALTER TABLE audit ADD COLUMN actor TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE actions SET status = 'succeeded' WHERE status = 'executed'")
            self._recover_stale_executing(conn)

    def _audit(self, conn, action_id: str, event: str, detail: str, actor: str = "") -> None:
        conn.execute(
            "INSERT INTO audit (action_id, event, detail, created_at, actor) VALUES (?, ?, ?, ?, ?)",
            (action_id, event, normalize_audit_detail(detail), _iso_z(_utc_now()), actor),
        )

    def _recover_stale_executing(self, conn: sqlite3.Connection) -> None:
        cutoff = _iso_z(_utc_now() - timedelta(seconds=self.action_ttl_seconds))
        rows = conn.execute(
            "SELECT action_id FROM actions WHERE status = 'executing' "
            "AND COALESCE(executing_at, created_at) <= ?",
            (cutoff,),
        ).fetchall()
        for row in rows:
            cur = conn.execute(
                "UPDATE actions SET status = 'failed', detail = 'failed:adapter_unavailable' "
                "WHERE action_id = ? AND status = 'executing'",
                (row["action_id"],),
            )
            if cur.rowcount:
                self._audit(conn, row["action_id"], "failed", "failed:adapter_unavailable")

    def _expire_due(self, conn: sqlite3.Connection) -> None:
        now = _iso_z(_utc_now())
        rows = conn.execute(
            "SELECT action_id FROM actions WHERE status = 'pending' AND expires_at < ?", (now,)
        ).fetchall()
        for row in rows:
            cur = conn.execute(
                "UPDATE actions SET status = 'expired' WHERE action_id = ? AND status = 'pending'",
                (row["action_id"],),
            )
            if cur.rowcount:
                self._audit(conn, row["action_id"], "expired", "failed:expired")

    @staticmethod
    def _row(row: sqlite3.Row) -> PendingAction:
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
            approver=row["approver"],
            decided_at=row["decided_at"],
            detail=row["detail"],
            result_json=row["result_json"],
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
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO actions (action_id, action, target, role, params_json, summary, "
                "created_at, expires_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (
                    action_id,
                    action,
                    target,
                    role,
                    json.dumps(params or {}),
                    summary,
                    _iso_z(created),
                    _iso_z(created + timedelta(seconds=self.action_ttl_seconds)),
                ),
            )
            self._audit(conn, action_id, "proposed", "ok", role)
        found = self.get_action(action_id)
        assert found is not None
        return found

    def get_action(self, action_id: str) -> PendingAction | None:
        with self._connect() as conn:
            self._expire_due(conn)
            row = conn.execute("SELECT * FROM actions WHERE action_id = ?", (action_id,)).fetchone()
        return self._row(row) if row else None

    def list_actions(
        self, statuses: tuple[str, ...] | None = None, limit: int = 50
    ) -> list[PendingAction]:
        limit = max(1, min(int(limit), 200))
        with self._connect() as conn:
            self._expire_due(conn)
            if statuses:
                marks = ",".join("?" for _ in statuses)
                rows = conn.execute(
                    f"SELECT * FROM actions WHERE status IN ({marks}) "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (*statuses, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM actions ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
                ).fetchall()
        return [self._row(row) for row in rows]

    def claim_pending(self, action_id: str, approver: str) -> PendingAction | None:
        now = _iso_z(_utc_now())
        with self._connect() as conn:
            self._expire_due(conn)
            cur = conn.execute(
                "UPDATE actions SET status = 'executing', executing_at = ?, approver = ?, "
                "decided_at = ? WHERE action_id = ? AND status = 'pending' AND expires_at >= ?",
                (now, approver, now, action_id, now),
            )
            if cur.rowcount == 0:
                return None
            self._audit(conn, action_id, "approved", "ok", approver)
        return self.get_action(action_id)

    def reject(self, action_id: str, approver: str) -> PendingAction | None:
        now = _iso_z(_utc_now())
        with self._connect() as conn:
            self._expire_due(conn)
            cur = conn.execute(
                "UPDATE actions SET status = 'rejected', approver = ?, decided_at = ? "
                "WHERE action_id = ? AND status = 'pending'",
                (approver, now, action_id),
            )
            if cur.rowcount == 0:
                return None
            self._audit(conn, action_id, "rejected", "rejected", approver)
        return self.get_action(action_id)

    def mark_executed(
        self, action_id: str, ok: bool, detail: str, result: dict | None = None
    ) -> PendingAction | None:
        status = "succeeded" if ok else "failed"
        safe = normalize_audit_detail(detail)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE actions SET status = ?, detail = ?, result_json = ? "
                "WHERE action_id = ? AND status = 'executing'",
                (status, safe, json.dumps(result or {}), action_id),
            )
            if cur.rowcount == 0:
                return None
            self._audit(conn, action_id, status, safe)
        return self.get_action(action_id)

    def list_audit(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, action_id, event, detail, created_at, actor FROM audit "
                "ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "action_id": row["action_id"],
                "event": row["event"],
                "detail": normalize_audit_detail(row["detail"]),
                "actor": row["actor"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
