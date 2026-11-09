"""The source: pushing this memoreei's messages to the memoreeis it's paired with.

Each pairing keeps where it got to: the last memories rowid sent (new messages) and the
last memory_changes seq (deletes and edits). A push round sends, in this order, what the
person unticked as deletes, the changes, what they ticked again, and the new messages,
in pages, moving the checkpoint after each page the receiver took. A failure leaves the
checkpoint where it was and backs off; the next round carries on from there, so a laptop
that slept catches up when it wakes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Awaitable, Callable

import httpx

from memoreei.peers import store
from memoreei.peers.receive import model_of
from memoreei.storage.database import Database

log = logging.getLogger("memoreei.peers")

PAGE_ROWS = 300
PAGE_BYTES = 700_000  # under the public listener's limit on a push, with room for JSON
TICK = 60
BACKOFF = (30, 3600)


def item_of(source: str, via: str | None) -> str:
    """What a tick box on the sending side stands for: a platform of this memoreei's own
    ('signal'), or everything that came through one pairing ('via:<id>')."""
    return f"via:{via}" if via else source.partition(":")[0]


def _upsert(row: Any, me: str) -> dict[str, Any]:
    emb = row["embedding"]
    if isinstance(emb, str):  # older JSON storage
        import numpy as np
        emb = np.asarray(json.loads(emb), dtype=np.float32).tobytes()
    return {
        # A row an older memoreei wrote after the migration has no stamp: it is this one's own.
        "origin": row["origin"] or me, "origin_source": row["origin_source"] or row["source"],
        "origin_id": row["origin_id"] or row["id"],
        "source": row["source"], "source_id": row["source_id"], "content": row["content"],
        "summary": row["summary"], "participants": json.loads(row["participants"] or "[]"),
        "ts": row["ts"], "metadata": json.loads(row["metadata"] or "{}"),
        "embedding": base64.b64encode(emb).decode() if emb else None,
    }


def _size(obj: dict[str, Any]) -> int:
    return len(obj.get("content") or "") * 2 + len(obj.get("embedding") or "") + 400


class Rejected(Exception):
    """The receiver no longer takes this pairing's key."""


Send = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


async def http_send(link: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(link["address"].rstrip("/") + "/peer/push", json=payload,
                               headers={"authorization": f"Bearer {link['push_key']}"})
    if r.status_code in (401, 403, 410):
        raise Rejected(_error(r))
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code}: {_error(r)}")
    return r.json()


def _error(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error_description") or r.json().get("error"))
    except ValueError:
        return r.text[:200] or r.reason_phrase


class _Pages:
    """Collects upserts and deletes into pushes of a sensible size."""

    def __init__(self, send: Callable[[dict[str, Any]], Awaitable[None]], base: dict[str, Any]) -> None:
        self.send, self.base = send, base
        self.upserts: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        self.bytes = 0

    async def add(self, upsert: dict[str, Any] | None = None, delete: dict[str, Any] | None = None) -> bool:
        """Add one; True when that filled a page and it was sent."""
        if upsert is not None:
            self.upserts.append(upsert)
            self.bytes += _size(upsert)
        if delete is not None:
            self.deletes.append(delete)
            self.bytes += 120
        if self.bytes >= PAGE_BYTES or len(self.upserts) + len(self.deletes) >= PAGE_ROWS:
            await self.flush()
            return True
        return False

    async def flush(self) -> None:
        if self.upserts or self.deletes:
            await self.send({**self.base, "upserts": self.upserts, "deletes": self.deletes})
        self.upserts, self.deletes, self.bytes = [], [], 0


async def push_link(db: Database, embedder: Any, link: dict[str, Any], send: Send = http_send) -> dict[str, int]:
    """One round for one pairing: everything waiting, page by page. Raises on failure,
    leaving the checkpoint at the last page the receiver took."""
    assert db._db is not None
    conn = db._db
    excluded = set(link["excluded"])
    refill = {"send": dict(link["refill"].get("send", {})), "drop": dict(link["refill"].get("drop", {}))}
    sent = {"sent": 0, "deleted": 0}
    base = {"from": db.memoreei_id, "model": model_of(embedder)}
    pending: dict[str, Any] = {}  # checkpoint fields the page being filled will move

    async def deliver(payload: dict[str, Any]) -> None:
        await send(link, payload)
        sent["sent"] += len(payload["upserts"])
        sent["deleted"] += len(payload["deletes"])
        fields = dict(pending)
        fields["refill"] = refill
        fields["sent"] = link["sent"] + sent["sent"]
        await store.update_link(db, link["id"], **fields)

    pages = _Pages(deliver, base)

    def wanted(source: str, via: str | None, origin: str | None) -> bool:
        return origin != link["peer_id"] and item_of(source, via) not in excluded

    # 1. What was unticked: a delete for each copy the receiver was sent.
    for item, (after, upto) in list(refill["drop"].items()):
        while True:
            async with conn.execute(
                "SELECT rowid, origin, origin_id, source, via FROM memories WHERE rowid > ? AND rowid <= ? "
                "ORDER BY rowid LIMIT 1000", (after, upto)) as cursor:
                rows = await cursor.fetchall()
            if not rows:
                break
            for row in rows:
                after = row["rowid"]
                if item_of(row["source"], row["via"]) == item and row["origin"] != link["peer_id"]:
                    refill["drop"][item] = [after, upto]
                    await pages.add(delete={"origin": row["origin"], "origin_id": row["origin_id"]})
            refill["drop"][item] = [after, upto]
        await pages.flush()  # sent before the item is forgotten, or a failure would lose them
        refill["drop"].pop(item, None)

    # 2. Deletes and edits since the last round.
    last_change = link["last_change"]
    while True:
        async with conn.execute(
            "SELECT * FROM memory_changes WHERE seq > ? ORDER BY seq LIMIT 500", (last_change,)) as cursor:
            changes = await cursor.fetchall()
        if not changes:
            break
        for ch in changes:
            last_change = ch["seq"]
            pending["last_change"] = last_change
            if ch["origin"] == link["peer_id"]:
                continue
            if ch["kind"] == "delete":
                await pages.add(delete={"origin": ch["origin"], "origin_id": ch["origin_id"]})
                continue
            async with conn.execute(
                "SELECT rowid, * FROM memories WHERE origin = ? AND origin_id = ?",
                (ch["origin"], ch["origin_id"])) as cursor:
                row = await cursor.fetchone()
            # An edit to a message not sent yet goes with the new ones.
            if row and row["rowid"] <= link["last_rowid"] and wanted(row["source"], row["via"], row["origin"]):
                await pages.add(upsert=_upsert(row, db.memoreei_id))
    pending["last_change"] = last_change

    # 3. What was ticked again: everything of it up to where it was unticked.
    for item, (after, upto) in list(refill["send"].items()):
        while True:
            async with conn.execute(
                "SELECT rowid, * FROM memories WHERE rowid > ? AND rowid <= ? ORDER BY rowid LIMIT ?",
                (after, upto, PAGE_ROWS)) as cursor:
                rows = await cursor.fetchall()
            if not rows:
                break
            for row in rows:
                after = row["rowid"]
                refill["send"][item] = [after, upto]
                if item_of(row["source"], row["via"]) == item and wanted(row["source"], row["via"], row["origin"]):
                    await pages.add(upsert=_upsert(row, db.memoreei_id))
        await pages.flush()
        refill["send"].pop(item, None)

    # 4. New messages.
    last_rowid = link["last_rowid"]
    while True:
        async with conn.execute(
            "SELECT rowid, * FROM memories WHERE rowid > ? ORDER BY rowid LIMIT ?", (last_rowid, PAGE_ROWS)
        ) as cursor:
            rows = await cursor.fetchall()
        if not rows:
            break
        for row in rows:
            last_rowid = row["rowid"]
            pending["last_rowid"] = last_rowid
            if wanted(row["source"], row["via"], row["origin"]):
                await pages.add(upsert=_upsert(row, db.memoreei_id))
    pending["last_rowid"] = last_rowid
    await pages.flush()
    # Nothing was left to send, but the checkpoint may still have moved past skipped rows.
    await store.update_link(db, link["id"], **pending, refill=refill, sent=link["sent"] + sent["sent"])
    return sent


async def set_excluded(db: Database, link_id: str, excluded: list[str]) -> None:
    """Change what goes to a receiver. Unticked items are deleted there; ticked-again ones
    are sent again from the start, up to where new messages have got to."""
    link = await store.link(db, link_id)
    if link is None or link["direction"] != "to":
        raise KeyError(link_id)
    before, after = set(link["excluded"]), set(excluded)
    refill = {"send": dict(link["refill"].get("send", {})), "drop": dict(link["refill"].get("drop", {}))}
    for item in after - before:
        refill["send"].pop(item, None)
        refill["drop"][item] = [0, link["last_rowid"]]
    for item in before - after:
        refill["drop"].pop(item, None)
        refill["send"][item] = [0, link["last_rowid"]]
    await store.update_link(db, link_id, excluded=sorted(after), refill=refill)


async def push_all(db: Database, embedder: Any, send: Send = http_send, force: bool = False) -> dict[str, Any]:
    """A round for every live receiver due one. Failures back off; a refused key ends the pairing."""
    out: dict[str, Any] = {}
    now = int(time.time())
    for link in await store.links(db, "to", active=True):
        if not force and link["next_try_at"] > now:
            continue
        try:
            out[link["id"]] = await push_link(db, embedder, link, send)
        except Rejected as e:
            log.warning("push to %s refused: %s; the pairing has ended", link["name"], e)
            await store.end_link(db, link["id"], f"{link['name']} no longer takes pushes from here ({e})")
            out[link["id"]] = {"error": str(e), "ended": True}
            continue
        except Exception as e:  # noqa: BLE001 - any failure means "try again later"
            failures = link["failures"] + 1
            wait = min(BACKOFF[0] * 2 ** (failures - 1), BACKOFF[1])
            log.warning("push to %s failed (%s); next try in %ds", link["name"], e, wait)
            await store.update_link(db, link["id"], failures=failures, last_try_at=now,
                                    last_error=str(e)[:300] or type(e).__name__, next_try_at=now + wait)
            out[link["id"]] = {"error": str(e)}
            continue
        await store.update_link(db, link["id"], failures=0, last_try_at=now, last_ok_at=int(time.time()),
                                last_error=None, next_try_at=0)
    await store.trim_changes(db)
    return out


class Pusher:
    """Pushes in the background for as long as the server runs: after every sync, after a
    push arrives (for chains), and once a minute anyway, which is what catches up after sleep."""

    def __init__(self) -> None:
        self._kick: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None

    def kick(self) -> None:
        if self._kick is not None:
            self._kick.set()

    async def round(self, db: Database, embedder: Any, force: bool = False) -> dict[str, Any]:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            return await push_all(db, embedder, force=force)

    async def run(self, get_tools: Callable[[], Awaitable[Any]]) -> None:
        self._kick = asyncio.Event()
        while True:
            try:
                await asyncio.wait_for(self._kick.wait(), TICK)
            except asyncio.TimeoutError:
                pass
            self._kick.clear()
            try:
                tools = await get_tools()
                if await store.links(tools.db, "to", active=True):
                    await self.round(tools.db, tools.embedder)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.warning("push round failed: %r", e)


pusher = Pusher()
