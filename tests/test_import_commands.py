from core.telegram_bot import TelegramInterface


def test_historical_import_commands_are_registered_on_interface():
    assert hasattr(TelegramInterface, "cmd_import")
    assert hasattr(TelegramInterface, "cmd_batch")
    assert hasattr(TelegramInterface, "cmd_edit")
