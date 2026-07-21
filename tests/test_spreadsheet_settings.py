from config.settings import Settings


def test_spreadsheet_settings_defaults(monkeypatch):
    monkeypatch.delenv("FINANCE_ACCOUNT", raising=False)
    monkeypatch.delenv("FINANCE_EXPORT_DIR", raising=False)
    value = Settings()
    assert value.finance_account == "Cash"
    assert value.finance_export_dir == "/app/data/finance"


def test_finance_reference_paths_fall_back_to_repo_files(monkeypatch, tmp_path):
    monkeypatch.setenv("FINANCE_CATEGORY_REFERENCE", str(tmp_path / "missing-categories.xlsx"))
    monkeypatch.setenv("FINANCE_HISTORY_WORKBOOK", str(tmp_path / "missing-history.xlsx"))
    value = Settings()
    assert value.finance_category_reference.endswith("Kategori Money Manager.xlsx")
    assert value.finance_history_workbook.endswith("MoneyManager-2025.xlsx")


def test_outlook_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MS_OUTLOOK_ENABLED", raising=False)
    assert Settings().ms_outlook_enabled is False
