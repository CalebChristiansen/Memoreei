"""The dashboard at /admin: status, connections, and what memoreei reads from.

Server-rendered with Jinja; htmx swaps fragments for the buttons. Every route runs
behind AdminGuard (see admin/auth.py for the rules).
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates
from markupsafe import Markup, escape

from memoreei import __version__
from memoreei.admin import auth
from memoreei.admin.account import account_store
from memoreei.catalog import (
    CONNECTORS,
    DASHBOARD_CONNECTORS,
    DASHBOARD_UPLOADS,
    UPLOADS,
    is_connector_configured,
    kind_name,
)
from memoreei.config import (
    current_settings,
    ensure_home,
    get_config,
    legacy_home,
    memoreei_home,
    save_settings,
)
from memoreei.connectors.signal.sync import DISAPPEARING_SKIPPED

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))

# The page's own files only. htmx needs inline styles for its indicators.
SECURITY_HEADERS = {
    "content-security-policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
    "frame-ancestors 'none'; form-action 'self'; base-uri 'none'",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    # Not no-referrer: under it, browsers send "Origin: null" on plain form posts, and
    # the dashboard's own forms fail the same-origin check.
    "referrer-policy": "same-origin",
    "cache-control": "no-store",
}

log = logging.getLogger("memoreei.admin")
_background: set[asyncio.Task] = set()


# ── Guard ────────────────────────────────────────────────────────────────────


class AdminGuard:
    """ASGI middleware enforcing the dashboard's three locks (admin/auth.py).

    One guard per listener. The LAN listener's (``public_origin`` None) tells the
    computer from elsewhere by address and ``Host``. The public listener's is built
    with the public address it answers to, and nothing through it is ever "on the
    computer", whatever its address or ``Host`` say.
    """

    def __init__(self, app: Any, public_origin: str | None = None) -> None:
        self.app = app
        self.public_origin = auth.origin_of(public_origin) if public_origin else None

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        host = request.headers.get("host", "")
        client = request.client.host if request.client else None
        path = scope.get("path", "")
        peer = (scope.get("state") or {}).get("memoreei_peer") or client or "?"
        static = "/admin/static/" in path
        store = await account_store()

        if self.public_origin is not None:
            # The gate only passes /admin on when it's open; this says so again, and
            # answers only to the address memoreei is known by.
            local = False
            if auth.host_name(host) != urlsplit(self.public_origin).hostname or not await store.remote_open():
                log.warning("%s %s from %s refused: the dashboard isn't open from anywhere (Host %s)",
                            request.method, path, peer, host)
                await _plain(404, "Not found.")(scope, receive, send)
                return
            origins = {self.public_origin}
            secure = self.public_origin.startswith("https:")
        else:
            local = auth.local_request(client, host)
            public_url = get_config().public_url  # behind a proxy of your own, at that address
            if not local and not (await store.remote_open() and auth.names_this_computer(host, public_url)):
                log.warning("%s %s from %s refused: not loopback (Host %s)", request.method, path, client, host)
                await _plain(403, "The Memoreei dashboard only answers on the computer it runs on.")(
                    scope, receive, send
                )
                return
            origins = {f"{request.url.scheme}://{host}".lower()}
            secure = request.url.scheme == "https"
            if not local and public_url and auth.host_name(host) == (urlsplit(public_url).hostname or ""):
                origins.add(auth.origin_of(public_url))
                secure = secure or public_url.startswith("https:")

        if request.method not in ("GET", "HEAD") and not _origin_allowed(request, origins):
            log.warning("%s %s from %s refused: cross-origin (Origin %s, Sec-Fetch-Site %s)",
                        request.method, path, peer, request.headers.get("origin"),
                        request.headers.get("sec-fetch-site"))
            await _plain(403, "Cross-origin request refused.")(scope, receive, send)
            return

        if path.endswith("/admin/login") and not local:
            await _plain(403, "One-time dashboard links work only on the computer memoreei runs on.")(
                scope, receive, send
            )
            return
        session = None if static else await store.session(request.cookies.get(auth.COOKIE))
        if session is not None and session["how"] == "link" and not local:
            session = None  # a link's session never leaves the computer
        scope.setdefault("state", {})["memoreei_admin"] = {
            "local": local, "public": self.public_origin is not None, "peer": peer,
            "session": session, "secure": secure,
        }
        if session is None and not (path.endswith(("/admin/login", "/admin/signin")) or static):
            next_path = path if request.method in ("GET", "HEAD") else None
            response = await signin_page(request, next_path=next_path, status_code=401)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _origin_allowed(request: Request, origins: set[str]) -> bool:
    origin = request.headers.get("origin")
    if origin is None or origin == "null":
        return request.headers.get("sec-fetch-site") == "same-origin"
    return origin.rstrip("/").lower() in origins


def where(request: Request) -> dict[str, Any]:
    """What the guard found out about this request: local, public, peer, session, secure."""
    return (request.scope.get("state") or {}).get("memoreei_admin") or {
        "local": False, "public": False, "peer": None, "session": None, "secure": False}


async def signin_page(request: Request, next_path: str | None = None, error: str | None = None,
                      status_code: int = 200) -> Response:
    """Signed out: the password form, where there's a password, else how to get a link."""

    account = await (await account_store()).account()
    if not account["password_hash"]:
        return _secure(templates.TemplateResponse(
            request, "signed_out.html", {"version": __version__}, status_code=status_code))
    return _secure(templates.TemplateResponse(request, "signin.html", {
        "version": __version__, "next": auth.safe_next(next_path), "totp": bool(account["totp_secret"]),
        "error": error, "local": where(request)["local"],
    }, status_code=status_code))


def _plain(status: int, text: str) -> Response:
    return _secure(PlainTextResponse(text + "\n", status_code=status))


def _secure(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def _page(request: Request, template: str, status_code: int = 200, **context: Any) -> Response:
    context.setdefault("version", __version__)
    context.setdefault("nav", "")
    context.setdefault("local", where(request)["local"])
    return _secure(templates.TemplateResponse(request, template, context, status_code=status_code))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _full_disk_access(chat_db: str | None = None) -> bool | None:
    """Whether this process can read Messages. None when there's nothing to check.

    This process is the one that needs the grant (Memoreei.app's, or the interpreter
    running the service), so the answer here is the one that counts.
    """
    chat_db = chat_db or get_config().imessage_db_path
    if sys.platform != "darwin" or not chat_db:
        return None
    from memoreei.service._macos_access import can_read_messages

    return can_read_messages(chat_db)


def _ago(ts: float | None) -> str:
    if not ts:
        return "never"
    seconds = int(time.time() - ts)
    if seconds < 60:
        return "just now"
    for size, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


def _tilde(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


def _linkify(text: str) -> Markup:
    """A catalog hint, with its web address made a link."""
    parts = re.split(r"(https?://[^\s)]+)", text)
    return Markup("").join(
        Markup('<a href="{0}" rel="noreferrer">{1}</a>').format(p, p.split("://", 1)[1])
        if i % 2 else escape(p)
        for i, p in enumerate(parts)
    )


def _connector_key(sync_name: str) -> str | None:
    """The dashboard page for a connector, by the name sync reports it under."""
    for key in _dashboard_connectors():
        if CONNECTORS[key].get("sync_name", key) == sync_name:
            return key
    return None


@functools.lru_cache(maxsize=None)
def _static(name: str) -> str:
    """A static file's URL, fingerprinted by its content. Browsers keep these files
    between versions (a test build can even share a version number), and a new page
    on an old stylesheet is a sight nobody should see: a changed file is a new URL."""
    digest = hashlib.sha256((HERE / "static" / name).read_bytes()).hexdigest()[:10]
    return f"/admin/static/{name}?v={digest}"


templates.env.filters["ago"] = _ago
templates.env.filters["date"] = lambda ts: (
    f"{time.localtime(ts).tm_mday} {time.strftime('%b %Y', time.localtime(ts))}" if ts else "never")
templates.env.globals["static"] = _static
templates.env.filters["tilde"] = _tilde
templates.env.filters["linkify"] = _linkify
templates.env.filters["kind_name"] = kind_name
templates.env.filters["connector_key"] = lambda name: _connector_key(name)
# Memoreei.app sets MEMOREEI_APP; the dashboard then points at its menu, not the CLI.
templates.env.globals["in_app"] = lambda: bool(os.environ.get("MEMOREEI_APP"))


def _env_vars() -> dict[str, str]:
    return current_settings()


def _on_mac() -> bool:
    return sys.platform == "darwin"


def _offered(key: str) -> bool:
    if key == "imessage":
        return _on_mac()
    if key == "whatsapp":
        # Where WhatsApp for Mac is, or wherever someone already pointed it.
        from memoreei.connectors.whatsapp import mac_app_present

        return (_on_mac() and mac_app_present()) or "WHATSAPP_DB_PATH" in _env_vars()
    if key == "signal":
        from memoreei.connectors.signal import app_present

        return app_present() or "SIGNAL_DB_KEY" in _env_vars()
    return True


def _dashboard_connectors() -> tuple[str, ...]:
    """The connectors the dashboard offers here: iMessage only exists on a Mac,
    WhatsApp only where WhatsApp for Mac keeps its chats, and Signal only where Signal
    Desktop has been set up."""
    return tuple(k for k in DASHBOARD_CONNECTORS if _offered(k))


def _whatsapp_readable(db_path: str | None) -> bool | None:
    """Whether this process can open WhatsApp's database. None when it isn't there."""
    from memoreei.connectors.whatsapp import ChatStorageReader, get_db_path

    path = Path(db_path or get_db_path()).expanduser()
    if not path.exists():
        return None
    try:
        with ChatStorageReader(path):
            return True
    except sqlite3.Error:
        return False


def _accounts() -> list[dict[str, Any]]:
    """The connectors the dashboard sets up, and whether each is."""
    env = _env_vars()
    return [
        {"key": k, **CONNECTORS[k], "configured": is_connector_configured(k, env)}
        for k in _dashboard_connectors()
    ]


def _uploads() -> list[dict[str, Any]]:
    return [{"key": k, **UPLOADS[k]} for k in DASHBOARD_UPLOADS]


async def _status_context() -> dict[str, Any]:
    from memoreei.oauth.store import OAuthStore
    from memoreei.server import _get_db, _sync_manager

    db = await _get_db()
    sources = await db.list_sources()
    by_kind: dict[str, int] = {}
    for source, count in sources.items():
        kind = source.split(":", 1)[0]
        by_kind[kind] = by_kind.get(kind, 0) + count
    cfg = get_config()
    connectors = cfg.configured_connectors()
    # What's set up, and what's been read (imports included), by the name people know.
    accounts = _accounts()
    names = [kind_name(c) for c in connectors]
    names += [a["short"] for a in accounts if a["configured"] and a["short"] not in names]
    names += [kind_name(k) for k in by_kind if kind_name(k) not in names]
    fda = _full_disk_access()
    # A stale linked Mac (the phone offline for weeks, or WhatsApp dropping this macOS)
    # syncs nothing without an error; the newest message's age is what gives it away.
    whatsapp_newest = await db.newest_ts("whatsapp") if "whatsapp" in connectors else None
    # Signal Desktop expires its own builds, and an old Mac can be left on the last one
    # that runs there; its messages stopping is the only sign.
    signal_newest = await db.newest_ts("signal") if "signal" in connectors else None
    signal_disappearing = await db.get_signal_checkpoint(DISAPPEARING_SKIPPED) if signal_newest else None
    last_run = _sync_manager.last_run
    failed = bool(last_run) and any(
        isinstance(r, dict) for r in last_run["result"].get("connectors", {}).values()
    )
    # Chatbots and keys alike, as the Connections page lists them.
    connected = len(await db.list_api_keys()) + len(await OAuthStore(db).grants())
    has_source = any(a["configured"] for a in accounts) or bool(sources)
    return {
        "home": str(memoreei_home()),
        "port": cfg.port,
        "total": sum(sources.values()),
        "by_kind": sorted(by_kind.items(), key=lambda kv: -kv[1]),
        "connectors": connectors,
        "source_names": names,
        "auto_sync": cfg.auto_sync,
        "interval_min": max(1, cfg.sync_interval // 60),
        "fda": fda,
        "whatsapp_newest": whatsapp_newest,
        "signal_newest": signal_newest,
        "signal_disappearing": signal_disappearing,
        "running": _sync_manager.running,
        "last_run": last_run,
        "connected": connected,
        # As the Sources page counts it, so the two never disagree.
        "has_source": has_source,
        # A new install: the two setup steps lead the page until both are done.
        "welcome": not has_source or not connected,
        "problems": failed or fda is False,
        # The welcome's third step, which is optional and leaves with the other two.
        "has_password": await (await account_store()).has_password(),
    }


def _address() -> str:
    """Where clients reach this server, as a new key's setup tells them."""
    from memoreei.auth import server_urls

    cfg = get_config()
    url = server_urls(cfg.port, cfg.public_url, tls=bool(cfg.tls_cert))[0]
    return url.split("://", 1)[-1].removesuffix("/mcp")


async def _service_context() -> dict[str, Any] | None:
    """The switches of whatever keeps this server running: Memoreei.app on a Mac, the
    systemd user unit on Linux. None where there's neither (a container, `serve` run by
    hand, the `service install` LaunchAgent)."""
    from memoreei.service import _app

    if sys.platform == "darwin" and _app.available():
        return {"app": True, "enabled": _app.start_at_login(), "active": True}
    if not sys.platform.startswith("linux"):
        return None
    from memoreei.service import _systemd as sd

    def read() -> dict[str, Any] | None:
        if not sd.manager_available():
            return None
        state = sd.unit_state()
        if not state["installed"]:
            return None
        return {**state, "linger": sd.linger(), "linger_command": sd.linger_command()}

    return await asyncio.to_thread(read)


def _page_notices() -> dict[str, Any]:
    """What the status page mentions above everything else."""
    from memoreei import updates
    from memoreei.service._systemd import bundle_root

    legacy = legacy_home()
    return {
        "update": updates.available(),
        "legacy": str(legacy) if legacy else None,
        "bundle": bundle_root() is not None,
    }


# ── Routes ───────────────────────────────────────────────────────────────────


def _set_cookie(request: Request, response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, path="/admin", httponly=True, samesite="lax",
                        secure=where(request)["secure"])


async def login(request: Request) -> Response:
    from memoreei.server import _get_db

    db = await _get_db()
    session = await auth.redeem_login_token(db, request.query_params.get("token", ""), where(request)["peer"],
                                            request.headers.get("user-agent"))
    if session is None:
        return _secure(
            templates.TemplateResponse(
                request, "signed_out.html", {"version": __version__, "expired": True}, status_code=401
            )
        )
    await auth.end_session(db, request.cookies.get(auth.COOKIE))  # whatever this browser had, it's done
    response = RedirectResponse("/admin/", status_code=303)
    _set_cookie(request, response, auth.COOKIE, session, auth.SESSION_TTL)
    return _secure(response)


SIGNIN_LIMITS: Any = None  # memoreei.admin.account.SignInLimits, made on first use


def _limits() -> Any:
    global SIGNIN_LIMITS
    if SIGNIN_LIMITS is None:
        from memoreei.admin.account import SignInLimits

        SIGNIN_LIMITS = SignInLimits()
    return SIGNIN_LIMITS


async def signin(request: Request) -> Response:
    """The password form (GET), and signing in with it (POST)."""
    from memoreei.admin.account import BROWSER_TTL, PASSWORD_SESSION_TTL

    if request.method == "GET":
        next_path = request.query_params.get("next")
        if where(request)["session"] is not None:
            return RedirectResponse(auth.safe_next(next_path), status_code=303)
        return await signin_page(request, next_path=next_path)

    form = await request.form()
    password = str(form.get("password", ""))
    code = str(form.get("code", ""))[:64]
    next_path = str(form.get("next", "")) or None
    store = await account_store()
    w = where(request)
    peer = w["peer"]
    if not await store.has_password():
        return await signin_page(request, status_code=401)
    known = await store.browser_known(request.cookies.get(auth.BROWSER_COOKIE))
    # On the computer there's no limit (it has the one-time link anyway). Elsewhere the
    # attempt is counted before the slow check, so many at once can't all slip in.
    limited = not w["local"]
    if limited and (why := _limits().take(peer, known)) is not None:
        log.warning("dashboard sign-in from %s refused: %s", peer, why)
        return await signin_page(request, next_path, status_code=429,
                                 error="Too many failed sign-ins. Wait a few minutes and try again.")
    ok, how = await store.check(password, code)
    if ok and limited:
        _limits().forgive(peer)
    if not ok:
        log.warning("dashboard sign-in from %s failed: %s", peer, how)
        await store.record_signin(peer, "password", False, how)
        return await signin_page(request, next_path, status_code=401,
                                 error="That didn't work. Check the password" +
                                       (" and the code." if (await store.account())["totp_secret"] else "."))
    await store.end_session(request.cookies.get(auth.COOKIE))  # never carry a session across a sign-in
    session = await store.add_session("password", peer, request.headers.get("user-agent"))
    await store.record_signin(peer, "password", True, how)
    log.info("dashboard: signed in from %s with the %s%s", peer, how, "" if w["local"] else ", away from the computer")
    response = RedirectResponse(auth.safe_next(next_path), status_code=303)
    _set_cookie(request, response, auth.COOKIE, session, PASSWORD_SESSION_TTL)
    if not known:
        _set_cookie(request, response, auth.BROWSER_COOKIE, await store.add_browser(), BROWSER_TTL)
    return _secure(response)


async def logout(request: Request) -> Response:
    from memoreei.server import _get_db

    await auth.end_session(await _get_db(), request.cookies.get(auth.COOKIE))
    response = RedirectResponse("/admin/", status_code=303)
    response.delete_cookie(auth.COOKIE, path="/admin", secure=where(request)["secure"], httponly=True,
                           samesite="lax")
    return _secure(response)


async def status(request: Request) -> Response:
    return _page(
        request, "status.html", nav="status", **await _status_context(), **_page_notices(),
        service=await _service_context(), linger_failed="linger" in request.query_params,
        address=_address(),
    )


async def status_fragment(request: Request) -> Response:
    context = await _status_context()
    response = _page(request, "_status_card.html", **context)
    # htmx polls this while a sync runs. Once it's over, reload the page: the counts,
    # the sources and the welcome steps above the card have all moved on too.
    if request.headers.get("hx-request") and not context["running"]:
        response.headers["hx-refresh"] = "true"
    return response


async def _start_sync() -> None:
    """Sync everything in the background, unless a sync is already running."""
    from memoreei.server import _get_tools, _sync_manager

    if not _sync_manager.running:
        task = asyncio.create_task(_sync_manager.sync_everything(await _get_tools()))
        _background.add(task)
        task.add_done_callback(_background.discard)
        await asyncio.sleep(0)  # let it take the lock, so the page says "Syncing"


async def sync_now(request: Request) -> Response:
    await _start_sync()
    return await status_fragment(request)


# ── The Linux service (the Mac app's menu items, for a desktop without a tray) ──


async def service_autostart(request: Request) -> Response:
    from memoreei.service import _systemd as sd

    on = str((await request.form()).get("on", "")) == "1"
    from memoreei.service import _app

    if sys.platform == "darwin" and _app.available():
        await asyncio.to_thread(_app.set_start_at_login, on)
        return RedirectResponse("/admin/", status_code=303)
    await asyncio.to_thread(sd.set_enabled, on)
    # `memoreei open` turns this on the first time only; from now on it's this switch.
    (ensure_home() / ".start-at-login-chosen").touch()
    return RedirectResponse("/admin/", status_code=303)


async def service_linger(request: Request) -> Response:
    from memoreei.service import _systemd as sd

    ok = await asyncio.to_thread(sd.enable_linger)
    return RedirectResponse("/admin/" if ok else "/admin/?linger=failed", status_code=303)


async def service_stop(request: Request) -> Response:
    """Stop the server, after this page has been sent: the page is its last word."""
    from memoreei.service import _systemd as sd

    from memoreei.service import _app

    def stop() -> None:
        if sys.platform == "darwin" and _app.available():
            _app.request_quit()  # so the app quits with it, rather than restarting it
        if sd.running_as_unit():
            sd.stop_soon()  # so systemd knows it was asked, and doesn't restart it
        else:
            import signal

            os.kill(os.getpid(), signal.SIGTERM)

    response = _page(request, "stopped.html", nav="none")
    response.background = BackgroundTask(stop)
    return response


async def log_page(request: Request) -> Response:
    from memoreei.service import _systemd as sd

    from memoreei.service import _app

    if sys.platform == "darwin" and _app.available():
        text = await asyncio.to_thread(_app.log_tail, 300)
        hint = f"The whole file: <code>{escape(_tilde(str(_app.log_path())))}</code>"
    else:
        text = await asyncio.to_thread(sd.journal, 300)
        hint = "In a terminal: <code>memoreei service logs</code>"
    return _page(request, "log.html", nav="status", log=text, log_hint=hint)


async def key_create(request: Request) -> Response:
    """A new key, shown once, with the Connections table brought up to date beside it."""
    from memoreei.admin.connections import _reachable, connections
    from memoreei.auth import KEY_NAME_RE, client_config_text, generate_key, hash_key, server_urls
    from memoreei.server import _get_db

    name = str((await request.form()).get("name", "")).strip()
    db = await _get_db()
    error = None
    if not KEY_NAME_RE.match(name):
        error = "Use letters, digits, '.', '_' or '-', up to 64 characters, e.g. laptop."
    else:
        key = generate_key()
        if not await db.add_api_key(name, hash_key(key)):
            error = f"There's already a key called “{name}”. Pick another name, or revoke it first."
    listed = {"connections": await connections(), "reachable": _reachable()}
    if error:
        return _page(request, "_key_created.html", error=error, **listed)
    cfg = get_config()
    urls = server_urls(cfg.port, cfg.public_url, tls=bool(cfg.tls_cert))
    return _page(
        request,
        "_key_created.html",
        name=name,
        key=key,
        urls=urls,
        claude_code=f'claude mcp add --transport http memoreei {urls[0]} \\\n'
        f'  --header "Authorization: Bearer {key}"',
        config=client_config_text(key, urls, from_public_url=bool(cfg.public_url)),
        **listed,
    )


async def key_revoke(request: Request) -> Response:
    from memoreei.admin.connections import connections, list_fragment
    from memoreei.server import _get_db

    await (await _get_db()).revoke_api_key(request.path_params["name"])
    return list_fragment(request, await connections())


async def sources_page(request: Request) -> Response:
    return _page(request, "sources.html", nav="sources", accounts=_accounts(), uploads=_uploads())


def _enabled_connector(request: Request) -> dict[str, Any] | None:
    key = request.path_params["key"]
    if key not in _dashboard_connectors():
        return None
    return {"key": key, **CONNECTORS[key]}


async def source_form(request: Request, error: str | None = None) -> Response:
    connector = _enabled_connector(request)
    if connector is None:
        return _plain(404, "Not available in the dashboard.")
    env = _env_vars()
    fields = [
        {
            "var": var[0],
            "label": var[1],
            "secret": var[2],
            "hint": var[3],
            # Secrets are never sent back to the browser, only whether one is set.
            "value": "" if var[2] else env.get(var[0], var[4] if len(var) > 4 else ""),
            "is_set": var[0] in env,
        }
        for var in connector["vars"]
    ]
    return _page(
        request,
        "source_form.html",
        nav="sources",
        c=connector,
        fields=fields,
        configured=is_connector_configured(connector["key"], env),
        fda=_full_disk_access(env.get("IMESSAGE_DB_PATH") or "~/Library/Messages/chat.db")
        if connector["key"] == "imessage"
        else None,
        readable=_whatsapp_readable(env.get("WHATSAPP_DB_PATH"))
        if connector["key"] == "whatsapp"
        else None,
        keyring=_keyring_name() if connector.get("connect") else None,
        error=error,
    )


def _keyring_name() -> str:
    """What this computer calls the place Signal keeps its key, for the Connect button's page."""
    return "the Keychain" if _on_mac() else "your desktop's keyring"


async def _connect(request: Request, connector: dict[str, Any]) -> Response:
    """Connect: read Signal's key from the keyring now, which is when macOS asks.

    The prompt waits for whoever is at the screen, so the read runs in a thread and this
    request waits with it. Only the key is saved; if anything stops it, the page says why.
    """
    from memoreei.connectors.signal import KeyUnavailable, connect

    try:
        key = await asyncio.to_thread(connect)
    except KeyUnavailable as exc:
        return await source_form(request, error=str(exc))
    updates: list[tuple[str, str]] = [("SIGNAL_DB_KEY", key)]
    if "AUTO_SYNC" not in _env_vars():
        updates.append(("AUTO_SYNC", "true"))
    save_settings(updates)
    await _start_sync()
    return RedirectResponse("/admin/", status_code=303)


async def source_save(request: Request) -> Response:
    connector = _enabled_connector(request)
    if connector is None:
        return _plain(404, "Not available in the dashboard.")
    if connector.get("connect"):
        return await _connect(request, connector)
    form = await request.form()
    updates: list[tuple[str, str]] = []
    for var in connector["vars"]:
        value = str(form.get(var[0], "")).strip()
        if value:
            updates.append((var[0], value))
        elif not var[2]:
            # An empty plain field clears it; an empty secret field keeps the old secret.
            updates.append((var[0], ""))
    env = _env_vars()
    if "AUTO_SYNC" not in env:
        updates.append(("AUTO_SYNC", "true"))
    save_settings(updates)
    # Read it now rather than at the next round, and show that happening.
    await _start_sync()
    return RedirectResponse("/admin/", status_code=303)


async def source_remove(request: Request) -> Response:
    connector = _enabled_connector(request)
    if connector is None:
        return _plain(404, "Not available in the dashboard.")
    save_settings([(var[0], "") for var in connector["vars"]])
    return RedirectResponse("/admin/sources", status_code=303)


async def upload(request: Request) -> Response:
    """Import an uploaded file and register it for `sync`. Upload kinds are listed in
    DASHBOARD_UPLOADS; none are yet, so for now this only ever says no."""
    from memoreei.imports import import_and_register
    from memoreei.server import _get_tools

    kind = request.path_params["kind"]
    if kind not in DASHBOARD_UPLOADS:
        return _plain(404, "Not available in the dashboard.")
    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        return _plain(400, "No file.")
    folder = ensure_home() / "imports" / kind
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / Path(str(file.filename or "upload")).name
    target.write_bytes(await file.read())
    result = await import_and_register(await _get_tools(), kind, str(target))
    return _page(request, "_upload_result.html", result=result, filename=target.name)


async def root_redirect(request: Request) -> Response:
    return RedirectResponse("/admin/", status_code=307)


def build_admin_app(public_origin: str | None = None) -> Any:
    """The dashboard, meant to be mounted at /admin. With ``public_origin``, the public
    listener's copy, answering only to that address (see AdminGuard)."""
    from memoreei.admin.cloud import routes as cloud_routes
    from memoreei.admin.connections import routes as connections_routes
    from memoreei.admin.security import routes as security_routes

    routes = [
        Route("/", status),
        Route("/login", login),
        Route("/signin", signin, methods=["GET", "POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/status", status_fragment),
        Route("/sync", sync_now, methods=["POST"]),
        Route("/keys", key_create, methods=["POST"]),
        Route("/keys/{name}/revoke", key_revoke, methods=["POST"]),
        Route("/sources", sources_page),
        Route("/sources/{key}", source_form),
        Route("/sources/{key}", source_save, methods=["POST"]),
        Route("/sources/{key}/remove", source_remove, methods=["POST"]),
        Route("/upload/{kind}", upload, methods=["POST"]),
        Route("/service/autostart", service_autostart, methods=["POST"]),
        Route("/service/linger", service_linger, methods=["POST"]),
        Route("/service/stop", service_stop, methods=["POST"]),
        Route("/log", log_page),
        *connections_routes(),
        *cloud_routes(),
        *security_routes(),
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    return AdminGuard(Starlette(routes=routes), public_origin=public_origin)
