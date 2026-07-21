from config import persona
from config.settings import Settings
from core import telegram_bot


def test_nyx_prompt_identifies_nyx_and_preserves_address(monkeypatch):
    monkeypatch.setenv("HERMES_PERSONA", "nyx")
    monkeypatch.setenv("HERMES_ADDRESS", "Master")
    monkeypatch.setattr(persona, "settings", Settings())

    prompt = persona.get_system_prompt()

    assert "Nyx Assistant" in prompt
    assert "Master" in prompt
    assert "oracle" in prompt.casefold()
    assert "Jangan mengarang data" in prompt
    assert "titah" in prompt.casefold()  # listed as forbidden diction
    assert "kulaksanakan" in prompt
    assert "KEBIJAKAN KERAHASIAAN" in prompt


def test_nyx_start_message_uses_active_persona(monkeypatch):
    monkeypatch.setattr(telegram_bot.settings, "persona_name", "nyx")
    monkeypatch.setattr(telegram_bot.settings, "address", "Master")

    message = telegram_bot.get_start_message()

    assert "Nyx Assistant" in message
    assert "squire" not in message
    assert "Master" in message


def test_nyx_is_default_persona(monkeypatch):
    monkeypatch.delenv("HERMES_PERSONA", raising=False)
    assert Settings().persona_name == "nyx"


def test_active_persona_display_name(monkeypatch):
    monkeypatch.setattr(persona.settings, "persona_name", "nyx")
    assert persona.display_name() == "Nyx Assistant"


def test_nyx_help_and_forget_avoid_squire_diction(monkeypatch):
    monkeypatch.setattr(telegram_bot.settings, "persona_name", "nyx")
    help_text = telegram_bot.get_help_text()
    assert "Titah" not in help_text
    assert "Perintah yang tersedia" in help_text
