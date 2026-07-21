"""Outbound mTLS forwarding from the laptop relay to Hermes Cloud."""

from __future__ import annotations

import ssl

import httpx


class CloudHttpForwarder:
    def __init__(
        self,
        cloud_url: str,
        *,
        ca_file: str,
        client_cert: str,
        client_key: str,
    ):
        self.url = f"{cloud_url.rstrip('/')}/api/v1/office/snapshots"
        self.ssl_context = ssl.create_default_context(cafile=ca_file)
        self.ssl_context.load_cert_chain(client_cert, client_key)

    async def forward(self, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
        async with httpx.AsyncClient(timeout=20.0, verify=self.ssl_context) as client:
            response = await client.post(
                self.url,
                content=body,
                headers={**headers, "content-type": "application/json"},
            )
        try:
            payload = response.json()
        except ValueError:
            payload = {"accepted": False}
        return response.status_code, payload
