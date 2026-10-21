"""Memoreei Cloud and chatbots, for the Connections page (admin/connections.py):
connect to Memoreei Cloud, disconnect, and approve and remove chatbots.

Connect runs the device-code flow in the server, in the background (it waits for
the person to approve on the service's page, then for a certificate); the page
polls for progress. Nothing here contacts Memoreei Cloud until Connect is pressed.

Chatbots are approved and removed here (memoreei.oauth): a chatbot asking to connect
waits, with a code, until the person answers on the Connections page (or with
`memoreei chatbots approve`). Every other page shows a note while one waits.
"""
from __future__ import annotations

import asyncio
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from memoreei.admin.connections import PATH
from memoreei.config import get_config

# The one enrollment in progress, if any: {"state", "code", "error", "task"}.
_connecting: dict[str, Any] = {}
# What Disconnect said, for the page it redirects to.
_said: dict[str, str] = {}


def _runtime() -> Any:
    from memoreei.public import runtime

    return runtime.RUNTIME


def offer_visible() -> bool:
    """Whether to suggest Memoreei Cloud: at the end of `memoreei setup`, and on the
    Connections page while this computer isn't reachable any other way."""
    from memoreei.cloud.state import CloudState

    cfg = get_config()
    return cfg.cloud_offer and cfg.public_port is None and CloudState().device() is None


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
    from memoreei.admin.connections import connections_page

    return await connections_page(request)


async def cloud_cancel(request: Request) -> Response:
    from starlette.responses import RedirectResponse

    task = _connecting.get("task")
    if task is not None and not task.done():
        task.cancel()
    _connecting.clear()
    return RedirectResponse(PATH, status_code=303)


async def cloud_disconnect(request: Request) -> Response:
    from starlette.responses import RedirectResponse

    from memoreei.cloud.connect import disconnect

    message = await disconnect()
    rt = _runtime()
    if rt is not None:
        rt.poke()
    _said["message"] = message
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"hx-redirect": PATH})
    return RedirectResponse(PATH, status_code=303)


# ── chatbots: approving them, and removing them ─────────────────────────────


async def _store() -> Any:
    from memoreei.oauth.store import OAuthStore
    from memoreei.server import _get_db

    return OAuthStore(await _get_db())


async def chatbots_waiting(request: Request) -> Response:
    """The Connections page's list of requests (polled), or, with ?banner, every other page's note."""
    from memoreei.admin.app import _page

    waiting = await (await _store()).waiting()
    if "banner" in request.query_params:
        return _page(request, "_chatbot_banner.html", waiting=waiting)
    return _page(request, "_chatbot_requests.html", waiting=waiting)


async def chatbot_request(request: Request) -> Response:
    """One waiting request, on a page of its own: where "sign in to approve" on the
    chatbot's waiting page leads, away from the computer."""
    from memoreei.admin.app import _page

    store = await _store()
    row = await store.request(request.path_params["request_id"])
    waiting = row is not None and row["decided_at"] is None and row["expires_at"] >= _now()
    return _page(request, "chatbot_request.html", nav="connections", r=row if waiting else None)


def _now() -> int:
    import time

    return int(time.time())


async def chatbot_decide(request: Request) -> Response:
    import logging

    from starlette.responses import RedirectResponse

    from memoreei.admin.app import _page, where

    answer = request.path_params["answer"]
    if answer not in ("approve", "deny"):
        return Response(status_code=404)
    approve = answer == "approve"
    store = await _store()
    row = await store.request(request.path_params["request_id"])
    decided = row is not None and await store.decide(row["id"], approve)
    if not decided:
        said = "That request had already been answered, or has lapsed."
    else:
        w = where(request)
        logging.getLogger("memoreei.oauth").info(
            "the person %s %s (%s), code %s%s", "approved" if approve else "denied", row["client_name"],
            row["client_host"], row["user_code"], "" if w["local"] else f", signed in from {w['peer']}")
        said = (f"Approved {row['client_name']}. It finishes connecting by itself in a few seconds."
                if approve else f"Denied {row['client_name']}.")
    if "page" in (await request.form()):  # from chatbot_request's page rather than the list
        if decided and row["kind"] == "authorize" and where(request)["public"]:
            # The chatbot's own waiting page, on this same address, now sends it on its way.
            from urllib.parse import urlencode

            return RedirectResponse(f"/authorize/wait?{urlencode({'request': row['id']})}", status_code=303)
        return _page(request, "chatbot_request.html", nav="connections", r=None, said=said)
    return _page(request, "_chatbot_requests.html", waiting=await store.waiting(), said=said)


async def chatbot_remove(request: Request) -> Response:
    import logging

    from memoreei.admin.connections import connections, list_fragment

    store = await _store()
    grant = await store.live_grant(request.path_params["grant_id"])
    if grant is not None and await store.revoke_grant(grant["id"], "removed in the dashboard"):
        logging.getLogger("memoreei.oauth").info("the person removed %s (grant %s)", grant["client_name"], grant["id"])
    return list_fragment(request, await connections())


def routes() -> list:
    from starlette.routing import Route

    return [
        Route("/cloud/progress", cloud_progress),
        Route("/cloud/connect", cloud_connect, methods=["POST"]),
        Route("/cloud/cancel", cloud_cancel, methods=["POST"]),
        Route("/cloud/disconnect", cloud_disconnect, methods=["POST"]),
        Route("/chatbots/waiting", chatbots_waiting),
        Route("/chatbots/requests/{request_id}", chatbot_request),
        Route("/chatbots/requests/{request_id}/{answer:str}", chatbot_decide, methods=["POST"]),
        Route("/chatbots/{grant_id}/remove", chatbot_remove, methods=["POST"]),
    ]
