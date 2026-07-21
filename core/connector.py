"""Base connector interface.

Every external integration (Money Manager, Gmail, Calendar, Outlook, manual
agenda) implements this interface so the orchestrator can treat them uniformly
and degrade gracefully when one is unavailable.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("hermes.connector")


class Connector(ABC):
    #: Human-readable connector name shown in status/briefing.
    name: str = "connector"
    #: Short emoji/icon for display.
    icon: str = "🔌"

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the connector is reachable/usable right now."""
        raise NotImplementedError

    def is_available(self) -> bool:
        """Cheap, non-blocking availability check (e.g. credentials present).

        Defaults to True; override for connectors with optional config.
        """
        return True

    async def close(self) -> None:
        """Release any resources. Safe no-op by default."""
        return None
