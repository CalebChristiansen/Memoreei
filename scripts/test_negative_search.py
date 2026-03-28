#!/usr/bin/env python3
"""Test that nonsense queries return empty or very low relevance results."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from memoreei.search.embeddings import get_provider
from memoreei.search.hybrid import HybridSearch
from memoreei.storage.database import Database

QUERY = "underwater basket weaving championship 2019"
# RRF scores top out around 1/(60+1) ≈ 0.0164 per list; anything under 0.02
# for a result that appears in only one list is considered low relevance.
MAX_ALLOWED_SCORE = 0.02


async def main() -> None:
    db_path = os.environ.get("MEMOREEI_DB_PATH", "./memoreei.db")
    embedder = get_provider()

    async with Database(db_path=db_path) as db:
        search = HybridSearch(db=db, embedder=embedder)
        results = await search.search(QUERY, limit=5)

    print(f"Query: {QUERY!r}")
    print(f"Results: {len(results)}")

    for r in results:
        print(f"  score={r['score']:.6f}  content={r['content'][:80]!r}")

    # Pass if no results OR all scores are very low (single-list RRF only)
    high_relevance = [r for r in results if r["score"] > MAX_ALLOWED_SCORE]

    if not high_relevance:
        print("PASS: no high-relevance results for nonsense query")
    else:
        print(f"FAIL: {len(high_relevance)} result(s) scored above {MAX_ALLOWED_SCORE}")
        for r in high_relevance:
            print(f"  score={r['score']:.6f}  content={r['content'][:80]!r}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
