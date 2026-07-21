from core.telegram_bot import TelegramInterface, get_help_text


def test_help_text_is_nyx_not_squire():
    text = get_help_text()
    assert "Perintah yang tersedia" in text
    assert "Titah" not in text
    assert "/batch" in text
    assert "tsv" in text.casefold()


def test_batch_helpers_exist_on_interface():
    assert hasattr(TelegramInterface, "cmd_batch")
    assert hasattr(TelegramInterface, "_send_batch_card")
    assert hasattr(TelegramInterface, "_on_batch_callback")
    assert hasattr(TelegramInterface, "_send_export_files")
    assert hasattr(TelegramInterface, "_clear_stale_finance_state")


def test_format_batch_card_shows_review_progress():
    bot = TelegramInterface(agent=None, briefing=None)
    text = bot._format_batch_card(
        {
            "id": 12,
            "transaction_date": "2026-07-15",
            "type": "expense",
            "amount": 23000,
            "category": "Makanan",
            "subcategory": "Cafe",
            "merchant": "Kopi",
            "account": "Cash",
            "description": "Latte",
            "needs_review": 1,
            "possible_duplicate": 0,
        },
        index=1,
        total=7,
        period="2026-07",
    )
    assert "Review 1/7 — 2026-07" in text
    assert "[12]" in text
    assert "Date: 2026-07-15" in text
    assert "Account: Cash" in text
    assert "Note: Kopi" in text
    assert "Description: Latte" in text
    assert "Local lookup" in text or "Final recommendation" in text
    assert "Needs manual review" in text
    assert "Merchant:" not in text
    assert "Akun:" not in text


def test_format_batch_card_transfer_uses_from_to():
    bot = TelegramInterface(agent=None, briefing=None)
    text = bot._format_batch_card(
        {
            "id": 3,
            "transaction_date": "2025-09-02",
            "type": "transfer",
            "amount": 4002500,
            "category": "Tabungan - Istri",
            "subcategory": "-",
            "merchant": "Nurizka Khoerani",
            "account": "Cash",
            "description": "Transfer dengan BI Fast ke NURIZKA KHOERANI",
            "needs_review": 0,
            "possible_duplicate": 0,
        },
        index=2,
        total=7,
        period="2025-09",
    )
    assert "Type: Transfer" in text
    assert "From: Cash" in text
    assert "To: Tabungan - Istri" in text
    assert "Note: Nurizka Khoerani" in text
    assert "Change to Expense" in text
    assert "Keep as Transfer" in text


def test_transfer_keyboard_offers_expense_conversion():
    bot = TelegramInterface(agent=None, briefing=None)
    markup = bot._batch_card_keyboard(
        "abc",
        {"type": "transfer", "category": "Tabungan - Istri"},
    )
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "→ Expense" in " ".join(labels) or any("Expense" in label for label in labels)
    assert any("Keep Transfer" in label for label in labels)
