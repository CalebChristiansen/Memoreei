"""Refresh tokens: signed by memoreei, numbered, rotated on every use.

A refresh token is ``mrt_<payload>.<signature>``: the payload names the grant, its
generation and when it was issued; the signature is an HMAC-SHA256 under a key
kept in the encrypted database. Nothing about a refresh token is stored except the
grant's current generation, so the database can't leak working ones.

Each use returns the next generation and retires the one presented (RFC 9700
§4.14.2). A retired one coming back means two parties hold the token, so the grant
goes, and every token hanging off it (the "token family"), with one exception: the
generation just before the current one still works for REFRESH_GRACE seconds after
it was replaced, and gets a token for the current generation, without moving it on.
That's for a client that refreshed twice at once, or lost the answer to the first
(Okta's default leeway). A grant unused for REFRESH_IDLE stops refreshing.

Access tokens are opaque (``mat_``), ten minutes, and stored as hashes: issuer and
checker are the same process, so there's nothing a JWT would add.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

PREFIX = "mrt_"
REFRESH_IDLE = 30 * 86400


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class Refresh:
    key_id: str
    grant_id: str
    generation: int
    issued_at: int


def sign(key_id: str, key: bytes, grant_id: str, generation: int, issued_at: int) -> str:
    payload = _b64(json.dumps({"k": key_id, "g": grant_id, "n": generation, "t": issued_at,
                               "j": secrets.token_urlsafe(6)}, separators=(",", ":")).encode())
    mac = hmac.new(key, payload.encode(), hashlib.sha256).digest()
    return f"{PREFIX}{payload}.{_b64(mac)}"


def key_id_of(token: str) -> str | None:
    """The signing key a token names, before anything about it is trusted."""
    parsed = _parse(token)
    return parsed[1].get("k") if parsed else None


def _parse(token: str) -> tuple[str, dict, bytes] | None:
    if not isinstance(token, str) or not token.startswith(PREFIX) or len(token) > 512:
        return None
    payload, _, mac = token[len(PREFIX):].partition(".")
    try:
        claims = json.loads(_unb64(payload))
        return payload, claims, _unb64(mac)
    except (ValueError, TypeError):
        return None


def verify(token: str, key: bytes) -> Refresh | None:
    """The token's contents if its signature is good under this key, else None."""
    parsed = _parse(token)
    if parsed is None:
        return None
    payload, claims, mac = parsed
    if not hmac.compare_digest(mac, hmac.new(key, payload.encode(), hashlib.sha256).digest()):
        return None
    try:
        return Refresh(str(claims["k"]), str(claims["g"]), int(claims["n"]), int(claims["t"]))
    except (KeyError, TypeError, ValueError):
        return None
