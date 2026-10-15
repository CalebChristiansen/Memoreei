"""OAuth access tokens, checked on this computer.

The public listener takes access tokens signed by one issuer: Memoreei Cloud's
token server, or an expert's own (Keycloak, Authentik, Auth0, anything that
publishes a key set). memoreei never talks to the issuer about a token. It only
fetches the issuer's public keys: from ``jwks_uri`` in its metadata (RFC 8414,
else OpenID discovery), kept five minutes, and fetched again at once for a key
id it hasn't seen (a rotation), though never more than every ten seconds.

A token passes when its signature is good under an asymmetric algorithm, ``iss``
is the issuer exactly, ``aud`` names this listener's /mcp URL, and it is in date
(a minute's leeway either side). Symmetric algorithms are refused outright, so a
public key can never be used as an HMAC secret.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import httpx
import jwt

log = logging.getLogger("memoreei.public")

ALGORITHMS = ("ES256", "ES384", "RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "EdDSA")
LEEWAY = 60
KEYS_TTL = 300
REFETCH_GAP = 10


class TokenRejected(Exception):
    """Why a token was refused, in a few words for the log."""


@dataclass(frozen=True)
class Claims:
    subject: str
    client_id: str | None
    raw: dict[str, Any] = field(repr=False)


def metadata_urls(issuer: str) -> list[str]:
    """Where an issuer's metadata may be: RFC 8414's path-inserted form, then OpenID's."""
    parts = urlsplit(issuer)
    path = parts.path.rstrip("/")
    rfc8414 = urlunsplit((parts.scheme, parts.netloc, "/.well-known/oauth-authorization-server" + path, "", ""))
    openid = issuer.rstrip("/") + "/.well-known/openid-configuration"
    return [rfc8414, openid]


class IssuerKeys:
    """An issuer's signing keys, by key id."""

    def __init__(self, issuer: str, http: httpx.AsyncClient | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.issuer = issuer
        self.http = http
        self.clock = clock
        self._keys: dict[str | None, jwt.PyJWK] = {}
        self._fetched_at = float("-inf")
        self._tried_at = float("-inf")
        self._jwks_uri: str | None = None
        self._lock = asyncio.Lock()

    async def key(self, kid: str | None) -> jwt.PyJWK | None:
        async with self._lock:
            now = self.clock()
            stale = now - self._fetched_at > KEYS_TTL
            if (stale or kid not in self._keys) and now - self._tried_at >= REFETCH_GAP:
                self._tried_at = now
                try:
                    await self._refresh()
                    self._fetched_at = now
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                    log.warning("couldn't fetch %s's keys: %s", self.issuer, e)
            if kid is None and len(self._keys) == 1:
                return next(iter(self._keys.values()))
            return self._keys.get(kid)

    async def _get_json(self, url: str) -> Any:
        if self.http is not None:
            r = await self.http.get(url)
        else:
            async with httpx.AsyncClient(timeout=10) as http:
                r = await http.get(url)
        r.raise_for_status()
        return r.json()

    async def _refresh(self) -> None:
        if self._jwks_uri is None:
            for url in metadata_urls(self.issuer):
                try:
                    meta = await self._get_json(url)
                except (httpx.HTTPError, ValueError):
                    continue
                if isinstance(meta, dict) and isinstance(meta.get("jwks_uri"), str):
                    self._jwks_uri = meta["jwks_uri"]
                    break
            else:
                raise ValueError("no metadata with a jwks_uri")
        doc = await self._get_json(self._jwks_uri)
        keys: dict[str | None, jwt.PyJWK] = {}
        for raw in doc["keys"]:
            if not isinstance(raw, dict) or raw.get("kty") == "oct" or raw.get("use", "sig") != "sig":
                continue
            try:
                keys[raw.get("kid")] = jwt.PyJWK(raw)
            except jwt.PyJWTError:
                continue
        self._keys = keys


class TokenVerifier:
    def __init__(self, issuer: str, audience: str, keys: IssuerKeys | None = None) -> None:
        self.issuer = issuer
        self.audience = audience
        self.keys = keys or IssuerKeys(issuer)

    async def verify(self, token: str) -> Claims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise TokenRejected("malformed")
        alg = header.get("alg")
        if alg not in ALGORITHMS:
            raise TokenRejected(f"algorithm {alg!r} not accepted")
        key = await self.keys.key(header.get("kid"))
        if key is None:
            raise TokenRejected("unknown signing key")
        try:
            claims = jwt.decode(
                token, key.key, algorithms=[alg], audience=self.audience, issuer=self.issuer,
                leeway=LEEWAY, options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise TokenRejected("expired")
        except jwt.InvalidAudienceError:
            raise TokenRejected("for another audience")
        except jwt.InvalidIssuerError:
            raise TokenRejected("from another issuer")
        except jwt.ImmatureSignatureError:
            raise TokenRejected("not valid yet")
        except jwt.PyJWTError as e:
            raise TokenRejected(str(e) or type(e).__name__)
        if not isinstance(claims.get("sub"), str):
            raise TokenRejected("no subject")
        client = claims.get("client_id") or claims.get("azp")
        return Claims(subject=claims["sub"], client_id=client if isinstance(client, str) else None, raw=claims)
