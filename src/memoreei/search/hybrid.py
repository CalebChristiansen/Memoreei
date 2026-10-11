from __future__ import annotations

import asyncio
from typing import Any

from memoreei.search.embeddings import EmbeddingProvider
from memoreei.search.people import Filter, parse_date
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

RRF_K = 60


def _rrf_score(ranks: list[int]) -> float:
    return sum(1.0 / (RRF_K + r) for r in ranks)


def _parse_date(date_str: str) -> int:
    """Parse ISO date string to unix timestamp (local time when no zone is given)."""
    return parse_date(date_str)


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
        filter: Filter | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search using FTS5 + vector similarity with RRF fusion.

        *filter* narrows both searches before they rank anything. The plain *source*,
        *after* and *before* build one when it isn't given; *participant* is kept for
        callers that pass a name exactly as stored (MemoryTools resolves people properly).
        """
        if filter is None:
            filter = Filter(
                sources={source} if source else None,
                after_ts=_parse_date(after) if after else None,
                before_ts=parse_date(before, end=True) if before else None,
                labels={participant} if participant else None,
            )
        items = await self.search_items(query, limit, filter)
        return [{**item.to_dict(), "score": round(score, 6)} for score, item in items]

    async def search_items(self, query: str, limit: int, filter: Filter,
                           stats: dict | None = None,
                           any_words: bool = False) -> list[tuple[float, MemoryItem]]:
        """The best *limit* memories within *filter*, with their fused scores.

        *stats*, if given, gets ``keyword_hits``: how many messages contain every word.
        *any_words* lets keyword matches with only some of the words fill in (search_fts).
        """
        # Fetch more candidates than needed for better RRF fusion
        candidate_limit = max(limit * 3, 30)
        where = None if filter.empty else filter.sql()
        keep = None if filter.empty else filter.mask

        fts_stats: dict[str, int] = {}
        # FTS runs on the database's thread while the query is embedded on another
        fts_results, query_embedding = await asyncio.gather(
            self.db.search_fts(query, limit=candidate_limit, where=where, any_words=any_words,
                               stats=fts_stats),
            self.embedder.embed_query(query),
        )
        vec_results = await self.db.search_vector(query_embedding, limit=candidate_limit, keep=keep)
        if stats is not None:
            stats["keyword_hits"] = fts_stats.get("all_words", 0)

        fts_ranks = {item.id: rank for rank, item in enumerate(fts_results, 1)}
        vec_ranks = {item.id: rank for rank, item in enumerate(vec_results, 1)}
        all_items = {item.id: item for item in fts_results + vec_results}

        scored = []
        for item_id, item in all_items.items():
            ranks = [r for r in (fts_ranks.get(item_id), vec_ranks.get(item_id)) if r]
            scored.append((_rrf_score(ranks), item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:limit]
