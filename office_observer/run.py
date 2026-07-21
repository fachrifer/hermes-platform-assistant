"""Container entry point for the always-on office observer."""

from __future__ import annotations

import asyncio
import logging

from office_observer.collectors import HttpServiceCollector
from office_observer.config import ObserverConfig
from office_observer.main import OfficeObserver
from office_observer.relay_client import RelayHttpClient
from office_observer.spool import SnapshotSpool


def build_observer() -> OfficeObserver:
    config = ObserverConfig.from_env()
    return OfficeObserver(
        observer_id=config.observer_id,
        shared_secret=config.shared_secret,
        collector=HttpServiceCollector(config.service_urls),
        spool=SnapshotSpool(config.spool_path),
        relay=RelayHttpClient(
            config.relay_url,
            ca_file=config.relay_ca_file,
            client_cert=config.relay_client_cert,
            client_key=config.relay_client_key,
        ),
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(build_observer().run())
