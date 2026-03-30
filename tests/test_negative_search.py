"""Test that nonsense queries return no or very low-relevance results.

Adapted from scripts/test_negative_search.py — original used the live
fastembed provider; this version uses FTS only with a mock embedder so it
runs without any ML dependencies.

RRF scores top out around 1/(60+1) ≈ 0.0164 per contributing list. A result
appearing in only one list with rank=1 scores ~0.0164; anything above 0.02
means it showed up highly in both FTS and vector lists, which shouldn't happen
for a nonsense query against unrelated data.
"""
from __future__ import annotations

import time

import pytest
from ulid import ULID

from memoreei.search.hybrid import HybridSearch
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

NONSENSE_QUERY = "underwater basket weaving championship 2019"
MAX_ALLOWED_SCORE = 0.02

VOCAB = ["printer", "sentient", "office", "pineapple", "pizza", "donut", "heist", "discord", "message", "memory"]


class BagOfWordsEmbedder:
    """Bag-of-words embedder over a fixed vocabulary — no ML required."""

    dim = len(VOCAB)

    def _embed(self, text: str) -> list[float]:
        lower = text.lower()
        return [1.0 if w in lower else 0.0 for w in VOCAB]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


def _make_item(source_id: str, content: str) -> MemoryItem:
    emb = BagOfWordsEmbedder()._embed(content)
    return MemoryItem(
        id=str(ULID()),
        source="test",
        source_id=source_id,
        content=content,
        summary=None,
        participants=["Tester"],
        ts=int(time.time()),
        ingested_at=int(time.time()),
        metadata={},
        embedding=emb,
    )


@pytest.fixture
async def negative_db(tmp_path):
    db = Database(str(tmp_path / "neg.db"))
    await db.connect()

    for i, content in enumerate([
        "the office printer is sentient and evil",
        "pineapple on pizza is controversial",
        "the donut heist plan is ready",
        "discord message about the office printer",
        "memory palace technique for studying",
    ]):
        await db.insert_memory(_make_item(str(i), content))

    yield db
    await db.close()


@pytest.mark.asyncio
async def test_nonsense_fts_returns_nothing(negative_db):
    """FTS for a nonsense query returns no results against unrelated data."""
    results = await negative_db.search_fts(NONSENSE_QUERY, limit=5)
    assert len(results) == 0


@pytest.mark.asyncio
async def test_nonsense_hybrid_low_relevance(negative_db):
    """Hybrid search scores for a nonsense query stay below the RRF threshold."""
    embedder = BagOfWordsEmbedder()
    searcher = HybridSearch(db=negative_db, embedder=embedder)
    results = await searcher.search(NONSENSE_QUERY, limit=5)

    high_relevance = [r for r in results if r["score"] > MAX_ALLOWED_SCORE]
    assert high_relevance == [], (
        f"Expected no high-relevance results for nonsense query, "
        f"got {len(high_relevance)}: {high_relevance}"
    )


@pytest.mark.asyncio
async def test_known_query_returns_results(negative_db):
    """Sanity check: a known-good query does return relevant results."""
    results = await negative_db.search_fts("printer sentient", limit=5)
    assert len(results) >= 1
    assert any("printer" in r.content for r in results)
