"""Test historical search against existing memoreei.db."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memoreei.storage.database import Database

DB_PATH = Path(__file__).parent.parent / "memoreei.db"


async def main() -> int:
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        print("FAIL")
        return 1

    async with Database(str(DB_PATH)) as db:
        sources = await db.list_sources()
        print(f"Sources in DB: {sources}")

        results = None
        for query in ("existential", "AI", "discord", "memory"):
            results = await db.search_fts(query, limit=5)
            if results:
                print(f"Query '{query}' returned {len(results)} result(s)")
                break
            print(f"Query '{query}' returned 0 results")

        if not results:
            print("ERROR: No results found for any query")
            print("FAIL")
            return 1

        top = results[0]
        print(f"\nTop result:")
        print(f"  source:      {top.source}")
        print(f"  participant: {top.participants}")
        print(f"  content:     {top.content[:200]!r}")
        print("\nPASS")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
