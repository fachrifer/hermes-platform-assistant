def test_attachment_handler_exists():
    from core.telegram_bot import TelegramInterface

    assert hasattr(TelegramInterface, "on_attachment")
