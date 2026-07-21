from core.db import Database


def _transaction(email_id="Pribadi:abc"):
    return {
        "source_email_id": email_id,
        "account": "Pribadi",
        "transaction_date": "2026-01-15",
        "amount": 125000,
        "type": "expense",
        "category": "Makanan",
        "merchant": "Toko",
        "description": "Makan",
        "needs_review": 0,
        "possible_duplicate": 0,
    }


def test_staging_reuses_period_and_rejects_duplicate_email(tmp_path):
    db = Database(str(tmp_path / "state.db"))

    first = db.get_or_create_import_batch("2026-01")
    second = db.get_or_create_import_batch("2026-01")

    assert first["id"] == second["id"]
    assert db.stage_transaction(first["id"], _transaction()) is not None
    assert db.stage_transaction(first["id"], _transaction()) is None
    assert len(db.list_staged_transactions(first["id"])) == 1


def test_update_staged_transaction_changes_only_editable_fields(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    batch = db.get_or_create_import_batch("2026-01")
    tx_id = db.stage_transaction(batch["id"], {
        **_transaction("Kerja:x"),
        "account": "Kerja",
        "transaction_date": "2026-01-03",
        "amount": 10,
        "category": "Lainnya",
        "merchant": "X",
        "description": "X",
        "needs_review": 1,
        "possible_duplicate": 1,
    })

    db.update_staged_transaction(tx_id, {"amount": 20, "category": "Makanan"})
    row = db.list_staged_transactions(batch["id"])[0]

    assert row["amount"] == 20
    assert row["category"] == "Makanan"
    assert row["source_email_id"] == "Kerja:x"
