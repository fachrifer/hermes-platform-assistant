def test_calendar_reminder_callback_handler_exists():
    from core.telegram_bot import TelegramInterface

    assert hasattr(TelegramInterface, "_on_calendar_reminder_callback")
