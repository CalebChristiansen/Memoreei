"""OAuth clients: who may ask for a token, and where the answer may go.

Two ways in. CIMD (what Claude.ai and ChatGPT both use): the client_id is an https
URL, and the JSON document there names the client and its redirect URIs. DCR (RFC
7591), for everything else: a client POSTs its metadata and gets an id of ours.
Either way memoreei never hands out a secret. Clients authenticate at the token
endpoint with nothing (PKCE carries it) or, if their document says so, with a
private_key_jwt assertion signed by a key in their published key set.

Fetching a URL a stranger chose is the dangerous part, all the more on a computer
in someone's home. fetch_json only goes to https on 443, resolves the name itself,
refuses anything that isn't a public address (so not the router, not the rest of
the LAN, not loopback, not a cloud's metadata service), then connects to exactly
the address it checked, so DNS can't change its mind between the check and the
connection. No redirects, five seconds, 64 KiB.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import secrets
import socket
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import urlsplit

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

if TYPE_CHECKING:
    from memoreei.oauth.store import OAuthStore

CIMD_TTL = 3600             # refetch a client's document after an hour
CIMD_STALE_OK = 86400       # but keep using the cached one for a day if the refetch fails
MAX_DOCUMENT = 65536
FETCH_TIMEOUT = 5.0
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
AUTH_METHODS = ("none", "private_key_jwt")
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
GRANTS = ("authorization_code", "refresh_token", DEVICE_GRANT)
MAX_REDIRECT_URIS = 32      # Hermes lists ten: five loopback ports, each spelled two ways
LOOPBACK = ("127.0.0.1", "::1", "localhost")

Fetch = Callable[[str], Awaitable[Any]]


class ClientError(Exception):
    """The client, its document or its redirect URI won't do. Safe to show."""


# ── fetching what strangers point us at ──────────────────────────────────────


def public_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


async def resolve(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos}, key=lambda a: (":" in a, a))


async def fetch_json(url: str, max_bytes: int = MAX_DOCUMENT) -> Any:
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.fragment:
        raise ClientError(f"{url} is not a plain https URL")
    try:
        port = u.port
    except ValueError:
        raise ClientError(f"{url}: a bad port")
    if port not in (None, 443):
        raise ClientError(f"{url}: only port 443")
    host = u.hostname.lower()
    try:
        ipaddress.ip_address(host)
        raise ClientError(f"{url}: a name, not an address")
    except ValueError:
        pass
    try:
        addresses = await resolve(host)
    except OSError:
        raise ClientError(f"{host} doesn't resolve")
    if not addresses or not all(public_address(a) for a in addresses):
        raise ClientError(f"{host} resolves to an address memoreei won't fetch from")
    ip = addresses[0]
    target = f"https://{ip if ':' not in ip else '[' + ip + ']'}{u.path or '/'}"
    if u.query:
        target += "?" + u.query
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False) as http:
            async with http.stream("GET", target, headers={
                    "Host": host, "Accept": "application/json", "User-Agent": "memoreei"},
                    extensions={"sni_hostname": host}) as r:
                if r.status_code != 200:
                    raise ClientError(f"{url} answered {r.status_code}")
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > max_bytes:
                        raise ClientError(f"{url} is larger than {max_bytes} bytes")
    except httpx.HTTPError as e:
        raise ClientError(f"{url}: {type(e).__name__}")
    try:
        return json.loads(body)
    except ValueError:
        raise ClientError(f"{url} isn't JSON")


# ── metadata ─────────────────────────────────────────────────────────────────


def check_redirect_uri(uri: Any) -> bool:
    u = urlsplit(uri) if isinstance(uri, str) else None
    if u is None or u.fragment or not u.hostname:
        return False
    if u.scheme == "https":
        return True
    # Native apps listen on loopback (OAuth 2.1 §8.4.2). Nothing else goes over http.
    return u.scheme == "http" and u.hostname in LOOPBACK


def loopback_form(uri: Any) -> tuple | None:
    """A loopback redirect URI with its port left out, or None if it isn't one."""
    u = urlsplit(uri) if isinstance(uri, str) else None
    try:
        if u is None or u.scheme != "http" or u.hostname not in LOOPBACK or u.fragment or u.username:
            return None
        u.port                                  # raises on a port that isn't a number
    except ValueError:
        return None
    return u.hostname, u.path, u.query


def redirect_allowed(meta: dict, uri: Any) -> bool:
    """Exactly one of the client's redirect URIs, or a loopback one differing only
    in its port: a native app takes whichever port is free (RFC 8252 §7.3)."""
    registered = meta.get("redirect_uris") or []
    if not isinstance(uri, str) or not uri:
        return False
    if uri in registered:
        return True
    form = loopback_form(uri)
    return form is not None and any(loopback_form(r) == form for r in registered)


def check_metadata(meta: Any) -> dict[str, Any]:
    """The fields memoreei uses, checked; the rest dropped. Raises ClientError."""
    if not isinstance(meta, dict):
        raise ClientError("client metadata must be a JSON object")
    grants = meta.get("grant_types", ["authorization_code"])
    if not isinstance(grants, list) or not {"authorization_code", DEVICE_GRANT} & {
            g for g in grants if isinstance(g, str)}:
        raise ClientError("grant_types must include authorization_code or the device_code grant")
    # A client that only uses device codes is never redirected anywhere. One that
    # lists redirect URIs anyway (Hermes does) still has them checked.
    uris = meta.get("redirect_uris", [])
    least = 1 if "authorization_code" in grants else 0
    if not isinstance(uris, list) or not least <= len(uris) <= MAX_REDIRECT_URIS:
        raise ClientError(f"redirect_uris must list {least} to {MAX_REDIRECT_URIS} URIs")
    for uri in uris:
        if not check_redirect_uri(uri):
            raise ClientError(f"redirect URI not allowed: {str(uri)[:200]}")
    method = meta.get("token_endpoint_auth_method", "none")
    if method not in AUTH_METHODS:
        raise ClientError(f"token_endpoint_auth_method must be one of {', '.join(AUTH_METHODS)}")
    name = meta.get("client_name", "")
    if not isinstance(name, str) or len(name) > 100:
        raise ClientError("client_name must be a short string")
    # A document may also list the methods it can use. ChatGPT's says private_key_jwt
    # but lists none too, and sends none (seen 2026-10-14), so a client may use any
    # method its own document declares.
    supported = meta.get("token_endpoint_auth_methods_supported", [])
    supported = supported if isinstance(supported, list) else []
    out: dict[str, Any] = {
        "redirect_uris": uris, "token_endpoint_auth_method": method,
        "auth_methods": [method] + [m for m in AUTH_METHODS if m in supported and m != method],
        "grant_types": [g for g in GRANTS if g in grants],
        "client_name": clean_name(name),
    }
    if "private_key_jwt" in out["auth_methods"]:
        jwks_uri = meta.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri.startswith("https://"):
            raise ClientError("private_key_jwt needs an https jwks_uri")
        out["jwks_uri"] = jwks_uri
    return out


def clean_name(name: str) -> str:
    """A client's name as shown to the person: printable, one line, short."""
    return " ".join("".join(c if c.isprintable() else " " for c in name).split())[:60]


def is_cimd(client_id: str) -> bool:
    return client_id.startswith("https://")


def host_of(uri: str | None) -> str:
    return (urlsplit(uri).hostname or "") if uri else ""


def methods(meta: dict) -> list[str]:
    return meta.get("auth_methods") or [meta["token_endpoint_auth_method"]]


async def load(store: "OAuthStore", client_id: str, fetch: Fetch = fetch_json) -> dict[str, Any]:
    """The client's checked metadata. CIMD documents are fetched and cached.
    Raises ClientError for an unknown client or a bad document."""
    row = await store.get_client(client_id)
    if not is_cimd(client_id):
        if row is None or row["kind"] != "dcr":
            raise ClientError("unknown client_id")
        return json.loads(row["metadata"])
    if len(client_id) > 500 or not urlsplit(client_id).path.strip("/"):
        raise ClientError("a CIMD client_id must be an https URL with a path")
    age = time.time() - row["fetched_at"] if row is not None else None
    if row is not None and age is not None and age < CIMD_TTL:
        return json.loads(row["metadata"])
    try:
        doc = await fetch(client_id)
        if not isinstance(doc, dict) or doc.get("client_id") != client_id:
            raise ClientError("the document's client_id isn't its own URL")
        meta = check_metadata(doc)
    except ClientError:
        if row is not None and age is not None and age < CIMD_STALE_OK:
            return json.loads(row["metadata"])
        raise
    await store.save_client(client_id, "cimd", json.dumps(meta))
    return meta


def describe(client_id: str, meta: dict) -> tuple[str, str, bool]:
    """(name, host, registered itself?) as the person is shown them. A CIMD client's
    host is its document's, which it can't fake; a DCR client's name is its own say-so."""
    if is_cimd(client_id):
        host = host_of(client_id)
        return meta.get("client_name") or host or "An app", host, False
    uris = meta.get("redirect_uris") or []
    return meta.get("client_name") or "An app", host_of(uris[0]) if uris else "", True


async def register(store: "OAuthStore", body: Any) -> dict[str, Any]:
    """DCR. Returns the registration response. Secrets are never issued: a client
    that asks for one gets none, which RFC 7591 §3.2.1 allows us to substitute."""
    if not isinstance(body, dict):
        raise ClientError("send a JSON object")
    body = dict(body)
    if body.get("token_endpoint_auth_method") not in AUTH_METHODS:
        body["token_endpoint_auth_method"] = "none"
    if body["token_endpoint_auth_method"] == "private_key_jwt":
        raise ClientError("private_key_jwt is for CIMD clients; register with none")
    meta = check_metadata(body)
    client_id = "mcl_" + secrets.token_urlsafe(18)
    await store.save_client(client_id, "dcr", json.dumps(meta))
    return {"client_id": client_id, "client_id_issued_at": int(time.time()), **meta,
            "response_types": ["code"] if "authorization_code" in meta["grant_types"] else []}


# ── JWTs ─────────────────────────────────────────────────────────────────────


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def public_key(jwk: dict) -> Any:
    if jwk.get("kty") == "RSA":
        n = int.from_bytes(b64url_decode(jwk["n"]), "big")
        e = int.from_bytes(b64url_decode(jwk["e"]), "big")
        key = rsa.RSAPublicNumbers(e, n).public_key()
        if key.key_size < 2048:
            raise ClientError("RSA key too small")
        return key
    if jwk.get("kty") == "EC" and jwk.get("crv") == "P-256":
        x = int.from_bytes(b64url_decode(jwk["x"]), "big")
        y = int.from_bytes(b64url_decode(jwk["y"]), "big")
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    raise ClientError("unsupported key type")


def split_jwt(token: str) -> tuple[dict, dict, bytes, bytes]:
    try:
        h, p, s = token.split(".")
        header, claims = json.loads(b64url_decode(h)), json.loads(b64url_decode(p))
    except (ValueError, AttributeError):
        raise ClientError("malformed JWT")
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise ClientError("malformed JWT")
    return header, claims, (h + "." + p).encode(), b64url_decode(s)


def verify_signature(alg: str, key: Any, signing_input: bytes, sig: bytes) -> None:
    try:
        if alg == "RS256" and isinstance(key, rsa.RSAPublicKey):
            key.verify(sig, signing_input, padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256" and isinstance(key, ec.EllipticCurvePublicKey) and len(sig) == 64:
            der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
            key.verify(der, signing_input, ec.ECDSA(hashes.SHA256()))
        else:
            raise ClientError(f"algorithm {alg} with this key isn't accepted")
    except InvalidSignature:
        raise ClientError("bad signature")


_jwks_cache: dict[str, tuple[float, list]] = {}


async def client_keys(jwks_uri: str, fetch: Fetch = fetch_json, refresh: bool = False) -> list:
    hit = _jwks_cache.get(jwks_uri)
    if hit and not refresh and time.time() - hit[0] < 3600:
        return hit[1]
    doc = await fetch(jwks_uri)
    keys = doc.get("keys") if isinstance(doc, dict) else None
    if not isinstance(keys, list):
        raise ClientError("the client's jwks_uri has no keys")
    _jwks_cache[jwks_uri] = (time.time(), keys)
    return keys


async def verify_assertion(store: "OAuthStore", client_id: str, meta: dict, assertion: str,
                           audiences: list[str], fetch: Fetch = fetch_json) -> None:
    """RFC 7523 client authentication. audiences: what aud may be (the token
    endpoint, or the issuer)."""
    header, claims, signing_input, sig = split_jwt(assertion)
    alg, kid = header.get("alg"), header.get("kid")
    if alg not in ("RS256", "ES256"):
        raise ClientError(f"algorithm {alg} isn't accepted")
    candidates: list = []
    for refresh in (False, True):            # a key we haven't seen may be a rotation
        keys = await client_keys(meta["jwks_uri"], fetch, refresh)
        candidates = [k for k in keys if isinstance(k, dict) and (kid is None or k.get("kid") == kid)]
        if candidates:
            break
    if not candidates:
        raise ClientError("no key in the client's key set matches")
    for jwk in candidates:
        try:
            verify_signature(alg, public_key(jwk), signing_input, sig)
            break
        except (ClientError, KeyError, ValueError):
            continue
    else:
        raise ClientError("bad signature")
    now = time.time()
    aud = claims.get("aud")
    aud = aud if isinstance(aud, list) else [aud]
    if claims.get("iss") != client_id or claims.get("sub") != client_id:
        raise ClientError("iss and sub must be the client_id")
    if not set(a for a in aud if isinstance(a, str)) & set(audiences):
        raise ClientError("aud isn't us")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp < now - 30 or exp > now + 600:
        raise ClientError("exp is missing, past, or more than ten minutes away")
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti or not await store.first_jti(client_id, jti, int(exp) + 60):
        raise ClientError("jti missing or seen before")
