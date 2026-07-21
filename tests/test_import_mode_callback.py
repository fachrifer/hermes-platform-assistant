from core.telegram_bot import build_import_mode_keyboard, parse_import_mode_callback


def test_import_mode_keyboard_callback_data():
    markup = build_import_mode_keyboard("2026-07", True)
    data = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "import_mode:gmail:2026-07:1" in data
    assert "import_mode:paste:2026-07:0" in data


def test_parse_import_mode_callback():
    assert parse_import_mode_callback("import_mode:gmail:2026-07:1") == {
        "mode": "gmail",
        "period": "2026-07",
        "force": True,
    }
    assert parse_import_mode_callback("import_mode:paste:2026-01:0")["mode"] == "paste"
    assert parse_import_mode_callback("batch_ok:x") is None
