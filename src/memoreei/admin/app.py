"""The dashboard at /admin: status, client keys, and what memoreei reads from.

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
from memoreei.catalog import (
    CONNECTORS,
    DASHBOARD_CONNECTORS,
    DASHBOARD_UPLOADS,
    UPLOADS,
    is_connector_configured,
    kind_name,
    parse_env_vars,
    read_env_lines,
    write_env_updates,
)
from memoreei.config import (
    config_env_path,
    ensure_home,
    get_config,
    legacy_home,
    memoreei_home,
    reload_config,
)

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
    """ASGI middleware enforcing the dashboard's three locks (admin/auth.py)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        host = request.headers.get("host", "")
        client = request.client.host if request.client else None

        if not auth.local_request(client, host):
            log.warning("%s %s from %s refused: not loopback (Host %s)",
                        request.method, scope.get("path", ""), client, host)
            await _plain(403, "The Memoreei dashboard only answers on the computer it runs on.")(
                scope, receive, send
            )
            return
        if request.method not in ("GET", "HEAD") and not auth.same_origin(
            request.headers.get("origin"), host, request.url.scheme,
            request.headers.get("sec-fetch-site"),
        ):
            log.warning("%s %s refused: cross-origin (Origin %s, Sec-Fetch-Site %s)",
                        request.method, scope.get("path", ""), request.headers.get("origin"),
                        request.headers.get("sec-fetch-site"))
            await _plain(403, "Cross-origin request refused.")(scope, receive, send)
            return

        path = scope.get("path", "")
        if not (path.endswith("/admin/login") or "/admin/static/" in path):
            from memoreei.server import _get_db

            if not await auth.session_valid(await _get_db(), request.cookies.get(auth.COOKIE)):
                response = templates.TemplateResponse(
                    request, "signed_out.html", {"version": __version__}, status_code=401
                )
                _secure(response)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _plain(status: int, text: str) -> Response:
    return _secure(PlainTextResponse(text + "\n", status_code=status))


def _secure(response: Response) -> Response:
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


def _page(request: Request, template: str, **context: Any) -> Response:
    context.setdefault("version", __version__)
    context.setdefault("nav", "")
    return _secure(templates.TemplateResponse(request, template, context))


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
templates.env.globals["static"] = _static
templates.env.filters["tilde"] = _tilde
templates.env.filters["linkify"] = _linkify
templates.env.filters["kind_name"] = kind_name
templates.env.filters["connector_key"] = lambda name: _connector_key(name)
# Memoreei.app sets MEMOREEI_APP; the dashboard then points at its menu, not the CLI.
templates.env.globals["in_app"] = lambda: bool(os.environ.get("MEMOREEI_APP"))


def _env_vars() -> dict[str, str]:
    return parse_env_vars(read_env_lines(config_env_path()))


def _on_mac() -> bool:
    return sys.platform == "darwin"


def _offered(key: str) -> bool:
    if key == "imessage":
        return _on_mac()
    if key == "whatsapp":
        # Where WhatsApp for Mac is, or wherever someone already pointed it.
        from memoreei.connectors.whatsapp import mac_app_present

        return (_on_mac() and mac_app_present()) or "WHATSAPP_DB_PATH" in _env_vars()
    return True


def _dashboard_connectors() -> tuple[str, ...]:
    """The connectors the dashboard offers here: iMessage only exists on a Mac, and
    WhatsApp only where WhatsApp for Mac keeps its chats."""
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
    last_run = _sync_manager.last_run
    failed = bool(last_run) and any(
        isinstance(r, dict) for r in last_run["result"].get("connectors", {}).values()
    )
    keys = len(await db.list_api_keys())
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
        "running": _sync_manager.running,
        "last_run": last_run,
        "keys": keys,
        # As the Sources page counts it, so the two never disagree.
        "has_source": has_source,
        # A new install: the two setup steps lead the page until both are done.
        "welcome": not has_source or not keys,
        "problems": failed or fda is False,
    }


def _address() -> str:
    """Where clients reach this server, as the Clients page tells them."""
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


async def login(request: Request) -> Response:
    from memoreei.server import _get_db

    session = await auth.redeem_login_token(await _get_db(), request.query_params.get("token", ""))
    if session is None:
        return _secure(
            templates.TemplateResponse(
                request, "signed_out.html", {"version": __version__, "expired": True}, status_code=401
            )
        )
    response = RedirectResponse("/admin/", status_code=303)
    response.set_cookie(
        auth.COOKIE,
        session,
        max_age=auth.SESSION_TTL,
        path="/admin",
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return _secure(response)


async def logout(request: Request) -> Response:
    from memoreei.server import _get_db

    await auth.end_session(await _get_db(), request.cookies.get(auth.COOKIE))
    response = RedirectResponse("/admin/", status_code=303)
    response.delete_cookie(auth.COOKIE, path="/admin")
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


async def keys_page(request: Request) -> Response:
    from memoreei.server import _get_db

    return _page(request, "keys.html", nav="keys", keys=await (await _get_db()).list_api_keys())


async def key_create(request: Request) -> Response:
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
    if error:
        return _page(request, "_key_created.html", error=error, keys=await db.list_api_keys())
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
        keys=await db.list_api_keys(),
    )


async def key_revoke(request: Request) -> Response:
    from memoreei.server import _get_db

    db = await _get_db()
    await db.revoke_api_key(request.path_params["name"])
    return _page(request, "_key_list.html", keys=await db.list_api_keys())


async def sources_page(request: Request) -> Response:
    return _page(request, "sources.html", nav="sources", accounts=_accounts(), uploads=_uploads())


def _enabled_connector(request: Request) -> dict[str, Any] | None:
    key = request.path_params["key"]
    if key not in _dashboard_connectors():
        return None
    return {"key": key, **CONNECTORS[key]}


async def source_form(request: Request) -> Response:
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
    )


async def source_save(request: Request) -> Response:
    connector = _enabled_connector(request)
    if connector is None:
        return _plain(404, "Not available in the dashboard.")
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
    ensure_home()
    path = config_env_path()
    write_env_updates(path, read_env_lines(path), updates)
    reload_config()
    # Read it now rather than at the next round, and show that happening.
    await _start_sync()
    return RedirectResponse("/admin/", status_code=303)


async def source_remove(request: Request) -> Response:
    connector = _enabled_connector(request)
    if connector is None:
        return _plain(404, "Not available in the dashboard.")
    path = config_env_path()
    write_env_updates(path, read_env_lines(path), [(var[0], "") for var in connector["vars"]])
    reload_config()
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


def build_admin_app() -> Any:
    """The dashboard, meant to be mounted at /admin."""
    routes = [
        Route("/", status),
        Route("/login", login),
        Route("/logout", logout, methods=["POST"]),
        Route("/status", status_fragment),
        Route("/sync", sync_now, methods=["POST"]),
        Route("/keys", keys_page),
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
        Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
    ]
    return AdminGuard(Starlette(routes=routes))
