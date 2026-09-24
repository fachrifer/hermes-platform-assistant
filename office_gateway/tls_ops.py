from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


def tls_cert_status(cert_path: str | None) -> dict:
    """Read-only TLS leaf cert metadata for Janus / edge status.

    Never reads or requires a private key — only the public certificate PEM.
    """
    path = (cert_path or "").strip()
    if not path:
        return {
            "configured": False,
            "cert_path": "",
            "error": "OFFICE_EDGE_TLS_CERT_PATH not set",
        }
    cert_file = Path(path)
    if not cert_file.is_file():
        return {
            "configured": False,
            "cert_path": path,
            "error": "certificate file not found",
        }
    try:
        from cryptography import x509
    except ImportError as exc:  # pragma: no cover
        return {
            "configured": False,
            "cert_path": path,
            "error": f"cryptography not installed: {exc}",
        }
    try:
        cert = x509.load_pem_x509_certificate(cert_file.read_bytes())
    except Exception as exc:  # noqa: BLE001 — surface parse errors to the agent
        return {
            "configured": False,
            "cert_path": path,
            "error": f"invalid certificate: {exc}",
        }

    not_before = _aware_utc(cert.not_valid_before_utc)
    not_after = _aware_utc(cert.not_valid_after_utc)
    now = datetime.now(timezone.utc)
    days_remaining = (not_after.date() - now.date()).days
    return {
        "configured": True,
        "cert_path": path,
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "not_before": not_before.isoformat(),
        "not_after": not_after.isoformat(),
        "days_remaining": days_remaining,
        "expired": now >= not_after,
        "expires_soon": (not now >= not_after) and days_remaining <= 30,
        "warning_window_days": 30,
    }


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def write_self_signed_pem(path: Path, *, days_valid: int = 365) -> None:
    """Test helper: write a minimal self-signed PEM (no CA key retained)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "lab-edge-test")]
    )
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=days_valid))
        .sign(key, hashes.SHA256())
    )
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
