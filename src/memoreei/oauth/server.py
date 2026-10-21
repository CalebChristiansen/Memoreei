"""The sign-in endpoints, on the public listener, at this memoreei's own address.

  GET  /.well-known/oauth-authorization-server   RFC 8414 metadata
  GET  /authorize            check the client, its redirect URI, PKCE and the
                             resource, then ask the person at the computer
  GET  /authorize/wait       the chatbot's browser waits here, showing the code,
                             until the person answers; then back to the chatbot
  POST /authorize/cancel     the browser gives up
  POST /token                code, refresh token or device code → a ten-minute
                             access token and a new refresh token
  POST /revoke               RFC 7009
  POST /register             DCR (RFC 7591)
  POST /device_authorization RFC 8628, for agents with no browser
  GET  /device               where an agent's person is sent: "approve it on
                             your computer"

**The person approves on the computer**, in the dashboard (or `memoreei chatbots
approve CODE`), never on these pages: anyone on the internet can reach them, and
only someone at the computer can say yes. Or, once they've set a password and
opened the dashboard from anywhere (memoreei.admin.account), in the dashboard at
this same address, after signing in to it: the waiting page links there. The
chatbot's page and the dashboard show the same short code, so the person can tell
their request from someone else's. Requests wait ten minutes, and only so many can
wait at once.

Until the redirect URI is known good, errors are a page of ours. After, they go
back to the chatbot as error=... (RFC 6749 §4.1.2.1). Every redirect to the
chatbot carries iss (RFC 9207).

Rate limits are per address. Browsers come from their people's addresses, so
authorize and register count every request. The token endpoint hears from
Claude's and OpenAI's servers, a few addresses for everyone, so it counts only
refusals, with a generous ceiling on the rest.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import re
import secrets
import time
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from memoreei.oauth import clients, tokens
from memoreei.oauth.clients import ASSERTION_TYPE, DEVICE_GRANT, ClientError
from memoreei.oauth.limits import Limiter
from memoreei.oauth.store import ACCESS_TTL, REFRESH_GRACE, REQUEST_TTL, Busy, OAuthStore, now

log = logging.getLogger("memoreei.oauth")

COOKIE = "__Host-memoreei_ar"
PKCE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
WAIT_REFRESH = 2           # seconds between the waiting page's reloads
DEVICE_INTERVAL = 5
TOUCH_INTERVAL = 60

PATHS = frozenset({
    "/.well-known/oauth-authorization-server", "/authorize", "/authorize/wait", "/authorize/cancel",
    "/token", "/revoke", "/register", "/device_authorization", "/device",
})


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400) -> None:
        super().__init__(description)
        self.error, self.description, self.status = error, description, status


class Rejected(Exception):
    """Why an access token was refused, in a few words for the log."""


def s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def with_params(uri: str, params: dict[str, Any]) -> str:
    return uri + ("&" if urlsplit(uri).query else "?") + urlencode(
        {k: v for k, v in params.items() if v is not None})


def origin_of(uri: str) -> str:
    u = urlsplit(uri)
    return f"{u.scheme}://{u.netloc}"


# ── pages ────────────────────────────────────────────────────────────────────

STYLE = """
body { font: 16px/1.5 system-ui, sans-serif; margin: 0; background: #f6f5f2; color: #1d1d1b; }
main { max-width: 28rem; margin: 12vh auto; padding: 2rem; background: #fff;
       border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.12); }
h1 { font-size: 1.3rem; margin: 0 0 1rem; }
dl { margin: 1.25rem 0; } dt { font-size: .8rem; color: #6b6a66; } dd { margin: 0 0 .75rem; word-break: break-all; }
code { font-size: .9em; }
.code { font: 600 2.2rem/1.2 ui-monospace, monospace; letter-spacing: .2em; margin: .25rem 0 1rem; }
.buttons { display: flex; gap: .75rem; margin-top: 1.5rem; }
button { font: inherit; padding: .6rem 1.2rem; border-radius: 8px; border: 1px solid #c9c7c0;
         background: #fff; cursor: pointer; }
p.small, .small { font-size: .85rem; color: #6b6a66; }
@media (prefers-color-scheme: dark) {
  body { background: #181816; color: #ecebe6; } main { background: #23231f; box-shadow: none; }
  dt, p.small, .small { color: #a3a19a; }
  button { background: #23231f; color: inherit; border-color: #4a4944; }
}
"""


def page(status: int, title: str, body: str, form_to: str | None = None, refresh: str | None = None) -> HTMLResponse:
    form_action = "'self'" + (f" {form_to}" if form_to else "")
    meta = f"<meta http-equiv=refresh content='{html.escape(refresh)}'>" if refresh else ""
    return HTMLResponse(
        f"<!doctype html><html lang=en><meta charset=utf-8>"
        f"<meta name=viewport content='width=device-width,initial-scale=1'>{meta}"
        f"<title>{html.escape(title)} · Memoreei</title><style>{STYLE}</style>"
        f"<main><h1>{html.escape(title)}</h1>{body}</main></html>",
        status_code=status,
        headers={**NO_STORE,
                 "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                            f"form-action {form_action}; frame-ancestors 'none'; base-uri 'none'",
                 "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


def error_page(status: int, title: str, detail: str) -> HTMLResponse:
    return page(status, title, f"<p>{html.escape(detail)}</p>"
                "<p class=small>Go back to the chatbot and try connecting again.</p>")


def expired_page() -> HTMLResponse:
    return error_page(400, "This request has expired",
                      "It waited longer than ten minutes, was already answered, or started in a different browser.")


def who(row: dict[str, Any]) -> str:
    """How a request or grant's client is described: its name and where it's from."""
    name = html.escape(row["client_name"])
    if row["self_registered"]:
        return f"{name} <span class=small>(it named itself; {html.escape(row['client_host'] or 'no address')})</span>"
    return f"{name} <span class=small>({html.escape(row['client_host'])})</span>"


# ── the endpoints ────────────────────────────────────────────────────────────

Notify = Callable[[dict[str, Any]], Awaitable[None]]


class SignIn:
    """memoreei's OAuth server for one resource: this memoreei's public /mcp."""

    def __init__(self, store: Callable[[], Awaitable[OAuthStore]], issuer: str, resource: str,
                 fetch: clients.Fetch = clients.fetch_json, notify: Notify | None = None,
                 access_ttl: int = ACCESS_TTL, clock: Callable[[], float] = time.time,
                 admin_open: Callable[[], Awaitable[bool]] | None = None) -> None:
        self._store = store
        self.issuer = issuer.rstrip("/")
        self.resource = resource
        self.fetch, self.notify, self.access_ttl, self.clock = fetch, notify, access_ttl, clock
        # Whether the dashboard answers at this address too (a password, and "from
        # anywhere"): then the waiting page also offers to sign in there and approve.
        self.admin_open = admin_open
        self.token_endpoint = f"{self.issuer}/token"
        self.authorize_limit = Limiter(60, 600)
        self.wait_limit = Limiter(600, 600)
        self.register_limit = Limiter(20, 3600)
        self.device_limit = Limiter(20, 3600)
        self.token_limit = Limiter(600, 60)
        self.refusals = Limiter(60, 600)
        self._touched: dict[str, float] = {}
        self.app = Starlette(routes=[
            Route("/.well-known/oauth-authorization-server", self.metadata_endpoint, methods=["GET", "HEAD"]),
            Route("/authorize", self.authorize, methods=["GET"]),
            Route("/authorize/wait", self.wait, methods=["GET"]),
            Route("/authorize/cancel", self.cancel, methods=["POST"]),
            Route("/token", self.token, methods=["POST"]),
            Route("/revoke", self.revoke, methods=["POST"]),
            Route("/register", self.register, methods=["POST"]),
            Route("/device_authorization", self.device_authorization, methods=["POST"]),
            Route("/device", self.device_page, methods=["GET"]),
        ])

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        await self.app(scope, receive, send)

    async def store(self) -> OAuthStore:
        return await self._store()

    def metadata(self) -> dict[str, Any]:
        i = self.issuer
        return {
            "issuer": i,
            "authorization_endpoint": f"{i}/authorize",
            "device_authorization_endpoint": f"{i}/device_authorization",
            "token_endpoint": self.token_endpoint,
            "registration_endpoint": f"{i}/register",
            "revocation_endpoint": f"{i}/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token", DEVICE_GRANT],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": list(clients.AUTH_METHODS),
            "token_endpoint_auth_signing_alg_values_supported": ["RS256", "ES256"],
            "revocation_endpoint_auth_methods_supported": list(clients.AUTH_METHODS),
            "client_id_metadata_document_supported": True,
            "authorization_response_iss_parameter_supported": True,
        }

    async def metadata_endpoint(self, request: Request) -> Response:
        return JSONResponse(self.metadata(), headers={"Cache-Control": "public, max-age=300"})

    @staticmethod
    def peer(request: Request) -> str:
        """The caller's address, as the public gate worked it out (the relay names it)."""
        peer = (request.scope.get("state") or {}).get("memoreei_peer")
        return peer or (request.client.host if request.client else "?")

    def back_to_client(self, redirect_uri: str, state: str | None, **params: Any) -> RedirectResponse:
        return RedirectResponse(with_params(redirect_uri, {**params, "state": state, "iss": self.issuer}),
                                status_code=303, headers=NO_STORE)

    def finished(self, response: Response) -> Response:
        response.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return response

    async def _notify(self, request_row: dict[str, Any]) -> None:
        if self.notify is None:
            return
        try:
            await self.notify(request_row)
        except Exception as e:  # a notification is a courtesy; the request stands without it
            log.warning("couldn't notify about a sign-in request: %r", e)

    # ── the browser flow ────────────────────────────────────────────────────

    # GET /authorize
    async def authorize(self, request: Request) -> Response:
        peer = self.peer(request)
        if not self.authorize_limit.allow(peer):
            return page(429, "Too many tries", "<p>Wait ten minutes and try again.</p>")
        q = request.query_params
        if any(len(q.getlist(k)) > 1 for k in q.keys()):
            return error_page(400, "That sign-in link is malformed", "A parameter appears twice.")
        client_id = q.get("client_id", "")
        if not client_id:
            return error_page(400, "That sign-in link is malformed", "It names no app (client_id).")
        store = await self.store()
        try:
            meta = await clients.load(store, client_id, self.fetch)
        except ClientError as e:
            log.info("authorize: client %r refused: %s", client_id[:200], e)
            return error_page(400, "This app can't connect here", str(e))
        if "authorization_code" not in meta.get("grant_types", ["authorization_code"]):
            return error_page(400, "This app can't connect here", "It signs in by device code, not by this link.")
        redirect_uri = q.get("redirect_uri")
        if redirect_uri is None and len(meta["redirect_uris"]) == 1:
            redirect_uri = meta["redirect_uris"][0]
        if not clients.redirect_allowed(meta, redirect_uri):
            log.info("authorize: %r asked for unregistered redirect %r", client_id[:200], str(redirect_uri)[:200])
            return error_page(400, "This app can't connect here",
                              "It asked to be sent somewhere it hasn't registered.")
        assert redirect_uri is not None
        state = q.get("state")

        def fail(error: str, description: str) -> Response:
            return self.back_to_client(redirect_uri, state, error=error, error_description=description)

        if q.get("response_type") != "code":
            return fail("unsupported_response_type", "only response_type=code")
        if q.get("code_challenge_method") != "S256" or not CHALLENGE.match(q.get("code_challenge", "")):
            return fail("invalid_request", "PKCE with S256 is required")
        if q.get("resource") not in (None, self.resource):
            return fail("invalid_target", f"resource must be {self.resource}")
        name, host, self_registered = clients.describe(client_id, meta)
        browser = secrets.token_urlsafe(32)
        try:
            row = await store.add_request(
                "authorize", client_id=client_id, client_name=name, client_host=host,
                self_registered=self_registered, peer=peer, resource=self.resource, redirect_uri=redirect_uri,
                state=state, code_challenge=q["code_challenge"], browser_secret=browser)
        except Busy:
            return fail("temporarily_unavailable", "too many sign-ins are waiting; try again in a few minutes")
        log.info("authorize: %s (%s) from %s is waiting for the person, code %s",
                 name, client_id[:200], peer, row["user_code"])
        await self._notify(row)
        response = RedirectResponse(f"/authorize/wait?{urlencode({'request': row['id']})}",
                                    status_code=303, headers=NO_STORE)
        response.set_cookie(COOKIE, browser, max_age=REQUEST_TTL + 60, path="/", secure=True,
                            httponly=True, samesite="lax")
        return response

    # GET /authorize/wait
    async def wait(self, request: Request) -> Response:
        if not self.wait_limit.allow(self.peer(request)):
            return page(429, "Too many tries", "<p>Wait ten minutes and try again.</p>")
        store = await self.store()
        req = await store.browser_request(request.query_params.get("request", ""), request.cookies.get(COOKIE))
        if req is None or req["finished_at"] is not None:
            return expired_page()
        if req["decided_at"] is None and req["expires_at"] < now():
            await store.cancel(req["id"])
            return self.finished(self.back_to_client(req["redirect_uri"], req["state"], error="access_denied",
                                                     error_description="nobody approved it in time"))
        if req["decided_at"] is None:
            return self.waiting_page(req, approve_here=await self._admin_open())
        if not req["approved"]:
            return self.finished(self.back_to_client(req["redirect_uri"], req["state"], error="access_denied",
                                                     error_description="the person said no"))
        if not await store.finish(req["id"]):
            return expired_page()
        code = await store.add_code(req["id"])
        log.info("authorize: %s approved, code handed over", req["client_name"])
        return self.finished(self.back_to_client(req["redirect_uri"], req["state"], code=code))

    async def _admin_open(self) -> bool:
        if self.admin_open is None:
            return False
        try:
            return await self.admin_open()
        except Exception as e:  # the computer is still there to approve on
            log.warning("couldn't tell whether the dashboard is open from anywhere: %r", e)
            return False

    def waiting_page(self, req: dict[str, Any], approve_here: bool = False) -> Response:
        code = html.escape(req["user_code"])
        here = (f"<p><a href='/admin/chatbots/requests/{quote(req['id'])}'>Sign in to approve it here</a> "
                f"with memoreei's password, if you're away from the computer.</p>") if approve_here else ""
        return page(200, "Approve it on your computer",
                    f"<p>{who(req)} wants to use memoreei. To let it, approve this code on the computer "
                    f"memoreei runs on:</p>"
                    f"<p class=code>{code}</p>"
                    f"<p>Open memoreei's dashboard there (from the menu bar on a Mac), or run "
                    f"<code>memoreei chatbots approve {code}</code>. This page moves on by itself.</p>"
                    f"{here}"
                    f"<form method=post action=/authorize/cancel>"
                    f"<input type=hidden name=request value='{html.escape(req['id'])}'>"
                    f"<div class=buttons><button>Cancel</button></div></form>"
                    f"<p class=small>Sends you back to {html.escape(clients.host_of(req['redirect_uri']))}. "
                    f"The request lapses after ten minutes.</p>",
                    form_to=origin_of(req["redirect_uri"]), refresh=str(WAIT_REFRESH))

    # POST /authorize/cancel
    async def cancel(self, request: Request) -> Response:
        form = parse_qs((await request.body())[:4096].decode(errors="replace"))
        store = await self.store()
        req = await store.browser_request((form.get("request") or [""])[0], request.cookies.get(COOKIE))
        if req is None or req["finished_at"] is not None:
            return expired_page()
        await store.cancel(req["id"])
        return self.finished(self.back_to_client(req["redirect_uri"], req["state"], error="access_denied",
                                                 error_description="cancelled"))

    # ── the token endpoint ──────────────────────────────────────────────────

    async def form(self, request: Request) -> dict[str, str]:
        body = await request.body()
        if len(body) > 16384:
            raise OAuthError("invalid_request", "too large")
        if "application/x-www-form-urlencoded" not in request.headers.get("content-type", ""):
            raise OAuthError("invalid_request", "send application/x-www-form-urlencoded")
        parsed = parse_qs(body.decode(errors="replace"), keep_blank_values=True)
        if any(len(v) > 1 for v in parsed.values()):
            raise OAuthError("invalid_request", "a parameter appears twice")
        return {k: v[0] for k, v in parsed.items()}

    async def client(self, request: Request, form: dict[str, str]) -> tuple[str, dict[str, Any]]:
        """Who's asking: (client_id, metadata). A client may use the methods its
        document declares and no others; an assertion, if sent, must verify."""
        if request.headers.get("authorization", "").lower().startswith("basic "):
            raise OAuthError("invalid_client", "client secrets aren't used here", 401)
        store = await self.store()
        assertion = form.get("client_assertion")
        try:
            if assertion:
                if form.get("client_assertion_type") != ASSERTION_TYPE:
                    raise ClientError("unsupported client_assertion_type")
                client_id = clients.split_jwt(assertion)[1].get("iss")
                if not isinstance(client_id, str) or form.get("client_id") not in (None, client_id):
                    raise ClientError("the assertion's iss isn't the client_id")
                meta = await clients.load(store, client_id, self.fetch)
                if "private_key_jwt" not in clients.methods(meta):
                    raise ClientError("this client doesn't use private_key_jwt")
                await clients.verify_assertion(store, client_id, meta, assertion,
                                               [self.token_endpoint, self.issuer], self.fetch)
            else:
                client_id = form.get("client_id")
                if not client_id:
                    raise ClientError("client_id is required")
                meta = await clients.load(store, client_id, self.fetch)
                if "none" not in clients.methods(meta):
                    raise ClientError("this client must authenticate with private_key_jwt")
        except ClientError as e:
            raise OAuthError("invalid_client", str(e), 401)
        return client_id, meta

    def over_limit(self, request: Request) -> Response | None:
        """A 429 for an address that has been refused too often, or asks too fast."""
        ip = self.peer(request)
        if self.refusals.full(ip) or not self.token_limit.allow(ip):
            return JSONResponse({"error": "temporarily_unavailable",
                                 "error_description": "too many requests from this address; try again later"},
                                status_code=429, headers={**NO_STORE, "Retry-After": "600"})
        return None

    async def token(self, request: Request) -> Response:
        if (limited := self.over_limit(request)) is not None:
            return limited
        form: dict[str, str] = {}
        client_id = None
        try:
            form = await self.form(request)
            client_id, _ = await self.client(request, form)
            grant_type = form.get("grant_type")
            if grant_type == "authorization_code":
                grant, generation = await self.redeem_code(form, client_id), 0
            elif grant_type == "refresh_token":
                grant, generation = await self.redeem_refresh_token(form, client_id)
            elif grant_type == DEVICE_GRANT:
                grant, generation = await self.redeem_device_code(form, client_id), 0
            else:
                raise OAuthError("unsupported_grant_type", "authorization_code, refresh_token or device_code")
            if form.get("resource") not in (None, grant["resource"]):
                raise OAuthError("invalid_target", "this grant is for a different resource")
        except OAuthError as e:
            if e.error not in ("authorization_pending", "slow_down"):    # a device code's polling is not a refusal
                self.refusals.note(self.peer(request))
                asker = client_id or form.get("client_id") or ("an assertion" if form.get("client_assertion") else "?")
                log.warning("token: refused %s (%s): %s: %s", asker[:200], form.get("grant_type", "?"),
                            e.error, e.description)
            headers = {**NO_STORE, **({"WWW-Authenticate": "Basic"} if e.status == 401 else {})}
            return JSONResponse({"error": e.error, "error_description": e.description},
                                status_code=e.status, headers=headers)
        return JSONResponse(await self.issue(grant, generation), headers=NO_STORE)

    async def issue(self, grant: dict[str, Any], generation: int) -> dict[str, Any]:
        store = await self.store()
        key_id, key = await store.signing_key()
        return {"access_token": await store.add_access_token(grant["id"], self.access_ttl), "token_type": "Bearer",
                "expires_in": self.access_ttl,
                "refresh_token": tokens.sign(key_id, key, grant["id"], generation, int(self.clock()))}

    async def redeem_code(self, form: dict[str, str], client_id: str) -> dict[str, Any]:
        store = await self.store()
        row, used = await store.take_code(form.get("code", ""))
        if row is None:
            raise OAuthError("invalid_grant", "unknown or expired code")
        if used:
            if row["code_grant_id"] and await store.revoke_grant(row["code_grant_id"], "authorization code used twice"):
                log.warning("token: a code was used twice; its grant %s is revoked", row["code_grant_id"])
            raise OAuthError("invalid_grant", "code already used")
        if row["client_id"] != client_id:
            raise OAuthError("invalid_grant", "unknown or expired code")
        if form.get("redirect_uri") not in (None, row["redirect_uri"]):
            raise OAuthError("invalid_grant", "redirect_uri doesn't match the authorization request")
        verifier = form.get("code_verifier", "")
        if not PKCE.match(verifier) or not secrets.compare_digest(s256(verifier), row["code_challenge"]):
            raise OAuthError("invalid_grant", "code_verifier doesn't match")
        grant = await store.add_grant(row)
        await store.code_led_to(row["code_hash"], grant["id"])
        log.info("token: %s connected (grant %s)", grant["client_name"], grant["id"])
        return grant

    async def redeem_refresh_token(self, form: dict[str, str], client_id: str) -> tuple[dict[str, Any], int]:
        """RFC 9700 §4.14.2: rotate, and treat a retired token coming back as theft."""
        store = await self.store()
        presented = form.get("refresh_token", "")
        key_id = tokens.key_id_of(presented)
        key = await store.key_by_id(key_id) if isinstance(key_id, str) else None
        refresh = tokens.verify(presented, key) if key else None
        if refresh is None:
            raise OAuthError("invalid_grant", "unknown refresh token")
        grant = await store.grant(refresh.grant_id)
        if grant is None or grant["client_id"] != client_id:
            raise OAuthError("invalid_grant", "unknown refresh token")
        if grant["revoked_at"] is not None:
            raise OAuthError("invalid_grant", "this grant has been revoked")
        t = int(self.clock())
        if t - refresh.issued_at > tokens.REFRESH_IDLE:
            raise OAuthError("invalid_grant", "the refresh token expired; sign in again")
        current = grant["refresh_generation"]
        if refresh.generation == current and await store.rotate_refresh(grant["id"], current):
            return grant, current + 1
        grant = await store.live_grant(grant["id"]) or grant
        current = grant["refresh_generation"]
        if grant["revoked_at"] is None and refresh.generation == current - 1 \
                and t - (grant["refreshed_at"] or 0) <= REFRESH_GRACE:
            log.info("token: %s presented the refresh token before the newest, within the grace; "
                     "it gets the newest again", grant["client_name"])
            return grant, current
        await store.revoke_grant(grant["id"], "refresh token used twice")
        log.warning("token: a retired refresh token for %s came back; grant %s is revoked",
                    grant["client_name"], grant["id"])
        raise OAuthError("invalid_grant", "refresh token already used")

    async def redeem_device_code(self, form: dict[str, str], client_id: str) -> dict[str, Any]:
        """RFC 8628 §3.4-3.5. The first poll after the person's yes makes the grant
        and takes its tokens. A device code used again is treated like a code used twice."""
        store = await self.store()
        row = await store.device_request(form.get("device_code", ""))
        if row is None or row["client_id"] != client_id:
            raise OAuthError("invalid_grant", "unknown device code")
        if row["finished_at"] is not None:
            if row["grant_id"] and await store.revoke_grant(row["grant_id"], "device code used twice"):
                log.warning("token: a device code was used twice; grant %s is revoked", row["grant_id"])
            raise OAuthError("invalid_grant", "device code already used")
        t, interval = self.clock(), row["interval"] or DEVICE_INTERVAL
        if row["last_poll_at"] is not None and t - row["last_poll_at"] < interval - 1:
            interval += 5                                       # §3.5: and keep it
            await store.note_poll(row["id"], t, interval)
            raise OAuthError("slow_down", f"poll every {interval} s")
        await store.note_poll(row["id"], t, interval)
        if row["approved"] == 0:
            raise OAuthError("access_denied", "the person said no")
        if row["approved"] is None:
            if row["expires_at"] < now():
                raise OAuthError("expired_token", "the code expired; start again")
            raise OAuthError("authorization_pending", "waiting for the person to approve it on their computer")
        if not await store.finish(row["id"]):
            raise OAuthError("expired_token", "too late to collect; start again")
        grant = await store.add_grant(row)
        await store.set_grant(row["id"], grant["id"])
        log.info("token: %s connected by device code (grant %s)", grant["client_name"], grant["id"])
        return grant

    # POST /revoke (RFC 7009): 200 whatever the token was, so it reveals nothing.
    async def revoke(self, request: Request) -> Response:
        if (limited := self.over_limit(request)) is not None:
            return limited
        try:
            form = await self.form(request)
            client_id, _ = await self.client(request, form)
        except OAuthError as e:
            self.refusals.note(self.peer(request))
            return JSONResponse({"error": e.error, "error_description": e.description},
                                status_code=e.status, headers=NO_STORE)
        store = await self.store()
        token = form.get("token", "")
        grant_id = None
        if token.startswith(tokens.PREFIX):
            key_id = tokens.key_id_of(token)
            key = await store.key_by_id(key_id) if isinstance(key_id, str) else None
            refresh = tokens.verify(token, key) if key else None
            grant_id = refresh.grant_id if refresh else None
        elif token:
            grant_id = await store.access_token_grant_id(token)
        grant = await store.grant(grant_id) if grant_id else None
        if grant is not None and grant["client_id"] == client_id:
            if await store.revoke_grant(grant["id"], "revoked by the client"):
                log.info("revoke: %s let go of grant %s", grant["client_name"], grant["id"])
        return Response(status_code=200, headers=NO_STORE)

    # POST /register (RFC 7591)
    async def register(self, request: Request) -> Response:
        if not self.register_limit.allow(self.peer(request)):
            return JSONResponse({"error": "temporarily_unavailable",
                                 "error_description": "too many registrations from this address; try again later"},
                                status_code=429, headers={**NO_STORE, "Retry-After": "3600"})
        body = await request.body()
        try:
            if len(body) > 8192:
                raise ClientError("too large")
            try:
                parsed = json.loads(body)
            except ValueError:
                raise ClientError("send JSON")
            answer = await clients.register(await self.store(), parsed)
        except ClientError as e:
            return JSONResponse({"error": "invalid_client_metadata", "error_description": str(e)},
                                status_code=400, headers=NO_STORE)
        log.info("register: %s (%r) -> %s", answer["client_id"], answer["client_name"],
                 ", ".join(clients.host_of(u) for u in answer["redirect_uris"]))
        return JSONResponse(answer, status_code=201, headers=NO_STORE)

    # ── the device flow ─────────────────────────────────────────────────────

    # POST /device_authorization (RFC 8628 §3.1-3.2)
    async def device_authorization(self, request: Request) -> Response:
        def oauth_error(error: str, description: str, status: int = 400) -> Response:
            return JSONResponse({"error": error, "error_description": description}, status_code=status,
                                headers=NO_STORE)

        peer = self.peer(request)
        if not self.device_limit.allow(peer):
            return oauth_error("slow_down", "too many device logins from this address; try again in an hour", 429)
        try:
            form = await self.form(request)
            client_id, meta = await self.client(request, form)
        except OAuthError as e:
            return oauth_error(e.error, e.description, e.status)
        # Only a client whose own metadata lists the grant may ask, so nobody can start
        # one as Claude's CIMD client and have the request say "Claude".
        if DEVICE_GRANT not in meta.get("grant_types", []):
            return oauth_error("unauthorized_client", "this client's metadata doesn't list the device_code grant")
        if form.get("resource") not in (None, self.resource):
            return oauth_error("invalid_target", f"resource must be {self.resource}")
        name, host, self_registered = clients.describe(client_id, meta)
        device_code = "mdc_" + secrets.token_urlsafe(32)
        store = await self.store()
        try:
            row = await store.add_request(
                "device", client_id=client_id, client_name=name, client_host=host, self_registered=self_registered,
                peer=peer, resource=self.resource, device_code=device_code, interval=DEVICE_INTERVAL)
        except Busy:
            return oauth_error("temporarily_unavailable", "too many sign-ins are waiting; try again shortly", 503)
        log.info("device: %s (%s) from %s is waiting for the person, code %s",
                 name, client_id[:200], peer, row["user_code"])
        await self._notify(row)
        return JSONResponse({
            "device_code": device_code,
            "user_code": row["user_code"],
            "verification_uri": f"{self.issuer}/device",
            "expires_in": REQUEST_TTL,
            "interval": DEVICE_INTERVAL,
        }, headers=NO_STORE)

    # GET /device
    async def device_page(self, request: Request) -> Response:
        here = ("<p>Away from the computer? <a href='/admin/connections'>Sign in to memoreei's dashboard here</a> "
                "with its password, and approve it on the Connections page.</p>") if await self._admin_open() else ""
        return page(200, "Approve it on your computer",
                    "<p>An app asked to use memoreei and gave you a code. To let it, approve that code on "
                    "the computer memoreei runs on: in memoreei's dashboard (from the menu bar on a Mac), "
                    "or with <code>memoreei chatbots approve CODE</code>.</p>" + here +
                    "<p class=small>Only approve a code you started yourself, just now. If someone sent you a "
                    "code, don't: it would let them into your memoreei.</p>")

    # ── checking access tokens (for the public gate) ────────────────────────

    async def verify(self, token: str) -> dict[str, Any]:
        """The caller behind an access token, or Rejected."""
        if not token.startswith("mat_"):
            raise Rejected("not one of ours")
        store = await self.store()
        grant = await store.access_grant(token)
        if grant is None:
            raise Rejected("unknown, expired, or its chatbot was removed")
        if grant["resource"] != self.resource:
            raise Rejected("for another address")
        t = time.monotonic()
        if t - self._touched.get(grant["id"], -TOUCH_INTERVAL) >= TOUCH_INTERVAL:
            self._touched[grant["id"]] = t
            await store.touch_grant(grant["id"])
        return {"kind": "oauth", "client": grant["client_name"], "subject": grant["id"]}

