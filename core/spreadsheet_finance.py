"""Realbyte-compatible spreadsheet export and finance reporting."""

from __future__ import annotations

import csv
import datetime as dt
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from openpyxl import Workbook, load_workbook
from difflib import SequenceMatcher

from config.settings import settings

REALBYTE_COLUMNS = [
    "Date", "Account", "Category", "Subcategory", "Note", "Amount",
    "Income/Expense", "Description",
]


def _key(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


class CategoryReference:
    def __init__(self, path: str | None = None, recommendations_path: str | None = None):
        self.path = Path(path or settings.finance_category_reference)
        self.recommendations_path = Path(
            recommendations_path or settings.finance_category_recommendations
        )
        self._pairs: dict[tuple[str, str], tuple[str, str]] | None = None

    def pairs(self) -> set[tuple[str, str]]:
        self._load()
        return set((self._pairs or {}).values())

    def canonical(self, category: str, subcategory: str = "-") -> tuple[str, str] | None:
        self._load()
        return (self._pairs or {}).get((_key(category), _key(subcategory)))

    def suggest(
        self,
        category: str,
        subcategory: str = "-",
        *,
        limit: int = 5,
        prefer_category: str | None = None,
    ) -> list[tuple[str, str]]:
        """Return closest valid category/subcategory pairs for an invalid input."""
        self._load()
        values = list((self._pairs or {}).values())
        if not values:
            return []

        query_cat = _key(category)
        query_sub = _key(subcategory or "-")
        query = f"{query_cat} / {query_sub}".strip(" /")
        prefer = _key(prefer_category or category)

        scored: list[tuple[float, tuple[str, str]]] = []
        for pair in values:
            cat, sub = pair
            cat_key, sub_key = _key(cat), _key(sub)
            label = f"{cat_key} / {sub_key}"
            score = 0.0
            if cat_key == query_cat:
                score += 5.0
            elif prefer and cat_key == prefer:
                score += 3.0
            elif query_cat and (query_cat in cat_key or cat_key in query_cat):
                score += 2.0
            if sub_key == query_sub:
                score += 4.0
            elif query_sub and query_sub not in {"", "-"} and (
                query_sub in sub_key or sub_key in query_sub
            ):
                score += 2.5
            # Token overlap for typos like "Makan Siang" vs "Makanan"
            query_tokens = {token for token in query.replace("/", " ").split() if len(token) >= 3}
            pair_tokens = {token for token in label.replace("/", " ").split() if len(token) >= 3}
            if query_tokens and pair_tokens:
                score += 1.5 * len(query_tokens & pair_tokens)
            # Light fuzzy ratio
            score += SequenceMatcher(None, query, label).ratio()
            if score <= 0:
                continue
            scored.append((score, pair))

        scored.sort(key=lambda item: (-item[0], item[1][0], item[1][1]))
        unique: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for _, pair in scored:
            if pair in seen:
                continue
            seen.add(pair)
            unique.append(pair)
            if len(unique) >= limit:
                break
        if not unique:
            # Fallback: pairs under the same category name, then alphabetical.
            same = sorted(pair for pair in values if _key(pair[0]) == prefer)
            unique = same[:limit] or sorted(values)[:limit]
        return unique

    def prompt_text(self) -> str:
        self._load()
        values = sorted(self._pairs.values()) if self._pairs else []
        return "\n".join(f"- {category} / {subcategory}" for category, subcategory in values)

    def is_loaded(self) -> bool:
        self._load()
        return bool(self._pairs)

    def _load(self) -> None:
        if self._pairs is not None:
            return
        pairs = self._load_from_xlsx()
        if not pairs:
            # Docker often mounts only data/; fall back to pairs embedded in
            # category_recommendations.json so /batch still works offline.
            pairs = self._load_from_recommendations_json()
        self._pairs = pairs

    def _load_from_xlsx(self) -> dict[tuple[str, str], tuple[str, str]]:
        pairs: dict[tuple[str, str], tuple[str, str]] = {}
        if not self.path.exists():
            return pairs
        workbook = load_workbook(self.path, read_only=True, data_only=True)
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                values = [str(value).strip() for value in row if value not in (None, "")]
                if not values or values[0] in {"Kategori Pengeluaran", "Kategori Pendapatan"}:
                    continue
                category = values[0]
                subcategories = values[1:] or ["-"]
                for subcategory in subcategories:
                    pairs[(_key(category), _key(subcategory))] = (category, subcategory)
        return pairs

    def _load_from_recommendations_json(self) -> dict[tuple[str, str], tuple[str, str]]:
        import json

        pairs: dict[tuple[str, str], tuple[str, str]] = {}
        if not self.recommendations_path.exists():
            return pairs
        try:
            payload = json.loads(self.recommendations_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return pairs

        explicit = payload.get("pairs") or []
        for item in explicit:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            category = str(item[0] or "").strip()
            subcategory = str(item[1] or "-").strip() or "-"
            if not category:
                continue
            pairs[(_key(category), _key(subcategory))] = (category, subcategory)

        for section in ("phrases", "keywords"):
            for row in payload.get(section) or []:
                if not isinstance(row, dict):
                    continue
                category = str(row.get("category") or "").strip()
                subcategory = str(row.get("subcategory") or "-").strip() or "-"
                if not category:
                    continue
                pairs[(_key(category), _key(subcategory))] = (category, subcategory)
        return pairs


@dataclass(frozen=True)
class ExportResult:
    period: str
    xlsx_path: str
    tsv_path: str
    rows: int


class SpreadsheetFinanceService:
    def __init__(self, export_dir: str | None = None, category_reference: str | None = None,
                 history_workbook: str | None = None):
        self.export_dir = Path(export_dir or settings.finance_export_dir)
        self.category_reference = CategoryReference(category_reference)
        self.history_workbook = Path(history_workbook or settings.finance_history_workbook)

    def export_batch(self, period: str, rows: list[dict], force: bool = False) -> ExportResult:
        if not re.fullmatch(r"\d{4}-\d{2}", period):
            raise ValueError("Periode harus berformat YYYY-MM.")
        self.export_dir.mkdir(parents=True, exist_ok=True)
        xlsx_path = self.export_dir / f"{period}.xlsx"
        tsv_path = self.export_dir / f"{period}.tsv"
        if not force and (xlsx_path.exists() or tsv_path.exists()):
            raise ValueError(f"Export {period} sudah ada. Gunakan export ulang secara eksplisit.")

        output = [self._row(row) for row in rows]
        invalid = [
            row for row, original in zip(output, rows)
            if row[1] == ""
            or (
                str(original.get("type") or "").casefold() != "transfer"
                and not self.category_reference.canonical(str(original.get("category") or ""), str(original.get("subcategory") or "-") or "-")
            )
            or (
                str(original.get("type") or "").casefold() == "transfer"
                and not str(original.get("category") or "").strip()
            )
        ]
        if invalid:
            raise ValueError(f"{len(invalid)} transaksi memiliki account/kategori yang belum valid.")

        temporary_xlsx = xlsx_path.with_suffix(".xlsx.tmp")
        temporary_tsv = tsv_path.with_suffix(".tsv.tmp")

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(REALBYTE_COLUMNS)
        # Keep Date column as text so Sheets/Excel won't re-parse locale formats.
        for col in range(1, len(REALBYTE_COLUMNS) + 1):
            sheet.cell(1, col).number_format = "@"
        for values in output:
            sheet.append(values)
            sheet.cell(sheet.max_row, 1).number_format = "@"
        sheet.freeze_panes = "A2"
        workbook.save(temporary_xlsx)

        # Tab-separated + CRLF (Realbyte uses tabs, not commas).
        with temporary_tsv.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
            writer.writerow(REALBYTE_COLUMNS)
            writer.writerows(output)
        temporary_xlsx.replace(xlsx_path)
        temporary_tsv.replace(tsv_path)
        return ExportResult(period, str(xlsx_path), str(tsv_path), len(output))

    def _row(self, row: dict) -> list[object]:
        try:
            date = dt.date.fromisoformat(str(row["transaction_date"])[:10])
        except (KeyError, ValueError):
            raise ValueError(f"Tanggal transaksi tidak valid: {row.get('transaction_date', '')}")
        amount = int(row.get("amount", 0))
        if amount <= 0:
            raise ValueError("Nominal transaksi harus lebih besar dari nol.")

        account = str(row.get("account") or settings.finance_account).strip() or settings.finance_account
        category = str(row.get("category", "")).strip()
        subcategory = str(row.get("subcategory", "-") or "-").strip() or "-"
        tx_type = str(row.get("type") or "expense").casefold()

        # Match MoneyManager-2025.xlsx (app's own export):
        # Income/Expense values are "Income" / "Expense" (singular).
        # Help-center "Expenses"/"Transfer-Out" variants are NOT what this app exports.
        # Transfer-Out: Account=From, Category=destination account (To).
        if tx_type == "income":
            kind = "Income"
            canonical = self.category_reference.canonical(category, subcategory)
            if canonical:
                category, subcategory = canonical
        elif tx_type == "transfer":
            expense_pair = self.category_reference.canonical(category, subcategory)
            if expense_pair:
                # Staged as transfer but category is a spending pair → export as Expense
                kind = "Expense"
                category, subcategory = expense_pair
            else:
                if not category:
                    raise ValueError("Transfer membutuhkan Category = akun tujuan (To).")
                kind = "Transfer-Out"
                subcategory = "-"
        else:
            kind = "Expense"
            canonical = self.category_reference.canonical(category, subcategory)
            if canonical:
                category, subcategory = canonical

        date_fmt = settings.finance_export_date_format or "%d/%m/%Y"
        note = str(row.get("merchant", "") or "").strip()
        description = str(row.get("description", "") or "").strip()
        return [
            date.strftime(date_fmt), account,
            category, subcategory, note, amount, kind,
            description,
        ]

    def read_rows(self) -> list[dict]:
        rows: list[dict] = []
        if self.history_workbook.exists():
            rows.extend(self._read_workbook(self.history_workbook))
        for path in sorted(self.export_dir.glob("????-??.xlsx")):
            if path.resolve() != self.history_workbook.resolve():
                rows.extend(self._read_workbook(path))
        return rows

    def history_category_examples(
        self,
        merchant: str,
        description: str,
        *,
        limit: int = 8,
    ) -> list[dict]:
        """Return similar historical rows to guide category recommendations."""
        query = _key(f"{merchant} {description}")
        if not query:
            return []
        tokens = {token for token in query.split() if len(token) >= 3}
        scored: list[tuple[int, dict]] = []
        for row in self.read_rows():
            hay = _key(f"{row.get('note', '')} {row.get('description', '')}")
            if not hay:
                continue
            score = 0
            if query in hay or hay in query:
                score += 10
            score += sum(2 for token in tokens if token in hay)
            drink_query = any(
                token in query for token in ("kopi", "cafe", "coffee", "americano", "latte", "espresso")
            )
            if drink_query:
                if _key(row.get("subcategory", "")) == "cafe" or any(
                    token in hay for token in ("kopi", "cafe", "coffee", "famima")
                ):
                    score += 6
                if _key(row.get("subcategory", "")) == "grocery" and "americano" in query:
                    score -= 2
            if score <= 0:
                continue
            subcategory = str(row.get("subcategory") or "-").strip() or "-"
            scored.append((score, {
                "category": str(row.get("category") or ""),
                "subcategory": subcategory,
                "note": str(row.get("note") or ""),
                "description": str(row.get("description") or ""),
                "amount": int(row.get("amount") or 0),
            }))
        scored.sort(key=lambda item: (-item[0], -item[1]["amount"]))
        # Deduplicate by category/subcategory + short description.
        unique: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for _, row in scored:
            key = (_key(row["category"]), _key(row["subcategory"]), _key(row["description"])[:48])
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
            if len(unique) >= limit:
                break
        return unique

    def history_examples_prompt(self, merchant: str, description: str, *, limit: int = 8) -> str:
        examples = self.history_category_examples(merchant, description, limit=limit)
        if not examples:
            return "(tidak ada contoh historis yang mirip di MoneyManager-2025.xlsx)"
        lines = []
        for row in examples:
            label = f"{row['category']} / {row['subcategory']}"
            note = str(row.get("note") or "").strip()
            desc = str(row.get("description") or "").strip()
            sample = note if note not in {"", "-"} else desc
            sample = sample or "-"
            lines.append(f'- "{sample}" → {label}')
        return "\n".join(lines)

    def _read_workbook(self, path: Path) -> list[dict]:
        workbook = load_workbook(path, read_only=True, data_only=True)
        sheet = workbook.active
        headers = [str(value).strip() if value is not None else "" for value in next(sheet.iter_rows(values_only=True))]
        if headers[: len(REALBYTE_COLUMNS)] != REALBYTE_COLUMNS:
            return []
        result = []
        for values in sheet.iter_rows(min_row=2, values_only=True):
            if not values or values[0] in (None, ""):
                continue
            try:
                date = self._date_value(values[0])
                amount = int(float(values[5] or 0))
            except (TypeError, ValueError):
                continue
            result.append({
                "date": date, "account": str(values[1] or ""), "category": str(values[2] or ""),
                "subcategory": str(values[3] or "-"), "note": str(values[4] or ""),
                "amount": amount, "kind": str(values[6] or "").title(),
                "description": str(values[7] or ""),
            })
        return result

    @staticmethod
    def _date_value(value: object) -> dt.date:
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        raw = str(value).strip()
    @staticmethod
    def _date_value(value: object) -> dt.date:
        if isinstance(value, dt.datetime):
            return value.date()
        if isinstance(value, dt.date):
            return value
        raw = str(value).strip()
        for fmt in (
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y %H:%M",
            "%m/%d/%Y",
            "%d.%m.%Y",
            "%Y.%m.%d",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%Y/%m/%d",
        ):
            try:
                return dt.datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        # Fallback: first 10 chars as ISO-ish date
        try:
            return dt.date.fromisoformat(raw[:10])
        except ValueError:
            raise ValueError(raw) from None

    def month_summary(self, period: str) -> dict:
        rows = [row for row in self.read_rows() if row["date"].strftime("%Y-%m") == period]
        by_category: dict[str, int] = defaultdict(int)
        by_subcategory: dict[str, int] = defaultdict(int)
        income = expense = 0
        for row in rows:
            amount = row["amount"]
            if row["kind"] == "Income":
                income += amount
            else:
                expense += amount
            label = row["category"]
            by_category[label] += amount
            by_subcategory[f"{label} / {row['subcategory']}"] += amount
        largest = sorted(rows, key=lambda row: row["amount"], reverse=True)[:5]
        return {
            "period": period, "income_total": income, "expense_total": expense,
            "by_category": dict(sorted(by_category.items())),
            "by_subcategory": dict(sorted(by_subcategory.items())),
            "largest": largest, "rows": rows,
        }

    def report_prompt(self, period: str, comparison_period: str | None = None) -> str:
        summary = self.month_summary(period)
        comparison = self.month_summary(comparison_period) if comparison_period else None
        return (
            "Buat laporan keuangan ringkas dalam Bahasa Indonesia berdasarkan data spreadsheet. "
            "Sebutkan pemasukan, pengeluaran, kategori terbesar, dan transaksi terbesar. "
            f"Periode: {period}\nData: {summary}\n"
            + (f"Perbandingan {comparison_period}: {comparison}\n" if comparison else "")
        )
