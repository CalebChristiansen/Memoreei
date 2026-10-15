"""The dashboard's Cloud page: connect to Memoreei Cloud, see it working, disconnect.

Connect runs the device-code flow in the server, in the background (it waits for
the person to approve on the service's page, then for a certificate); the page
polls for progress. Nothing here contacts Memoreei Cloud until Connect is pressed.

If the public listener is set up by hand instead (MEMOREEI_PUBLIC_PORT, for the
person's own tunnel), the page shows that, and its access log, instead.
"""
from __future__ import annotations

import asyncio
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from memoreei.config import get_config

ADVANCED_URL = "https://github.com/CalebChristiansen/Memoreei#your-own-tunnel-and-sign-in"

# The one enrollment in progress, if any: {"state", "code", "error", "task"}.
_connecting: dict[str, Any] = {}
# What Disconnect said, for the page it redirects to.
_said: dict[str, str] = {}


def _runtime() -> Any:
    from memoreei.public import runtime

    return runtime.RUNTIME


def offer_visible() -> bool:
    """The status page's "Use memoreei from Claude.ai or ChatGPT" card."""
    from memoreei.cloud.state import CloudState

    cfg = get_config()
    return cfg.cloud_offer and cfg.public_port is None and not cfg.oauth_issuer and CloudState().device() is None


async def _context() -> dict[str, Any]:
    from memoreei.cloud import certs
    from memoreei.cloud.connect import refuse_reason
    from memoreei.cloud.state import CloudState
    from memoreei.server import _get_db

    state = CloudState()
    rt = _runtime()
    live = rt.status() if rt is not None else {"mode": "off"}
    db = await _get_db()
    device = state.device()
    return {
        "device": device,
        "ready": state.ready(),
        "certificate": certs.describe(state),
        "live": live,
        "tunnel": live.get("tunnel") or {},
        "connecting": _connecting if _connecting.get("state") not in (None, "done") else None,
        "refused": refuse_reason(),
        "custom": live.get("mode") == "custom",
        "clients": await db.list_access_clients() if live.get("mode") != "off" or device else [],
        "calls": await db.list_access(50) if live.get("mode") != "off" or device else [],
        "advanced_url": ADVANCED_URL,
        "cloud_url": get_config().cloud_url,
    }


async def cloud_page(request: Request) -> Response:
    from memoreei.admin.app import _page

    message = _said.pop("message", None)  # once, and never from the URL
    return _page(request, "cloud.html", nav="cloud", message=message, **await _context())


async def cloud_progress(request: Request) -> Response:
    from memoreei.admin.app import _page

    response = _page(request, "_cloud_connecting.html", connecting=_connecting)
    if _connecting.get("state") in ("done", "failed", None):  # the whole page has moved on
        response.headers["hx-refresh"] = "true"
    return response


async def cloud_connect(request: Request) -> Response:
    from memoreei.cloud.connect import connect
    from memoreei.cloud.service import CloudError

    task = _connecting.get("task")
    if task is None or task.done():
        _connecting.clear()
        _connecting["state"] = "starting"
        ready = asyncio.Event()

        def step(what: str, detail: Any) -> None:
            _connecting["state"] = what
            if what == "code":
                _connecting["code"] = detail
                ready.set()

        async def run() -> None:
            try:
                await connect(None, step)
                _connecting["state"] = "done"
                rt = _runtime()
                if rt is not None:
                    rt.poke()
            except CloudError as e:
                _connecting.update(state="failed", error=str(e))
            except Exception as e:  # shown, not swallowed: this is the page the person is looking at
                _connecting.update(state="failed", error=f"Something went wrong: {e!r}")
            finally:
                ready.set()

        _connecting["task"] = asyncio.create_task(run())
        # Wait for the code, so the page can show it straight away.
        try:
            await asyncio.wait_for(ready.wait(), 20)
        except asyncio.TimeoutError:
            pass
    return await cloud_page(request)


async def cloud_cancel(request: Request) -> Response:
    from starlette.responses import RedirectResponse

    task = _connecting.get("task")
    if task is not None and not task.done():
        task.cancel()
    _connecting.clear()
    return RedirectResponse("/admin/cloud", status_code=303)


async def cloud_disconnect(request: Request) -> Response:
    from starlette.responses import RedirectResponse

    from memoreei.cloud.connect import disconnect

    message = await disconnect()
    rt = _runtime()
    if rt is not None:
        rt.poke()
    _said["message"] = message
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"hx-redirect": "/admin/cloud"})
    return RedirectResponse("/admin/cloud", status_code=303)


def routes() -> list:
    from starlette.routing import Route

    return [
        Route("/cloud", cloud_page),
        Route("/cloud/progress", cloud_progress),
        Route("/cloud/connect", cloud_connect, methods=["POST"]),
        Route("/cloud/cancel", cloud_cancel, methods=["POST"]),
        Route("/cloud/disconnect", cloud_disconnect, methods=["POST"]),
    ]
