"""Finance intake: paste/upload → LLM map → SQLite staging → export."""

from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import html as html_lib
import re
from email import message_from_bytes
from email.utils import parsedate_to_datetime

from config.settings import settings
from core.category_recommendations import CategoryRecommendationLookup
from core.db import Database
from core.spreadsheet_finance import ExportResult, SpreadsheetFinanceService, _key

VALID_TX_TYPES = frozenset({"expense", "income", "transfer"})
TEXT_MIME_TYPES = frozenset({
    "text/plain",
    "text/html",
    "message/rfc822",
})
ATTACHMENT_MIME_TYPES = frozenset({
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
})


class InvalidCategoryError(ValueError):
    """Raised when a category/subcategory pair is not in Money Manager."""

    def __init__(
        self,
        message: str,
        *,
        attempted: str,
        suggestions: list[tuple[str, str]],
        transaction_id: int | None = None,
    ):
        super().__init__(message)
        self.attempted = attempted
        self.suggestions = suggestions
        self.transaction_id = transaction_id


def normalize_finance_account(value: object | None = None) -> str:
    """Money Manager account for this workspace is Cash only."""
    account = str(value or "").strip()
    if account.casefold() in {"", "cash", "pribadi"}:
        return settings.finance_account or "Cash"
    # Any other label still maps to Cash per user rule.
    return settings.finance_account or "Cash"


def normalize_tx_type(value: object | None = None) -> str:
    raw = str(value or "expense").strip().casefold()
    aliases = {
        "pengeluaran": "expense",
        "pemasukan": "income",
        "income": "income",
        "expense": "expense",
        "transfer": "transfer",
        "transfer out": "transfer",
        "transfer-out": "transfer",
    }
    return aliases.get(raw, raw if raw in VALID_TX_TYPES else "expense")


def upload_source_id(payload: bytes) -> str:
    return "upload:" + hashlib.sha256(payload).hexdigest()[:16]


def default_import_period() -> str:
    return dt.date.today().strftime("%Y-%m")


def parse_import_period(period: str) -> tuple[str, str]:
    """Return inclusive ISO start/end dates for YYYY or YYYY-MM input."""
    value = period.strip()
    if len(value) == 4 and value.isdigit():
        year = int(value)
        return f"{year:04d}-01-01", f"{year:04d}-12-31"
    if len(value) == 7 and value[4] == "-" and value[:4].isdigit() and value[5:].isdigit():
        year, month = int(value[:4]), int(value[5:])
        if 1 <= month <= 12:
            last_day = calendar.monthrange(year, month)[1]
            return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"
    raise ValueError("Periode harus berformat YYYY atau YYYY-MM.")


def _resolve_import_period_token(token: str) -> str:
    """Normalize YYYY or YYYY-MM token to YYYY-MM and validate."""
    first = token.strip()
    if len(first) == 7 and first[4] == "-":
        parse_import_period(first)
        return first
    if len(first) == 4 and first.isdigit():
        today = dt.date.today()
        period = today.strftime("%Y-%m") if int(first) == today.year else f"{first}-01"
        parse_import_period(period)
        return period
    raise ValueError(
        "Format: /import | /import YYYY-MM [force] | "
        "/import gmail|paste YYYY-MM [force] | /import done | /import cancel"
    )


def _import_format_error() -> str:
    return (
        "Format: /import | /import YYYY-MM [force] | "
        "/import gmail|paste YYYY-MM [force] | /import done | /import cancel"
    )


def parse_import_command(args: list[str] | None) -> dict:
    """Parse /import into {action, period, force}.

    actions: choose_mode | start_gmail | start_paste | done | cancel
    """
    raw = list(args or [])
    if not raw:
        return {"action": "choose_mode", "period": default_import_period(), "force": False}
    head = raw[0].casefold()
    if head in {"done", "selesai"}:
        return {"action": "done", "period": None, "force": False}
    if head in {"cancel", "batal"}:
        return {"action": "cancel", "period": None, "force": False}

    if head in {"gmail", "paste"}:
        action = "start_gmail" if head == "gmail" else "start_paste"
        period: str | None = None
        force = False
        for token in raw[1:]:
            lowered = token.casefold()
            if lowered in {"force", "rescan", "retry"}:
                force = True
            elif period is None:
                period = _resolve_import_period_token(token)
            else:
                raise ValueError(_import_format_error())
        return {
            "action": action,
            "period": period or default_import_period(),
            "force": force,
        }

    force = False
    for token in raw[1:]:
        lowered = token.casefold()
        if lowered in {"force", "rescan", "retry"}:
            force = True
        else:
            raise ValueError(_import_format_error())

    period = _resolve_import_period_token(raw[0])
    return {"action": "choose_mode", "period": period, "force": force}


def parse_import_args(args: list[str]) -> tuple[str, bool]:
    """Compatibility wrapper → (period, force) for gmail/paste start commands."""
    cmd = parse_import_command(args)
    if cmd["action"] not in {"start_gmail", "start_paste"} or not cmd["period"]:
        raise ValueError("Format: /import gmail|paste YYYY-MM [force]")
    return cmd["period"], bool(cmd["force"])


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_eml_bytes(content: bytes) -> dict:
    msg = message_from_bytes(content)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True) or b""
                body = payload.decode(part.get_content_charset() or "utf-8", "ignore")
                break
            if ctype == "text/html" and not body:
                payload = part.get_payload(decode=True) or b""
                body = html_to_text(
                    payload.decode(part.get_content_charset() or "utf-8", "ignore")
                )
    else:
        payload = msg.get_payload(decode=True) or b""
        text = payload.decode(msg.get_content_charset() or "utf-8", "ignore")
        body = html_to_text(text) if msg.get_content_type() == "text/html" else text
    return {
        "from": str(msg.get("From", "") or ""),
        "subject": str(msg.get("Subject", "") or ""),
        "date": str(msg.get("Date", "") or ""),
        "body": body.strip(),
    }


def parse_export_args(args: list[str]) -> tuple[str, bool, str, bool]:
    """Parse /export args into (period, force, mode, auto).

    mode is 'both' (default) or 'tsv'.
    auto=True skips interactive /batch review (recommend + approve valid rows).
    """
    if not args:
        raise ValueError("Format: /export YYYY-MM [tsv] [force] [auto]")
    period = args[0]
    if not (len(period) == 7 and period[4] == "-" and period[:4].isdigit() and period[5:].isdigit()):
        raise ValueError("Format: /export YYYY-MM [tsv] [force] [auto]")
    force = False
    auto = False
    mode = "both"
    for token in args[1:]:
        lowered = token.casefold()
        if lowered == "force":
            force = True
        elif lowered in {"auto", "noreview", "skip-review"}:
            auto = True
        elif lowered == "tsv":
            mode = "tsv"
        else:
            raise ValueError("Format: /export YYYY-MM [tsv] [force] [auto]")
    return period, force, mode, auto


def month_period(value: str) -> str:
    """Normalize an ISO or RFC 2822 date string to YYYY-MM."""
    raw = (value or "").strip()
    try:
        parsed = dt.date.fromisoformat(raw[:10]) if len(raw) >= 10 and raw[4] == "-" else None
        if parsed is None:
            raise ValueError
    except ValueError:
        parsed = _parse_date(raw)
    return f"{parsed.year:04d}-{parsed.month:02d}"


def _parse_date(value: str) -> dt.date:
    try:
        return parsedate_to_datetime(value).date()
    except ValueError:
        for fmt in ("%d %b %Y", "%d %B %Y", "%a, %d %b %Y %H:%M:%S %z"):
            try:
                return dt.datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"Tanggal tidak dikenali: {value}")


class FinanceImportService:
    EDITABLE_FIELDS = {
        "tanggal": "transaction_date",
        "date": "transaction_date",
        "nominal": "amount",
        "amount": "amount",
        "tipe": "type",
        "type": "type",
        "kategori": "category",
        "category": "category",
        "to": "category",  # Transfer destination account (Realbyte Category column)
        "subkategori": "subcategory",
        "subcategory": "subcategory",
        "akun": "account",
        "account": "account",
        "from": "account",
        "merchant": "merchant",
        "note": "merchant",
        "catatan": "merchant",
        "deskripsi": "description",
        "description": "description",
    }

    def __init__(self, db: Database, llm, money=None, spreadsheet_service=None,
                 category_recommendations: CategoryRecommendationLookup | None = None,
                 gmail=None):
        # gmail arg ignored (removed); kept only for old call sites during transition.
        self.db = db
        self.llm = llm
        self.spreadsheet = spreadsheet_service or SpreadsheetFinanceService()
        self.category_recommendations = category_recommendations or CategoryRecommendationLookup(
            category_reference=self.spreadsheet.category_reference
        )

    async def stage_from_sources(
        self,
        sources: list[dict],
        *,
        period_hint: str | None = None,
        force: bool = False,
    ) -> dict:
        """Stage transactions from pasted text / uploaded file dicts.

        Each source may include: id, from, subject, date, body, account,
        and optionally content+mime_type for multimodal extract.
        """
        cleared = 0
        if force:
            cleared = self.db.clear_skipped_emails(summary="bukan transaksi")
        categories = self.spreadsheet.category_reference.prompt_text()
        existing = self.db.list_all_staged_transactions()
        staged = 0
        skipped = 0
        extract_errors = 0
        imported_periods: set[str] = set()
        last_row: dict | None = None

        for source in sources:
            email_id = str(source.get("id") or "").strip()
            if not email_id:
                payload = source.get("content") or str(source.get("body") or "").encode("utf-8")
                email_id = upload_source_id(payload if isinstance(payload, bytes) else str(payload).encode("utf-8"))
            if self.db.is_email_handled(email_id):
                skipped += 1
                continue

            mime = str(source.get("mime_type") or "").strip().casefold()
            content = source.get("content")
            if content is not None and mime in ATTACHMENT_MIME_TYPES:
                tx = await self.llm.extract_transaction_attachment(content, mime, categories)
            else:
                body = str(source.get("body") or "")
                text = (
                    f"Dari: {source.get('from', '')}\n"
                    f"Subjek: {source.get('subject', '')}\n\n"
                    f"{body}"
                ).strip()
                tx = await self.llm.extract_transaction(text, categories)

            if tx.get("extract_error"):
                extract_errors += 1
                continue
            if not tx.get("is_transaction"):
                self.db.mark_email(email_id, "skipped", "bukan transaksi")
                skipped += 1
                continue

            fallback_date = str(source.get("date") or "")
            if period_hint and len(period_hint) == 7 and not str(tx.get("transaction_date") or "").strip():
                # Prefer period hint day-1 if LLM omitted date.
                fallback_date = f"{period_hint}-01"
            transaction_date = self._transaction_date(tx, fallback_date)
            amount = int(tx.get("amount", 0) or 0)
            transaction_type = normalize_tx_type(tx.get("type", "expense"))
            suspicious = bool(tx.get("suspicious_instruction"))
            merchant = str(tx.get("merchant", "") or "")
            description = str(tx.get("description", "") or "")
            if transaction_type == "transfer":
                category = str(tx.get("category") or tx.get("transfer_to") or "").strip() or "-"
                subcategory = str(tx.get("subcategory", "-") or "-") or "-"
                category_ok = category not in {"", "-"}
            else:
                category, subcategory, category_ok = await self.resolve_category(
                    merchant=merchant,
                    description=description,
                    amount=amount,
                    tx_type=transaction_type,
                    category=str(tx.get("category", "Lainnya") or "Lainnya"),
                    subcategory=str(tx.get("subcategory", "-") or "-"),
                )
            candidate = {
                "source_email_id": email_id,
                "account": normalize_finance_account(
                    source.get("account") or settings.finance_account
                ),
                "transaction_date": transaction_date,
                "amount": amount,
                "type": transaction_type,
                "category": category,
                "subcategory": subcategory,
                "merchant": merchant,
                "description": description,
                "needs_review": int(
                    suspicious
                    or amount <= 0
                    or transaction_type not in VALID_TX_TYPES
                    or not category_ok
                ),
                "possible_duplicate": int(
                    self._is_duplicate(
                        existing, transaction_date, amount, transaction_type, merchant
                    )
                ),
            }
            batch = self.db.get_or_create_import_batch(month_period(transaction_date))
            row_id = self.db.stage_transaction(batch["id"], candidate)
            if row_id is not None:
                existing.append(candidate)
                staged += 1
                imported_periods.add(batch["period"])
                self.db.mark_email(email_id, "recorded", f"staged {batch['period']}")
                last_row = {**candidate, "id": row_id, "period": batch["period"]}

        period = period_hint or (sorted(imported_periods)[-1] if imported_periods else default_import_period())
        batch = self.db.get_import_batch(period) if len(period) == 7 else None
        existing_count = len(self.db.list_staged_transactions(batch["id"])) if batch else 0
        return {
            "period": period,
            "batch_id": batch["id"] if batch else None,
            "staged": staged,
            "skipped": skipped,
            "extract_errors": extract_errors,
            "cleared_skips": cleared,
            "existing_in_batch": existing_count,
            "periods": sorted(imported_periods),
            "last_row": last_row,
        }

    async def stage_paste(self, text: str, *, period_hint: str | None = None, force: bool = False) -> dict:
        payload = text.encode("utf-8")
        return await self.stage_from_sources(
            [{
                "id": upload_source_id(payload),
                "from": "",
                "subject": "paste",
                "date": "",
                "body": text,
                "account": settings.finance_account,
            }],
            period_hint=period_hint,
            force=force,
        )

    async def stage_upload(
        self,
        content: bytes,
        mime_type: str,
        *,
        filename: str = "",
        period_hint: str | None = None,
        force: bool = False,
    ) -> dict:
        mime = (mime_type or "application/octet-stream").casefold()
        name = (filename or "").casefold()
        source_id = upload_source_id(content)
        if mime == "message/rfc822" or name.endswith(".eml"):
            parsed = parse_eml_bytes(content)
            source = {"id": source_id, "account": settings.finance_account, **parsed}
        elif mime == "text/html" or name.endswith((".html", ".htm")):
            source = {
                "id": source_id,
                "from": "",
                "subject": filename or "upload.html",
                "date": "",
                "body": html_to_text(content.decode("utf-8", "ignore")),
                "account": settings.finance_account,
            }
        elif mime in TEXT_MIME_TYPES or name.endswith(".txt") or mime.startswith("text/"):
            source = {
                "id": source_id,
                "from": "",
                "subject": filename or "upload.txt",
                "date": "",
                "body": content.decode("utf-8", "ignore"),
                "account": settings.finance_account,
            }
        elif mime in ATTACHMENT_MIME_TYPES:
            source = {
                "id": source_id,
                "from": "",
                "subject": filename or "attachment",
                "date": "",
                "body": "",
                "content": content,
                "mime_type": mime,
                "account": settings.finance_account,
            }
        else:
            raise ValueError(
                f"Tipe file tidak didukung: {mime_type or filename}. "
                "Pakai paste teks, .txt/.eml/.html, PDF, atau gambar."
            )
        return await self.stage_from_sources(
            [source], period_hint=period_hint, force=force
        )

    async def import_period(self, period: str, *, force: bool = False) -> dict:
        """Gmail scan removed — start a paste/upload session instead."""
        cleared = 0
        if force:
            cleared = self.db.clear_skipped_emails(summary="bukan transaksi")
        batch = self.db.get_import_batch(period) if len(period) == 7 else None
        existing_count = len(self.db.list_staged_transactions(batch["id"])) if batch else 0
        return {
            "period": period,
            "batch_id": batch["id"] if batch else None,
            "staged": 0,
            "skipped": 0,
            "extract_errors": 0,
            "cleared_skips": cleared,
            "existing_in_batch": existing_count,
            "periods": [],
            "manual_intake": True,
        }

    def stage_scanned_transaction(self, tx: dict) -> int:
        """Save a daily-confirmed scan into the current monthly review batch."""
        transaction_date = self._transaction_date(tx, dt.date.today().isoformat())
        category = str(tx.get("category", "Lainnya") or "Lainnya")
        subcategory = str(tx.get("subcategory", "-") or "-")
        amount = int(tx.get("amount", 0) or 0)
        transaction_type = normalize_tx_type(tx.get("type", "expense"))
        merchant = str(tx.get("merchant", "") or "")
        if transaction_type == "transfer":
            category = str(tx.get("category") or tx.get("transfer_to") or category).strip() or "-"
            category_ok = category not in {"", "-"}
        else:
            category_ok = self.spreadsheet.category_reference.canonical(category, subcategory) is not None
        candidate = {
            "source_email_id": tx["email_id"],
            "account": normalize_finance_account(tx.get("account") or settings.finance_account),
            "transaction_date": transaction_date,
            "amount": amount,
            "type": transaction_type,
            "category": category,
            "subcategory": subcategory,
            "merchant": merchant,
            "description": str(tx.get("description", "") or ""),
            "needs_review": int(
                bool(tx.get("needs_review"))
                or amount <= 0
                or transaction_type not in VALID_TX_TYPES
                or not category_ok
            ),
        }
        batch = self.db.get_or_create_import_batch(month_period(transaction_date))
        row_id = self.db.stage_transaction(batch["id"], candidate)
        if row_id is None:
            existing = next(
                row for row in self.db.list_staged_transactions(batch["id"])
                if row["source_email_id"] == tx["email_id"]
            )
            row_id = existing["id"]
        self.db.mark_email(tx["email_id"], "staged", f"daily review {batch['period']}")
        return row_id

    @staticmethod
    def _transaction_date(transaction: dict, email_date: str) -> str:
        value = transaction.get("transaction_date") or email_date
        try:
            raw = str(value).strip()
            return dt.date.fromisoformat(raw[:10]).isoformat()
        except ValueError:
            try:
                return _parse_date(str(value)).isoformat()
            except (TypeError, ValueError, IndexError):
                return email_date[:10] if email_date else dt.date.today().isoformat()

    @staticmethod
    def _is_duplicate(existing: list[dict], date: str, amount: int, tx_type: str, merchant: str) -> bool:
        fingerprint = (date, amount, tx_type, merchant.casefold().strip())
        return any(
            (row.get("transaction_date"), row.get("amount"), row.get("type"), row.get("merchant", "").casefold().strip())
            == fingerprint
            for row in existing
        )

    def list_batches(self) -> list[dict]:
        return [self._batch_summary(batch) for batch in self.db.list_import_batches()]

    def latest_review_period(self) -> str | None:
        """Prefer the newest reviewing batch; otherwise the newest batch overall."""
        batches = self.db.list_import_batches()
        if not batches:
            return None
        reviewing = [batch for batch in batches if batch["status"] == "reviewing"]
        chosen = reviewing[-1] if reviewing else batches[-1]
        return chosen["period"]

    async def resolve_category(
        self,
        *,
        merchant: str,
        description: str,
        amount: int,
        tx_type: str,
        category: str,
        subcategory: str,
        force_recommend: bool = False,
        allow_llm: bool = False,
    ) -> tuple[str, str, bool]:
        """Return (category, subcategory, is_valid).

        Priority:
        1. Canonical current pair (unless force_recommend)
        2. Local lookup from config/category_recommendations.json
        3. Similar history rows from MoneyManager-2025.xlsx
        4. Optional LLM (disabled by default for batch speed/reliability)
        """
        reference = self.spreadsheet.category_reference
        canonical = reference.canonical(category, subcategory)
        if canonical and not force_recommend:
            return canonical[0], canonical[1], True

        local = self.category_recommendations.recommend(merchant, description)
        if local:
            return local[0], local[1], True

        for example in self.spreadsheet.history_category_examples(merchant, description):
            candidate = reference.canonical(example["category"], example["subcategory"])
            if candidate:
                return candidate[0], candidate[1], True

        if not allow_llm or self.llm is None:
            if canonical:
                return canonical[0], canonical[1], True
            return category or "Lainnya", subcategory or "-", False

        history_examples = self.spreadsheet.history_examples_prompt(merchant, description)
        recommendation = await self.llm.recommend_category(
            merchant=merchant,
            description=description,
            amount=amount,
            tx_type=tx_type,
            categories_text=reference.prompt_text(),
            history_examples_text=history_examples,
            current_category=category,
            current_subcategory=subcategory,
        )
        recommended_category = str(recommendation.get("category") or category or "Lainnya")
        recommended_subcategory = str(recommendation.get("subcategory") or subcategory or "-")
        canonical = reference.canonical(recommended_category, recommended_subcategory)
        if canonical:
            return canonical[0], canonical[1], True
        return recommended_category, recommended_subcategory, False

    async def recommend_categories_for_batch(self, period: str) -> dict:
        """Apply local/history category recommendations before interactive review."""
        if not self.category_recommendations.is_loaded():
            raise ValueError(
                "File rekomendasi kategori belum ditemukan. "
                "Jalankan: python scripts/build_category_recommendations.py"
            )
        # CategoryReference loads from xlsx, or falls back to pairs in the JSON.
        if not self.spreadsheet.category_reference.is_loaded():
            raise ValueError(
                "Referensi kategori kosong. Pastikan "
                "'Kategori Money Manager.xlsx' ada di data/reference/ "
                "atau config/category_recommendations.json berisi pasangan kategori."
            )
        batch = self.get_batch(period)
        updated = 0
        still_needs_review = 0
        for row in batch["transactions"]:
            tx_type = normalize_tx_type(row.get("type"))
            account = normalize_finance_account(row.get("account"))
            if tx_type == "transfer":
                category = str(row.get("category") or "").strip() or "-"
                subcategory = str(row.get("subcategory") or "-") or "-"
                category_ok = category not in {"", "-"}
            else:
                category, subcategory, category_ok = await self.resolve_category(
                    merchant=str(row.get("merchant") or ""),
                    description=str(row.get("description") or ""),
                    amount=int(row.get("amount") or 0),
                    tx_type=tx_type,
                    category=str(row.get("category") or "Lainnya"),
                    subcategory=str(row.get("subcategory") or "-"),
                    force_recommend=True,
                    allow_llm=False,
                )
            needs_review = int(
                (not category_ok)
                or int(row.get("amount") or 0) <= 0
                or tx_type not in VALID_TX_TYPES
                or not account
            )
            changed = (
                category != row.get("category")
                or subcategory != row.get("subcategory")
                or account != row.get("account")
                or tx_type != row.get("type")
                or needs_review != int(bool(row.get("needs_review")))
            )
            if changed:
                self.db.update_staged_transaction(
                    row["id"],
                    {
                        "category": category,
                        "subcategory": subcategory,
                        "account": account,
                        "type": tx_type,
                    },
                )
                self.db.set_staged_needs_review(row["id"], needs_review)
                updated += 1
            if needs_review:
                still_needs_review += 1
        return {
            "period": period,
            "updated": updated,
            "still_needs_review": still_needs_review,
            "total": batch["count"],
        }

    async def validate_category_for_row(self, row: dict) -> dict:
        """Run LLM validation on a staged row after local recommendation.

        Returns display metadata. Updates staging when LLM proposes a valid alternative.
        The user still decides via OK/Ubah/Lewati.
        """
        merchant = str(row.get("merchant") or "")
        description = str(row.get("description") or "")
        proposed_category = str(row.get("category") or "Lainnya")
        proposed_subcategory = str(row.get("subcategory") or "-")
        reference = self.spreadsheet.category_reference
        tx_type = normalize_tx_type(row.get("type"))

        if tx_type == "transfer":
            category_ok = proposed_category not in {"", "-"}
            expense_suggestion = await self.suggest_expense_alternative(row)
            return {
                "lookup_category": proposed_category,
                "lookup_subcategory": proposed_subcategory,
                "category": proposed_category,
                "subcategory": proposed_subcategory,
                "agreed": True,
                "note": "Transfer: Category = destination account (To).",
                "category_ok": category_ok,
                "expense_suggestion": expense_suggestion,
            }

        if self.llm is None:
            return {
                "lookup_category": proposed_category,
                "lookup_subcategory": proposed_subcategory,
                "category": proposed_category,
                "subcategory": proposed_subcategory,
                "agreed": True,
                "note": "LLM tidak aktif.",
                "category_ok": reference.canonical(proposed_category, proposed_subcategory) is not None,
            }

        validation = await self.llm.validate_category_recommendation(
            merchant=merchant,
            description=description,
            amount=int(row.get("amount") or 0),
            tx_type=tx_type,
            proposed_category=proposed_category,
            proposed_subcategory=proposed_subcategory,
            categories_text=reference.prompt_text(),
            history_examples_text=self.spreadsheet.history_examples_prompt(merchant, description),
        )
        category = str(validation.get("category") or proposed_category)
        subcategory = str(validation.get("subcategory") or proposed_subcategory)
        canonical = reference.canonical(category, subcategory)
        if canonical:
            category, subcategory = canonical
            category_ok = True
        else:
            # Keep local proposal if LLM returned an invalid pair.
            category, subcategory = proposed_category, proposed_subcategory
            category_ok = reference.canonical(category, subcategory) is not None
            validation["agreed"] = False
            validation["note"] = (
                (validation.get("note") or "") + " Usulan LLM di luar daftar; tetap pakai lookup lokal."
            ).strip()

        if category != row.get("category") or subcategory != row.get("subcategory"):
            patched = {**row, "category": category, "subcategory": subcategory, "type": tx_type}
            needs_review = int(self._row_needs_review(patched))
            self.db.update_staged_transaction(
                row["id"],
                {"category": category, "subcategory": subcategory},
            )
            self.db.set_staged_needs_review(row["id"], needs_review)

        return {
            "lookup_category": proposed_category,
            "lookup_subcategory": proposed_subcategory,
            "category": category,
            "subcategory": subcategory,
            "agreed": bool(validation.get("agreed")),
            "note": str(validation.get("note") or "").strip(),
            "category_ok": category_ok,
        }

    async def suggest_expense_alternative(self, row: dict) -> dict:
        """Recommend an Expense category if this Transfer is actually living cost."""
        merchant = str(row.get("merchant") or "")
        description = str(row.get("description") or "")
        to_account = str(row.get("category") or "")
        hay = f"{merchant} {description} {to_account}".casefold()

        living = self._living_cost_label(hay)
        if living:
            pair = self.spreadsheet.category_reference.canonical(living["category"], living["subcategory"])
            if pair:
                return {
                    "category": pair[0],
                    "subcategory": pair[1],
                    "note": living["note"],
                    "category_ok": True,
                    "reason": f"Living cost → Note '{living['note']}'",
                }

        category, subcategory, category_ok = await self.resolve_category(
            merchant=merchant,
            description=description,
            amount=int(row.get("amount") or 0),
            tx_type="expense",
            category="Lainnya",
            subcategory="-",
            force_recommend=True,
            allow_llm=False,
        )
        note = merchant.strip() or None
        if category_ok and _key(category) == "keluarga":
            if _key(subcategory) == "istri":
                note = "Bulanan Istri"
            elif _key(subcategory) in {"orang tua", "orangtua"}:
                note = "Bulanan Mama"
        if not category_ok:
            transfer_fee = self.spreadsheet.category_reference.canonical(
                "Penarikan Dana dan Transfer", "Biaya Transfer"
            )
            if transfer_fee:
                category, subcategory = transfer_fee
                category_ok = True
        return {
            "category": category,
            "subcategory": subcategory,
            "note": note,
            "category_ok": category_ok,
            "reason": "Possible living-cost Expense instead of account Transfer",
        }

    @staticmethod
    def _living_cost_label(hay: str) -> dict | None:
        """Map common family-support wording to Keluarga + preferred Note."""
        if any(
            token in hay
            for token in (
                "bulanan istri", "istri", "nurizka", "tabungan - istri", "tabungan istri",
            )
        ):
            return {"category": "Keluarga", "subcategory": "Istri", "note": "Bulanan Istri"}
        if any(
            token in hay
            for token in (
                "bulanan mama", "mama", "ibu", "ratna", "orang tua", "orangtua",
            )
        ):
            return {"category": "Keluarga", "subcategory": "Orang Tua", "note": "Bulanan Mama"}
        return None

    async def convert_transfer_to_expense(
        self,
        transaction_id: int,
        *,
        category: str | None = None,
        subcategory: str | None = None,
    ) -> dict:
        """Convert a Transfer row to Expense with a recommended living-cost category."""
        row = self.db.get_staged_transaction(transaction_id)
        if not row:
            raise ValueError(f"Transaksi [{transaction_id}] tidak ditemukan.")
        if normalize_tx_type(row.get("type")) != "transfer":
            raise ValueError(f"Transaksi [{transaction_id}] bukan Transfer.")

        if category and subcategory:
            chosen = self.spreadsheet.category_reference.canonical(category, subcategory)
            if not chosen:
                raise ValueError(f"Kategori tidak valid: {category} / {subcategory}")
            final_category, final_subcategory = chosen
            category_ok = True
            note = str(row.get("merchant") or "")
            if _key(final_subcategory) == "istri":
                note = "Bulanan Istri"
            elif _key(final_subcategory) in {"orang tua", "orangtua"}:
                note = "Bulanan Mama"
            suggestion = {
                "category": final_category,
                "subcategory": final_subcategory,
                "note": note,
                "category_ok": True,
                "reason": "User-selected Expense category",
            }
        else:
            suggestion = await self.suggest_expense_alternative(row)
            final_category = suggestion["category"]
            final_subcategory = suggestion["subcategory"]
            category_ok = bool(suggestion.get("category_ok"))
            note = str(suggestion.get("note") or row.get("merchant") or "")

        account = normalize_finance_account(row.get("account"))
        patched = {
            **row,
            "type": "expense",
            "account": account,
            "category": final_category,
            "subcategory": final_subcategory,
            "merchant": note,
        }
        needs_review = int(self._row_needs_review(patched) or not category_ok)
        self.db.update_staged_transaction(
            transaction_id,
            {
                "type": "expense",
                "account": account,
                "category": final_category,
                "subcategory": final_subcategory,
                "merchant": note,
            },
        )
        self.db.set_staged_needs_review(transaction_id, needs_review)
        return {
            "id": transaction_id,
            "type": "expense",
            "category": final_category,
            "subcategory": final_subcategory,
            "note": note,
            "needs_review": needs_review,
            "suggestion": suggestion,
        }

    async def reresolve_transaction_category(self, transaction_id: int) -> str:
        """Re-run history-aware category recommendation after a field edit."""
        row = self.db.get_staged_transaction(transaction_id)
        if not row:
            raise ValueError(f"Transaksi [{transaction_id}] tidak ditemukan.")
        tx_type = normalize_tx_type(row.get("type"))
        if tx_type == "transfer":
            account = normalize_finance_account(row.get("account"))
            category = str(row.get("category") or "").strip() or "-"
            needs_review = int(self._row_needs_review({**row, "account": account, "type": tx_type}))
            self.db.update_staged_transaction(transaction_id, {"account": account, "type": tx_type})
            self.db.set_staged_needs_review(transaction_id, needs_review)
            suggestion = await self.suggest_expense_alternative({**row, "account": account})
            status = "siap OK" if not needs_review else "masih perlu review"
            return (
                f"🔁 Transfer [{transaction_id}]: From {account} → To {category} ({status}).\n"
                f"💡 Alternative Expense: {suggestion['category']} / {suggestion['subcategory']}"
            )
        # Coming from Transfer → Expense: old Category may still be a destination account.
        prior_category = str(row.get("category") or "")
        looks_like_account = prior_category not in {"", "-"} and (
            self.spreadsheet.category_reference.canonical(prior_category, str(row.get("subcategory") or "-"))
            is None
        )
        seed_category = "Lainnya" if looks_like_account else prior_category
        seed_subcategory = "-" if looks_like_account else str(row.get("subcategory") or "-")
        category, subcategory, category_ok = await self.resolve_category(
            merchant=str(row.get("merchant") or ""),
            description=str(row.get("description") or ""),
            amount=int(row.get("amount") or 0),
            tx_type=tx_type,
            category=seed_category,
            subcategory=seed_subcategory,
            force_recommend=True,
            allow_llm=False,
        )
        account = normalize_finance_account(row.get("account"))
        needs_review = int(
            self._row_needs_review({
                **row,
                "category": category,
                "subcategory": subcategory,
                "account": account,
                "type": tx_type,
            })
        )
        self.db.update_staged_transaction(
            transaction_id,
            {"category": category, "subcategory": subcategory, "account": account, "type": tx_type},
        )
        self.db.set_staged_needs_review(transaction_id, needs_review)
        status = "siap OK" if not needs_review else "masih perlu review"
        return (
            f"🔁 Recommendation [{transaction_id}]: "
            f"Type {tx_type} · {category} / {subcategory} ({status})."
        )

    def get_batch(self, period: str) -> dict:
        batch = self.db.get_import_batch(period)
        if not batch:
            raise ValueError(f"Batch {period} belum ditemukan.")
        return {**self._batch_summary(batch), "transactions": self.db.list_staged_transactions(batch["id"])}

    def _batch_summary(self, batch: dict) -> dict:
        rows = self.db.list_staged_transactions(batch["id"])
        return {
            **batch,
            "count": len(rows),
            "expense_total": sum(row["amount"] for row in rows if row["type"] == "expense"),
            "income_total": sum(row["amount"] for row in rows if row["type"] == "income"),
            "transfer_total": sum(row["amount"] for row in rows if row["type"] == "transfer"),
            "needs_review": sum(row["needs_review"] for row in rows),
            "possible_duplicate": sum(row["possible_duplicate"] for row in rows),
        }

    def edit_transaction(self, transaction_id: int, field: str, value: str) -> str:
        column = self.EDITABLE_FIELDS.get(field.strip().casefold())
        if not column:
            raise ValueError(
                "Field harus: tanggal, nominal, tipe, kategori, subkategori, akun, merchant/note, atau deskripsi."
            )
        row = self.db.get_staged_transaction(transaction_id)
        if not row:
            raise ValueError(f"Transaksi [{transaction_id}] tidak ditemukan.")

        patch: dict = {}
        display_value: object = value

        if column == "amount":
            parsed = int(value.replace(".", "").replace(",", "").strip())
            if parsed <= 0:
                raise ValueError("Nominal harus lebih besar dari nol.")
            patch["amount"] = parsed
            display_value = parsed
        elif column == "type":
            parsed_type = normalize_tx_type(value)
            if parsed_type not in VALID_TX_TYPES:
                raise ValueError("Type harus expense, income, atau transfer.")
            patch["type"] = parsed_type
            display_value = parsed_type
        elif column == "transaction_date":
            patch["transaction_date"] = dt.date.fromisoformat(value.strip()).isoformat()
            display_value = patch["transaction_date"]
        elif column == "account":
            patch["account"] = normalize_finance_account(value)
            display_value = patch["account"]
        elif column == "category":
            raw = value.strip()
            if not raw:
                raise ValueError("category tidak boleh kosong.")
            tx_type = normalize_tx_type(row.get("type"))
            if "/" in raw:
                category_part, _, subcategory_part = raw.partition("/")
                category_part = category_part.strip()
                subcategory_part = subcategory_part.strip() or "-"
            else:
                category_part = raw
                subcategory_part = str(row.get("subcategory") or "-") or "-"

            if tx_type == "transfer":
                # Realbyte: Category column = destination account (To).
                patch["category"] = category_part
                patch["subcategory"] = subcategory_part if "/" in raw else (str(row.get("subcategory") or "-") or "-")
                display_value = f"{patch['category']} / {patch['subcategory']}"
            else:
                canonical = self.spreadsheet.category_reference.canonical(category_part, subcategory_part)
                if not canonical:
                    suggestions = self.spreadsheet.category_reference.suggest(
                        category_part, subcategory_part, limit=5
                    )
                    attempted = f"{category_part} / {subcategory_part}"
                    raise InvalidCategoryError(
                        f"Kategori tidak valid untuk import Money Manager: {attempted}",
                        attempted=attempted,
                        suggestions=suggestions,
                        transaction_id=transaction_id,
                    )
                patch["category"] = canonical[0]
                patch["subcategory"] = canonical[1]
                display_value = f"{canonical[0]} / {canonical[1]}"
        elif column == "subcategory":
            raw = value.strip() or "-"
            if not raw:
                raise ValueError("subcategory tidak boleh kosong.")
            tx_type = normalize_tx_type(row.get("type"))
            if tx_type == "transfer":
                patch["subcategory"] = raw
                display_value = raw
            else:
                category_part = str(row.get("category") or "")
                canonical = self.spreadsheet.category_reference.canonical(category_part, raw)
                if not canonical:
                    suggestions = self.spreadsheet.category_reference.suggest(
                        category_part, raw, limit=5
                    )
                    attempted = f"{category_part} / {raw}"
                    raise InvalidCategoryError(
                        f"Subcategory tidak valid untuk import Money Manager: {attempted}",
                        attempted=attempted,
                        suggestions=suggestions,
                        transaction_id=transaction_id,
                    )
                patch["category"] = canonical[0]
                patch["subcategory"] = canonical[1]
                display_value = f"{canonical[0]} / {canonical[1]}"
        elif column == "merchant":
            if not str(value).strip():
                raise ValueError(f"{field} tidak boleh kosong.")
            patch["merchant"] = value.strip()
            display_value = patch["merchant"]
        elif column == "description":
            if not str(value).strip():
                raise ValueError(f"{field} tidak boleh kosong.")
            patch["description"] = value.strip()
            display_value = patch["description"]
        else:
            raise ValueError(f"Field tidak didukung: {field}")

        patch["account"] = normalize_finance_account(patch.get("account", row.get("account")))
        if "type" in patch:
            patch["type"] = normalize_tx_type(patch["type"])
        elif row.get("type"):
            # keep existing
            pass

        self.db.update_staged_transaction(transaction_id, patch)
        updated = self.db.get_staged_transaction(transaction_id) or {**row, **patch}
        needs_review = self._row_needs_review(updated)
        self.db.set_staged_needs_review(transaction_id, needs_review)
        return f"✅ Transaksi [{transaction_id}] diperbarui ({field}: {display_value}). Belum diekspor."

    def mark_transaction_ok(self, transaction_id: int) -> str:
        row = self.db.get_staged_transaction(transaction_id)
        if not row:
            raise ValueError(f"Transaksi [{transaction_id}] tidak ditemukan.")
        if self._row_needs_review(row):
            if normalize_tx_type(row.get("type")) != "transfer":
                suggestions = self.spreadsheet.category_reference.suggest(
                    str(row.get("category") or ""),
                    str(row.get("subcategory") or "-"),
                    limit=5,
                )
                # Also boost from note/description lookup.
                local = self.category_recommendations.recommend(
                    str(row.get("merchant") or ""),
                    str(row.get("description") or ""),
                )
                if local and local not in suggestions:
                    suggestions = [local, *[pair for pair in suggestions if pair != local]][:5]
                attempted = f"{row.get('category') or '-'} / {row.get('subcategory') or '-'}"
                raise InvalidCategoryError(
                    "Kategori/akun/nominal belum valid untuk import Money Manager.",
                    attempted=attempted,
                    suggestions=suggestions,
                    transaction_id=transaction_id,
                )
            raise ValueError(
                "Kategori/akun/nominal belum valid. Ubah dulu sebelum menandai OK."
            )
        self.db.set_staged_needs_review(transaction_id, 0)
        return f"✅ Transaksi [{transaction_id}] siap diekspor."

    def apply_category_pair(self, transaction_id: int, category: str, subcategory: str) -> str:
        """Apply a validated category pair chosen from suggestions."""
        row = self.db.get_staged_transaction(transaction_id)
        if not row:
            raise ValueError(f"Transaksi [{transaction_id}] tidak ditemukan.")
        if normalize_tx_type(row.get("type")) == "transfer":
            raise ValueError("Gunakan tombol → Expense untuk mengubah Transfer.")
        canonical = self.spreadsheet.category_reference.canonical(category, subcategory)
        if not canonical:
            raise InvalidCategoryError(
                f"Kategori tidak valid: {category} / {subcategory}",
                attempted=f"{category} / {subcategory}",
                suggestions=self.spreadsheet.category_reference.suggest(category, subcategory),
                transaction_id=transaction_id,
            )
        account = normalize_finance_account(row.get("account"))
        self.db.update_staged_transaction(
            transaction_id,
            {
                "category": canonical[0],
                "subcategory": canonical[1],
                "account": account,
            },
        )
        updated = self.db.get_staged_transaction(transaction_id) or row
        needs_review = self._row_needs_review(updated)
        self.db.set_staged_needs_review(transaction_id, needs_review)
        status = "siap OK / import" if not needs_review else "masih perlu review"
        return (
            f"✅ Category updated [{transaction_id}]: "
            f"{canonical[0]} / {canonical[1]} ({status})."
        )

    def _row_needs_review(self, row: dict) -> bool:
        amount = int(row.get("amount", 0) or 0)
        transaction_type = normalize_tx_type(row.get("type"))
        category = str(row.get("category", "") or "")
        subcategory = str(row.get("subcategory", "-") or "-")
        account = normalize_finance_account(row.get("account"))
        if amount <= 0 or transaction_type not in VALID_TX_TYPES or not account:
            return True
        if transaction_type == "transfer":
            return category in {"", "-"}
        return self.spreadsheet.category_reference.canonical(category, subcategory) is None

    def skip_period(self, period: str) -> str:
        self._require_batch(period)
        return f"⚠️ Batch {period} belum dilewati. Tidak ada data yang dikirim ke aplikasi."

    def confirm_skip(self, period: str) -> str:
        self._require_batch(period)
        self.db.set_import_batch_status(period, "skipped")
        return f"⏭️ Batch {period} dilewati. Tidak ada file dibuat."

    async def prepare_batch_auto(self, period: str) -> dict:
        """Apply local recommendations and auto-approve rows that are export-valid.

        Skips interactive /batch review. Rows that remain invalid are left flagged.
        """
        recommended = 0
        if self.category_recommendations.is_loaded() and self.spreadsheet.category_reference.is_loaded():
            recommendation = await self.recommend_categories_for_batch(period)
            recommended = int(recommendation.get("updated", 0))
        batch = self.get_batch(period)
        approved = 0
        still_invalid: list[int] = []
        for row in batch["transactions"]:
            patched = {
                **row,
                "account": normalize_finance_account(row.get("account")),
                "type": normalize_tx_type(row.get("type")),
            }
            if patched["account"] != row.get("account") or patched["type"] != row.get("type"):
                self.db.update_staged_transaction(
                    row["id"],
                    {"account": patched["account"], "type": patched["type"]},
                )
                row = {**row, **patched}
            if self._row_needs_review(row):
                still_invalid.append(row["id"])
                self.db.set_staged_needs_review(row["id"], 1)
                continue
            self.db.set_staged_needs_review(row["id"], 0)
            approved += 1
        return {
            "period": period,
            "recommended": recommended,
            "approved": approved,
            "still_invalid": still_invalid,
            "total": batch["count"],
        }

    async def export_period(self, period: str, force: bool = False, *, auto: bool = False) -> ExportResult:
        batch = self._require_batch(period)
        if auto:
            prep = await self.prepare_batch_auto(period)
            if prep["still_invalid"]:
                ids = ", ".join(str(item) for item in prep["still_invalid"][:12])
                extra = "…" if len(prep["still_invalid"]) > 12 else ""
                raise ValueError(
                    f"Auto-export: {len(prep['still_invalid'])} transaksi masih invalid "
                    f"(tidak bisa diimport Money Manager): {ids}{extra}. "
                    f"Perbaiki via /batch {period}, lalu /export {period} tsv force."
                )
        rows = self.db.list_staged_transactions(batch["id"])
        if not rows:
            raise ValueError(f"Tidak ada transaksi untuk diekspor pada batch {period}.")
        flagged = [row for row in rows if row["needs_review"] or self._row_needs_review(row)]
        if flagged:
            ids = ", ".join(str(row["id"]) for row in flagged[:12])
            extra = "…" if len(flagged) > 12 else ""
            raise ValueError(
                f"{len(flagged)} transaksi masih perlu review sebelum export: {ids}{extra}. "
                f"Gunakan /batch {period} atau /export {period} tsv force auto."
            )
        result = self.spreadsheet.export_batch(period, rows, force=force)
        self.db.set_import_batch_status(period, "exported")
        return result

    @staticmethod
    def format_export_message(result: ExportResult) -> str:
        return (
            f"✅ Batch {result.period} diekspor ({result.rows} transaksi).\n"
            f"XLSX: {result.xlsx_path}\n"
            f"TSV: {result.tsv_path}\n\n"
            "Cara import Money Manager (Realbyte):\n"
            "1. Pakai file .tsv (tab-separated), nama file bebas\n"
            "2. More → Backup → Import Excel File → pilih file TSV\n"
            "3. Tekan + untuk mendaftarkan data\n"
            "Tanggal: dd/MM/yyyy (contoh 16/07/2026), sama seperti export Money Manager."
        )

    def _require_batch(self, period: str) -> dict:
        batch = self.db.get_import_batch(period)
        if not batch:
            raise ValueError(f"Batch {period} belum ditemukan.")
        return batch
