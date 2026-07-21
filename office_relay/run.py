"""Laptop relay entry point. It requires client certificates from the observer."""

from __future__ import annotations

import os
import ssl

import uvicorn

from office_relay.cloud_client import CloudHttpForwarder
from office_relay.main import OfficeHours, create_app


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} wajib diisi")
    return value


if __name__ == "__main__":
    forwarder = CloudHttpForwarder(
        _required("HERMES_CLOUD_INGEST_URL"),
        ca_file=_required("HERMES_CLOUD_CA_FILE"),
        client_cert=_required("HERMES_CLOUD_CLIENT_CERT"),
        client_key=_required("HERMES_CLOUD_CLIENT_KEY"),
    )
    office_hours = OfficeHours(
        _required("OFFICE_RELAY_TIMEZONE"),
        _required("OFFICE_RELAY_START"),
        _required("OFFICE_RELAY_END"),
    )
    uvicorn.run(
        create_app(forwarder, office_hours=office_hours),
        host=os.getenv("OFFICE_RELAY_HOST", "0.0.0.0"),
        port=int(os.getenv("OFFICE_RELAY_PORT", "8443")),
        ssl_certfile=_required("OFFICE_RELAY_TLS_CERT"),
        ssl_keyfile=_required("OFFICE_RELAY_TLS_KEY"),
        ssl_ca_certs=_required("OFFICE_RELAY_CLIENT_CA_FILE"),
        ssl_cert_reqs=ssl.CERT_REQUIRED,
    )
