from __future__ import annotations

import time

import pytest

from memoreei.search.hybrid import HybridSearch, _rrf_score
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem


class MockEmbedder:
    """Returns simple bag-of-words-style embeddings for testing."""

    dimension = 10

    VOCAB = [
        "printer", "sentient", "office", "pineapple", "pizza",
        "donut", "heist", "discord", "message", "memory"
    ]

    def _embed(self, text: str) -> list[float]:
        lower = text.lower()
        return [1.0 if w in lower else 0.0 for w in self.VOCAB]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def make_item(content: str, source_id: str) -> MemoryItem:
    from ulid import ULID
    embedder = MockEmbedder()
    emb = embedder._embed(content)
    return MemoryItem(
        id=str(ULID()),
        source="test",
        source_id=source_id,
        content=content,
        summary=None,
        participants=["Alice"],
        ts=int(time.time()),
        ingested_at=int(time.time()),
        metadata={},
        embedding=emb,
    )


@pytest.fixture
async def search_setup(tmp_path):
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.connect()

    items = [
        make_item("the office printer is sentient and evil", "1"),
        make_item("pineapple on pizza is controversial", "2"),
        make_item("the donut heist plan is ready", "3"),
        make_item("discord message about the office printer", "4"),
    ]
    for item in items:
        await db.insert_memory(item)

    embedder = MockEmbedder()
    searcher = HybridSearch(db=db, embedder=embedder)
    yield searcher, items
    await db.close()


def test_rrf_score():
    # Higher rank should give lower score
    score_1 = _rrf_score([1])
    score_2 = _rrf_score([2])
    assert score_1 > score_2

    # Two contributing lists beats one
    score_both = _rrf_score([1, 1])
    assert score_both > score_1


@pytest.mark.asyncio
async def test_hybrid_returns_results(search_setup):
    searcher, items = search_setup
    results = await searcher.search("printer sentient office")
    assert len(results) >= 1
    assert any("printer" in r["content"] for r in results)


@pytest.mark.asyncio
async def test_hybrid_source_filter(search_setup):
    searcher, items = search_setup
    results = await searcher.search("printer", source="test")
    # All results should be from "test" source
    assert all(r["source"] == "test" for r in results)


@pytest.mark.asyncio
async def test_rrf_combines_signals(search_setup):
    searcher, items = search_setup
    # "printer" should appear in both FTS and vector results, so it gets boosted
    results = await searcher.search("printer")
    assert len(results) >= 1
    top = results[0]
    assert "printer" in top["content"]


@pytest.mark.asyncio
async def test_date_filter(search_setup):
    searcher, items = search_setup
    # Filter after year 2100 — should return nothing
    results = await searcher.search("printer", after="2100-01-01")
    assert len(results) == 0
