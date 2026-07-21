import pytest
from telegram.error import TelegramError

from core.telegram_format import format_telegram, send_formatted


def test_double_star_bold_becomes_html_bold():
    assert format_telegram("**Penting**") == "<b>Penting</b>"


def test_single_star_and_underscore_are_converted():
    assert format_telegram("*Judul* _catatan_") == "<b>Judul</b> <i>catatan</i>"


def test_special_characters_are_escaped():
    assert format_telegram("A & B < C") == "A &amp; B &lt; C"


@pytest.mark.asyncio
async def test_send_formatted_retries_plain_text_after_telegram_error():
    calls = []

    async def sender(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TelegramError("parse error")
        return "sent"

    result = await send_formatted(sender, "**Penting**")

    assert result == "sent"
    assert calls == [
        {"text": "<b>Penting</b>", "parse_mode": "HTML"},
        {"text": "**Penting**"},
    ]
