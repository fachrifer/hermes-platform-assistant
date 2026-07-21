from core.db import Database
from connectors.agenda_manual import ManualAgendaConnector


def test_add_verified_reads_back_saved_row(tmp_path):
    agenda = ManualAgendaConnector(Database(str(tmp_path / "state.db")))

    saved = agenda.add_verified("Rapat", "2026-07-20T10:00:00+07:00", "Catatan")

    assert saved["title"] == "Rapat"
    assert saved["when_at"] == "2026-07-20T10:00:00+07:00"
