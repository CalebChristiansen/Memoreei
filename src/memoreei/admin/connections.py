"""The dashboard's Connections page: everything that can read your messages, and how
this computer is reached.

From the top: how chatbots reach this computer (Memoreei Cloud, the person's own
tunnel, or the offer to connect); chatbots waiting for an answer; one table of every
chatbot and key, each removable; a form for a new key; the recent calls through the
public listener. The old Clients and Cloud pages redirect here.

The actions stay where they were: keys in admin/app.py, Memoreei Cloud and chatbots in
admin/cloud.py. Each of them answers with the table, or comes back to this page.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from memoreei.admin.account import account_store
from memoreei.config import get_config

PATH = "/admin/connections"

# Where memoreei says how to do without us. Every install keeps the link it shipped
# with, so it's ours to point wherever the docs live (a forward to the README for now).
SELF_HOST_URL = "https://docs.memoreei.com/self-host"

# Memoreei Cloud's price, shown beside Connect, once there's billing to charge it.
CLOUD_PRICE: str | None = None


async def connections() -> list[dict[str, Any]]:
    """Every live chatbot and key, as one list, the most recently used first."""
    from memoreei.admin.cloud import _store
    from memoreei.server import _get_db

    rows = [{
        "kind": "chatbot",
        "name": g["client_name"],
        "detail": (f"it named itself; {g['client_host']}" if g["client_host"] else "it named itself")
        if g["self_registered"] else g["client_host"],
        "how": "Signed in",
        "created_at": g["created_at"],
        "last_used_at": g["last_used_at"],
        "remove": f"/admin/chatbots/{quote(g['id'], safe='')}/remove",
        "confirm": f"Remove {g['client_name']}? It's cut off at once, and would have to ask again.",
    } for g in await (await _store()).grants()]
    rows += [{
        "kind": "key",
        "name": k["name"],
        "detail": None,
        "how": "Key",
        "created_at": k["created_at"],
        "last_used_at": k["last_used_at"],
        "remove": f"/admin/keys/{quote(k['name'], safe='')}/revoke",
        "confirm": f"Remove “{k['name']}”? Anything using its key loses access at once.",
    } for k in await (await _get_db()).list_api_keys()]
    return sorted(rows, key=lambda r: -(r["last_used_at"] or r["created_at"] or 0))


def list_fragment(request: Request, rows: list[dict[str, Any]], oob: bool = False) -> Response:
    """The table on its own, for the buttons that change it."""
    from memoreei.admin.app import _page

    return _page(request, "_connection_list.html", connections=rows, reachable=_reachable(), oob=oob)


def _reachable() -> bool:
    """Whether anything outside this computer can reach memoreei's sign-in."""
    from memoreei.admin.cloud import _runtime
    from memoreei.cloud.state import CloudState

    rt = _runtime()
    return (rt is not None and rt.status().get("mode") != "off") or CloudState().device() is not None


async def _context() -> dict[str, Any]:
    from memoreei.admin.cloud import _connecting, _runtime, _store
    from memoreei.cloud import certs
    from memoreei.cloud.state import CloudState
    from memoreei.server import _get_db

    state = CloudState()
    rt = _runtime()
    live = rt.status() if rt is not None else {"mode": "off"}
    device = state.device()
    shown = live.get("mode") != "off" or device is not None
    account = await (await account_store()).account()
    cfg = get_config()
    return {
        "device": device,
        "ready": state.ready(),
        "certificate": certs.describe(state),
        "live": live,
        "tunnel": live.get("tunnel") or {},
        "connecting": _connecting if _connecting.get("state") not in (None, "done") else None,
        "custom": live.get("mode") == "custom",
        "offer": cfg.cloud_offer,
        "waiting": await (await _store()).waiting(),
        "connections": await connections(),
        "reachable": shown,
        "calls": await (await _get_db()).list_access(50) if shown else [],
        "self_host_url": SELF_HOST_URL,
        "price": CLOUD_PRICE,
        "account": account,
        # Connected, and no password yet: suggest one, for the dashboard from anywhere.
        "password_offer": shown and cfg.password_offer and not account["password_hash"],
    }


async def connections_page(request: Request) -> Response:
    from memoreei.admin.app import _page
    from memoreei.admin.cloud import _said

    message = _said.pop("message", None)  # once, and never from the URL
    return _page(request, "connections.html", nav="connections", message=message, **await _context())


async def moved(request: Request) -> Response:
    """The old Clients and Cloud pages, and the Mac app's menu item: all here now."""
    return RedirectResponse(PATH, status_code=303)


def routes() -> list:
    from starlette.routing import Route

    return [
        Route("/connections", connections_page),
        Route("/keys", moved),
        Route("/cloud", moved),
    ]
