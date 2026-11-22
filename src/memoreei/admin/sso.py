"""Signing in to the dashboard by SSO: OpenID Connect, in place of the password and 2FA.

Set in the config (``config.env``, or the environment):

- ``MEMOREEI_SSO_ISSUER``: the provider's issuer, https (plain http only on this
  computer's loopback, for a provider running beside memoreei).
- ``MEMOREEI_SSO_CLIENT_ID``, and ``MEMOREEI_SSO_CLIENT_SECRET`` unless the provider
  registered memoreei as a public client. The secret is kept in the encrypted database,
  like sources' secrets (memoreei.storage.vault).
- ``MEMOREEI_SSO_ALLOW``: who may sign in, comma-separated. An entry with an ``@`` is an
  email, and matches only if the provider says it's verified; anything else is a
  subject (the ID token's ``sub``, which never changes for a person at one provider).
- ``MEMOREEI_SSO_NAME``: what the sign-in button calls the provider. The issuer's
  hostname if not set.

It's on when the first three are set (`memoreei admin sso set`), and it's **exclusive**
with the password and 2FA: setting SSO up removes them, and setting a password turns
SSO off. The dashboard on the computer itself answers either way, to a one-time link.
Away from the computer it still needs "from anywhere" switched on, as the password does
(memoreei.admin.account), and the provider sends the browser back to the address it
was signed in at: ``<that origin>/admin/sso/callback``, which must be registered there.

The flow is the authorization code with PKCE (S256), a ``state`` bound to the browser by
a cookie and a ``nonce`` bound into the ID token. The ID token is checked against the
issuer's own keys (its JWKS, fetched from the discovery document, which must name the
same issuer), and for its issuer, audience, expiry and nonce. Nothing else from the
provider is kept: a sign-in makes an ordinary dashboard session, a week long.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from memoreei.config import get_config

log = logging.getLogger("memoreei.admin")

CALLBACK = "/admin/sso/callback"
COOKIE = "memoreei_sso"
COOKIE_PATH = "/admin/sso"
PENDING_TTL = 10 * 60        # a sign-in started and not finished lapses
MAX_PENDING = 100
MAX_DOCUMENT = 256 * 1024
FETCH_TIMEOUT = 10.0
DISCOVERY_TTL = 3600
SKEW = 120                   # seconds of clock difference forgiven
ALGORITHMS = ("RS256", "ES256")
SCOPE = "openid email profile"
VARS = ("MEMOREEI_SSO_ISSUER", "MEMOREEI_SSO_CLIENT_ID", "MEMOREEI_SSO_CLIENT_SECRET",
        "MEMOREEI_SSO_ALLOW", "MEMOREEI_SSO_NAME")


class SSOError(Exception):
    """A sign-in that didn't work: ``reason`` for the log, ``said`` for the page."""

    def __init__(self, reason: str, said: str = "Signing in didn't work. Try again.") -> None:
        super().__init__(reason)
        self.reason, self.said = reason, said


@dataclass(frozen=True)
class Settings:
    issuer: str | None
    client_id: str | None
    client_secret: str | None
    allow: tuple[str, ...]
    name: str | None

    @property
    def on(self) -> bool:
        return bool(self.issuer and self.client_id and self.allow)

    @property
    def label(self) -> str:
        return self.name or urlsplit(self.issuer or "").hostname or "your sign-in provider"


def settings(with_secret: bool = False) -> Settings:
    """The settings as they are now, not as they were at start: `memoreei admin sso` and
    `admin password` are other processes, and the running server follows them at once
    (as it does stay-awake). The secret is read from the database only when asked for."""
    from memoreei.config import live_setting

    issuer = live_setting("MEMOREEI_SSO_ISSUER").strip() or None  # exactly as given: a trailing / counts
    secret = None
    if with_secret:
        secret = _live_secret()
    return Settings(issuer, live_setting("MEMOREEI_SSO_CLIENT_ID").strip() or None, secret,
                    tuple(a.strip() for a in live_setting("MEMOREEI_SSO_ALLOW").split(",") if a.strip()),
                    live_setting("MEMOREEI_SSO_NAME").strip() or None)


def _live_secret() -> str | None:
    from memoreei import config
    from memoreei.storage import vault

    if "MEMOREEI_SSO_CLIENT_SECRET" in config._process_env_keys:
        return os.environ.get("MEMOREEI_SSO_CLIENT_SECRET") or None
    return vault.read_secrets(get_config().db_path).get("MEMOREEI_SSO_CLIENT_SECRET") or None


def configured() -> bool:
    return settings().on


def from_environment() -> list[str]:
    """The SSO settings given in this process's real environment, which config.env and
    the database can't override, so neither the dashboard nor the CLI can change them."""
    from memoreei import config

    return [name for name in VARS if name in config._process_env_keys]


def environment_refusal() -> str | None:
    names = from_environment()
    if not names:
        return None
    return (f"Sign-in by SSO is set in memoreei's environment ({', '.join(names)}), not its config file: "
            "change it where memoreei is started.")


# ── checking what's configured ───────────────────────────────────────────────


def _loopback(host: str) -> bool:
    from memoreei.admin import auth

    return host in auth.LOOPBACK_HOSTS or auth.is_loopback_client(host)


def url_problem(url: str, what: str = "The issuer") -> str | None:
    """Why a provider's address won't do, or None. https; plain http only to loopback."""
    u = urlsplit(url or "")
    if not u.hostname or u.username or u.password or u.fragment:
        return f"{what} must be a plain URL, like https://login.example.com"
    if u.scheme == "https" or (u.scheme == "http" and _loopback(u.hostname)):
        return None
    return f"{what} must be https (plain http only on this computer itself): {url}"


def issuer_problem(issuer: str) -> str | None:
    if problem := url_problem(issuer):
        return problem
    if urlsplit(issuer).query:
        return "The issuer can't have a query string."
    return None


def allow_problem(allow: tuple[str, ...]) -> str | None:
    if not allow:
        return "Say who may sign in: an email address, or a subject (the provider's ID for you)."
    for entry in allow:
        if any(c.isspace() for c in entry) or len(entry) > 255:
            return f"Not an email or a subject: {entry!r}"
    return None


# ── talking to the provider ──────────────────────────────────────────────────


async def _request(method: str, url: str, what: str, **kwargs: Any) -> dict:
    """A JSON document from the provider: no redirects followed, bounded in size."""
    if problem := url_problem(url, what):
        raise SSOError(problem)
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False) as http:
            async with http.stream(method, url, headers={"Accept": "application/json", "User-Agent": "memoreei"},
                                   **kwargs) as r:
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_DOCUMENT:
                        raise SSOError(f"{what} at {url} sent more than {MAX_DOCUMENT} bytes")
                status = r.status_code
    except httpx.HTTPError as e:
        raise SSOError(f"{what} at {url}: {type(e).__name__}", "memoreei couldn't reach the sign-in provider.")
    try:
        doc = json.loads(body)
    except ValueError:
        doc = None
    if status != 200 or not isinstance(doc, dict):
        error = doc.get("error") if isinstance(doc, dict) else None
        raise SSOError(f"{what} at {url} answered {status}" + (f" ({str(error)[:100]})" if error else ""))
    return doc


_discovery: dict[str, tuple[float, dict]] = {}
_jwks: dict[str, tuple[float, list]] = {}


async def discover(issuer: str, refresh: bool = False) -> dict:
    """The provider's discovery document, checked: it must name this issuer exactly."""
    hit = _discovery.get(issuer)
    if hit and not refresh and time.time() - hit[0] < DISCOVERY_TTL:
        return hit[1]
    # OpenID Connect Discovery §4: the issuer, less any trailing /, then the well-known path.
    doc = await _request("GET", issuer.rstrip("/") + "/.well-known/openid-configuration", "The discovery document")
    if doc.get("issuer") != issuer:
        raise SSOError(f"the discovery document names the issuer {str(doc.get('issuer'))[:200]!r}, not {issuer!r}")
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(doc.get(field), str) or (problem := url_problem(doc[field], field)):
            raise SSOError(f"the discovery document's {field}: " + (problem if isinstance(doc.get(field), str)
                                                                      else "missing"))
    _discovery[issuer] = (time.time(), doc)
    return doc


async def _keys(jwks_uri: str, refresh: bool) -> list:
    hit = _jwks.get(jwks_uri)
    if hit and not refresh and time.time() - hit[0] < DISCOVERY_TTL:
        return hit[1]
    doc = await _request("GET", jwks_uri, "The key set")
    keys = doc.get("keys")
    if not isinstance(keys, list):
        raise SSOError("the provider's key set has no keys")
    _jwks[jwks_uri] = (time.time(), keys)
    return keys


# ── a sign-in in flight ──────────────────────────────────────────────────────

# state -> what the callback needs. In memory: a sign-in outlives neither ten minutes
# nor a restart, and one memoreei process serves the dashboard.
_pending: dict[str, dict[str, Any]] = {}
PER_ADDRESS_PENDING = 5


class Busy(Exception):
    """Too many sign-ins waiting from everyone: try again in a few minutes."""


def begin(s: Settings, doc: dict, redirect_uri: str, next_path: str, binding: str, peer: str,
          known_browser: bool = False) -> str:
    """Start a sign-in bound to this browser's cookie (``binding``): where to send it.

    At most five wait from one address (its oldest gives way), and a hundred in all. Once
    a hundred wait, only a browser that has signed in before may push the oldest out, so
    a stranger starting sign-ins from many addresses can't keep the owner's from finishing.
    """
    from memoreei.oauth.server import s256, with_params

    now = time.time()
    for state in [k for k, v in _pending.items() if now - v["at"] > PENDING_TTL]:
        _pending.pop(state, None)
    mine = [k for k, v in _pending.items() if v["peer"] == peer]
    for state in mine[:max(0, len(mine) - PER_ADDRESS_PENDING + 1)]:
        _pending.pop(state, None)
    if len(_pending) >= MAX_PENDING:
        if not known_browser:
            raise Busy()
        _pending.pop(next(iter(_pending)))  # the oldest
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    _pending[state] = {"verifier": verifier, "nonce": nonce, "next": next_path, "redirect_uri": redirect_uri,
                       "binding": hashlib.sha256(binding.encode()).hexdigest(), "at": now, "peer": peer}
    return with_params(doc["authorization_endpoint"], {
        "response_type": "code", "client_id": s.client_id, "redirect_uri": redirect_uri, "scope": SCOPE,
        "state": state, "nonce": nonce, "code_challenge": s256(verifier), "code_challenge_method": "S256",
    })


def take(state: str, binding: str | None) -> dict[str, Any]:
    """The sign-in this callback finishes, once: from this browser, within ten minutes."""
    pending = _pending.pop(state, None) if state else None
    if pending is None:
        raise SSOError("no sign-in waiting with that state", "That sign-in had lapsed, or was already used. Try again.")
    if time.time() - pending["at"] > PENDING_TTL:
        raise SSOError("the sign-in lapsed", "That sign-in took too long. Try again.")
    if not binding or not hmac.compare_digest(hashlib.sha256(binding.encode()).hexdigest(), pending["binding"]):
        raise SSOError("the sign-in came back to a different browser",
                       "That sign-in was started in a different browser. Start it again here.")
    return pending


async def finish(s: Settings, doc: dict, pending: dict[str, Any], code: str) -> dict[str, Any]:
    """Swap the code for an ID token, and check it. Returns its claims."""
    if not code:
        raise SSOError("no code came back")
    body = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending["redirect_uri"],
            "code_verifier": pending["verifier"]}
    auth = None
    if s.client_secret:
        # In the body where the provider takes it there: Basic wants both form-encoded
        # first (RFC 6749 §2.3.1), and not every provider decodes them again.
        methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        if "client_secret_post" in methods:
            body.update(client_id=s.client_id or "", client_secret=s.client_secret)
        elif "client_secret_basic" in methods:
            auth = httpx.BasicAuth(quote(s.client_id or "", safe=""), quote(s.client_secret, safe=""))
        else:
            raise SSOError(f"the provider takes neither client_secret_basic nor client_secret_post ({methods})")
    else:
        body["client_id"] = s.client_id or ""
    tokens = await _request("POST", doc["token_endpoint"], "The token endpoint", data=body, auth=auth)
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        raise SSOError("the token endpoint sent no ID token")
    return await verify_id_token(s, doc, id_token, pending["nonce"])


async def verify_id_token(s: Settings, doc: dict, id_token: str, nonce: str, now: float | None = None) -> dict:
    from memoreei.oauth.clients import ClientError, public_key, split_jwt, verify_signature

    try:
        header, claims, signing_input, sig = split_jwt(id_token)
    except ClientError as e:
        raise SSOError(f"the ID token: {e}")
    alg, kid = header.get("alg"), header.get("kid")
    if alg not in ALGORITHMS:
        raise SSOError(f"the ID token is signed with {str(alg)[:20]!r}, which memoreei doesn't accept")
    for refresh in (False, True):  # a key not seen before may be a rotation
        keys = await _keys(doc["jwks_uri"], refresh)
        candidates = [k for k in keys if isinstance(k, dict) and k.get("use", "sig") == "sig"
                      and (kid is None or k.get("kid") == kid)]
        for jwk in candidates:
            try:
                verify_signature(alg, public_key(jwk), signing_input, sig)
                break
            except (ClientError, KeyError, ValueError, TypeError):
                continue
        else:
            continue
        break
    else:
        raise SSOError("the ID token's signature doesn't match the provider's keys")
    now = time.time() if now is None else now
    if claims.get("iss") != s.issuer:
        raise SSOError(f"the ID token is from {str(claims.get('iss'))[:200]!r}, not {s.issuer!r}")
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if s.client_id not in audiences:
        raise SSOError("the ID token isn't for this client")
    azp = claims.get("azp")
    if (len(audiences) > 1 or azp is not None) and azp != s.client_id:
        raise SSOError("the ID token was issued to another party (azp)")
    exp, iat = claims.get("exp"), claims.get("iat")
    if not isinstance(exp, (int, float)) or exp < now - SKEW:
        raise SSOError("the ID token has expired")
    if not isinstance(iat, (int, float)) or iat > now + SKEW:
        raise SSOError("the ID token was issued in the future")
    if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
        raise SSOError("the ID token's nonce isn't this sign-in's")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise SSOError("the ID token has no subject")
    return claims


def who(claims: dict) -> str:
    """The person an ID token names, for the log and the page."""
    email = claims.get("email")
    return f"{email} ({claims['sub']})" if isinstance(email, str) and email else str(claims.get("sub"))


def allowed(s: Settings, claims: dict) -> bool:
    """Whether MEMOREEI_SSO_ALLOW lets this person in: by subject, or by an email the
    provider has verified."""
    email = claims.get("email")
    verified = claims.get("email_verified") in (True, "true")
    for entry in s.allow:
        if "@" in entry:
            if verified and isinstance(email, str) and email.strip().lower() == entry.lower():
                return True
        elif entry == claims.get("sub"):
            return True
    return False


# ── turning it on and off (the CLI, and setting a password) ──────────────────


async def turn_on(store: Any, issuer: str, client_id: str, allow: tuple[str, ...], secret: str | None,
                  name: str | None) -> None:
    """Save the settings, then remove the password and 2FA: one or the other."""
    from memoreei.config import save_settings

    if why := environment_refusal():
        raise SSOError(why, why)
    save_settings([("MEMOREEI_SSO_ISSUER", issuer), ("MEMOREEI_SSO_CLIENT_ID", client_id),
                   ("MEMOREEI_SSO_CLIENT_SECRET", secret or ""), ("MEMOREEI_SSO_ALLOW", ",".join(allow)),
                   ("MEMOREEI_SSO_NAME", name or "")])
    _discovery.clear()
    _jwks.clear()
    await store.end_sessions("sso")  # whoever signed in under the old settings signs in again
    await settle(store)


async def turn_off(store: Any) -> None:
    """Clear the settings and end every SSO session. Without a password the dashboard
    then answers only on the computer, as it does after `memoreei admin reset`."""
    from memoreei.config import save_settings

    if why := environment_refusal():
        raise SSOError(why, why)
    save_settings([(name, "") for name in VARS])
    await store.end_sessions("sso")
    if not await store.has_password():
        await store.set_remote(False)


async def settle(store: Any) -> bool:
    """With SSO on, no password or 2FA may be left behind (they may have been set before
    the config file was edited). True if one was removed."""
    if configured() and await store.has_password():
        await store.drop_password()
        log.warning("dashboard: sign-in by SSO is on, so the password and 2FA were removed")
        return True
    return False


# ── the routes ───────────────────────────────────────────────────────────────


def _cookie(request: Request, response: Response, value: str) -> None:
    """The browser's binding, one per browser and reused by every sign-in it starts, so
    two tabs (or a double click) don't undo each other. Lax, so the provider's redirect
    back (a top-level GET) carries it. It's worth nothing without a waiting state."""
    from memoreei.admin.app import where

    response.set_cookie(COOKIE, value, max_age=PENDING_TTL, path=COOKIE_PATH, httponly=True, samesite="lax",
                        secure=where(request)["secure"])


async def start(request: Request) -> Response:
    """GET /admin/sso/start: off to the provider."""
    from memoreei.admin import auth
    from memoreei.admin.account import account_store
    from memoreei.admin.app import _limits, _secure, signin_page, where

    s = settings()
    w = where(request)
    next_path = auth.safe_next(request.query_params.get("next"))
    if not s.on or w["session"] is not None:
        return RedirectResponse(next_path, status_code=303)
    known = await (await account_store()).browser_known(request.cookies.get(auth.BROWSER_COOKIE))
    if not w["local"] and (why := _limits().blocked(w["peer"], known)) is not None:
        log.warning("dashboard sign-in by SSO from %s refused: %s", w["peer"], why)
        return await signin_page(request, next_path, status_code=429,
                                 error="Too many failed sign-ins. Wait a few minutes and try again.")
    try:
        doc = await discover(s.issuer or "")
    except SSOError as e:
        log.warning("dashboard sign-in by SSO couldn't start: %s", e.reason)
        return await signin_page(request, next_path, status_code=502,
                                 error=f"memoreei couldn't reach {s.label} to sign you in. Try again in a minute.")
    binding = request.cookies.get(COOKIE) or ""
    if len(binding) < 32:
        binding = secrets.token_urlsafe(32)
    try:
        url = begin(s, doc, w["origin"] + CALLBACK, next_path, binding, w["peer"] or "?", known)
    except Busy:
        log.warning("dashboard sign-in by SSO from %s refused: %d sign-ins already waiting", w["peer"], MAX_PENDING)
        return await signin_page(request, next_path, status_code=429,
                                 error="Too many sign-ins are waiting. Wait a few minutes and try again.")
    response = RedirectResponse(url, status_code=303)
    _cookie(request, response, binding)
    return _secure(response)


async def callback(request: Request) -> Response:
    """GET /admin/sso/callback: back from the provider, with a code or an error."""
    from memoreei.admin import auth
    from memoreei.admin.account import BROWSER_TTL, PASSWORD_SESSION_TTL, account_store
    from memoreei.admin.app import _limits, _secure, _set_cookie, signin_page, where

    s = await asyncio.to_thread(settings, True)  # the secret is in the database
    w = where(request)
    peer, limited = w["peer"], not w["local"]
    if not s.on:
        return RedirectResponse("/admin/", status_code=303)
    store = await account_store()
    known = await store.browser_known(request.cookies.get(auth.BROWSER_COOKIE))
    q = request.query_params
    if limited and (why := _limits().blocked(peer, known)) is not None:
        # Not counted again: waiting it out must end it.
        log.warning("dashboard sign-in by SSO from %s refused: %s", peer, why)
        return await signin_page(request, status_code=429,
                                 error="Too many failed sign-ins. Wait a few minutes and try again.")
    try:
        pending = take(q.get("state", ""), request.cookies.get(COOKIE))
        if q.get("error"):
            raise SSOError(f"{s.label} said {q['error'][:100]}"
                           + (f": {q.get('error_description', '')[:200]}" if q.get("error_description") else ""),
                           f"{s.label} didn't sign you in.")
        claims = await finish(s, await discover(s.issuer or ""), pending, q.get("code", ""))
        if not allowed(s, claims):
            raise SSOError(f"{who(claims)} isn't allowed",
                           f"{who(claims)} isn't allowed to sign in to this memoreei. "
                           "Sign out of that account at the provider, or ask for it to be added.")
    except SSOError as e:
        if limited:
            _limits().fail(peer)
        log.warning("dashboard sign-in by SSO from %s refused: %s", peer, e.reason)
        await store.record_signin(peer, "sso", False, e.reason[:300])
        return await signin_page(request, status_code=403, error=e.said)
    await store.end_session(request.cookies.get(auth.COOKIE))  # never carry a session across a sign-in
    session = await store.add_session("sso", peer, request.headers.get("user-agent"))
    await store.record_signin(peer, "sso", True, f"as {who(claims)}"[:300])
    log.info("dashboard: signed in from %s by SSO as %s%s", peer, who(claims),
             "" if w["local"] else ", away from the computer")
    response = RedirectResponse(auth.safe_next(pending["next"]), status_code=303)
    _set_cookie(request, response, auth.COOKIE, session, PASSWORD_SESSION_TTL)
    if not known:
        _set_cookie(request, response, auth.BROWSER_COOKIE, await store.add_browser(), BROWSER_TTL)
    return _secure(response)


def routes() -> list:
    from starlette.routing import Route

    return [Route("/sso/start", start), Route("/sso/callback", callback)]
