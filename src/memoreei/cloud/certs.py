"""The device's TLS certificate.

memoreei makes its own P-256 key, which never leaves this computer, and sends
the service only a certificate signing request for its hostname. The relay
carries TLS it can't open, so this key is what keeps the relay out of the
conversation. Renewal asks again at two-thirds of the certificate's lifetime;
the service allows five a week per device, which is plenty to retry with.
"""
from __future__ import annotations

import time
from datetime import timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from memoreei.cloud.service import Service
from memoreei.cloud.state import CloudState


def load_or_make_key(state: CloudState) -> ec.EllipticCurvePrivateKey:
    if state.key_path.is_file():
        key = serialization.load_pem_private_key(state.key_path.read_bytes(), password=None)
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        return key
    key = ec.generate_private_key(ec.SECP256R1())
    state.save_key(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    return key


def make_csr(key: ec.EllipticCurvePrivateKey, hostname: str) -> str:
    return (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
        .decode()
    )


def validity(chain_pem: bytes) -> tuple[int, int]:
    """(not_before, not_after) of the first certificate in a chain, as Unix times."""
    cert = x509.load_pem_x509_certificate(chain_pem)
    return (int(cert.not_valid_before_utc.replace(tzinfo=timezone.utc).timestamp()),
            int(cert.not_valid_after_utc.replace(tzinfo=timezone.utc).timestamp()))


def renew_at(state: CloudState) -> int | None:
    """When to ask for the next certificate: two-thirds of the way through this one."""
    try:
        not_before, not_after = validity(state.cert_path.read_bytes())
    except (OSError, ValueError):
        return None
    return not_before + (not_after - not_before) * 2 // 3


async def obtain(state: CloudState, service: Service, hostname: str, token: str) -> int:
    """Ask for a certificate and keep it. Returns its not_after."""
    csr = make_csr(load_or_make_key(state), hostname)
    chain, not_after = await service.certificate(token, csr)
    state.save_certificate(chain)
    return not_after


def describe(state: CloudState) -> dict:
    try:
        not_before, not_after = validity(state.cert_path.read_bytes())
    except (OSError, ValueError):
        return {}
    return {"not_after": not_after, "renews_at": not_before + (not_after - not_before) * 2 // 3,
            "days_left": max(0, (not_after - int(time.time())) // 86400)}
