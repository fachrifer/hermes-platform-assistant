"""Briefing composer.

Builds startup + on-demand /brief: agenda + financial summary + connector status.
Nyx voice — calm, clear, no squire diction.
"""

from __future__ import annotations

import logging
from datetime import datetime

from config.settings import settings
from config.persona import display_name

logger = logging.getLogger("hermes.briefing")

_SQUIRE_FORBIDDEN = (
    "squire",
    "titah",
    "kulaksanakan",
    "dengan segala hormat",
    "siap sedia",
)


class BriefingService:
    def __init__(self, agent):
        self.agent = agent

    async def build(self, health: dict[str, bool] | None = None) -> str:
        if health is None:
            health = await self.agent.health_check_all()

        now = datetime.now().strftime("%A, %d %B %Y — %H:%M")
        lines = [
            f"*{display_name()}* · {settings.address}",
            f"_{now}_",
            "",
        ]

        lines.append("📅 *Agenda hari ini*")
        agenda = await self.agent.all_today_agenda()
        if agenda:
            for e in agenda:
                account = f" [{e['account']}]" if e.get("account") else ""
                lines.append(
                    f"  • {e.get('time', '')} — {e.get('title', '')} "
                    f"({e.get('source', '')}{account})"
                )
        else:
            lines.append("  • Tidak ada agenda tercatat.")
        lines.append("")

        lines.append("💰 *Keuangan*")
        lines.append(await self._finance_line(health))
        lines.append("")

        lines.append("🔌 *Status layanan*")
        for name, ok in health.items():
            lines.append(f"  {'✅' if ok else '❌'} {name}")

        text = "\n".join(lines)
        lowered = text.casefold()
        for marker in _SQUIRE_FORBIDDEN:
            if marker in lowered:
                logger.warning("Briefing mengandung diksi terlarang: %s", marker)
        return text

    async def _finance_line(self, health: dict[str, bool]) -> str:
        try:
            period = datetime.now().strftime("%Y-%m")
            summary = self.agent.spreadsheet_finance.month_summary(period)
            if not summary["rows"]:
                return "  • Belum ada spreadsheet transaksi bulan ini."
            return (
                f"  • Bulan ini: pemasukan Rp{summary['income_total']:,.0f}, "
                f"pengeluaran Rp{summary['expense_total']:,.0f}."
            ).replace(",", ".")
        except Exception as exc:  # noqa: BLE001
            logger.warning("finance briefing gagal: %s", exc)
            return "  • ⚠️ Gagal mengambil ringkasan keuangan."

    async def startup_brief(self, health: dict[str, bool]) -> str:
        greeting = (
            f"{display_name()} siap, {settings.address}.\n"
            "Berikut ringkasan singkat:\n\n"
        )
        return greeting + await self.build(health)
