"""The Sources page's other memoreeis (memoreei.peers): pairing in either direction, what
goes, and each pairing listed like any other source (last push, counts, errors).

Pairing from the dashboard is the CLI's, step for step. On the receiver, "Add another
memoreei" shows the addresses and the code and watches the invitation. On the source,
"Send to another memoreei" takes an address, shows the code the receiver answered with,
and sends nothing until the person approves it. The claim waits in this process for
that answer, ten minutes at most.
"""
from __future__ import annotations

import secrets
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

PENDING_TTL = 600
_pending: dict[str, tuple[float, dict[str, Any]]] = {}  # a claim awaiting the person's answer


async def _db() -> Any:
    from memoreei.server import _get_db

    return await _get_db()


async def peers_context() -> dict[str, Any]:
    """What _peers.html shows."""
    from memoreei.peers import store

    db = await _db()
    held = await store.copies_by_link(db)
    rows = await store.links(db)
    platforms = await db.platforms()
    froms = [r for r in rows if r["direction"] == "from"]
    out_rows = []
    for r in rows:
        r = {**r, "short": store.short_id(r["id"]), "held": held.get(r["id"], 0)}
        if r["direction"] == "to":
            # Tick boxes: this memoreei's platforms, then what each other memoreei sent here.
            items = [(p, p.capitalize()) for p in platforms]
            items += [(f"via:{f['id']}", f"From {f['name']}") for f in froms
                      if f["peer_id"] != r["peer_id"] and (f["active"] or held.get(f["id"]))]
            known = {i for i, _ in items}
            items += [(x, x) for x in r["excluded"] if x not in known]
            r["items"] = [{"key": k, "label": label, "on": k not in r["excluded"]} for k, label in items]
            r["catching_up"] = bool(r["refill"].get("send") or r["refill"].get("drop"))
        out_rows.append(r)
    return {
        "sends_to": [r for r in out_rows if r["direction"] == "to" and r["active"]],
        "receives_from": [r for r in out_rows if r["direction"] == "from" and r["active"]],
        "ended": sorted([r for r in out_rows if not r["active"]], key=lambda r: -(r["ended_at"] or 0))[:5],
    }


def _page(request: Request, template: str, **context: Any) -> Response:
    from memoreei.admin.app import _page as page

    return page(request, template, **context)


async def _list(request: Request, **extra: Any) -> Response:
    return _page(request, "_peers.html", **await peers_context(), **extra)


async def invite(request: Request) -> Response:
    from memoreei.peers import store

    token, code = await store.create_invite(await _db())
    return _page(request, "_peer_invite.html", code=code, addresses=store.invite_addresses(token),
                 watch=store._hash(token), state="waiting")


async def invite_state(request: Request) -> Response:
    """The invitation as it stands, polled by the page that made it."""
    db = await _db()
    conn = db._db
    async with conn.execute("SELECT * FROM peer_invites WHERE token_hash = ?",
                            (request.path_params["watch"],)) as cursor:
        row = await cursor.fetchone()
    if row is None:
        state = "gone"
    elif row["link_id"]:
        state = "paired"
    elif row["expires_at"] <= time.time():
        state = "expired"
    else:
        state = "claimed" if row["claimed_by_name"] else "waiting"
    return _page(request, "_peer_invite_state.html", state=state, row=dict(row) if row else None,
                 watch=request.path_params["watch"])


async def add(request: Request) -> Response:
    from memoreei.peers import client

    address = str((await request.form()).get("address", "")).strip()
    try:
        claimed = await client.claim(await _db(), address)
    except client.PairingError as e:
        return _page(request, "_peer_add.html", error=str(e))
    now = time.time()
    for pid in [p for p, (t, _) in _pending.items() if now - t > PENDING_TTL]:
        _pending.pop(pid, None)
    pid = secrets.token_urlsafe(12)
    _pending[pid] = (now, claimed)
    return _page(request, "_peer_add.html", pid=pid, claimed=claimed)


async def answer(request: Request) -> Response:
    from memoreei.peers import client
    from memoreei.peers.push import pusher

    pid = request.path_params["pid"]
    entry = _pending.pop(pid, None)
    if entry is None or time.time() - entry[0] > PENDING_TTL:
        return _page(request, "_peer_add.html", error="That request has gone. Start again with a new address.")
    if request.path_params["answer"] != "approve":
        return _page(request, "_peer_add.html", cancelled=entry[1]["name"])
    try:
        await client.confirm(await _db(), entry[1])
    except client.PairingError as e:
        return _page(request, "_peer_add.html", error=str(e))
    pusher.kick()
    return _page(request, "_peer_add.html", done=entry[1]["name"], refresh=True)


async def what(request: Request) -> Response:
    """The tick boxes: everything not ticked is kept back."""
    from memoreei.peers import push, store

    db = await _db()
    link = await store.link(db, request.path_params["id"])
    if link is None or link["direction"] != "to" or not link["active"]:
        return await _list(request, error="That pairing has ended.")
    form = await request.form()
    offered = set(form.getlist("offered"))
    ticked = set(form.getlist("send"))
    excluded = (set(link["excluded"]) - offered) | (offered - ticked)
    await push.set_excluded(db, link["id"], sorted(excluded))
    push.pusher.kick()
    return await _list(request)


async def end(request: Request) -> Response:
    from memoreei.peers import client, store

    db = await _db()
    link = await store.link(db, request.path_params["id"])
    if link is None or not link["active"]:
        return await _list(request)
    if link["direction"] == "to":
        await client.end(db, link, "ended here")
    else:
        keep = request.path_params.get("how") == "keep"
        await store.remove_source(db, link["id"], keep_messages=keep)
    return await _list(request)


async def listing(request: Request) -> Response:
    return await _list(request)


def routes() -> list:
    from starlette.routing import Route

    return [
        Route("/memoreeis", listing),
        Route("/memoreeis/invite", invite, methods=["POST"]),
        Route("/memoreeis/invite/{watch:str}", invite_state),
        Route("/memoreeis/add", add, methods=["POST"]),
        Route("/memoreeis/add/{pid:str}/{answer:str}", answer, methods=["POST"]),
        Route("/memoreeis/{id:str}/what", what, methods=["POST"]),
        Route("/memoreeis/{id:str}/end", end, methods=["POST"]),
        Route("/memoreeis/{id:str}/end/{how:str}", end, methods=["POST"]),
    ]
