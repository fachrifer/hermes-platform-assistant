"""SQLite storage abstraction for Hermes state.

All persistent local state (manual agenda, conversational memory, per-machine
overrides) goes through this thin layer. Keeping DB access in one place means we
can later swap the backend (e.g. Turso/libSQL) by changing only this module.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from config.settings import settings


class Database:
    def __init__(self, path: str | None = None):
        self.path = path or settings.db_path
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because FastAPI + PTB run across threads;
        # a lock serializes writes.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agenda (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    when_at TEXT,           -- ISO datetime string, nullable
                    notes TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    role TEXT NOT NULL,     -- 'user' | 'assistant'
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_memory_chat ON memory(chat_id, id);

                CREATE TABLE IF NOT EXISTS recorded_emails (
                    email_id TEXT PRIMARY KEY,   -- Gmail message id
                    status TEXT NOT NULL,        -- 'recorded' | 'skipped'
                    summary TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'reviewing',
                    created_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS staged_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    batch_id INTEGER NOT NULL,
                    source_email_id TEXT NOT NULL UNIQUE,
                    account TEXT NOT NULL DEFAULT '',
                    transaction_date TEXT NOT NULL,
                     amount INTEGER NOT NULL,
                     type TEXT NOT NULL,
                     category TEXT NOT NULL,
                     subcategory TEXT NOT NULL DEFAULT '-',
                     merchant TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    needs_review INTEGER NOT NULL DEFAULT 0,
                    possible_duplicate INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    commit_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (batch_id) REFERENCES import_batches(id)
                );
                CREATE INDEX IF NOT EXISTS idx_import_batch_period ON import_batches(period);
                CREATE INDEX IF NOT EXISTS idx_staged_batch ON staged_transactions(batch_id, id);
                CREATE INDEX IF NOT EXISTS idx_staged_status ON staged_transactions(status);
                CREATE INDEX IF NOT EXISTS idx_staged_source ON staged_transactions(source_email_id);

                CREATE TABLE IF NOT EXISTS monitoring_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observer_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(observer_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_monitoring_snapshots_observed
                    ON monitoring_snapshots(observed_at);

                CREATE TABLE IF NOT EXISTS monitoring_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    report_type TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(report_type, period_start, period_end)
                );
                """
            )
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(staged_transactions)")}
            if "subcategory" not in columns:
                self._conn.execute(
                    "ALTER TABLE staged_transactions ADD COLUMN subcategory TEXT NOT NULL DEFAULT '-'"
                )
            self._conn.commit()

    # --- generic helpers ---
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, params)
            return cur.fetchall()

    # --- recorded emails (anti-duplicate) ---
    def is_email_handled(self, email_id: str) -> bool:
        rows = self.query(
            "SELECT 1 FROM recorded_emails WHERE email_id = ? LIMIT 1", (email_id,)
        )
        return bool(rows)

    def mark_email(self, email_id: str, status: str, summary: str = "") -> None:
        self.execute(
            "INSERT OR REPLACE INTO recorded_emails (email_id, status, summary) VALUES (?, ?, ?)",
            (email_id, status, summary),
        )

    def clear_skipped_emails(self, summary: str | None = "bukan transaksi") -> int:
        """Remove skipped email markers so import can retry them."""
        if summary is None:
            cursor = self.execute("DELETE FROM recorded_emails WHERE status = ?", ("skipped",))
        else:
            cursor = self.execute(
                "DELETE FROM recorded_emails WHERE status = ? AND summary = ?",
                ("skipped", summary),
            )
        return int(cursor.rowcount or 0)

    # --- historical finance import staging ---
    def get_or_create_import_batch(self, period: str) -> dict:
        self.execute("INSERT OR IGNORE INTO import_batches (period) VALUES (?)", (period,))
        rows = self.query("SELECT * FROM import_batches WHERE period = ?", (period,))
        return dict(rows[0])

    def list_import_batches(self) -> list[dict]:
        return [
            dict(row)
            for row in self.query("SELECT * FROM import_batches ORDER BY period")
        ]

    def get_import_batch(self, period: str) -> dict | None:
        rows = self.query("SELECT * FROM import_batches WHERE period = ?", (period,))
        return dict(rows[0]) if rows else None

    def set_import_batch_status(self, period: str, status: str) -> None:
        self.execute("UPDATE import_batches SET status = ? WHERE period = ?", (status, period))

    def stage_transaction(self, batch_id: int, transaction: dict) -> int | None:
        cursor = self.execute(
            """
            INSERT OR IGNORE INTO staged_transactions (
                batch_id, source_email_id, account, transaction_date, amount, type,
                category, subcategory, merchant, description, needs_review, possible_duplicate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                transaction["source_email_id"],
                transaction.get("account", ""),
                transaction["transaction_date"],
                int(transaction.get("amount", 0)),
                transaction.get("type", "expense"),
                transaction.get("category", "Lainnya"),
                transaction.get("subcategory", "-"),
                transaction.get("merchant", ""),
                transaction.get("description", ""),
                int(bool(transaction.get("needs_review", 0))),
                int(bool(transaction.get("possible_duplicate", 0))),
            ),
        )
        return cursor.lastrowid if cursor.rowcount else None

    def list_staged_transactions(self, batch_id: int) -> list[dict]:
        return [
            dict(row)
            for row in self.query(
                "SELECT * FROM staged_transactions WHERE batch_id = ? ORDER BY transaction_date, id",
                (batch_id,),
            )
        ]

    def get_staged_transaction(self, transaction_id: int) -> dict | None:
        rows = self.query("SELECT * FROM staged_transactions WHERE id = ?", (transaction_id,))
        return dict(rows[0]) if rows else None

    def list_all_staged_transactions(self) -> list[dict]:
        return [dict(row) for row in self.query("SELECT * FROM staged_transactions ORDER BY id")]

    def update_staged_transaction(self, transaction_id: int, fields: dict) -> None:
        allowed = {
            "transaction_date",
            "amount",
            "type",
            "category",
            "subcategory",
            "account",
            "merchant",
            "description",
        }
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        self.execute(
            f"UPDATE staged_transactions SET {assignments}, needs_review = 0 WHERE id = ?",
            (*updates.values(), transaction_id),
        )

    def set_staged_needs_review(self, transaction_id: int, needs_review: int) -> None:
        self.execute(
            "UPDATE staged_transactions SET needs_review = ? WHERE id = ?",
            (int(bool(needs_review)), transaction_id),
        )

    def set_staged_status(self, transaction_id: int, status: str, commit_error: str = "") -> None:
        self.execute(
            "UPDATE staged_transactions SET status = ?, commit_error = ? WHERE id = ?",
            (status, commit_error, transaction_id),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- office monitoring ---
    def save_monitoring_snapshot(
        self, observer_id: str, sequence: int, observed_at: str, payload_json: str
    ) -> bool:
        cursor = self.execute(
            """
            INSERT OR IGNORE INTO monitoring_snapshots (
                observer_id, sequence, observed_at, payload_json
            ) VALUES (?, ?, ?, ?)
            """,
            (observer_id, sequence, observed_at, payload_json),
        )
        return bool(cursor.rowcount)

    def list_monitoring_snapshots(self, start_at: str, end_at: str) -> list[dict]:
        return [
            dict(row)
            for row in self.query(
                """
                SELECT observer_id, sequence, observed_at, payload_json
                FROM monitoring_snapshots
                WHERE observed_at >= ? AND observed_at < ?
                ORDER BY observed_at, observer_id, sequence
                """,
                (start_at, end_at),
            )
        ]

    def save_monitoring_report(
        self,
        report_type: str,
        period_start: str,
        period_end: str,
        content: str,
        *,
        replace: bool = False,
    ) -> bool:
        sql = """
            INSERT OR REPLACE INTO monitoring_reports (
                report_type, period_start, period_end, content
            ) VALUES (?, ?, ?, ?)
            """ if replace else """
            INSERT OR IGNORE INTO monitoring_reports (
                report_type, period_start, period_end, content
            ) VALUES (?, ?, ?, ?)
            """
        cursor = self.execute(sql, (report_type, period_start, period_end, content))
        return bool(cursor.rowcount)

    def get_monitoring_report(
        self, report_type: str, period_start: str, period_end: str
    ) -> dict | None:
        rows = self.query(
            """
            SELECT report_type, period_start, period_end, content, created_at
            FROM monitoring_reports
            WHERE report_type = ? AND period_start = ? AND period_end = ?
            LIMIT 1
            """,
            (report_type, period_start, period_end),
        )
        return dict(rows[0]) if rows else None

    def get_latest_monitoring_report(self, report_type: str) -> dict | None:
        rows = self.query(
            """
            SELECT report_type, period_start, period_end, content, created_at
            FROM monitoring_reports
            WHERE report_type = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (report_type,),
        )
        return dict(rows[0]) if rows else None

    def purge_monitoring_before(self, cutoff_at: str) -> None:
        self.execute("DELETE FROM monitoring_snapshots WHERE observed_at < ?", (cutoff_at,))
        self.execute("DELETE FROM monitoring_reports WHERE period_end < ?", (cutoff_at,))
