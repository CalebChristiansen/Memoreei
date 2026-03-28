from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from memoreei.search.embeddings import EmbeddingProvider
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

RRF_K = 60


def _rrf_score(ranks: list[int]) -> float:
    return sum(1.0 / (RRF_K + r) for r in ranks)


def _parse_date(date_str: str) -> int:
    """Parse ISO date string to unix timestamp."""
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str!r}. Use ISO 8601 (e.g. '2026-03-15' or '2026-03-15T09:00:00')")


class HybridSearch:
    def __init__(self, db: Database, embedder: EmbeddingProvider) -> None:
        self.db = db
        self.embedder = embedder

    async def search(
        self,
        query: str,
        limit: int = 10,
        source: str | None = None,
        participant: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search using FTS5 + vector similarity with RRF fusion."""
        after_ts = _parse_date(after) if after else None
        before_ts = _parse_date(before) if before else None

        # Fetch more candidates than needed for better RRF fusion
        candidate_limit = max(limit * 3, 30)

        # Run FTS and vector search in parallel (conceptually; we await them)
        fts_results = await self.db.search_fts(query, limit=candidate_limit)
        query_embedding = await self.embedder.embed_query(query)
        vec_results = await self.db.search_vector(
            query_embedding, limit=candidate_limit, source_filter=source
        )

        # Build rank maps
        fts_ranks: dict[str, int] = {item.id: rank for rank, item in enumerate(fts_results, 1)}
        vec_ranks: dict[str, int] = {item.id: rank for rank, item in enumerate(vec_results, 1)}

        # Collect all unique candidate IDs
        all_ids = set(fts_ranks.keys()) | set(vec_ranks.keys())

        # Index all items by ID
        all_items: dict[str, MemoryItem] = {}
        for item in fts_results + vec_results:
            all_items[item.id] = item

        # Score with RRF
        scored: list[tuple[float, MemoryItem]] = []
        for item_id in all_ids:
            item = all_items[item_id]

            # Apply filters
            if source and item.source != source:
                continue
            if participant and participant.lower() not in [p.lower() for p in item.participants]:
                continue
            if after_ts and item.ts < after_ts:
                continue
            if before_ts and item.ts > before_ts:
                continue

            ranks = []
            if item_id in fts_ranks:
                ranks.append(fts_ranks[item_id])
            if item_id in vec_ranks:
                ranks.append(vec_ranks[item_id])

            score = _rrf_score(ranks)
            scored.append((score, item))

        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {**item.to_dict(), "score": round(score, 6)}
            for score, item in scored[:limit]
        ]
