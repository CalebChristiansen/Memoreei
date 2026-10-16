"""The dashboard's Security page: the password, 2FA, access from anywhere, sessions.

Changes to the password and 2FA, and switching access from anywhere on, are made on
the computer only. Away from it the page shows how things stand, and offers the two
things that only ever take access away: sign out every other browser, and turn
access from anywhere off. So a stolen session can't change the password, or switch
2FA off, and lock the owner out; and a forgotten password is always reset where
memoreei runs (memoreei.admin.account).
"""
from __future__ import annotations

import logging
from typing import Any

from markupsafe import Markup
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from memoreei.admin import auth, credentials
from memoreei.admin.account import account_store
from memoreei.config import get_config, save_settings

log = logging.getLogger("memoreei.admin")

# What the last change said, for the page it redirects to. Once, and never from the URL.
_said: dict[str, str] = {}


def _where(request: Request) -> dict[str, Any]:
    from memoreei.admin.app import where

    return where(request)


def _on_computer_only() -> Response:
    from memoreei.admin.app import _plain

    return _plain(403, "Change this on the computer memoreei runs on.")


def _done(message: str, request: Request | None = None) -> Response:
    _said["message"] = message
    if request is not None and request.headers.get("hx-request"):  # htmx follows this, not a 303
        return Response(status_code=200, headers={"hx-redirect": "/admin/security"})
    return RedirectResponse("/admin/security", status_code=303)


def addresses() -> list[dict[str, str]]:
    """Where the dashboard answers once it's open from anywhere."""
    from memoreei.auth import server_urls
    from memoreei.public import runtime

    out = []
    rt = runtime.RUNTIME
    live = rt.status() if rt is not None else {}
    if live.get("issuer"):
        what = "Memoreei Cloud" if live.get("mode") == "cloud" else "your own tunnel or proxy"
        out.append({"url": live["issuer"] + "/admin/", "what": f"from anywhere, through {what}"})
    cfg = get_config()
    if cfg.host not in ("127.0.0.1", "localhost", "::1"):
        for url in server_urls(cfg.port, cfg.public_url, tls=bool(cfg.tls_cert)):
            out.append({"url": url.removesuffix("/mcp") + "/admin/",
                        "what": "behind your own proxy" if cfg.public_url else "on this network"})
    return out


async def security_page(request: Request, error: str | None = None) -> Response:
    from memoreei.admin.account import describe_browser
    from memoreei.admin.app import _page

    store = await account_store()
    session = _where(request)["session"]
    sessions = [{**s, "browser": describe_browser(s["user_agent"]),
                 "current": session is not None and s["token_hash"] == session["token_hash"]}
                for s in await store.sessions()]
    return _page(request, "security.html", nav="security", status_code=400 if error else 200, error=error,
                 message=None if error else _said.pop("message", None), account=await store.account(),
                 recovery_left=await store.recovery_codes_left(), sessions=sessions,
                 signins=await store.signins(20), addresses=addresses(), min_password=credentials.MIN_PASSWORD)


async def set_password(request: Request) -> Response:
    if not _where(request)["local"]:
        return _on_computer_only()
    form = await request.form()
    password, confirm = str(form.get("password", "")), str(form.get("confirm", ""))
    problem = credentials.password_problem(password)
    if problem is None and password != confirm:
        problem = "The two passwords don't match."
    if problem:
        return await security_page(request, error=problem)
    store = await account_store()
    had = await store.has_password()
    await store.set_password(await credentials.hash_password_async(password), keep_session=request.cookies.get(auth.COOKIE))
    log.info("dashboard: the password was %s on the computer", "changed" if had else "set")
    return _done("Password changed. Every browser signed in with the old one is signed out."
                 if had else "Password set.")


async def remove_password(request: Request) -> Response:
    if not _where(request)["local"]:
        return _on_computer_only()
    await (await account_store()).reset()
    log.info("dashboard: the password was removed on the computer")
    return _done("Password removed, with 2FA and access from anywhere. The dashboard answers only on this "
                 "computer again.", request)


async def totp_start(request: Request) -> Response:
    if not _where(request)["local"]:
        return _on_computer_only()
    store = await account_store()
    if not await store.has_password():
        return _done("Set a password first: 2FA is the second half of signing in with it.")
    return await _totp_page(request, await store.start_totp())


async def _totp_page(request: Request, secret: str, error: str | None = None) -> Response:
    from memoreei.admin.app import _page

    uri = credentials.totp_uri(secret)
    return _page(request, "security_2fa.html", nav="security", status_code=400 if error else 200,
                 qr=Markup(credentials.qr_svg(uri)), secret=" ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
                 uri=uri, error=error)


async def totp_confirm(request: Request) -> Response:
    from memoreei.admin.app import _page

    if not _where(request)["local"]:
        return _on_computer_only()
    store = await account_store()
    code = str((await request.form()).get("code", ""))
    codes = await store.confirm_totp(code, keep_session=request.cookies.get(auth.COOKIE))
    if codes is None:
        pending = (await store.account())["totp_pending"]
        if not pending:
            return _done("That setup had lapsed. Start again.")
        return await _totp_page(request, pending, "That code didn't match. Check the app shows "
                                                  "Memoreei, and try the newest code.")
    log.info("dashboard: 2FA switched on at the computer")
    return _page(request, "security_codes.html", nav="security", codes=codes, first=True)


async def totp_off(request: Request) -> Response:
    if not _where(request)["local"]:
        return _on_computer_only()
    await (await account_store()).disable_totp()
    log.info("dashboard: 2FA switched off at the computer")
    return _done("2FA is off. Signing in takes the password alone.")


async def recovery_codes(request: Request) -> Response:
    from memoreei.admin.app import _page

    if not _where(request)["local"]:
        return _on_computer_only()
    store = await account_store()
    if not (await store.account())["totp_secret"]:
        return _done("Recovery codes come with 2FA.")
    return _page(request, "security_codes.html", nav="security", codes=await store.new_recovery_codes(), first=False)


async def remote(request: Request) -> Response:
    on = str((await request.form()).get("on", "")) == "1"
    if on and not _where(request)["local"]:
        return _on_computer_only()
    store = await account_store()
    if not await store.set_remote(on):
        return _done("Set a password first: away from this computer, it's what lets you in.")
    if not on:
        await store.end_sessions("password", keep=request.cookies.get(auth.COOKIE) if _where(request)["local"] else None)
    log.info("dashboard: access from anywhere switched %s (%s)", "on" if on else "off",
             "at the computer" if _where(request)["local"] else f"from {_where(request)['peer']}")
    if not on and not _where(request)["local"]:
        return RedirectResponse("/admin/", status_code=303)  # and that was this browser's way in
    return _done("The dashboard now answers away from this computer, to the password." if on
                 else "The dashboard answers only on this computer again.")


async def sign_out_others(request: Request) -> Response:
    n = await (await account_store()).end_sessions(keep=request.cookies.get(auth.COOKIE))
    return _done(f"Signed out {n} other browser{'' if n == 1 else 's'}." if n else "No other browser was signed in.")


async def skip_offer(request: Request) -> Response:
    save_settings([("MEMOREEI_PASSWORD_OFFER", "0")])
    back = request.headers.get("referer", "")
    return RedirectResponse("/admin/cloud" if back.endswith("/admin/cloud") else "/admin/", status_code=303)


def routes() -> list:
    from starlette.routing import Route

    return [
        Route("/security", security_page),
        Route("/security/password", set_password, methods=["POST"]),
        Route("/security/password/remove", remove_password, methods=["POST"]),
        Route("/security/2fa", totp_start, methods=["POST"]),
        Route("/security/2fa/confirm", totp_confirm, methods=["POST"]),
        Route("/security/2fa/off", totp_off, methods=["POST"]),
        Route("/security/recovery", recovery_codes, methods=["POST"]),
        Route("/security/remote", remote, methods=["POST"]),
        Route("/security/sign-out-others", sign_out_others, methods=["POST"]),
        Route("/security/skip-offer", skip_offer, methods=["POST"]),
    ]
