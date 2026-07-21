import pytest

from core.db import Database
from core.finance_import import (
    FinanceImportService,
    month_period,
    parse_export_args,
    parse_import_args,
    parse_import_command,
    parse_import_period,
    upload_source_id,
)


def _transaction(email_id="A:1"):
    return {
        "source_email_id": email_id,
        "account": "A",
        "transaction_date": "2026-01-15",
        "amount": 10000,
        "type": "expense",
        "category": "Makanan",
        "subcategory": "Restoran",
        "merchant": "Warung",
        "description": "Makan",
    }


def test_parse_export_args_supports_tsv_and_force():
    assert parse_export_args(["2026-07"]) == ("2026-07", False, "both", False)
    assert parse_export_args(["2026-07", "tsv"]) == ("2026-07", False, "tsv", False)
    assert parse_export_args(["2026-07", "force"]) == ("2026-07", True, "both", False)
    assert parse_export_args(["2026-07", "tsv", "force"]) == ("2026-07", True, "tsv", False)
    assert parse_export_args(["2026-01", "tsv", "force", "auto"]) == ("2026-01", True, "tsv", True)
    assert parse_export_args(["2026-01", "auto"]) == ("2026-01", False, "both", True)


def test_parse_year_and_month_periods():
    assert parse_import_period("2026") == ("2026-01-01", "2026-12-31")
    assert parse_import_period("2026-02") == ("2026-02-01", "2026-02-28")


def test_parse_import_command_supports_session_and_force():
    assert parse_import_command([])["action"] == "start"
    assert parse_import_command(["done"])["action"] == "done"
    assert parse_import_command(["cancel"])["action"] == "cancel"
    assert parse_import_args(["2026-01"]) == ("2026-01", False)
    assert parse_import_args(["2026-01", "force"]) == ("2026-01", True)
    period, force = parse_import_args(["2026", "rescan"])
    assert force is True
    assert len(period) == 7


@pytest.mark.asyncio
async def test_paste_does_not_mark_when_extract_errors(tmp_path):
    class LLM:
        async def extract_transaction(self, body, categories):
            return {"is_transaction": False, "extract_error": True}

    db = Database(str(tmp_path / "state.db"))
    service = FinanceImportService(db, LLM())
    result = await service.stage_paste("Transfer 10000", period_hint="2026-01")

    assert result["staged"] == 0
    assert result["extract_errors"] == 1
    assert result["skipped"] == 0
    assert not db.is_email_handled(upload_source_id(b"Transfer 10000"))


@pytest.mark.asyncio
async def test_paste_force_clears_false_skips(tmp_path):
    class LLM:
        async def extract_transaction(self, body, categories):
            return {
                "is_transaction": True,
                "amount": 10000,
                "type": "expense",
                "category": "Makanan",
                "subcategory": "Restoran",
                "merchant": "Warung",
                "description": "Makan",
                "transaction_date": "2026-01-15",
            }

        async def recommend_category(self, **kwargs):
            return {"category": "Makanan", "subcategory": "Restoran"}

    text = "Makan di warung 10000"
    source = upload_source_id(text.encode("utf-8"))
    db = Database(str(tmp_path / "state.db"))
    db.mark_email(source, "skipped", "bukan transaksi")
    service = FinanceImportService(db, LLM())

    without = await service.stage_paste(text, period_hint="2026-01")
    assert without["staged"] == 0
    assert without["skipped"] == 1

    with_force = await service.stage_paste(text, period_hint="2026-01", force=True)
    assert with_force["cleared_skips"] == 1
    assert with_force["staged"] == 1
    assert db.is_email_handled(source)


def test_latest_review_period_prefers_reviewing(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    db.get_or_create_import_batch("2026-01")
    db.set_import_batch_status("2026-01", "exported")
    db.get_or_create_import_batch("2026-07")
    service = FinanceImportService(db, None, None, None)
    assert service.latest_review_period() == "2026-07"


def test_mark_transaction_ok_rejects_invalid_category(tmp_path):
    from openpyxl import Workbook
    from core.spreadsheet_finance import SpreadsheetFinanceService

    categories = tmp_path / "categories.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Kategori Pengeluaran", "Subkategori"])
    sheet.append(["Makanan", "Restoran"])
    workbook.save(categories)

    db = Database(str(tmp_path / "state.db"))
    batch = db.get_or_create_import_batch("2026-07")
    tx_id = db.stage_transaction(batch["id"], {
        **_transaction("A:1"),
        "category": "BukanKategori",
        "subcategory": "-",
        "needs_review": 1,
    })
    service = FinanceImportService(
        db, None, None,
        spreadsheet_service=SpreadsheetFinanceService(
            str(tmp_path / "exports"), str(categories), str(tmp_path / "history.xlsx")
        ),
    )

    try:
        service.mark_transaction_ok(tx_id)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "belum valid" in str(exc)


@pytest.mark.asyncio
async def test_export_period_auto_approves_valid_rows(tmp_path):
    import json
    from openpyxl import Workbook
    from core.category_recommendations import CategoryRecommendationLookup
    from core.spreadsheet_finance import CategoryReference, SpreadsheetFinanceService

    categories = tmp_path / "categories.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Kategori Pengeluaran", "Subkategori"])
    sheet.append(["Makanan", "Restoran"])
    workbook.save(categories)
    rec = tmp_path / "recs.json"
    rec.write_text(json.dumps({"phrases": [], "keywords": []}), encoding="utf-8")

    db = Database(str(tmp_path / "state.db"))
    batch = db.get_or_create_import_batch("2026-01")
    db.stage_transaction(batch["id"], {
        **_transaction("A:ok"),
        "account": "Cash",
        "needs_review": 1,
    })
    service = FinanceImportService(
        db, None, None,
        spreadsheet_service=SpreadsheetFinanceService(
            str(tmp_path / "exports"), str(categories), str(tmp_path / "history.xlsx")
        ),
        category_recommendations=CategoryRecommendationLookup(
            str(rec), category_reference=CategoryReference(str(categories))
        ),
    )
    result = await service.export_period("2026-01", force=True, auto=True)
    assert result.rows == 1
    row = db.list_staged_transactions(batch["id"])[0]
    assert row["needs_review"] == 0


@pytest.mark.asyncio
async def test_export_period_rejects_needs_review_rows(tmp_path):
    from openpyxl import Workbook
    from core.spreadsheet_finance import SpreadsheetFinanceService

    categories = tmp_path / "categories.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Kategori Pengeluaran", "Subkategori"])
    sheet.append(["Makanan", "Restoran"])
    workbook.save(categories)

    db = Database(str(tmp_path / "state.db"))
    batch = db.get_or_create_import_batch("2026-07")
    db.stage_transaction(batch["id"], {
        **_transaction("A:bad"),
        "category": "Unknown",
        "needs_review": 1,
    })
    service = FinanceImportService(
        db, None, None,
        spreadsheet_service=SpreadsheetFinanceService(
            str(tmp_path / "exports"), str(categories), str(tmp_path / "history.xlsx")
        ),
    )

    try:
        await service.export_period("2026-07")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "perlu review" in str(exc)


def test_month_period_uses_transaction_date():
    assert month_period("Sat, 15 Jan 2026 10:00:00 +0700") == "2026-01"


@pytest.mark.asyncio
async def test_stage_from_sources_flags_possible_duplicate(tmp_path):
    class LLM:
        async def extract_transaction(self, body, categories):
            return {
                "is_transaction": True,
                "amount": 10000,
                "type": "expense",
                "category": "Makanan",
                "subcategory": "Restoran",
                "merchant": "Warung",
                "description": "Makan",
                "transaction_date": "2026-01-15",
            }

        async def recommend_category(self, **kwargs):
            return {"category": "Makanan", "subcategory": "Restoran"}

    service = FinanceImportService(Database(str(tmp_path / "state.db")), LLM())
    result = await service.stage_from_sources(
        [
            {"id": "A:1", "account": "Cash", "date": "", "from": "", "subject": "A", "body": "1"},
            {"id": "A:2", "account": "Cash", "date": "", "from": "", "subject": "B", "body": "2"},
        ],
        period_hint="2026-01",
    )
    rows = service.db.list_staged_transactions(result["batch_id"])

    assert result["staged"] == 2
    assert sum(row["possible_duplicate"] for row in rows) == 1


def test_edit_transaction_validates_and_updates_staging(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    batch = db.get_or_create_import_batch("2026-01")
    tx_id = db.stage_transaction(batch["id"], {
        "source_email_id": "A:1",
        "account": "A",
        "transaction_date": "2026-01-15",
        "amount": 10,
        "type": "expense",
        "category": "Lainnya",
        "merchant": "Toko",
        "description": "Lama",
    })
    service = FinanceImportService(db, None, None, None)

    message = service.edit_transaction(tx_id, "nominal", "25000")

    assert "25000" in message
    assert db.get_staged_transaction(tx_id)["amount"] == 25000


@pytest.mark.asyncio
async def test_export_period_writes_files_without_money_manager(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    batch = db.get_or_create_import_batch("2026-01")
    db.stage_transaction(batch["id"], {**_transaction("A:ok")})
    db.stage_transaction(batch["id"], {
        **_transaction("A:fail"),
        "merchant": "Fail",
        "description": "Fail",
    })

    from core.spreadsheet_finance import SpreadsheetFinanceService

    categories = tmp_path / "categories.xlsx"
    from openpyxl import Workbook
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Kategori Pengeluaran", "Subkategori"])
    sheet.append(["Makanan", "Restoran"])
    workbook.save(categories)
    service = FinanceImportService(
        db, None, None,
        spreadsheet_service=SpreadsheetFinanceService(
            str(tmp_path / "exports"), str(categories), str(tmp_path / "history.xlsx")
        ),
    )

    result = await service.export_period("2026-01")
    rows = db.list_staged_transactions(batch["id"])

    assert result.period == "2026-01"
    assert result.rows == 2
    assert "diekspor" in service.format_export_message(result)
    assert [row["status"] for row in rows] == ["pending", "pending"]
    assert not db.is_email_handled("A:ok")
    assert (tmp_path / "exports/2026-01.xlsx").exists()
    assert (tmp_path / "exports/2026-01.tsv").exists()


@pytest.mark.asyncio
async def test_stage_scanned_transaction_creates_editable_monthly_row(tmp_path):
    db = Database(str(tmp_path / "state.db"))
    service = FinanceImportService(db, None, None, spreadsheet_service=None)

    row_id = service.stage_scanned_transaction({
        "email_id": "A:scan", "account": "Pribadi", "transaction_date": "2026-07-16",
        "amount": 20000, "type": "expense", "category": "Makanan",
        "subcategory": "Cafe", "merchant": "FamilyMart", "description": "Kopi",
    })

    batch = db.get_import_batch("2026-07")
    row = db.get_staged_transaction(row_id)
    assert batch["status"] == "reviewing"
    assert row["category"] == "Makanan"
    assert row["subcategory"] == "Cafe"
    assert row["status"] == "pending"
    assert db.is_email_handled("A:scan")
