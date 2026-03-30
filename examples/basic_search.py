#!/usr/bin/env python3
"""Example: search Memoreei memory database directly (without MCP)."""
import asyncio
from memoreei.storage.database import Database
from memoreei.search.embeddings import get_provider
from memoreei.search.hybrid import HybridSearch

async def main():
    db = Database(db_path='./memoreei.db')
    await db.connect()
    embedder = get_provider()
    search = HybridSearch(db=db, embedder=embedder)
    results = await search.search('what did we talk about?', limit=5)
    for r in results:
        print(f"[{r.get('source', '?')}] {r.get('content', '')[:100]}")

if __name__ == '__main__':
    asyncio.run(main())
