"""CML Application entry for hermes-office (Manager UI + background cycles)."""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn

from cml.hermes_office.app import create_app
from cml.hermes_office.clients import (
    FileLogSource,
    HermesCloudReportClient,
    OpenAIChatClient,
    build_collector,
)
from cml.hermes_office.config import OfficeConfig
from cml.hermes_office.observer import OfficeCycle
from cml.hermes_office.qwen_assist import QwenAssist
from cml.hermes_office.reporter import CloudReporter
from cml.hermes_office.store import OfficeStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hermes.cml.office")


async def _loop(cycle: OfficeCycle, reporter: CloudReporter, interval: int) -> None:
    while True:
        try:
            await cycle.run_once(period="status")
            await reporter.flush()
        except Exception:  # noqa: BLE001
            logger.exception("Office cycle failed")
        await asyncio.sleep(interval)


def main() -> None:
    config = OfficeConfig.from_env()
    store = OfficeStore(config.data_dir)
    assist = QwenAssist(
        OpenAIChatClient(config.qwen_base_url, model=config.qwen_model),
        model=config.qwen_model,
    )
    cycle = OfficeCycle(
        observer_id=config.observer_id,
        shared_secret=config.shared_secret,
        store=store,
        collector=build_collector(config.service_urls),
        log_source=FileLogSource(config.log_paths),
        assist=assist,
    )
    reporter = CloudReporter(
        store,
        HermesCloudReportClient(config.cloud_report_url, observer_id=config.observer_id),
    )
    app = create_app(store)

    @app.on_event("startup")
    async def _startup() -> None:
        asyncio.create_task(_loop(cycle, reporter, config.interval_seconds))

    port = int(os.environ.get("CDSW_APP_PORT") or os.environ.get("PORT") or "8080")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
