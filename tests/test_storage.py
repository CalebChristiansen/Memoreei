from __future__ import annotations

import asyncio
import time

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
        participants=["Alice"],
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
