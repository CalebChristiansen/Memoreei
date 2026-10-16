"""The dashboard password's hash, authenticator codes (TOTP), and recovery codes.

No new dependencies for the secrets themselves: the password is hashed with the
standard library's scrypt, and TOTP (RFC 6238) is a few lines of hmac. Only the QR
code for an authenticator app comes from a library (segno, pure Python).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import secrets
import socket
import struct
import time
import weakref
from urllib.parse import quote, urlencode

MIN_PASSWORD = 10
MAX_PASSWORD = 1024

# scrypt at 2^15 × 8: 32 MiB and about a tenth of a second per guess. The parameters
# are stored with each hash, so they can be raised later without breaking old ones.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1
# Two at once: 32 MiB each, whoever asks.
_hashing: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()

TOTP_STEP = 30
TOTP_DIGITS = 6
RECOVERY_CODES = 10
RECOVERY_ALPHABET = "bcdfghjkmnpqrtvwxz2346789"  # as the chatbot codes: no vowels, no look-alikes


def password_problem(password: str) -> str | None:
    """Why a new password won't do, or None."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters. A few words strung together are easiest."
    if len(password) > MAX_PASSWORD:
        return f"Use at most {MAX_PASSWORD} characters."
    if password.strip() != password:
        return "Leave off spaces at the start and end: they're too easy to lose."
    return None


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=128 * n * r * 2, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(digest)}"


def check_password(password: str, stored: str | None) -> bool:
    if not stored or len(password) > MAX_PASSWORD:
        return False
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, base64.b64decode(digest))


async def check_password_async(password: str, stored: str | None) -> bool:
    """check_password off the event loop, at most two at a time (per loop: a semaphore
    belongs to the loop that first waits on it)."""
    loop = asyncio.get_running_loop()
    hashing = _hashing.get(loop)
    if hashing is None:
        hashing = _hashing[loop] = asyncio.Semaphore(2)
    async with hashing:
        return await asyncio.to_thread(check_password, password, stored)


async def hash_password_async(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


# ── TOTP (RFC 6238), the codes an authenticator app shows ───────────────────


def new_totp_secret() -> str:
    """160 random bits, in the base32 authenticator apps take."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _secret_bytes(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)


def totp(secret: str, step: int, digits: int = TOTP_DIGITS) -> str:
    mac = hmac.new(_secret_bytes(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    number = (struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF) % 10**digits
    return f"{number:0{digits}d}"


def current_step(when: float | None = None) -> int:
    return int((time.time() if when is None else when) // TOTP_STEP)


def check_totp(secret: str, code: str, last_step: int | None, when: float | None = None) -> int | None:
    """The step a code belongs to, if it's right for now (or half a minute either side,
    for clocks that disagree) and newer than the last one used. None otherwise: a
    code works once."""
    code = "".join(c for c in code if c.isdigit())
    if len(code) != TOTP_DIGITS:
        return None
    now = current_step(when)
    found = None
    for step in (now - 1, now, now + 1):  # all three, so timing says nothing about which
        if hmac.compare_digest(totp(secret, step), code) and (last_step is None or step > last_step):
            found = step
    return found


def totp_uri(secret: str, label: str | None = None) -> str:
    """The otpauth:// address an authenticator app reads from the QR code."""
    label = label or f"Memoreei ({socket.gethostname().split('.')[0]})"
    params = urlencode({"secret": secret, "issuer": "Memoreei", "algorithm": "SHA1",
                        "digits": TOTP_DIGITS, "period": TOTP_STEP})
    return f"otpauth://totp/{quote(label)}?{params}"


def qr_svg(text: str) -> str:
    """A QR code as inline SVG: dark on white, whatever the page's theme."""
    import segno

    return segno.make(text, error="m").svg_inline(scale=5, dark="#000", light="#fff", border=3)


# ── recovery codes, for a lost phone ────────────────────────────────────────


def new_recovery_codes() -> list[str]:
    """Ten codes like ``k7qm2-x9fdh``, each about 50 bits, shown once and kept as hashes."""
    def one() -> str:
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"

    return [one() for _ in range(RECOVERY_CODES)]


def normalize_recovery_code(text: str) -> str:
    return "".join(c for c in text.lower() if c in RECOVERY_ALPHABET)


def recovery_hash(code: str) -> str:
    return hashlib.sha256(normalize_recovery_code(code).encode()).hexdigest()
