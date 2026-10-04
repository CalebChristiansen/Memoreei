from __future__ import annotations

import asyncio
import json
import time

import numpy as np
import pytest

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

import tempfile
import os


@pytest.fixture
async def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = Database(db_path=db_path)
    await database.connect()
    yield database
    await database.close()


def make_item(
    source: str = "test",
    source_id: str | None = None,
    content: str = "hello world",
    ts: int | None = None,
    embedding: list[float] | None = None,
) -> MemoryItem:
    from ulid import ULID
    return MemoryItem(
        id=str(ULID()),
        source=source,
        source_id=source_id,
        content=content,
        summary=None,
        participants=["Zezima"],
        ts=ts or int(time.time()),
        ingested_at=int(time.time()),
        metadata={},
        embedding=embedding,
    )


@pytest.mark.asyncio
async def test_insert_and_get(db):
    item = make_item(content="test content for retrieval")
    await db.insert_memory(item)

    result = await db.get_by_id(item.id)
    assert result is not None
    assert result.content == item.content
    assert result.source == item.source


@pytest.mark.asyncio
async def test_dedup(db):
    item = make_item(source="whatsapp", source_id="msg:001", content="original")
    await db.insert_memory(item)

    # Insert duplicate (same source + source_id)
    dup = make_item(source="whatsapp", source_id="msg:001", content="duplicate")
    await db.insert_memory(dup)

    sources = await db.list_sources()
    assert sources.get("whatsapp", 0) == 1


@pytest.mark.asyncio
async def test_fts_search(db):
    items = [
        make_item(content="the printer is sentient and terrifying", source_id="1"),
        make_item(content="pizza toppings debate escalating fast", source_id="2"),
        make_item(content="donut heist planning phase three", source_id="3"),
    ]
    for item in items:
        await db.insert_memory(item)

    results = await db.search_fts("printer sentient")
    assert len(results) >= 1
    assert any("printer" in r.content for r in results)


@pytest.mark.asyncio
async def test_vector_search(db):
    embedding_a = [1.0, 0.0, 0.0] + [0.0] * 381
    embedding_b = [0.0, 1.0, 0.0] + [0.0] * 381
    embedding_query = [0.9, 0.1, 0.0] + [0.0] * 381  # Should match a

    item_a = make_item(content="document alpha", source_id="a", embedding=embedding_a)
    item_b = make_item(content="document beta", source_id="b", embedding=embedding_b)
    await db.insert_memory(item_a)
    await db.insert_memory(item_b)

    results = await db.search_vector(embedding_query, limit=2)
    assert len(results) >= 1
    assert results[0].id == item_a.id


def _unit(i: int, dim: int = 384) -> list[float]:
    v = [0.0] * dim
    v[i] = 1.0
    return v


@pytest.mark.asyncio
async def test_vector_search_ranks_by_similarity_and_keeps_the_limit(db):
    # Three memories at decreasing similarity to the query, plus unrelated ones.
    query = [1.0, 0.5, 0.2] + [0.0] * 381
    near = make_item(content="near", source_id="near", embedding=[1.0, 0.5, 0.2] + [0.0] * 381)
    middle = make_item(content="middle", source_id="middle", embedding=[1.0, 0.0, 0.0] + [0.0] * 381)
    far = make_item(content="far", source_id="far", embedding=_unit(1))
    for item in (far, middle, near):
        await db.insert_memory(item)
    for i in range(5, 25):
        await db.insert_memory(make_item(content=f"other {i}", source_id=f"o{i}", embedding=_unit(i)))

    results = await db.search_vector(query, limit=3)
    assert [r.id for r in results] == [near.id, middle.id, far.id]
    assert results[0].content == "near"  # whole rows come back, not just ids


@pytest.mark.asyncio
async def test_vector_search_reads_json_embeddings_and_skips_other_sizes(db):
    item = make_item(content="stored as json", source_id="j", embedding=_unit(0))
    other = make_item(content="another model", source_id="k", embedding=_unit(0))
    await db.insert_memory(item)
    await db.insert_memory(other)
    assert db._db is not None
    # Older versions stored JSON; a different embedding model has another dimension.
    await db._db.execute("UPDATE memories SET embedding = ? WHERE id = ?",
                         (json.dumps(_unit(0)), item.id))
    await db._db.execute("UPDATE memories SET embedding = ? WHERE id = ?",
                         (np.ones(1536, dtype=np.float32).tobytes(), other.id))
    await db._db.commit()

    results = await db.search_vector(_unit(0), limit=5)
    assert [r.id for r in results] == [item.id]


@pytest.mark.asyncio
async def test_vector_search_filters_by_source(db):
    a = make_item(source="whatsapp", content="a", source_id="a", embedding=_unit(0))
    b = make_item(source="discord", content="b", source_id="b", embedding=_unit(0))
    await db.insert_memory(a)
    await db.insert_memory(b)
    results = await db.search_vector(_unit(0), limit=5, source_filter="discord")
    assert [r.id for r in results] == [b.id]


@pytest.mark.asyncio
async def test_vector_search_sees_inserts_and_deletes_after_the_first_search(db):
    first = make_item(source="a", content="first", source_id="1", embedding=_unit(0))
    await db.insert_memory(first)
    assert [r.id for r in await db.search_vector(_unit(0), limit=5)] == [first.id]

    closer = make_item(source="b", content="closer", source_id="2", embedding=_unit(1))
    await db.insert_memory(closer)
    assert [r.id for r in await db.search_vector(_unit(1), limit=1)] == [closer.id]

    await db.delete_by_source("b")
    assert [r.id for r in await db.search_vector(_unit(1), limit=5)] == [first.id]
    assert list(db._vectors.ids) == [first.id]  # taken out of the index, not rebuilt
    assert not db._rebuilding()


@pytest.mark.asyncio
async def test_vector_search_adds_inserts_to_an_index_of_several_rows(db):
    # one row broadcasts into any number of rows, so it hides a bad copy; three don't
    old = [make_item(content=f"old {i}", source_id=f"old{i}", embedding=_unit(i)) for i in range(3)]
    for item in old:
        await db.insert_memory(item)
    assert [r.id for r in await db.search_vector(_unit(0), limit=1)] == [old[0].id]

    new = [make_item(content=f"new {i}", source_id=f"new{i}", embedding=_unit(i)) for i in range(3, 5)]
    for item in new:
        await db.insert_memory(item)
    for i, item in enumerate(old + new):
        assert [r.id for r in await db.search_vector(_unit(i), limit=1)] == [item.id]
    assert not db._rebuilding()  # added to, not rebuilt


async def _rowid(db: Database, memory_id: str) -> int:
    assert db._db is not None
    async with db._db.execute("SELECT rowid FROM memories WHERE id = ?", (memory_id,)) as cursor:
        (rowid,) = await cursor.fetchone()
    return rowid


@pytest.mark.asyncio
async def test_vector_search_sees_writes_from_another_connection(db):
    keep = make_item(source="a", content="keep", source_id="1", embedding=_unit(0))
    gone = make_item(source="a", content="gone", source_id="2", embedding=_unit(1))
    await db.insert_memory(keep)
    await db.insert_memory(gone)
    await db.search_vector(_unit(0), limit=5)  # builds the in-memory index
    gone_rowid = await _rowid(db, gone.id)

    # A CLI import beside the server deletes the newest row and inserts one, which
    # reuses its rowid: count and max(rowid) come out exactly as they were.
    other = Database(db_path=db.db_path)
    await other.connect()
    try:
        assert other._db is not None
        await other._db.execute("DELETE FROM memories WHERE id = ?", (gone.id,))
        await other._db.commit()
        new = make_item(source="a", content="new", source_id="3", embedding=_unit(2))
        await other.insert_memory(new)
        assert await _rowid(other, new.id) == gone_rowid
    finally:
        await other.close()

    await db.warm_vectors()
    found = {r.content for r in await db.search_vector(_unit(1), limit=5)}
    assert found == {"keep", "new"}


@pytest.mark.asyncio
async def test_vector_search_serves_the_old_index_while_it_rebuilds(db, monkeypatch):
    import threading

    from memoreei.storage import database as database_module

    keep = make_item(source="a", content="keep", source_id="1", embedding=_unit(0))
    await db.insert_memory(keep)
    await db.search_vector(_unit(0), limit=5)

    other = Database(db_path=db.db_path)
    await other.connect()
    try:
        await other.insert_memory(make_item(source="a", content="new", source_id="2", embedding=_unit(1)))
    finally:
        await other.close()

    release = threading.Event()
    real_read = database_module._read_vectors

    def slow_read(*args):
        release.wait(5)
        return real_read(*args)

    monkeypatch.setattr(database_module, "_read_vectors", slow_read)
    # The rebuild is stuck, and the search answers from the old index regardless.
    stale = await asyncio.wait_for(db.search_vector(_unit(1), limit=5), timeout=2)
    assert [r.content for r in stale] == ["keep"]
    assert db._rebuilding()

    release.set()
    await db.warm_vectors()
    assert [r.content for r in await db.search_vector(_unit(1), limit=1)] == ["new"]


@pytest.mark.asyncio
async def test_a_delete_during_a_rebuild_is_not_undone_by_it(db, monkeypatch):
    import threading

    from memoreei.storage import database as database_module

    await db.insert_memory(make_item(source="a", content="a", source_id="1", embedding=_unit(0)))
    await db.insert_memory(make_item(source="b", content="b", source_id="2", embedding=_unit(1)))

    release = threading.Event()
    real_read = database_module._read_vectors
    reads = []

    def slow_read(*args):
        index = real_read(*args)  # read before the delete, returned after it
        if not reads:
            release.wait(5)
        reads.append(1)
        return index

    monkeypatch.setattr(database_module, "_read_vectors", slow_read)
    first = asyncio.ensure_future(db.warm_vectors())
    await asyncio.sleep(0.05)
    await db.delete_by_source("b")
    release.set()
    await first
    assert len(reads) == 2
    assert set(db._vectors.sources) == {"a"}


@pytest.mark.asyncio
async def test_vector_search_uses_the_embedding_size_most_rows_have(db):
    odd = make_item(content="odd one out", source_id="odd", embedding=[1.0] * 8)
    await db.insert_memory(odd)  # first in the table, and the wrong size
    items = [make_item(content=f"m{i}", source_id=f"m{i}", embedding=_unit(i)) for i in range(3)]
    for item in items:
        await db.insert_memory(item)
    assert [r.id for r in await db.search_vector(_unit(2), limit=1)] == [items[2].id]


@pytest.mark.asyncio
async def test_list_sources(db):
    for i in range(3):
        await db.insert_memory(make_item(source="whatsapp", source_id=f"w{i}", content=f"msg {i}"))
    for i in range(2):
        await db.insert_memory(make_item(source="discord", source_id=f"d{i}", content=f"msg {i}"))

    sources = await db.list_sources()
    assert sources["whatsapp"] == 3
    assert sources["discord"] == 2


@pytest.mark.asyncio
async def test_delete_by_source(db):
    for i in range(5):
        await db.insert_memory(make_item(source="temp", source_id=f"t{i}", content=f"temp msg {i}"))

    count = await db.delete_by_source("temp")
    assert count == 5

    sources = await db.list_sources()
    assert "temp" not in sources


@pytest.mark.asyncio
async def test_discord_checkpoint(db):
    await db.set_discord_checkpoint("chan123", "msg456")
    result = await db.get_discord_checkpoint("chan123")
    assert result == "msg456"

    # Update checkpoint
    await db.set_discord_checkpoint("chan123", "msg789")
    result = await db.get_discord_checkpoint("chan123")
    assert result == "msg789"
