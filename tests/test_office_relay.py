import datetime as dt

from fastapi.testclient import TestClient

from office_relay.main import OfficeHours, create_app


def test_relay_forwards_signed_envelope_without_rewriting_payload():
    received: dict[str, object] = {}

    class Cloud:
        async def forward(self, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
            received["body"] = body
            received["headers"] = headers
            return 202, {"accepted": True}

    client = TestClient(
        create_app(
            Cloud(),
            office_hours=OfficeHours("UTC", "00:00", "23:59"),
            now=lambda: dt.datetime(2026, 7, 21, 12, 0, tzinfo=dt.timezone.utc),
        )
    )
    response = client.post(
        "/v1/relay/snapshots",
        content=b'{"observer_id":"office-observer-1"}',
        headers={
            "content-type": "application/json",
            "X-Hermes-Observer": "office-observer-1",
            "X-Hermes-Signature": "signed-by-observer",
        },
    )

    assert response.status_code == 202
    assert response.json() == {"accepted": True}
    assert received["body"] == b'{"observer_id":"office-observer-1"}'
    assert received["headers"] == {
        "x-hermes-observer": "office-observer-1",
        "x-hermes-signature": "signed-by-observer",
    }


def test_relay_rejects_oversized_or_unsigned_envelopes():
    class Cloud:
        async def forward(self, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
            raise AssertionError("invalid relay request must not reach cloud")

    client = TestClient(
        create_app(
            Cloud(),
            office_hours=OfficeHours("UTC", "00:00", "23:59"),
            now=lambda: dt.datetime(2026, 7, 21, 12, 0, tzinfo=dt.timezone.utc),
        )
    )

    missing_signature = client.post("/v1/relay/snapshots", content=b"{}")
    oversized = client.post(
        "/v1/relay/snapshots",
        content=b"x" * 65_537,
        headers={"X-Hermes-Observer": "office-observer-1", "X-Hermes-Signature": "sig"},
    )

    assert missing_signature.status_code == 401
    assert oversized.status_code == 413


def test_relay_refuses_forwarding_outside_configured_office_hours():
    class Cloud:
        async def forward(self, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
            raise AssertionError("closed relay must not forward")

    client = TestClient(
        create_app(
            Cloud(),
            office_hours=OfficeHours("Asia/Jakarta", "08:00", "18:00"),
            now=lambda: dt.datetime(2026, 7, 21, 20, 0),
        )
    )

    response = client.post(
        "/v1/relay/snapshots",
        content=b"{}",
        headers={"X-Hermes-Observer": "office-observer-1", "X-Hermes-Signature": "sig"},
    )

    assert response.status_code == 503
