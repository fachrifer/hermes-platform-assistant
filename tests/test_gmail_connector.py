from datetime import date
from types import SimpleNamespace

from connectors.gmail import GmailConnector


def test_finance_range_query_is_inclusive(monkeypatch):
    monkeypatch.setattr(
        "connectors.gmail.settings.finance_email_senders",
        "bank.example",
        raising=False,
    )
    monkeypatch.setattr(
        "connectors.gmail.settings.finance_email_keywords",
        "transfer",
        raising=False,
    )
    connector = GmailConnector()
    query = connector._build_finance_range_query(date(2026, 1, 1), date(2026, 1, 31))
    assert "after:2025/12/31" in query
    assert "before:2026/02/01" in query
    assert "from:(bank.example)" in query
    assert '"transfer"' in query


def test_scan_merges_accounts_and_qualifies_ids(monkeypatch):
    class FakeAccount:
        def __init__(self, label):
            self.label = label
            self.credentials = object()

    def fake_load():
        return [FakeAccount("Pribadi"), FakeAccount("Kerja")]

    monkeypatch.setattr("connectors.gmail.load_all_credentials", fake_load)

    connector = GmailConnector()

    def fake_scan_account(account, start, end, max_results):
        return [{
            "id": f"{account.label}:m1",
            "account": account.label,
            "from": "a@x",
            "subject": "s",
            "date": "2026-01-02",
            "body": "Transfer 10000",
        }]

    monkeypatch.setattr(connector, "_scan_account_range", fake_scan_account)
    rows = connector.scan_finance_range(date(2026, 1, 1), date(2026, 1, 31))
    assert {r["account"] for r in rows} == {"Pribadi", "Kerja"}
    assert all(":" in r["id"] for r in rows)


def test_scan_skips_failed_account(monkeypatch):
    class FakeAccount:
        def __init__(self, label):
            self.label = label
            self.credentials = object()

    monkeypatch.setattr(
        "connectors.gmail.load_all_credentials",
        lambda: [FakeAccount("Pribadi"), FakeAccount("Kerja")],
    )
    connector = GmailConnector()

    def fake_scan_account(account, start, end, max_results):
        if account.label == "Pribadi":
            raise RuntimeError("boom")
        return [{
            "id": "Kerja:m2",
            "account": "Kerja",
            "from": "b@x",
            "subject": "s",
            "date": "2026-01-03",
            "body": "ok",
        }]

    monkeypatch.setattr(connector, "_scan_account_range", fake_scan_account)
    rows = connector.scan_finance_range(date(2026, 1, 1), date(2026, 1, 31))
    assert len(rows) == 1
    assert rows[0]["account"] == "Kerja"


def test_scan_account_skips_failed_message(monkeypatch):
    class FakeAccount:
        label = "Pribadi"
        credentials = object()

    class FakeMessages:
        def list(self, **kwargs):
            return SimpleNamespace(
                execute=lambda: {"messages": [{"id": "good"}, {"id": "bad"}]}
            )

        def get(self, **kwargs):
            if kwargs["id"] == "bad":
                raise RuntimeError("corrupt message")
            return SimpleNamespace(
                execute=lambda: {
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "a@x"},
                            {"name": "Subject", "value": "ok"},
                            {"name": "Date", "value": "Thu, 02 Jan 2026 10:00:00 +0000"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": "dGVzdA=="},
                    }
                }
            )

    class FakeUsers:
        def messages(self):
            return FakeMessages()

    class FakeService:
        def users(self):
            return FakeUsers()

    connector = GmailConnector()
    monkeypatch.setattr(connector, "_service", lambda creds: FakeService())

    rows = connector._scan_account_range(
        FakeAccount(), date(2026, 1, 1), date(2026, 1, 31), 100
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "Pribadi:good"
