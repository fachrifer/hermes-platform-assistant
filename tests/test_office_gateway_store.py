import sqlite3
from datetime import datetime, timedelta, timezone

from office_gateway.store import GatewayStore


def _store(tmp_path, ttl=600):
    return GatewayStore(str(tmp_path / "gw.db"), ttl)


def _expire(store, action_id):
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE actions SET expires_at = ? WHERE action_id = ?", (past, action_id))


def test_propose_then_approve_then_succeed(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {"reason": "hung"}, "restart api")
    assert a.status == "pending" and a.params == {"reason": "hung"}
    claimed = store.claim_pending(a.action_id, "tim")
    assert claimed.status == "executing" and claimed.approver == "tim"
    assert store.claim_pending(a.action_id, "tim") is None
    done = store.mark_executed(a.action_id, ok=True, detail="ok", result={"x": 1})
    assert done.status == "succeeded" and done.result == {"x": 1}
    events = [row["event"] for row in store.list_audit(10)]
    assert events == ["succeeded", "approved", "proposed"]
    assert store.list_audit(10)[1]["actor"] == "tim"


def test_failed_execution_records_detail(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {}, "restart api")
    store.claim_pending(a.action_id, "tim")
    done = store.mark_executed(a.action_id, ok=False, detail="failed:rolled_back", result={"rolled_back": True})
    assert done.status == "failed" and done.detail == "failed:rolled_back"


def test_reject_only_pending(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {}, "restart api")
    rejected = store.reject(a.action_id, "tim")
    assert rejected.status == "rejected" and rejected.approver == "tim"
    assert store.reject(a.action_id, "tim") is None
    assert store.claim_pending(a.action_id, "tim") is None


def test_expired_actions_cannot_be_claimed_and_show_expired(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {}, "restart api")
    _expire(store, a.action_id)
    assert store.claim_pending(a.action_id, "tim") is None
    assert store.get_action(a.action_id).status == "expired"
    assert [x.status for x in store.list_actions(None)] == ["expired"]
    assert store.list_actions(("pending",)) == []


def test_list_actions_filters_and_orders_newest_first(tmp_path):
    store = _store(tmp_path)
    first = store.propose("restart_service", "a", "lab-host", {}, "restart a")
    second = store.propose("restart_service", "b", "lab-host", {}, "restart b")
    store.reject(first.action_id, "tim")
    pending = store.list_actions(("pending",))
    assert [x.action_id for x in pending] == [second.action_id]
    assert [x.action_id for x in store.list_actions(None)] == [second.action_id, first.action_id]


def test_migrates_old_executed_status(tmp_path):
    db = tmp_path / "gw.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE actions (action_id TEXT PRIMARY KEY, action TEXT NOT NULL, target TEXT NOT NULL,
              role TEXT NOT NULL, params_json TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL, status TEXT NOT NULL, executing_at TEXT);
            CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, action_id TEXT NOT NULL,
              event TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO actions VALUES ('old','restart_service','x','lab-host','{}','restart x',
              '2026-01-01T00:00:00Z','2026-01-01T00:10:00Z','executed',NULL);
            """
        )
    store = GatewayStore(str(db), 600)
    assert store.get_action("old").status == "succeeded"
    assert store.list_audit(5) == []


def test_stale_executing_recovered_as_failed(tmp_path):
    db = tmp_path / "gw.db"
    store = GatewayStore(str(db), 60)
    a = store.propose("restart_service", "api", "lab-host", {}, "restart api")
    store.claim_pending(a.action_id, "tim")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE actions SET executing_at = '2020-01-01T00:00:00Z' WHERE action_id = ?", (a.action_id,))
    again = GatewayStore(str(db), 60)
    assert again.get_action(a.action_id).status == "failed"


def test_unknown_audit_detail_is_normalized(tmp_path):
    store = _store(tmp_path)
    a = store.propose("restart_service", "api", "lab-host", {}, "restart api")
    store.claim_pending(a.action_id, "tim")
    store.mark_executed(a.action_id, ok=False, detail="stack trace with secrets")
    assert store.list_audit(1)[0]["detail"] == "failed:not_found"
