"""The receiver: taking a push from a paired memoreei."""
from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import numpy as np
from ulid import ULID

from memoreei.peers import store
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

log = logging.getLogger("memoreei.peers")

MAX_ITEMS = 2000
_TEXT_FIELDS = ("origin", "origin_source", "origin_id", "source", "content")


class BadPush(ValueError):
    pass


def model_of(embedder: Any) -> str:
    return getattr(embedder, "MODEL_NAME", type(embedder).__name__)


def decode_embedding(value: Any) -> list[float] | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return np.frombuffer(base64.b64decode(value), dtype=np.float32).tolist()
    except (ValueError, TypeError):
        return None


def _check(upsert: Any) -> dict[str, Any]:
    if not isinstance(upsert, dict):
        raise BadPush("each upsert is an object")
    for key in _TEXT_FIELDS:
        if not isinstance(upsert.get(key), str) or not upsert[key]:
            raise BadPush(f"upsert needs {key}")
    if not isinstance(upsert.get("ts"), int):
        raise BadPush("upsert needs ts")
    if not isinstance(upsert.get("participants", []), list) or not isinstance(upsert.get("metadata", {}), dict):
        raise BadPush("participants is a list and metadata an object")
    return upsert


async def _held(db: Database, keys: list[tuple[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    """The rows held for these origin keys."""
    assert db._db is not None
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for start in range(0, len(keys), 400):
        chunk = keys[start:start + 400]
        where = " OR ".join("(origin = ? AND origin_id = ?)" for _ in chunk)
        async with db._db.execute(
            f"SELECT origin, origin_id, content, summary, participants, ts, metadata FROM memories WHERE {where}",
            [v for k in chunk for v in k],
        ) as cursor:
            for row in await cursor.fetchall():
                found[(row["origin"], row["origin_id"])] = dict(row)
    return found


async def apply_push(db: Database, embedder: Any, link: dict[str, Any], payload: Any) -> dict[str, int]:
    """Take one push. Idempotent: the same push twice changes nothing the second time."""
    if not isinstance(payload, dict):
        raise BadPush("a push is a JSON object")
    upserts = payload.get("upserts") or []
    deletes = payload.get("deletes") or []
    if not isinstance(upserts, list) or not isinstance(deletes, list) or len(upserts) + len(deletes) > MAX_ITEMS:
        raise BadPush(f"upserts and deletes are lists, {MAX_ITEMS} items at most")
    upserts = [_check(u) for u in upserts]
    me = db.memoreei_id
    counts = {"added": 0, "updated": 0, "held": 0, "skipped": 0, "deleted": 0}
    assert db._db is not None

    # Never take back a copy of one of this memoreei's own messages.
    own = [u for u in upserts if u["origin"] == me]
    counts["skipped"] += len(own)
    upserts = [u for u in upserts if u["origin"] != me]

    held = await _held(db, [(u["origin"], u["origin_id"]) for u in upserts])
    fresh: list[dict[str, Any]] = []
    reembedded = False
    for u in upserts:
        row = held.get((u["origin"], u["origin_id"]))
        if row is None:
            fresh.append(u)
            continue
        new = (u["content"], u.get("summary"), json.dumps(u.get("participants") or []), u["ts"],
               json.dumps(u.get("metadata") or {}))
        old = (row["content"], row["summary"], row["participants"], row["ts"], row["metadata"])
        if new == old:
            counts["held"] += 1
            continue
        embedding = None
        if row["content"] != u["content"]:
            embedding = await _embeddings(embedder, payload, [u])
            reembedded = True
        await db._db.execute(
            "UPDATE memories SET content = ?, summary = ?, participants = ?, ts = ?, metadata = ?, "
            "embedding = coalesce(?, embedding) WHERE origin = ? AND origin_id = ? AND origin != ?",
            (*new, (np.array(embedding[0], np.float32).tobytes() if embedding and embedding[0] else None),
             u["origin"], u["origin_id"], me),
        )
        counts["updated"] += 1

    if fresh:
        vectors = await _embeddings(embedder, payload, fresh)
        now = int(time.time())
        for u, vector in zip(fresh, vectors):
            item = MemoryItem(
                id=str(ULID()), source=u["source"], source_id=u.get("source_id"), content=u["content"],
                summary=u.get("summary"), participants=u.get("participants") or [], ts=u["ts"],
                ingested_at=now, metadata=u.get("metadata") or {}, embedding=vector,
                origin=u["origin"], origin_source=u["origin_source"], origin_id=u["origin_id"], via=link["id"],
            )
            if await db.insert_new(item, commit=False):
                counts["added"] += 1
            else:
                counts["held"] += 1  # this memoreei read the same message itself

    for d in deletes:
        if not isinstance(d, dict) or not isinstance(d.get("origin"), str) or not isinstance(d.get("origin_id"), str):
            raise BadPush("each delete has origin and origin_id")
        if d["origin"] == me:
            counts["skipped"] += 1
            continue
        cursor = await db._db.execute(
            "DELETE FROM memories WHERE origin = ? AND origin_id = ? AND origin != ?",
            (d["origin"], d["origin_id"], me),
        )
        counts["deleted"] += cursor.rowcount

    await db._db.execute(
        "UPDATE peer_links SET received = received + ?, last_ok_at = ?, last_try_at = ?, last_error = NULL "
        "WHERE id = ?",
        (counts["added"], int(time.time()), int(time.time()), link["id"]),
    )
    await db._db.commit()
    if counts["deleted"] or reembedded:
        db.invalidate_vectors()
    return counts


async def _embeddings(embedder: Any, payload: dict[str, Any], items: list[dict[str, Any]]) -> list[list[float] | None]:
    """Each item's embedding: the sender's when it used the same model, else made here."""
    same = payload.get("model") == model_of(embedder)
    vectors = [decode_embedding(u.get("embedding")) if same else None for u in items]
    if same:
        dim = getattr(embedder, "dimension", None)  # a property on the real providers
        dim = dim if isinstance(dim, int) else None
        vectors = [v if v is not None and (dim is None or len(v) == dim) else None for v in vectors]
    missing = [n for n, v in enumerate(vectors) if v is None]
    if missing:
        made = await embedder.embed([items[n]["content"] for n in missing])
        for n, v in zip(missing, made):
            vectors[n] = v
    return vectors
