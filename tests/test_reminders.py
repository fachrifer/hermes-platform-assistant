from core.reminders import build_reminders_payload, parse_reminders


def test_no_reminder_request_returns_empty():
    assert parse_reminders("Rapat dengan client") == []
    assert build_reminders_payload([]) is None


def test_unspecified_method_defaults_to_popup():
    assert parse_reminders("ingatkan 30 menit sebelumnya") == [
        {"method": "popup", "minutes": 30}
    ]


def test_explicit_email_and_multiple_reminders():
    assert parse_reminders("ingatkan 1 jam sebelumnya lewat email dan 15 menit sebelumnya popup") == [
        {"method": "email", "minutes": 60},
        {"method": "popup", "minutes": 15},
    ]
