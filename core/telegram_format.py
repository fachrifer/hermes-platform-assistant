"""Safe formatting and delivery helpers for Telegram messages."""

from __future__ import annotations

import html
import re
from collections.abc import Awaitable, Callable
from typing import Any

from telegram.error import TelegramError


def format_telegram(text: str) -> str:
    """Convert Hermes' small Markdown subset to escaped Telegram HTML."""
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<b>\1</b>", escaped)
    escaped = re.sub(r"_([^_\n]+?)_", r"<i>\1</i>", escaped)
    return escaped


async def send_formatted(
    sender: Callable[..., Awaitable[Any]], text: str, **kwargs: Any
) -> Any:
    """Send HTML-formatted text and retry plain text on Telegram parse errors."""
    try:
        return await sender(text=format_telegram(text), parse_mode="HTML", **kwargs)
    except TelegramError:
        return await sender(text=text, **kwargs)
