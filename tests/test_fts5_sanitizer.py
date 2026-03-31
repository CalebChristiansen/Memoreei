"""Tests for FTS5 query sanitizer in Database.search_fts."""

from __future__ import annotations

import time
import uuid

import pytest

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem


def _make_memory(content: str, **kwargs) -> MemoryItem:
    return MemoryItem(
        id=kwargs.get("id", str(uuid.uuid4())),
        source=kwargs.get("source", "test"),
        source_id=kwargs.get("source_id", str(uuid.uuid4())),
        content=content,
        summary=kwargs.get("summary"),
        participants=kwargs.get("participants", ["alice"]),
        ts=kwargs.get("ts", int(time.time())),
        ingested_at=kwargs.get("ingested_at", int(time.time())),
        metadata=kwargs.get("metadata", {}),
    )


@pytest.fixture
async def db(tmp_path):
    database = Database(db_path=str(tmp_path / "test.db"))
    await database.connect()
    # Insert some test messages
    await database.insert_memory(_make_memory("caleb sent a message about the project"))
    await database.insert_memory(_make_memory("hello world from the test suite"))
    await database.insert_memory(_make_memory("what is up yo this is great"))
    await database.insert_memory(_make_memory("testing wildcards and search"))
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_query_with_apostrophe(db):
    results = await db.search_fts("caleb's messages")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_query_with_double_quotes(db):
    results = await db.search_fts('"exact phrase"')
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_query_with_parentheses(db):
    results = await db.search_fts("(hello) world")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_query_with_asterisk(db):
    results = await db.search_fts("test*")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_query_with_curly_braces(db):
    results = await db.search_fts("{test}")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_query_all_special_chars(db):
    results = await db.search_fts("\"''(){}*^~")
    assert isinstance(results, list)
    assert results == []


@pytest.mark.asyncio
async def test_query_mixed_normal_and_special(db):
    results = await db.search_fts("what's up (yo)")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_empty_query_after_sanitization(db):
    results = await db.search_fts("***")
    assert isinstance(results, list)
    assert results == []


@pytest.mark.asyncio
async def test_normal_query(db):
    results = await db.search_fts("hello world")
    assert isinstance(results, list)
    assert len(results) > 0
