"""Pairings and invitations, in the database (peer_links, peer_invites)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import secrets
import time
from typing import Any

from ulid import ULID

from memoreei.peers import PUSH_KEY_PREFIX
from memoreei.storage.database import Database

INVITE_TTL = 600
CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ23456789"  # no vowels: no words, no 0/O or 1/I


class PeerError(Exception):
    """A pairing request refused: *status* is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))


def own_name() -> str:
    """What this memoreei calls itself to another: the computer's name."""
    name = os.environ.get("MEMOREEI_NAME") or platform.node() or "memoreei"
    return name.removesuffix(".local")[:64]


def _row(row: Any) -> dict[str, Any]:
    d = dict(row)
    for key, empty in (("excluded", []), ("refill", {})):
        if key in d:
            try:
                d[key] = json.loads(d[key]) if d[key] else empty
            except ValueError:
                d[key] = empty
    d["active"] = d.get("ended_at") is None
    return d


async def _conn(db: Database) -> Any:
    assert db._db is not None
    return db._db


# ── the receiver's side ────────────────────────────────────────────────────


async def create_invite(db: Database, now: int | None = None) -> tuple[str, str]:
    """A new invitation: (token for the address, code shown on both sides)."""
    conn = await _conn(db)
    now = now or int(time.time())
    token, code = secrets.token_urlsafe(24), _code()
    await conn.execute("DELETE FROM peer_invites WHERE expires_at < ?", (now - 86400,))
    await conn.execute(
        "INSERT INTO peer_invites(token_hash, user_code, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (_hash(token), code, now, now + INVITE_TTL),
    )
    await conn.commit()
    return token, code


async def invite(db: Database, token: str) -> dict[str, Any] | None:
    conn = await _conn(db)
    async with conn.execute("SELECT * FROM peer_invites WHERE token_hash = ?", (_hash(token),)) as cursor:
        row = await cursor.fetchone()
    return dict(row) if row else None


async def open_invites(db: Database) -> list[dict[str, Any]]:
    """Invitations still waiting or claimed but not confirmed, newest first."""
    conn = await _conn(db)
    async with conn.execute(
        "SELECT * FROM peer_invites WHERE expires_at > ? AND link_id IS NULL ORDER BY created_at DESC",
        (int(time.time()),),
    ) as cursor:
        return [dict(r) for r in await cursor.fetchall()]


async def cancel_invites(db: Database) -> None:
    conn = await _conn(db)
    await conn.execute("DELETE FROM peer_invites WHERE link_id IS NULL")
    await conn.commit()


def _usable(row: dict[str, Any] | None, now: int) -> dict[str, Any]:
    if row is None:
        raise PeerError(404, "There is no such invitation. Ask the other memoreei for a new address.")
    if row["link_id"] is not None:
        raise PeerError(410, "This invitation has been used. Ask the other memoreei for a new address.")
    if row["expires_at"] <= now:
        raise PeerError(410, "This invitation has expired. Ask the other memoreei for a new address.")
    return row


async def claim_invite(db: Database, token: str, peer_id: str, name: str) -> dict[str, Any]:
    """A source asks to pair. Answers the code to compare and a claim secret for the confirm.

    One memoreei per invitation: a second claimant is refused, so a leaked address can't
    be raced once the person's own computer has asked.
    """
    if not peer_id or len(peer_id) > 64 or not name or len(name) > 64:
        raise PeerError(400, "memoreei_id and name are required.")
    if peer_id == db.memoreei_id:
        raise PeerError(400, "That's this memoreei's own address.")
    conn = await _conn(db)
    now = int(time.time())
    row = _usable(await invite(db, token), now)
    if row["claimed_by_id"] not in (None, peer_id):
        raise PeerError(409, "Another memoreei has already asked with this invitation.")
    claim = secrets.token_urlsafe(24)
    await conn.execute(
        "UPDATE peer_invites SET claim_hash = ?, claimed_by_id = ?, claimed_by_name = ?, claimed_at = ? "
        "WHERE token_hash = ?",
        (_hash(claim), peer_id, name[:64], now, _hash(token)),
    )
    await conn.commit()
    return {"code": row["user_code"], "claim": claim, "memoreei_id": db.memoreei_id,
            "name": own_name(), "expires_in": row["expires_at"] - now}


async def confirm_invite(db: Database, token: str, claim: str) -> dict[str, Any]:
    """The source's person approved: make the pairing and hand over its push key."""
    conn = await _conn(db)
    now = int(time.time())
    row = _usable(await invite(db, token), now)
    if not row["claim_hash"] or not claim or not hmac.compare_digest(_hash(claim), row["claim_hash"]):
        raise PeerError(403, "This request didn't claim the invitation.")
    key = PUSH_KEY_PREFIX + secrets.token_urlsafe(32)
    peer_id, name = row["claimed_by_id"], row["claimed_by_name"]
    # The same memoreei pairing again keeps its row, so its copies stay counted under it.
    async with conn.execute(
        "SELECT id FROM peer_links WHERE direction = 'from' AND peer_id = ?", (peer_id,)
    ) as cursor:
        existing = await cursor.fetchone()
    if existing:
        link_id = existing[0]
        await conn.execute(
            "UPDATE peer_links SET name = ?, push_key_hash = ?, ended_at = NULL, ended_why = NULL, "
            "last_error = NULL WHERE id = ?",
            (name, _hash(key), link_id),
        )
    else:
        link_id = str(ULID())
        await conn.execute(
            "INSERT INTO peer_links(id, direction, peer_id, name, push_key_hash, created_at) "
            "VALUES (?, 'from', ?, ?, ?, ?)",
            (link_id, peer_id, name, _hash(key), now),
        )
    await conn.execute("UPDATE peer_invites SET link_id = ? WHERE token_hash = ?", (link_id, _hash(token)))
    await conn.commit()
    return {"push_key": key, "link": link_id, "memoreei_id": db.memoreei_id, "name": own_name()}


async def link_for_key(db: Database, key: str) -> dict[str, Any] | None:
    """The live pairing a push key belongs to."""
    if not key.startswith(PUSH_KEY_PREFIX):
        return None
    conn = await _conn(db)
    async with conn.execute(
        "SELECT * FROM peer_links WHERE direction = 'from' AND push_key_hash = ? AND ended_at IS NULL",
        (_hash(key),),
    ) as cursor:
        row = await cursor.fetchone()
    return _row(row) if row else None


# ── the source's side ──────────────────────────────────────────────────────


async def add_receiver(db: Database, peer_id: str, name: str, address: str, key: str) -> str:
    """Remember a receiver this memoreei now pushes to. Everything goes, from the start."""
    conn = await _conn(db)
    now = int(time.time())
    async with conn.execute("SELECT coalesce(max(seq), 0) FROM memory_changes") as cursor:
        (seq,) = await cursor.fetchone()
    async with conn.execute(
        "SELECT id, excluded FROM peer_links WHERE direction = 'to' AND peer_id = ?", (peer_id,)
    ) as cursor:
        existing = await cursor.fetchone()
    if existing:
        link_id = existing[0]
        await conn.execute(
            "UPDATE peer_links SET name = ?, address = ?, push_key = ?, ended_at = NULL, ended_why = NULL, "
            "last_rowid = 0, last_change = ?, refill = '{}', failures = 0, next_try_at = 0, last_error = NULL "
            "WHERE id = ?",
            (name, address, key, seq, link_id),
        )
    else:
        link_id = str(ULID())
        await conn.execute(
            "INSERT INTO peer_links(id, direction, peer_id, name, address, push_key, created_at, last_change) "
            "VALUES (?, 'to', ?, ?, ?, ?, ?, ?)",
            (link_id, peer_id, name, address, key, now, seq),
        )
    await conn.commit()
    return link_id


# ── either side ────────────────────────────────────────────────────────────


async def links(db: Database, direction: str | None = None, active: bool = False) -> list[dict[str, Any]]:
    conn = await _conn(db)
    sql, params = "SELECT * FROM peer_links WHERE 1", []
    if direction:
        sql += " AND direction = ?"
        params.append(direction)
    if active:
        sql += " AND ended_at IS NULL"
    async with conn.execute(sql + " ORDER BY created_at", params) as cursor:
        return [_row(r) for r in await cursor.fetchall()]


async def link(db: Database, link_id: str) -> dict[str, Any] | None:
    conn = await _conn(db)
    async with conn.execute("SELECT * FROM peer_links WHERE id = ?", (link_id,)) as cursor:
        row = await cursor.fetchone()
    return _row(row) if row else None


async def find_link(db: Database, prefix: str) -> list[dict[str, Any]]:
    """Pairings named *prefix*, or whose short id (the last 8 of the id, as listed) it starts."""
    p = prefix.lower()
    return [r for r in await links(db) if r["name"] == prefix or (len(p) >= 4 and short_id(r["id"]).startswith(p))]


def short_id(link_id: str) -> str:
    """A pairing's id as people see it: a ULID's first characters are its time, alike for
    pairings made together, so the end."""
    return link_id[-8:].lower()


async def end_link(db: Database, link_id: str, why: str) -> None:
    """End a pairing. Its row stays, for what it did; a receiver forgets the key's hash."""
    conn = await _conn(db)
    await conn.execute(
        "UPDATE peer_links SET ended_at = ?, ended_why = ?, push_key = NULL, push_key_hash = NULL "
        "WHERE id = ? AND ended_at IS NULL",
        (int(time.time()), why, link_id),
    )
    await conn.commit()
    await trim_changes(db)


async def update_link(db: Database, link_id: str, **fields: Any) -> None:
    conn = await _conn(db)
    for key in ("excluded", "refill"):
        if key in fields:
            fields[key] = json.dumps(fields[key])
    sets = ", ".join(f"{k} = ?" for k in fields)
    await conn.execute(f"UPDATE peer_links SET {sets} WHERE id = ?", (*fields.values(), link_id))
    await conn.commit()


async def trim_changes(db: Database) -> None:
    """Forget changes every live receiver has been sent (all of them, with none)."""
    conn = await _conn(db)
    await conn.execute(
        "DELETE FROM memory_changes WHERE seq <= coalesce("
        "(SELECT min(last_change) FROM peer_links WHERE direction = 'to' AND ended_at IS NULL), "
        "(SELECT max(seq) FROM memory_changes))"
    )
    await conn.commit()


async def copies_by_link(db: Database) -> dict[str, int]:
    """How many messages each pairing's memoreei has pushed here, held now."""
    conn = await _conn(db)
    async with conn.execute(
        "SELECT via, count(*) FROM memories WHERE via IS NOT NULL GROUP BY via"
    ) as cursor:
        return {via: n for via, n in await cursor.fetchall()}


async def names(db: Database) -> dict[str, str]:
    """Each pairing's name, by id: what results say a copy came through."""
    conn = await _conn(db)
    async with conn.execute("SELECT id, name FROM peer_links WHERE direction = 'from'") as cursor:
        return {i: n for i, n in await cursor.fetchall()}


async def remove_source(db: Database, link_id: str, keep_messages: bool = False) -> int:
    """On the receiver: end a pairing and, unless *keep_messages*, delete what it pushed here
    (which then goes as deletes to any memoreei this one sends to). How many were deleted."""
    await end_link(db, link_id, "stopped here, its messages kept" if keep_messages else "removed here")
    if keep_messages:
        return 0
    conn = await _conn(db)
    cursor = await conn.execute("DELETE FROM memories WHERE via = ?", (link_id,))
    await conn.commit()
    if cursor.rowcount:
        db.invalidate_vectors()
    return cursor.rowcount


def invite_addresses(token: str) -> list[tuple[str, str]]:
    """Where a source could reach this memoreei with an invitation: (what it is, address).
    The internet first (Memoreei Cloud's address, or one's own), then the LAN, then this
    computer itself."""
    from urllib.parse import urlsplit

    from memoreei.auth import local_ipv4_addresses
    from memoreei.cloud.state import CloudState
    from memoreei.config import get_config, listening

    cfg = get_config()
    host, port, tls = listening()
    path = f"/peer/pair/{token}"
    out: list[tuple[str, str]] = []
    device = CloudState().device()
    if device is not None:
        out.append(("from anywhere (Memoreei Cloud)", f"https://{device.hostname}{path}"))
    elif cfg.public_mcp_url and cfg.public_port:
        out.append(("from anywhere", f"https://{urlsplit(cfg.public_mcp_url).netloc}{path}"))
    scheme = "https" if tls else "http"
    if host not in ("127.0.0.1", "::1", "localhost"):
        out += [("on your network", f"{scheme}://{ip}:{port}{path}") for ip in local_ipv4_addresses()]
    out.append(("on this computer", f"{scheme}://localhost:{port}{path}"))
    return out
