"""Telegram interface (polling) for Hermes.

    Runs in-process inside the FastAPI lifespan. Provides commands (/start, /help,
    /brief, /status, /agenda, /export, /finance, /advice, /forget) plus natural-language
routing through the agent. All replies carry Hermes' persona.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from config.settings import settings
from config.persona import display_name
from core.agenda_extraction import parse_agenda_text
from core.finance_import import (
    InvalidCategoryError,
    normalize_finance_account,
    parse_export_args,
    parse_import_command,
)
from core.spreadsheet_finance import _key
from core.telegram_format import send_formatted

logger = logging.getLogger("hermes.telegram")


def build_import_mode_keyboard(period: str, force: bool) -> InlineKeyboardMarkup:
    force_flag = "1" if force else "0"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📧 Gmail", callback_data=f"import_mode:gmail:{period}:{force_flag}"),
        InlineKeyboardButton("📋 Paste / Upload", callback_data=f"import_mode:paste:{period}:0"),
    ]])


def parse_import_mode_callback(data: str) -> dict | None:
    if not data.startswith("import_mode:"):
        return None
    parts = data.split(":")
    if len(parts) != 4:
        return None
    _, mode, period, force_s = parts
    if mode not in {"gmail", "paste"}:
        return None
    return {
        "mode": mode,
        "period": period,
        "force": force_s == "1",
    }


_BATCH_EDIT_FIELDS = (
    ("date", "Date"),
    ("amount", "Amount"),
    ("type", "Type"),
    ("account", "Account / From"),
    ("category", "Category / To"),
    ("subcategory", "Subcategory"),
    ("note", "Note"),
    ("description", "Description"),
)


def get_start_message() -> str:
    """Return the welcome message for the active assistant persona."""
    name = display_name()
    return (
        f"🌌 Selamat datang, {settings.address}. {name} aktif dan siap membantu.\n"
        "Ketik /help untuk melihat perintah yang tersedia."
    )


def get_help_text() -> str:
    """Return slash-command help in Nyx voice (no squire diction)."""
    return (
        "Perintah yang tersedia:\n"
        "/brief — laporan lengkap (agenda, keuangan, status)\n"
        "/status — status layanan/konektor\n"
        "/office [daily|weekly|monthly|status] — laporan platform kantor\n"
        "/quota — cek quota Tavily dan Gemini\n"
        "/import [YYYY-MM] — pilih Gmail atau paste/upload ke staging\n"
        "/import gmail|paste YYYY-MM [force] — langsung ke mode tertentu\n"
        "/import done — tutup sesi import\n"
        "/batch [YYYY-MM|list] — review batch satu per satu\n"
        "/edit <id> <field> <value> — edit transaksi staging\n"
        "/export YYYY-MM [tsv] [force] [auto] — buat & kirim TSV Money Manager\n"
        "  (auto = tanpa review interaktif)\n"
        "/finance YYYY-MM — laporan dari spreadsheet\n"
        "/agenda — lihat agenda | /agenda tambah <judul> | /agenda hapus <id>\n"
        "/advice — analisis & saran keuangan\n"
        "/forget — lupakan konteks percakapan\n\n"
        "Setelah /import paste, kirim teks transaksi atau .txt/.eml/.html/PDF/gambar.\n"
        "Atau bicara biasa, mis. 'catat kopi 25000'."
    )


class TelegramInterface:
    def __init__(self, agent, briefing):
        self.agent = agent
        self.briefing = briefing
        self.app: Application | None = None
        # Pending scanned transactions awaiting confirmation: token -> tx dict.
        self.pending: dict[str, dict] = {}
        self.pending_latest: dict[str, dict] = {}
        self.pending_recent: dict[str, list[dict]] = {}
        self.pending_confirmations: dict[str, dict] = {}
        self.pending_agenda: dict[str, dict] = {}
        self.pending_calendar_reminders: dict[str, dict] = {}
        self.pending_batch_review: dict[str, dict] = {}
        self.pending_batch_edit: dict[str, dict] = {}
        self.pending_category_picks: dict[str, dict] = {}
        # chat_id -> {period, staged, skipped, extract_errors, force}
        self.import_sessions: dict[str, dict] = {}

    def _is_authorized(self, update: Update) -> bool:
        chat = update.effective_chat
        allowed = settings.is_telegram_chat_allowed(chat.id if chat else None)
        if not allowed:
            logger.warning(
                "Tolak akses Telegram dari chat_id=%s user=%s",
                getattr(chat, "id", None),
                getattr(update.effective_user, "id", None),
            )
        return allowed

    async def _deny_unauthorized(self, update: Update) -> None:
        message = update.effective_message
        chat = update.effective_chat
        chat_id = getattr(chat, "id", None)
        if message:
            await message.reply_text(
                "⚠️ Akses ditolak. Chat ini belum ada di allowlist Nyx.\n\n"
                f"Chat ID Anda: `{chat_id}`\n"
                "Tambahkan ke `.env`:\n"
                f"TELEGRAM_ALLOWED_CHAT_IDS={chat_id}\n"
                "TELEGRAM_CHAT_ID={chat_id}\n\n"
                "Lalu restart agent. "
                "(Jangan pakai angka dari token bot — itu ID bot, bukan chat Anda.)"
            )

    # ------------------------------------------------------------------
    def build(self) -> Application:
        app = Application.builder().token(settings.telegram_token).build()
        app.add_handler(TypeHandler(Update, self.guard_access), group=-1)
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("brief", self.cmd_brief))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("office", self.cmd_office))
        app.add_handler(CommandHandler("quota", self.cmd_quota))
        app.add_handler(CommandHandler("import", self.cmd_import))
        app.add_handler(CommandHandler("batch", self.cmd_batch))
        app.add_handler(CommandHandler("edit", self.cmd_edit))
        app.add_handler(CommandHandler("export", self.cmd_export))
        app.add_handler(CommandHandler("finance", self.cmd_finance))
        app.add_handler(CommandHandler("agenda", self.cmd_agenda))
        app.add_handler(CommandHandler("advice", self.cmd_advice))
        app.add_handler(CommandHandler("forget", self.cmd_forget))
        app.add_handler(CallbackQueryHandler(self.on_callback))
        app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, self.on_attachment))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))
        self.app = app
        return app

    async def guard_access(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if self._is_authorized(update):
            return
        if update.callback_query:
            await update.callback_query.answer("Akses ditolak", show_alert=True)
        else:
            await self._deny_unauthorized(update)
        raise ApplicationHandlerStop

    # ------------------------------------------------------------------
    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(get_start_message())

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(get_help_text())

    async def cmd_brief(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.chat.send_action("typing")
        text = await self.briefing.build()
        await send_formatted(update.message.reply_text, text)

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        health = await self.agent.health_check_all()
        lines = [f"🔌 Status layanan, {settings.address}:"]
        for name, ok in health.items():
            lines.append(f"  {'✅' if ok else '❌'} {name}")
        await update.message.reply_text("\n".join(lines))

    async def cmd_office(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        report_type = (ctx.args[0].casefold() if ctx.args else "status")
        if report_type not in {"status", "daily", "weekly", "monthly"} or len(ctx.args or []) > 1:
            await update.message.reply_text(
                "Format: /office [daily|weekly|monthly|status]"
            )
            return
        await update.message.reply_text(self.agent.office_report(report_type))

    async def cmd_quota(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(self.agent.quota_report())

    def _open_import_paste_session(self, chat_id: str, period: str, force: bool) -> str:
        cleared = 0
        if force:
            cleared = self.agent.db.clear_skipped_emails(summary="bukan transaksi")
        self.import_sessions[chat_id] = {
            "period": period,
            "force": False,
            "staged": 0,
            "skipped": 0,
            "extract_errors": 0,
        }
        lines = [
            f"📥 Sesi import {period} aktif.",
            "Paste teks transaksi, atau kirim .txt / .eml / .html / PDF / gambar.",
            "Hermes akan mengekstrak, memformat, dan merekomendasikan kategori.",
            "Selesai: /import done · Batal: /import cancel",
        ]
        if cleared:
            lines.append(f"Skip lama dihapus (force): {cleared}")
        return "\n".join(lines)

    async def _reply_gmail_import(self, message, period: str, force: bool) -> None:
        await message.reply_text(f"📧 Memindai Gmail untuk {period}…")
        try:
            result = await self.agent.finance_import.import_period(period, force=force)
        except Exception as exc:  # noqa: BLE001
            await message.reply_text(f"⚠️ {exc}\nCoba /import paste {period}")
            return
        await message.reply_text(
            f"📥 Import Gmail {period} selesai.\n"
            f"Masuk staging: {result.get('staged', 0)}\n"
            f"Dilewati: {result.get('skipped', 0)}\n"
            f"Gagal ekstrak: {result.get('extract_errors', 0)}\n\n"
            f"Review: /batch {period}"
        )

    async def cmd_import(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = str(update.effective_chat.id)
        try:
            cmd = parse_import_command(list(ctx.args or []))
        except ValueError as exc:
            await update.message.reply_text(
                f"⚠️ {exc}\n"
                "Contoh:\n/import\n/import 2026-01\n/import 2026-01 force\n"
                "/import gmail 2026-01 force\n/import paste 2026-01\n"
                "/import done\n/import cancel"
            )
            return

        if cmd["action"] == "done":
            session = self.import_sessions.pop(chat_id, None)
            if not session:
                await update.message.reply_text("Tidak ada sesi /import yang aktif.")
                return
            period = session["period"]
            await update.message.reply_text(
                f"✅ Sesi import {period} ditutup.\n"
                f"Baru masuk staging: {session.get('staged', 0)}\n"
                f"Dilewati/duplikat: {session.get('skipped', 0)}\n"
                f"Gagal ekstrak: {session.get('extract_errors', 0)}\n\n"
                f"Review: /batch {period}\n"
                f"Atau: /export {period} tsv force auto"
            )
            return

        if cmd["action"] == "cancel":
            self.import_sessions.pop(chat_id, None)
            await update.message.reply_text("Sesi /import dibatalkan.")
            return

        if cmd["action"] == "choose_mode":
            await update.message.reply_text(
                f"📥 Import {cmd['period']}\nPilih sumber:",
                reply_markup=build_import_mode_keyboard(cmd["period"], cmd["force"]),
            )
            return

        if cmd["action"] == "start_gmail":
            await self._reply_gmail_import(update.message, cmd["period"], cmd["force"])
            return

        if cmd["action"] == "start_paste":
            text = self._open_import_paste_session(chat_id, cmd["period"], cmd["force"])
            await update.message.reply_text(text)
            return

    async def _stage_import_result(self, message, chat_id: str, result: dict) -> None:
        session = self.import_sessions.get(chat_id)
        if session:
            session["staged"] += int(result.get("staged") or 0)
            session["skipped"] += int(result.get("skipped") or 0)
            session["extract_errors"] += int(result.get("extract_errors") or 0)
            period = session["period"]
        else:
            period = result.get("period") or "——"

        if result.get("extract_errors"):
            await message.reply_text(
                "⚠️ Gagal ekstrak (kuota/API Gemini). Coba lagi sebentar — belum ditandai skip."
            )
            return
        if result.get("staged"):
            row = result.get("last_row") or {}
            amount = f"Rp{int(row.get('amount') or 0):,.0f}".replace(",", ".")
            await message.reply_text(
                f"✅ Masuk staging [{row.get('id', '?')}]\n"
                f"Date: {row.get('transaction_date', '')}\n"
                f"Type: {row.get('type', '')}\n"
                f"Category: {row.get('category', '')} / {row.get('subcategory', '-')}\n"
                f"Note: {row.get('merchant', '')}\n"
                f"Amount: {amount}\n"
                f"Description: {row.get('description', '')}\n\n"
                f"Lanjut paste, atau /import done → /batch {period}"
            )
            return
        if result.get("skipped"):
            await message.reply_text("⏭️ Sudah pernah diproses atau bukan transaksi.")
            return
        await message.reply_text("⚠️ Tidak ada transaksi yang masuk staging.")

    async def cmd_batch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args or []
        service = self.agent.finance_import
        chat_id = str(update.effective_chat.id)

        if args and args[0].casefold() == "list":
            await self._reply_batch_list(update.message)
            return

        if not args:
            period = service.latest_review_period()
            if not period:
                await update.message.reply_text(
                    "📦 Belum ada batch. Gunakan /import 2026 atau /import 2026-01."
                )
                return
        elif len(args) == 1:
            period = args[0]
        else:
            await update.message.reply_text("Format: /batch | /batch YYYY-MM | /batch list")
            return

        try:
            batch = service.get_batch(period)
        except ValueError as exc:
            await update.message.reply_text(f"⚠️ {exc}\nGunakan /batch list untuk melihat daftar.")
            return

        if not batch["transactions"]:
            await update.message.reply_text(f"📦 Batch {period} kosong.")
            return

        self._clear_stale_finance_state(chat_id)
        await update.message.reply_text(
            f"🧠 Menyiapkan rekomendasi kategori (lookup lokal dari "
            f"category_recommendations.json) untuk batch {period}…"
        )
        try:
            recommendation = await service.recommend_categories_for_batch(period)
        except ValueError as exc:
            await update.message.reply_text(f"⚠️ {exc}")
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gagal menyiapkan rekomendasi batch %s", period)
            await update.message.reply_text(
                f"⚠️ Gagal menyiapkan rekomendasi kategori: {exc}"
            )
            return
        batch = service.get_batch(period)
        if not batch["transactions"]:
            await update.message.reply_text(f"📦 Batch {period} kosong.")
            return

        token = uuid.uuid4().hex[:12]
        self.pending_batch_review[token] = {
            "period": period,
            "items": [row["id"] for row in batch["transactions"]],
            "index": 0,
            "total": batch["count"],
            "ok": 0,
            "skipped": 0,
            "message": update.message,
            "chat_id": chat_id,
        }
        await update.message.reply_text(
            f"📦 Review batch {period} — {batch['count']} transaksi.\n"
            f"Rekomendasi diperbarui: {recommendation['updated']}\n"
            f"Perlu perhatian ekstra: {recommendation['still_needs_review']}\n"
            "Saya tampilkan satu per satu. Terima rekomendasi dengan OK, atau Ubah jika perlu."
        )
        await self._send_batch_card(token)

    async def _reply_batch_list(self, message) -> None:
        batches = self.agent.finance_import.list_batches()
        if not batches:
            await message.reply_text("📦 Belum ada batch. Gunakan /import 2026 atau /import 2026-01.")
            return
        lines = [
            "📦 Daftar batch (staging saja, belum mengubah Money Manager):",
            "Mulai review: /batch atau /batch YYYY-MM",
            "",
        ]
        for batch in batches:
            lines.append(
                f"{batch['period']} — {batch['count']} transaksi — {batch['status']} "
                f"(review {batch['needs_review']}, duplikat {batch['possible_duplicate']})"
            )
        await message.reply_text("\n".join(lines))

    def _clear_stale_finance_state(self, chat_id: str) -> None:
        self.pending_confirmations.pop(chat_id, None)
        self.pending_latest.pop(chat_id, None)
        self.pending_batch_edit.pop(chat_id, None)
        self.pending_category_picks.pop(chat_id, None)

    def _category_suggestion_keyboard(
        self,
        token: str,
        suggestions: list[tuple[str, str]],
        *,
        include_back: bool = True,
    ) -> InlineKeyboardMarkup:
        buttons = [
            InlineKeyboardButton(
                f"{category} / {subcategory}"[:64],
                callback_data=f"batch_pick_cat:{token}:{index}",
            )
            for index, (category, subcategory) in enumerate(suggestions)
        ]
        rows = [buttons[i:i + 1] for i in range(len(buttons))]
        footer = []
        if include_back:
            footer.append(InlineKeyboardButton("⬅️ Back", callback_data=f"batch_back:{token}"))
        footer.append(InlineKeyboardButton("✏️ Edit again", callback_data=f"batch_field:{token}:category"))
        rows.append(footer)
        return InlineKeyboardMarkup(rows)

    async def _reply_invalid_category(
        self,
        *,
        chat_id: str,
        token: str,
        transaction_id: int,
        error: InvalidCategoryError,
        message,
        edit_query=None,
    ) -> None:
        suggestions = list(error.suggestions or [])
        self.pending_category_picks[chat_id] = {
            "token": token,
            "transaction_id": transaction_id,
            "suggestions": suggestions,
            "attempted": error.attempted,
        }
        lines = [
            f"⚠️ Invalid category (cannot import to Money Manager):",
            f"{error.attempted}",
            "",
            "Suggested valid pairs — tap one to apply:",
        ]
        if not suggestions:
            lines.append("(no close matches found — pick Edit again)")
        text = "\n".join(lines)
        markup = self._category_suggestion_keyboard(token, suggestions)
        if edit_query is not None:
            await edit_query.edit_message_text(text, reply_markup=markup)
        else:
            await message.reply_text(text, reply_markup=markup)

    def _format_batch_card(
        self,
        row: dict,
        index: int,
        total: int,
        period: str,
        validation: dict | None = None,
    ) -> str:
        tx_type = str(row.get("type") or "expense").casefold()
        if tx_type == "income":
            kind = "Income"
        elif tx_type == "transfer":
            kind = "Transfer"
        else:
            kind = "Expense"
        amount = f"Rp{int(row['amount']):,.0f}".replace(",", ".")
        flags = []
        if row.get("needs_review"):
            flags.append("⚠️ Needs manual review")
        if row.get("possible_duplicate"):
            flags.append("🔁 Possible duplicate")
        flag_text = ("\n" + " · ".join(flags)) if flags else ""

        lookup_category = (validation or {}).get("lookup_category", row["category"])
        lookup_subcategory = (validation or {}).get("lookup_subcategory", row.get("subcategory") or "-")
        final_category = (validation or {}).get("category", row["category"])
        final_subcategory = (validation or {}).get("subcategory", row.get("subcategory") or "-")
        agreed = (validation or {}).get("agreed")
        note = (validation or {}).get("note") or ""

        if tx_type == "transfer":
            expense = (validation or {}).get("expense_suggestion") or {}
            expense_cat = expense.get("category") or "Keluarga"
            expense_sub = expense.get("subcategory") or "Istri"
            expense_note = expense.get("note") or (
                "Bulanan Istri" if _key(expense_sub) == "istri" else
                "Bulanan Mama" if "tua" in _key(expense_sub) else "-"
            )
            recommend_block = (
                "💡 Recommendation before you decide (no typing needed):\n"
                f"A) Keep as Transfer → To: {row.get('category') or '-'}\n"
                f"B) Change to Expense (living cost)\n"
                f"   Category: {expense_cat} / {expense_sub}\n"
                f"   Note: {expense_note}\n"
                "Tap Keep Transfer or → Expense below."
            )
        elif validation is None:
            recommend_block = (
                f"🧠 Local lookup: {lookup_category} / {lookup_subcategory}\n"
                "🔎 LLM validation: not run yet\n"
                f"📌 Final recommendation: {final_category} / {final_subcategory}\n"
            )
        else:
            if agreed and (lookup_category, lookup_subcategory) == (final_category, final_subcategory):
                validation_line = "🔎 LLM validation: agrees with lookup"
            else:
                validation_line = (
                    f"🔎 LLM validation: {final_category} / {final_subcategory}"
                    + (f" — {note}" if note else "")
                )
            recommend_block = (
                f"🧠 Local lookup: {lookup_category} / {lookup_subcategory}\n"
                f"{validation_line}\n"
                f"📌 Final recommendation: {final_category} / {final_subcategory}\n"
            )

        account = normalize_finance_account(row.get("account"))
        category = row.get("category") or "-"
        subcategory = row.get("subcategory") or "-"
        if tx_type == "transfer":
            detail_block = (
                f"Date: {row.get('transaction_date') or '-'}\n"
                f"Type: Transfer\n"
                f"From: {account}\n"
                f"To: {category}\n"
                f"Amount: {amount}\n"
                f"Note: {row.get('merchant') or '-'}\n"
                f"Description: {row.get('description') or '-'}"
            )
            decision = "Final decision: Keep Transfer / → Expense / Edit / Skip."
        else:
            detail_block = (
                f"Date: {row.get('transaction_date') or '-'}\n"
                f"Type: {kind}\n"
                f"Account: {account}\n"
                f"Category: {category} / {subcategory}\n"
                f"Amount: {amount}\n"
                f"Note: {row.get('merchant') or '-'}\n"
                f"Description: {row.get('description') or '-'}"
            )
            decision = "Final decision is yours: OK / Edit / Skip."
            reference = None
            try:
                reference = self.agent.finance_import.spreadsheet.category_reference
            except Exception:  # noqa: BLE001
                reference = None
            if reference is not None and reference.canonical(category, subcategory) is None:
                suggestions = reference.suggest(category, subcategory, limit=4)
                local = None
                try:
                    local = self.agent.finance_import.category_recommendations.recommend(
                        str(row.get("merchant") or ""),
                        str(row.get("description") or ""),
                    )
                except Exception:  # noqa: BLE001
                    local = None
                if local and local not in suggestions:
                    suggestions = [local, *suggestions][:4]
                suggest_lines = "\n".join(
                    f"  • {item[0]} / {item[1]}" for item in suggestions
                ) or "  • (no suggestions)"
                recommend_block = (
                    f"{recommend_block}\n"
                    f"⚠️ Current Category is INVALID for import:\n"
                    f"{category} / {subcategory}\n"
                    f"Suggested fixes (use buttons below or Edit):\n{suggest_lines}\n"
                )

        return (
            f"📦 Review {index}/{total} — {period}\n"
            f"[{row['id']}]\n"
            f"{detail_block}{flag_text}\n\n"
            f"{recommend_block}\n\n"
            f"{decision}"
        )

    def _invalid_category_suggestions(self, row: dict) -> list[tuple[str, str]]:
        if str(row.get("type") or "").casefold() == "transfer":
            return []
        try:
            reference = self.agent.finance_import.spreadsheet.category_reference
        except Exception:  # noqa: BLE001
            return []
        category = str(row.get("category") or "-")
        subcategory = str(row.get("subcategory") or "-")
        if reference.canonical(category, subcategory) is not None:
            return []
        suggestions = reference.suggest(category, subcategory, limit=4)
        try:
            local = self.agent.finance_import.category_recommendations.recommend(
                str(row.get("merchant") or ""),
                str(row.get("description") or ""),
            )
        except Exception:  # noqa: BLE001
            local = None
        if local and local not in suggestions:
            suggestions = [local, *suggestions][:4]
        return suggestions

    def _batch_card_keyboard(self, token: str, row: dict | None = None) -> InlineKeyboardMarkup:
        row = row or {}
        tx_type = str(row.get("type") or "").casefold()
        if tx_type == "transfer":
            return InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Keep Transfer", callback_data=f"batch_ok:{token}"),
                    InlineKeyboardButton("💸 → Expense", callback_data=f"batch_as_expense:{token}"),
                ],
                [
                    InlineKeyboardButton("✏️ Edit", callback_data=f"batch_edit:{token}"),
                    InlineKeyboardButton("⏭️ Skip", callback_data=f"batch_skip:{token}"),
                    InlineKeyboardButton("📋 List", callback_data=f"batch_list:{token}"),
                ],
            ])

        suggestions = self._invalid_category_suggestions(row)
        if suggestions:
            chat_id = None
            state = self.pending_batch_review.get(token)
            if state:
                chat_id = state.get("chat_id")
                self.pending_category_picks[chat_id] = {
                    "token": token,
                    "transaction_id": row.get("id"),
                    "suggestions": suggestions,
                    "attempted": f"{row.get('category') or '-'} / {row.get('subcategory') or '-'}",
                }
            pick_rows = [
                [InlineKeyboardButton(
                    f"✅ {category} / {subcategory}"[:64],
                    callback_data=f"batch_pick_cat:{token}:{index}",
                )]
                for index, (category, subcategory) in enumerate(suggestions)
            ]
            return InlineKeyboardMarkup([
                *pick_rows,
                [
                    InlineKeyboardButton("✏️ Edit", callback_data=f"batch_edit:{token}"),
                    InlineKeyboardButton("⏭️ Skip", callback_data=f"batch_skip:{token}"),
                    InlineKeyboardButton("📋 List", callback_data=f"batch_list:{token}"),
                ],
            ])

        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ OK", callback_data=f"batch_ok:{token}"),
            InlineKeyboardButton("✏️ Edit", callback_data=f"batch_edit:{token}"),
            InlineKeyboardButton("⏭️ Skip", callback_data=f"batch_skip:{token}"),
            InlineKeyboardButton("📋 List", callback_data=f"batch_list:{token}"),
        ]])

    async def _send_batch_card(self, token: str) -> None:
        state = self.pending_batch_review.get(token)
        if not state:
            return
        if state["index"] >= len(state["items"]):
            await self._finish_batch_review(token)
            return
        row_id = state["items"][state["index"]]
        row = self.agent.finance_import.db.get_staged_transaction(row_id)
        if not row:
            state["index"] += 1
            await self._send_batch_card(token)
            return

        await state["message"].reply_text(
            f"🔎 Preparing recommendation dialog for [{row_id}]…"
        )
        validation = await self.agent.finance_import.validate_category_for_row(row)
        row = self.agent.finance_import.db.get_staged_transaction(row_id) or row
        text = self._format_batch_card(
            row,
            state["index"] + 1,
            state["total"],
            state["period"],
            validation=validation,
        )
        await state["message"].reply_text(
            text, reply_markup=self._batch_card_keyboard(token, row)
        )

    async def _finish_batch_review(self, token: str) -> None:
        state = self.pending_batch_review.pop(token, None)
        if not state:
            return
        period = state["period"]
        try:
            batch = self.agent.finance_import.get_batch(period)
            remaining = batch["needs_review"]
        except ValueError:
            remaining = 0
        text = (
            f"✅ Review {period} selesai.\n"
            f"OK: {state['ok']} · Dilewati: {state['skipped']} · Masih perlu review: {remaining}\n"
            f"Export TSV untuk Money Manager: /export {period} tsv"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "📤 Export TSV",
                callback_data=f"batch_export:{period}",
            )
        ]])
        await state["message"].reply_text(text, reply_markup=keyboard)

    async def _on_batch_callback(self, query, data: str) -> None:
        if data.startswith("batch_export:"):
            period = data.split(":", 1)[1]
            await self._send_export_files(query.message, period, force=False, mode="tsv")
            await query.edit_message_reply_markup(reply_markup=None)
            return

        action, _, token = data.partition(":")
        if action == "batch_pick_cat":
            # batch_pick_cat:token:index
            parts = data.split(":")
            if len(parts) != 3:
                await query.edit_message_text("⚠️ Pilihan kategori tidak valid.")
                return
            _, token, index_raw = parts
            state = self.pending_batch_review.get(token)
            if not state:
                await query.edit_message_text("⚠️ Review batch ini sudah selesai atau kedaluwarsa.")
                return
            callback_chat_id = str(query.message.chat_id) if query.message else ""
            if state.get("chat_id") and state["chat_id"] != callback_chat_id:
                await query.answer("Akses ditolak", show_alert=True)
                return
            pick = self.pending_category_picks.get(state["chat_id"])
            if not pick or pick.get("token") != token:
                await query.answer("Pilihan kedaluwarsa", show_alert=True)
                await self._send_batch_card(token)
                return
            try:
                index = int(index_raw)
                category, subcategory = pick["suggestions"][index]
            except (TypeError, ValueError, IndexError, KeyError):
                await query.answer("Pilihan tidak valid", show_alert=True)
                return
            try:
                reply = self.agent.finance_import.apply_category_pair(
                    int(pick["transaction_id"]), category, subcategory
                )
            except InvalidCategoryError as exc:
                await self._reply_invalid_category(
                    chat_id=state["chat_id"],
                    token=token,
                    transaction_id=int(pick["transaction_id"]),
                    error=exc,
                    message=state["message"],
                    edit_query=query,
                )
                return
            except ValueError as exc:
                await query.answer("Gagal", show_alert=True)
                await state["message"].reply_text(f"⚠️ {exc}")
                return
            self.pending_category_picks.pop(state["chat_id"], None)
            await query.edit_message_text(reply)
            await self._send_batch_card(token)
            return

        if action == "batch_field":
            # batch_field:token:field
            parts = data.split(":", 2)
            if len(parts) != 3:
                await query.edit_message_text("⚠️ Permintaan ubah field tidak valid.")
                return
            _, token, field = parts
            state = self.pending_batch_review.get(token)
            if not state or state["index"] >= len(state["items"]):
                await query.edit_message_text("⚠️ Review batch ini sudah selesai atau kedaluwarsa.")
                return
            callback_chat_id = str(query.message.chat_id) if query.message else ""
            if state.get("chat_id") and state["chat_id"] != callback_chat_id:
                await query.answer("Akses ditolak", show_alert=True)
                return
            row_id = state["items"][state["index"]]
            self.pending_batch_edit[state["chat_id"]] = {
                "token": token,
                "field": field,
                "transaction_id": row_id,
            }
            await query.edit_message_text(
                f"✏️ Kirim nilai baru untuk {field} transaksi [{row_id}].\n"
                + (
                    "Format: `Category / Subcategory` (contoh: `Makanan / Restoran` atau `Makanan / Makanan`).\n"
                    if field in {"category", "kategori", "to"}
                    else ""
                )
                + "Balas pesan ini dengan nilai tersebut (atau ketik batal)."
            )
            return

        state = self.pending_batch_review.get(token)
        if not state:
            await query.edit_message_text("⚠️ Review batch ini sudah selesai atau kedaluwarsa.")
            return
        callback_chat_id = str(query.message.chat_id) if query.message else ""
        if state.get("chat_id") and state["chat_id"] != callback_chat_id:
            await query.answer("Akses ditolak", show_alert=True)
            return

        if action == "batch_list":
            self.pending_batch_review.pop(token, None)
            self.pending_batch_edit.pop(state["chat_id"], None)
            await query.edit_message_text("📋 Review dihentikan. Menampilkan daftar batch.")
            await self._reply_batch_list(state["message"])
            return

        if action == "batch_edit":
            field_buttons = [
                InlineKeyboardButton(label, callback_data=f"batch_field:{token}:{field}")
                for field, label in _BATCH_EDIT_FIELDS
            ]
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([
                    *[field_buttons[i:i + 2] for i in range(0, len(field_buttons), 2)],
                    [InlineKeyboardButton("⬅️ Kembali", callback_data=f"batch_back:{token}")],
                ])
            )
            return

        if action == "batch_back":
            row_id = state["items"][state["index"]]
            row = self.agent.finance_import.db.get_staged_transaction(row_id)
            if not row:
                await query.edit_message_text("⚠️ Transaksi tidak ditemukan.")
                state["index"] += 1
                await self._send_batch_card(token)
                return
            text = self._format_batch_card(
                row, state["index"] + 1, state["total"], state["period"]
            )
            await query.edit_message_text(
                text, reply_markup=self._batch_card_keyboard(token, row)
            )
            return

        if action == "batch_as_expense":
            row_id = state["items"][state["index"]]
            row = self.agent.finance_import.db.get_staged_transaction(row_id)
            if not row or str(row.get("type") or "").casefold() != "transfer":
                await query.answer("Bukan Transfer", show_alert=True)
                return
            suggestion = await self.agent.finance_import.suggest_expense_alternative(row)
            await query.edit_message_text(
                f"💸 Recommendation dialog — convert [{row_id}]?\n\n"
                f"Current: Transfer · From Cash · To {row.get('category') or '-'}\n"
                f"Proposed: Expense · {suggestion['category']} / {suggestion['subcategory']}\n"
                f"Note: {suggestion.get('note') or row.get('merchant') or '-'}\n"
                f"Description: {row.get('description') or '-'}\n\n"
                "Confirm to apply (no typing). Or go Back.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "✅ Confirm → Expense",
                        callback_data=f"batch_confirm_expense:{token}",
                    ),
                    InlineKeyboardButton("⬅️ Back", callback_data=f"batch_back:{token}"),
                ]]),
            )
            return

        if action == "batch_confirm_expense":
            row_id = state["items"][state["index"]]
            try:
                result = await self.agent.finance_import.convert_transfer_to_expense(row_id)
            except ValueError as exc:
                await query.answer("Gagal", show_alert=True)
                await state["message"].reply_text(f"⚠️ {exc}")
                return
            await query.edit_message_text(
                f"✅ Converted [{row_id}] to Expense\n"
                f"Category: {result['category']} / {result['subcategory']}\n"
                f"Note: {result.get('note') or '-'}\n"
                "Review the updated recommendation card next."
            )
            await self._send_batch_card(token)
            return

        if action == "batch_ok":
            row_id = state["items"][state["index"]]
            try:
                reply = self.agent.finance_import.mark_transaction_ok(row_id)
            except InvalidCategoryError as exc:
                await query.answer("Kategori invalid", show_alert=True)
                await self._reply_invalid_category(
                    chat_id=state["chat_id"],
                    token=token,
                    transaction_id=row_id,
                    error=exc,
                    message=state["message"],
                    edit_query=query,
                )
                return
            except ValueError as exc:
                await query.answer("Belum valid", show_alert=True)
                await state["message"].reply_text(
                    f"⚠️ Belum bisa OK untuk [{row_id}]: {exc}\n"
                    "Pilih Ubah untuk memperbaiki kategori/akun, lalu coba OK lagi."
                )
                return
            state["ok"] += 1
            state["index"] += 1
            await query.edit_message_text(reply)
            await self._send_batch_card(token)
            return

        if action == "batch_skip":
            row_id = state["items"][state["index"]]
            state["skipped"] += 1
            state["index"] += 1
            await query.edit_message_text(f"⏭️ Transaksi [{row_id}] dilewati.")
            await self._send_batch_card(token)
            return

    async def cmd_edit(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if len(ctx.args) < 3 or not ctx.args[0].isdigit():
            await update.message.reply_text(
                "✏️ Edit only updates staging (not Money Manager yet).\n"
                "/edit <id> date 2026-01-15\n"
                "/edit <id> amount 125000\n"
                "/edit <id> type expense|income|transfer\n"
                "/edit <id> account Cash\n"
                "/edit <id> category Makanan\n"
                "/edit <id> subcategory Cafe\n"
                "/edit <id> to Tabungan - Istri   (Transfer destination)\n"
                "/edit <id> note Family Mart\n"
                "/edit <id> description cranberry americano"
            )
            return
        try:
            reply = self.agent.finance_import.edit_transaction(
                int(ctx.args[0]), ctx.args[1], " ".join(ctx.args[2:])
            )
            await update.message.reply_text(reply)
        except (TypeError, ValueError) as exc:
            await update.message.reply_text(f"⚠️ {exc}")

    async def cmd_agenda(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args
        agenda = self.agent.agenda
        if not args:
            items = agenda.list_all()
            if not items:
                await update.message.reply_text("Belum ada agenda tercatat.")
                return
            lines = ["📝 Agenda tersimpan:"]
            for it in items:
                when = it.get("when_at") or "kapan saja"
                lines.append(f"  [{it['id']}] {when} — {it['title']}")
            await update.message.reply_text("\n".join(lines))
            return

        sub = args[0].lower()
        if sub == "tambah" and len(args) > 1:
            title = " ".join(args[1:])
            try:
                reply = await self.agent.save_agenda_item(parse_agenda_text(title))
                await update.message.reply_text(reply)
            except (TypeError, ValueError, RuntimeError) as exc:
                await update.message.reply_text(f"⚠️ Agenda gagal disimpan: {exc}")
        elif sub == "hapus" and len(args) > 1 and args[1].isdigit():
            ok = agenda.delete(int(args[1]))
            await update.message.reply_text(
                "🗑️ Agenda dihapus." if ok else "Agenda tidak ditemukan."
            )
        else:
            await update.message.reply_text(
                "Format: /agenda | /agenda tambah <judul> | /agenda hapus <id>"
            )

    async def cmd_export(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            period, force, mode, auto = parse_export_args(ctx.args or [])
        except ValueError as exc:
            await update.message.reply_text(str(exc))
            return
        if auto:
            await update.message.reply_text(
                f"⚡ Auto-export {period} (tanpa review interaktif): "
                "rekomendasi lokal → approve yang valid → buat TSV…"
            )
        await self._send_export_files(
            update.message, period, force=force, mode=mode, auto=auto
        )

    async def _send_export_files(
        self, message, period: str, *, force: bool, mode: str, auto: bool = False
    ) -> None:
        try:
            result = await self.agent.finance_import.export_period(
                period, force=force, auto=auto
            )
        except ValueError as exc:
            await message.reply_text(f"⚠️ {exc}")
            return

        await message.reply_text(self.agent.finance_import.format_export_message(result))
        try:
            with open(result.tsv_path, "rb") as handle:
                await message.reply_document(
                    document=InputFile(handle, filename=f"{period}.tsv"),
                    caption=(
                        f"{period}.tsv — tab-separated, tanggal dd/MM/yyyy.\n"
                        "More → Backup → Import Excel File (nama file bebas)."
                    ),
                )
            if mode == "both":
                with open(result.xlsx_path, "rb") as handle:
                    await message.reply_document(
                        document=InputFile(handle, filename=f"{period}.xlsx"),
                        caption=(
                            f"XLSX {period} (fallback):\n"
                            "1. Buka di Google Sheets / Excel\n"
                            "2. Kolom Date → Format → dd/MM/yyyy\n"
                            "3. File → Download → Tab Separated Values (.tsv)"
                        ),
                    )
        except OSError as exc:
            await message.reply_text(f"⚠️ File diekspor, tetapi gagal dikirim ke chat: {exc}")

    async def cmd_finance(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if len(ctx.args) != 1:
            await update.message.reply_text("Format: /finance YYYY-MM")
            return
        await update.message.chat.send_action("typing")
        await update.message.reply_text(await self.agent.finance_report(ctx.args[0]))

    async def cmd_advice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.chat.send_action("typing")
        text = await self.agent.financial_advice()
        await update.message.reply_text(text)

    async def cmd_forget(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self.agent.memory.clear(str(update.effective_chat.id))
        await update.message.reply_text("🧹 Konteks percakapan sudah saya lepaskan.")

    # ------------------------------------------------------------------
    async def cmd_scan(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "Gunakan /import lalu paste atau upload file transaksi."
        )

    async def _run_scan(self, message) -> None:
        result = await self.agent.scan_email_transactions()
        await message.reply_text(result["message"])

    async def on_attachment(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.message
        chat_id = str(update.effective_chat.id)
        session = self.import_sessions.get(chat_id)

        mime_type = "image/jpeg"
        filename = ""
        if message.photo:
            file_id = message.photo[-1].file_id
            filename = "photo.jpg"
        elif message.document:
            filename = message.document.file_name or "upload"
            mime_type = (
                message.document.mime_type
                or mimetypes.guess_type(filename)[0]
                or "application/octet-stream"
            )
            file_id = message.document.file_id
        else:
            return

        if session:
            finance_mimes = {
                "text/plain",
                "text/html",
                "message/rfc822",
                "application/pdf",
                "image/jpeg",
                "image/png",
                "image/webp",
                "application/octet-stream",
            }
            name_ok = filename.casefold().endswith(
                (".txt", ".eml", ".html", ".htm", ".pdf", ".jpg", ".jpeg", ".png", ".webp")
            )
            if mime_type not in finance_mimes and not name_ok:
                await message.reply_text(
                    "⚠️ Untuk sesi /import, kirim .txt/.eml/.html/PDF/gambar, atau paste teks."
                )
                return
            try:
                telegram_file = await ctx.bot.get_file(file_id)
                content = bytes(await telegram_file.download_as_bytearray())
                await message.chat.send_action("typing")
                result = await self.agent.finance_import.stage_upload(
                    content,
                    mime_type,
                    filename=filename,
                    period_hint=session["period"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Gagal import attachment finance")
                await message.reply_text(f"⚠️ File tidak bisa diproses: {exc}")
                return
            await self._stage_import_result(message, chat_id, result)
            return

        # Agenda path (no finance session)
        if message.document and mime_type not in {
            "image/jpeg", "image/png", "image/webp", "application/pdf"
        }:
            await message.reply_text("⚠️ Format belum didukung. Kirim JPG, PNG, WEBP, atau PDF.")
            return

        try:
            telegram_file = await ctx.bot.get_file(file_id)
            content = bytes(await telegram_file.download_as_bytearray())
            items = await self.agent.llm.extract_agenda_attachment(content, mime_type)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Gagal membaca attachment agenda")
            await message.reply_text(f"⚠️ File tidak bisa dibaca: {exc}")
            return
        if not items:
            await message.reply_text("⚠️ Tidak ada agenda yang berhasil dibaca dari file tersebut.")
            return

        token = uuid.uuid4().hex[:12]
        self.pending_agenda[token] = {
            "items": items,
            "total": len(items),
            "saved": 0,
            "skipped": 0,
            "message": message,
        }
        await message.reply_text(f"📄 Ditemukan {len(items)} agenda. Saya tampilkan satu per satu.")
        await self._send_next_agenda(token)

    async def on_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data.startswith("import_mode:"):
            parsed = parse_import_mode_callback(data)
            if not parsed or not query.message:
                return
            period = parsed["period"]
            if parsed["mode"] == "gmail":
                await query.edit_message_text(f"📥 Import {period}\nSumber: Gmail")
                await self._reply_gmail_import(query.message, period, parsed["force"])
            else:
                chat_id = str(query.message.chat_id)
                await query.edit_message_text(f"📥 Import {period}\nSumber: Paste / Upload")
                text = self._open_import_paste_session(chat_id, period, parsed["force"])
                await query.message.reply_text(text)
            return
        if data.startswith("agenda_confirm:") or data.startswith("agenda_skip:"):
            await self._on_agenda_callback(query, data)
            return
        if data.startswith("calendar_reminder_confirm:") or data.startswith("calendar_reminder_skip:"):
            await self._on_calendar_reminder_callback(query, data)
            return
        if data.startswith("batch_"):
            await self._on_batch_callback(query, data)
            return
        action, _, token = data.partition(":")
        tx = self.pending.get(token)
        if not tx:
            await query.edit_message_text("⚠️ Data transaksi ini sudah kedaluwarsa.")
            return
        callback_chat_id = str(query.message.chat_id) if query.message else ""
        if tx.get("chat_id") and tx["chat_id"] != callback_chat_id:
            await query.answer("Akses ditolak", show_alert=True)
            return

        if action == "rec":
            if tx.get("needs_review"):
                await send_formatted(
                    query.edit_message_text,
                    "⚠️ Kategori belum valid. Minta saya menyesuaikan kategori terlebih dahulu "
                    "sebelum transaksi dicatat.",
                )
                return
            reply = await self.agent.record_scanned_transaction(tx)
            self.pending.pop(token, None)
            for chat_id, pending in list(self.pending_latest.items()):
                if pending is tx:
                    self.pending_latest.pop(chat_id, None)
            await send_formatted(query.edit_message_text, reply)
        elif action == "skip":
            self.agent.mark_email_skipped(tx["email_id"])
            self.pending.pop(token, None)
            await send_formatted(query.edit_message_text, "⏭️ Dilewati (tidak akan muncul lagi).")

    async def _send_next_agenda(self, token: str) -> None:
        state = self.pending_agenda.get(token)
        if not state or not state["items"]:
            if state:
                await state["message"].reply_text(
                    f"✅ Review file selesai. Dicatat: {state['saved']}, dilewati: {state['skipped']}."
                )
            self.pending_agenda.pop(token, None)
            return
        item = state["items"][0]
        index = state.get("total", len(state["items"])) - len(state["items"]) + 1
        warning = "\n⚠️ Perlu review manual." if item.get("needs_review") else ""
        reminder_text = ""
        if item.get("reminders"):
            reminder_text = "\nReminder: " + ", ".join(
                f"{r['method']} {r['minutes']} menit sebelumnya" for r in item["reminders"]
            )
        text = (
            f"📅 Agenda {index}/{state.get('total', len(state['items']))}\n"
            f"Judul: {item['title']}\n"
            f"Tanggal: {item.get('date') or '(tidak jelas)'}\n"
            f"Waktu: {item.get('start_time') or '(tidak jelas)'}"
            f"{('–' + item['end_time']) if item.get('end_time') else ''}\n"
            f"Lokasi: {item.get('location') or '-'}\n"
            f"Catatan: {item.get('notes') or '-'}{reminder_text}{warning}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Catat", callback_data=f"agenda_confirm:{token}"),
            InlineKeyboardButton("⏭️ Lewati", callback_data=f"agenda_skip:{token}"),
        ]])
        await state["message"].reply_text(text, reply_markup=keyboard)

    async def _on_agenda_callback(self, query, data: str) -> None:
        _, token = data.split(":", 1)
        state = self.pending_agenda.get(token)
        if not state or not state["items"]:
            await query.edit_message_text("⚠️ Review agenda ini sudah selesai atau kedaluwarsa.")
            return
        item = state["items"].pop(0)
        if data.startswith("agenda_confirm:"):
            try:
                result = await self.agent.save_agenda_item(item)
                state["saved"] += 1
                await query.edit_message_text(result)
            except Exception as exc:  # noqa: BLE001
                await query.edit_message_text(f"⚠️ Agenda gagal disimpan: {exc}")
        else:
            state["skipped"] += 1
            await query.edit_message_text("⏭️ Agenda dilewati.")
        await self._send_next_agenda(token)

    async def _send_next_calendar_reminder(self, token: str) -> None:
        state = self.pending_calendar_reminders.get(token)
        if not state or not state["items"]:
            if state:
                await state["message"].reply_text(
                    f"🔔 Selesai. Dikonfirmasi: {state['confirmed']}, dilewati: {state['skipped']}."
                )
            self.pending_calendar_reminders.pop(token, None)
            return
        event = state["items"][0]
        text = (
            f"🔔 Agenda {state['total'] - len(state['items']) + 1}/{state['total']}\n"
            f"{event['title']}\n"
            f"Waktu: {event.get('start', '(tidak diketahui)')}\n"
            f"Reminder baru: popup {state['minutes']} menit sebelumnya\n\n"
            "Perubahan belum dikirim ke Google Calendar."
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Konfirmasi", callback_data=f"calendar_reminder_confirm:{token}"),
            InlineKeyboardButton("⏭️ Lewati", callback_data=f"calendar_reminder_skip:{token}"),
        ]])
        await state["message"].reply_text(text, reply_markup=keyboard)

    async def _on_calendar_reminder_callback(self, query, data: str) -> None:
        _, token = data.split(":", 1)
        state = self.pending_calendar_reminders.get(token)
        if not state or not state["items"]:
            await query.edit_message_text("⚠️ Update reminder ini sudah selesai atau kedaluwarsa.")
            return
        event = state["items"].pop(0)
        if data.startswith("calendar_reminder_confirm:"):
            try:
                await self.agent.update_calendar_event_reminders(event["id"], state["minutes"])
                state["confirmed"] += 1
                await query.edit_message_text(f"✅ Reminder agenda '{event['title']}' berhasil diubah.")
            except Exception as exc:  # noqa: BLE001
                await query.edit_message_text(f"⚠️ Reminder belum diubah: {exc}")
        else:
            state["skipped"] += 1
            await query.edit_message_text(f"⏭️ Reminder agenda '{event['title']}' dilewati.")
        await self._send_next_calendar_reminder(token)

    async def on_message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.chat.send_action("typing")
        text = update.message.text
        chat_id = str(update.effective_chat.id)

        session = self.import_sessions.get(chat_id)
        if session and text and text.strip():
            if text.strip().casefold() in {"batal", "cancel"}:
                self.import_sessions.pop(chat_id, None)
                await update.message.reply_text("Sesi /import dibatalkan.")
                return
            try:
                result = await self.agent.finance_import.stage_paste(
                    text, period_hint=session["period"]
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Gagal stage paste")
                await update.message.reply_text(f"⚠️ Gagal memproses paste: {exc}")
                return
            await self._stage_import_result(update.message, chat_id, result)
            return

        edit_state = self.pending_batch_edit.get(chat_id)
        if edit_state:
            if text.strip().casefold() in {"batal", "cancel"}:
                self.pending_batch_edit.pop(chat_id, None)
                await update.message.reply_text("✏️ Perubahan dibatalkan.")
                await self._send_batch_card(edit_state["token"])
                return
            try:
                reply = self.agent.finance_import.edit_transaction(
                    edit_state["transaction_id"], edit_state["field"], text
                )
            except InvalidCategoryError as exc:
                await self._reply_invalid_category(
                    chat_id=chat_id,
                    token=edit_state["token"],
                    transaction_id=edit_state["transaction_id"],
                    error=exc,
                    message=update.message,
                )
                # Keep pending edit cleared so taps on suggestions work; user can Edit again.
                self.pending_batch_edit.pop(chat_id, None)
                return
            except (TypeError, ValueError) as exc:
                await update.message.reply_text(f"⚠️ {exc}\nKirim nilai baru, atau ketik `batal`.")
                return
            self.pending_batch_edit.pop(chat_id, None)
            await update.message.reply_text(reply)
            # Re-recommend only when type/note/description change — never overwrite
            # an explicit Category / Subcategory edit from the user.
            if edit_state["field"] in {
                "merchant", "note", "deskripsi", "description", "type", "tipe",
            }:
                try:
                    refreshed = await self.agent.finance_import.reresolve_transaction_category(
                        edit_state["transaction_id"]
                    )
                    await update.message.reply_text(refreshed)
                except ValueError as exc:
                    await update.message.reply_text(f"⚠️ {exc}")
            await self._send_batch_card(edit_state["token"])
            return

        confirmation = self.pending_confirmations.get(chat_id)
        if confirmation and self.is_confirmation(text):
            if confirmation.get("needs_review"):
                await update.message.reply_text(
                    "⚠️ Rekomendasi belum valid untuk kategori Money Manager. "
                    "Sebutkan merchant/kategori yang ingin dipakai sebelum saya mencatatnya."
                )
                return
            reply = await self.agent.record_scanned_transaction(confirmation)
            self.pending_confirmations.pop(chat_id, None)
            self.pending_latest.pop(chat_id, None)
            await update.message.reply_text(reply)
            return

        pending = self.select_pending_transaction(self.pending_recent.get(chat_id, []), text)
        pending = pending or self.pending_latest.get(chat_id)
        if pending and any(word in text.casefold() for word in ("catat", "simpan", "ubah kategori")):
            recommendation = await self.agent.recommend_scanned_transaction(pending, text)
            self.pending_confirmations[chat_id] = recommendation
            await update.message.reply_text(
                self.format_category_recommendation(recommendation)
            )
            return

        plan = await self.agent.plan_message(text)
        if plan.get("action") == "scan_email":
            self.agent.memory.add(chat_id, "user", text)
            await self._run_scan(update.message)
            return

        if plan.get("action") == "calendar_update_reminders":
            title_query = plan["args"].get("title", "")
            agenda_id = plan["args"].get("agenda_id")
            if agenda_id:
                local_item = next(
                    (item for item in self.agent.agenda.list_all() if item["id"] == agenda_id),
                    None,
                )
                if not local_item:
                    await update.message.reply_text(f"⚠️ Agenda lokal [{agenda_id}] tidak ditemukan.")
                    return
                title_query = local_item["title"]
            try:
                events = await self.agent.find_calendar_events(title_query)
            except Exception as exc:  # noqa: BLE001
                await update.message.reply_text(f"⚠️ Google Calendar tidak bisa dibaca: {exc}")
                return
            if not events:
                await update.message.reply_text("📅 Tidak ada agenda yang cocok. Tidak ada reminder yang diubah.")
                return
            token = uuid.uuid4().hex[:12]
            self.pending_calendar_reminders[token] = {
                "items": events,
                "minutes": plan["args"].get("minutes", 0),
                "total": len(events),
                "confirmed": 0,
                "skipped": 0,
                "message": update.message,
            }
            await update.message.reply_text(f"🔔 Ditemukan {len(events)} agenda. Konfirmasi satu per satu.")
            await self._send_next_calendar_reminder(token)
            return

        reply = await self.agent.handle_message(chat_id, text, plan=plan)
        await send_formatted(update.message.reply_text, reply)

    @staticmethod
    def select_pending_transaction(items: list[dict], instruction: str) -> dict | None:
        """Choose the scanned item matching a natural-language correction."""
        lowered = instruction.casefold()
        for item in items:
            haystack = " ".join(
                str(item.get(field, "")).casefold()
                for field in ("description", "merchant", "subject")
            )
            if any(token in haystack for token in lowered.split() if len(token) >= 4):
                return item
            if "family" in lowered and "famima" in haystack:
                return item
        return None

    @staticmethod
    def is_confirmation(text: str) -> bool:
        return text.strip().casefold() in {"iya", "ya", "oke", "ok", "setuju", "konfirmasi", "catat"}

    @staticmethod
    def format_category_recommendation(tx: dict) -> str:
        return (
            "🧠 Category recommendation before save:\n"
            f"Note: {tx.get('merchant') or '-'}\n"
            f"Description: {tx.get('description') or '-'}\n"
            f"Amount: Rp{int(tx.get('amount', 0)):,.0f}".replace(",", ".")
            + f"\nCategory: {tx.get('category', 'Lainnya')} / {tx.get('subcategory', '-')}\n"
            "Accept? Reply `iya` or `oke`. Edit by mentioning note/category."
        )

    # ------------------------------------------------------------------
    async def send_startup_brief(self, text: str) -> None:
        if not settings.telegram_chat_id:
            logger.warning("TELEGRAM_CHAT_ID kosong — startup brief tidak dikirim.")
            return
        try:
            await send_formatted(
                self.app.bot.send_message,
                text,
                chat_id=settings.telegram_chat_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gagal kirim startup brief: %s", exc)
