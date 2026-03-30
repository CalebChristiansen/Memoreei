"""FTS search tests against a seeded in-memory DB.

Adapted from scripts/test_historical_search.py — original required a live
populated database; this version seeds its own data so it runs anywhere.
"""
from __future__ import annotations

import time

import pytest
from ulid import ULID

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem


def _make_item(source: str, source_id: str, content: str, participants: list[str] | None = None) -> MemoryItem:
    return MemoryItem(
        id=str(ULID()),
        source=source,
        source_id=source_id,
        content=content,
        summary=None,
        participants=participants or ["Alice"],
        ts=int(time.time()),
        ingested_at=int(time.time()),
        metadata={},
    )


@pytest.fixture
async def seeded_db(tmp_path):
    db = Database(str(tmp_path / "hist.db"))
    await db.connect()

    items = [
        _make_item("discord", "d1", "existential crisis about the nature of memory", ["Alice"]),
        _make_item("whatsapp", "w1", "AI is getting better at understanding context", ["Bob"]),
        _make_item("discord", "d2", "using discord to coordinate the team meeting", ["Charlie"]),
        _make_item("telegram", "t1", "memory palace technique for studying", ["Dave"]),
    ]
    for item in items:
        await db.insert_memory(item)

    yield db
    await db.close()


@pytest.mark.asyncio
async def test_fts_finds_existential(seeded_db):
    results = await seeded_db.search_fts("existential", limit=5)
    assert len(results) >= 1
    assert any("existential" in r.content for r in results)


@pytest.mark.asyncio
async def test_fts_finds_ai(seeded_db):
    results = await seeded_db.search_fts("AI", limit=5)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_fts_finds_discord(seeded_db):
    results = await seeded_db.search_fts("discord", limit=5)
    assert len(results) >= 1
    assert any("discord" in r.content for r in results)


@pytest.mark.asyncio
async def test_fts_finds_memory(seeded_db):
    results = await seeded_db.search_fts("memory", limit=5)
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_fts_result_has_required_fields(seeded_db):
    results = await seeded_db.search_fts("discord", limit=5)
    assert results
    top = results[0]
    assert top.source
    assert top.content
    assert top.participants is not None


@pytest.mark.asyncio
async def test_list_sources_contains_seeded(seeded_db):
    sources = await seeded_db.list_sources()
    assert "discord" in sources
    assert "whatsapp" in sources
    assert sources["discord"] == 2
    assert sources["whatsapp"] == 1
