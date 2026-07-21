"""mTLS HTTP client for sending signed snapshots to the office-hours laptop relay."""

from __future__ import annotations

import ssl

import httpx


def relay_response_acknowledged(status_code: int, payload: dict) -> bool:
    """A duplicate cloud insert is safe to remove from the local retry queue."""
    return status_code in {200, 202} and isinstance(payload, dict)


class RelayHttpClient:
    def __init__(
        self,
        relay_url: str,
        *,
        ca_file: str,
        client_cert: str,
        client_key: str,
    ):
        self.url = f"{relay_url.rstrip('/')}/v1/relay/snapshots"
        self.ssl_context = ssl.create_default_context(cafile=ca_file)
        self.ssl_context.load_cert_chain(client_cert, client_key)

    async def send(self, snapshot: dict, signature: str) -> bool:
        headers = {
            "X-Hermes-Observer": str(snapshot["observer_id"]),
            "X-Hermes-Signature": signature,
        }
        async with httpx.AsyncClient(timeout=15.0, verify=self.ssl_context) as client:
            response = await client.post(self.url, json=snapshot, headers=headers)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return relay_response_acknowledged(response.status_code, payload)
