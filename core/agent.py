"""Hermes orchestrator.

Owns the connector registry, LLM client, memory, and the high-level actions the
Telegram bot invokes (record expense, finance query, advice, chat). Designed to
degrade gracefully: any connector can be missing/offline without crashing.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re

from config.security_policy import looks_like_harmful_request, refusal_for_harmful_request
from config.settings import settings
from connectors.agenda_manual import ManualAgendaConnector
from connectors.gcal import GoogleCalendarConnector
from connectors.gmail import GmailConnector
from connectors.outlook import OutlookConnector
from connectors.tavily import TavilyConnector
from core.db import Database
from core.finance_import import FinanceImportService
from core.llm_client import LLMClient
from core.memory import Memory
from core.office_monitor import OfficeMonitoringService
from core.quota import QuotaService
from core.reminders import parse_reminders
from core.spreadsheet_finance import CategoryReference, SpreadsheetFinanceService

logger = logging.getLogger("hermes.agent")

# Intent router description passed to the LLM.
_TOOLS_DESC = (
    "- add_expense: user ingin mencatat transaksi/pengeluaran/pemasukan. "
    'args: {"amount": number, "type": "expense"|"income", "category": string, "description": string}\n'
    "- finance_query: user bertanya tentang keuangan/pengeluaran/pemasukan/saldo. "
    'args: {"question": string}\n'
    "- advice: user minta saran/analisis keuangan. args: {}\n"
    "- scan_email: user ingin mencatat transaksi via paste/upload "
    "(arahkan ke /import). args: {}\n"
    '- web_search: cari informasi web atau berita terbaru. args: {"query": string, "news": boolean}\n'
    '- calendar_query: user ingin membaca agenda Google Calendar. args: {"question": string, "range": "today"|"tomorrow"|"week"}\n'
    '- calendar_update_reminders: user ingin mengubah reminder event Calendar. args: {"title": string, "minutes": number}\n'
    '- agenda_add: user ingin membuat agenda. args: {"title": string, "date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM", "location": string, "notes": string, "reminder_text": string}\n'
    '- agenda_delete: user ingin menghapus agenda lokal. args: {"id": number}\n'
    "- chat: obrolan umum atau pertanyaan lain. args: {}"
)


class HermesAgent:
    def __init__(self):
        settings.ensure_dirs()
        self.db = Database()
        self.office_monitoring = OfficeMonitoringService(self.db)
        self.llm = LLMClient()
        self.memory = Memory(self.db)

        # Connectors (Gmail for finance scan; Calendar + local agenda)
        self.gmail = GmailConnector()
        self.gcal = GoogleCalendarConnector()
        self.outlook = OutlookConnector() if settings.ms_outlook_enabled else None
        self.agenda = ManualAgendaConnector(self.db)
        self.tavily = TavilyConnector()
        self.quota = QuotaService(self.tavily)
        self.spreadsheet_finance = SpreadsheetFinanceService()
        self.category_reference = CategoryReference()
        self.finance_import = FinanceImportService(
            self.db, self.llm, gmail=self.gmail, spreadsheet_service=self.spreadsheet_finance
        )

        self.connectors = [self.gmail, self.gcal, self.agenda]
        if self.outlook:
            self.connectors.append(self.outlook)

    # ------------------------------------------------------------------
    async def health_check_all(self) -> dict[str, bool]:
        """Return {connector_name: healthy}. Never raises."""
        result: dict[str, bool] = {}
        for c in self.connectors:
            try:
                result[c.name] = await c.health_check()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Health check %s error: %s", c.name, exc)
                result[c.name] = False
        return result

    # ------------------------------------------------------------------
    async def plan_message(self, text: str) -> dict:
        """Classify a message into an intent (shared by bot before dispatch)."""
        lowered = text.casefold()
        delete_match = re.search(r"(?:hapus|delete)\s+agenda(?:\s+(?:nomor|id))?\s*(\d+)", lowered)
        if delete_match:
            return {"action": "agenda_delete", "args": {"id": int(delete_match.group(1))}}
        has_duration = any(unit in lowered for unit in ("menit", "jam", "hari", "minute", "hour", "day"))
        if (any(word in lowered for word in ("reminder", "pengingat")) or has_duration) and any(
            word in lowered for word in ("ubah", "sesuaikan", "menjadi", "update", "edit", "ganti")
        ):
            reminders = parse_reminders(text)
            if not reminders:
                from core.reminders import parse_reminders as parse_requested_reminder

                reminders = parse_requested_reminder("ingatkan " + text)
            title = text
            for marker in ("menjadi", "jadi"):
                if marker in title.casefold():
                    title = title[: title.casefold().index(marker)]
                    break
            title = title.replace("ubah", "").replace("sesuaikan", "").replace("reminder", "").replace("pengingat", "")
            for prefix in ("seluruh agenda perjalanan", "semua agenda perjalanan", "agenda perjalanan"):
                title = title.replace(prefix, "")
            agenda_id_match = re.search(r"\bagenda\s+(?:nomor\s+|id\s+)?(\d+)\b", lowered)
            return {
                "action": "calendar_update_reminders",
                "args": {
                    "title": title.strip(" ,"),
                    "agenda_id": int(agenda_id_match.group(1)) if agenda_id_match else None,
                    "minutes": reminders[0]["minutes"] if reminders else 0,
                },
            }
        if any(word in lowered for word in ("google calendar", "agenda", "jadwal")) and any(
            word in lowered for word in ("cek", "lihat", "ada", "minggu", "besok", "hari ini")
        ):
            range_name = "week" if "minggu" in lowered else "tomorrow" if "besok" in lowered else "today"
            return {"action": "calendar_query", "args": {"question": text, "range": range_name}}
        return await self.llm.plan(text, _TOOLS_DESC)

    def quota_report(self) -> str:
        return self.quota.render()

    def office_report(self, report_type: str) -> str:
        """Render a local deterministic report from verified office snapshots."""
        if report_type == "status":
            report_type = "daily"
        return self.office_monitoring.render_report(report_type)

    async def save_agenda_item(self, item: dict) -> str:
        title = item.get("title", "").strip()
        date = item.get("date", "").strip()
        start_time = item.get("start_time", "").strip()
        end_time = item.get("end_time", "").strip()
        location = item.get("location", "").strip()
        notes = item.get("notes", "").strip()
        if not title:
            raise ValueError("Judul agenda tidak boleh kosong.")

        if date and start_time:
            try:
                start = dt.datetime.fromisoformat(f"{date}T{start_time}:00+07:00")
                end = (
                    dt.datetime.fromisoformat(f"{date}T{end_time}:00+07:00")
                    if end_time
                    else start + dt.timedelta(hours=1)
                )
                event = await asyncio.to_thread(
                    self.gcal.create_event,
                    title,
                    start.isoformat(),
                    end.isoformat(),
                    location,
                    notes,
                    reminders=item.get("reminders"),
                )
                link = event.get("htmlLink", "")
                return f"✅ Agenda tersimpan di Google Calendar Pribadi.{(' ' + link) if link else ''}"
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google Calendar write gagal, gunakan fallback lokal: %s", exc)
                fallback_note = f"Google Calendar fallback: {exc}"
                saved = self.agenda.add_verified(title, date, f"{notes}\n{fallback_note}".strip())
                return f"⚠️ Google Calendar gagal; agenda [{saved['id']}] disimpan lokal."

        saved = self.agenda.add_verified(title, date or None, notes or None)
        return f"✅ Agenda [{saved['id']}] tersimpan di agenda lokal."

    async def handle_message(self, chat_id: str, text: str, plan: dict | None = None) -> str:
        """Route a natural-language message through the LLM and act on it.

        Note: the 'scan_email' action is handled by the Telegram layer (needs
        interactive buttons), so it is not processed here.
        """
        self.memory.add(chat_id, "user", text)
        if plan is None:
            plan = await self.plan_message(text)
        action = plan.get("action", "chat")
        args = plan.get("args", {}) or {}

        if args.get("refuse_harmful") or looks_like_harmful_request(text):
            reply = refusal_for_harmful_request()
            self.memory.add(chat_id, "assistant", reply)
            return reply

        if action == "add_expense":
            reply = "⚠️ Pencatatan langsung dinonaktifkan. Gunakan /import bulan ini, review batch, lalu /export."
        elif action == "finance_query":
            reply = await self._do_finance_query(args.get("question", text))
        elif action == "advice":
            reply = await self.financial_advice()
        elif action == "web_search":
            reply = await self._do_web_search(
                args.get("query", text), news=bool(args.get("news", False))
            )
        elif action == "calendar_query":
            start, end = self._calendar_range(args.get("range", "today"))
            reply = await self._do_calendar_query(args.get("question", text), start, end)
        elif action == "agenda_add":
            item = {
                "title": args.get("title", text),
                "date": args.get("date", ""),
                "start_time": args.get("start_time", ""),
                "end_time": args.get("end_time", ""),
                "location": args.get("location", ""),
                "notes": args.get("notes", ""),
                "reminders": parse_reminders(args.get("reminder_text", "")),
            }
            reply = await self.save_agenda_item(item)
        elif action == "agenda_delete":
            agenda_id = int(args.get("id", 0))
            deleted = self.agenda.delete(agenda_id)
            reply = (
                f"✅ Agenda [{agenda_id}] berhasil dihapus dari agenda lokal."
                if deleted
                else f"⚠️ Agenda [{agenda_id}] tidak ditemukan. Tidak ada yang dihapus."
            )
        elif action == "chat" and text.strip().casefold() in {
            "hi",
            "hai",
            "halo",
            "hello",
            "pagi",
            "siang",
            "malam",
        }:
            reply = f"Halo, {settings.address}. Nyx aktif. Ada yang bisa saya bantu?"
        else:
            history = self.memory.recent(chat_id)
            reply = await self.llm.generate(text, history=history)

        self.memory.add(chat_id, "assistant", reply)
        return reply

    async def _do_web_search(self, query: str, news: bool = False) -> str:
        results = self.tavily.search(query, news=news)
        if not results:
            return "⚠️ Search web belum tersedia atau tidak menemukan hasil. Periksa TAVILY_API_KEY."

        sources = "\n\n".join(
            f"[{index}] {item['title']}\n{item['snippet']}\nSumber: {item['url']}"
            for index, item in enumerate(results, start=1)
        )
        prompt = (
            "Jawab pertanyaan berdasarkan hasil pencarian web berikut. "
            "Gunakan Bahasa Indonesia yang ringkas dan jujur. Sertakan nomor sumber "
            "([1], [2], dst.) pada klaim penting dan daftar URL sumber di akhir.\n\n"
            f"Pertanyaan: {query}\n\nHasil pencarian:\n{sources}"
        )
        return await self.llm.summarize(prompt)

    @staticmethod
    def _calendar_range(range_name: str) -> tuple[dt.datetime, dt.datetime]:
        now = dt.datetime.now().astimezone()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if range_name == "tomorrow":
            start = today + dt.timedelta(days=1)
            return start, start + dt.timedelta(days=1)
        if range_name == "week":
            start = today - dt.timedelta(days=today.weekday())
            return start, start + dt.timedelta(days=7)
        return today, today + dt.timedelta(days=1)

    async def _do_calendar_query(self, question: str, start: dt.datetime, end: dt.datetime) -> str:
        try:
            events = await asyncio.to_thread(self.gcal.query_events, start, end)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Calendar query gagal: %s", exc)
            return f"⚠️ Google Calendar Pribadi belum bisa dibaca: {exc}"
        if not events:
            return f"📅 Tidak ada agenda di Google Calendar Pribadi untuk periode yang diminta."
        lines = ["📅 Agenda Google Calendar Pribadi:"]
        for event in events:
            lines.append(
                f"- {event['start']} — {event['title']}"
                f"{(' | ' + event['location']) if event['location'] else ''}"
            )
        return "\n".join(lines)

    async def find_calendar_events(self, title_query: str) -> list[dict]:
        now = dt.datetime.now().astimezone()
        events = await asyncio.to_thread(
            self.gcal.query_events,
            now,
            now + dt.timedelta(days=365),
        )
        terms = [
            part.strip().casefold()
            for part in title_query.replace("&", ",").split(",")
            if part.strip()
        ]
        terms = [term for term in terms if len(term) > 2]
        return [
            event for event in events
            if not terms or any(term in event.get("title", "").casefold() for term in terms)
        ]

    async def update_calendar_event_reminders(self, event_id: str, minutes: int) -> dict:
        return await asyncio.to_thread(
            self.gcal.update_event_reminders,
            event_id,
            [{"method": "popup", "minutes": minutes}],
        )

    async def _do_finance_query(self, question: str) -> str:
        period = dt.date.today().strftime("%Y-%m")
        prompt = self.spreadsheet_finance.report_prompt(period)
        prompt += f"\nPertanyaan {settings.address}: {question}"
        return await self.llm.summarize(prompt)

    async def financial_advice(self) -> str:
        period = dt.date.today().strftime("%Y-%m")
        prompt = self.spreadsheet_finance.report_prompt(period)
        prompt += "\nBeri 3-5 saran praktis dan disclaimer bahwa ini bukan nasihat finansial profesional."
        return await self.llm.summarize(prompt)

    # ------------------------------------------------------------------
    async def scan_email_transactions(self, days: int = 1) -> dict:
        """Legacy /scan entry — direct users to /import Gmail or paste mode."""
        return {
            "ok": False,
            "message": (
                "Import Gmail tersedia lagi. Pakai /import untuk pilih Gmail atau paste/upload, "
                "atau /import gmail YYYY-MM untuk langsung memindai Gmail."
            ),
            "pending": [],
            "warnings": [],
        }

    async def record_scanned_transaction(self, tx: dict) -> str:
        """Save a confirmed daily scan into the editable monthly staging batch."""
        row_id = self.finance_import.stage_scanned_transaction(tx)
        period = dt.date.today().strftime("%Y-%m")
        return f"✅ Transaksi [{row_id}] disimpan ke review {period}. Anda masih bisa mengubahnya dengan /edit."

    async def recommend_scanned_transaction(self, tx: dict, instruction: str) -> dict:
        """Apply an AI category recommendation without writing to staging."""
        context_description = (
            f"{tx.get('description', '')}\nInstruksi pengguna: {instruction}".strip()
        )
        category, subcategory, category_ok = await self.finance_import.resolve_category(
            merchant=str(tx.get("merchant", "") or ""),
            description=context_description,
            amount=int(tx.get("amount", 0) or 0),
            tx_type=str(tx.get("type", "expense") or "expense"),
            category=str(tx.get("category", "Lainnya") or "Lainnya"),
            subcategory=str(tx.get("subcategory", "-") or "-"),
        )
        updated = dict(tx)
        updated["category"] = category
        updated["subcategory"] = subcategory
        updated["needs_review"] = (not category_ok) or bool(updated.get("suspicious_instruction"))
        return updated

    async def revise_scanned_transaction(self, tx: dict, instruction: str) -> str:
        """Apply a recommendation and stage it after an explicit confirmation."""
        return await self.record_scanned_transaction(
            await self.recommend_scanned_transaction(tx, instruction)
        )

    def mark_email_skipped(self, email_id: str) -> None:
        self.db.mark_email(email_id, "skipped", "dilewati manual")

    # ------------------------------------------------------------------
    async def all_today_agenda(self) -> list[dict]:
        """Merge agenda from all sources for today."""
        merged: list[dict] = []
        for src in (self.gcal, self.outlook, self.agenda):
            if src is None:
                continue
            try:
                if src.is_available():
                    merged.extend(await src.today_events())
            except Exception as exc:  # noqa: BLE001
                logger.warning("Agenda %s gagal: %s", src.name, exc)
        merged.sort(key=lambda e: e.get("time", ""))
        return merged

    async def close(self) -> None:
        for c in self.connectors:
            try:
                await c.close()
            except Exception:  # noqa: BLE001
                pass
        self.db.close()

    async def finance_report(self, period: str) -> str:
        summary = self.spreadsheet_finance.month_summary(period)
        if not summary["rows"]:
            return f"⚠️ Belum ada spreadsheet transaksi untuk periode {period}."
        return await self.llm.summarize(self.spreadsheet_finance.report_prompt(period))
