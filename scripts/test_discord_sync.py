#!/usr/bin/env python3
"""Test Discord sync: remove DB, sync channel, verify count, run hybrid search."""

import asyncio
import os
import sys
from pathlib import Path

# Load .env before anything else
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from memoreei.storage.database import Database
from memoreei.search.embeddings import get_provider
from memoreei.connectors.discord_connector import DiscordConnector
from memoreei.search.hybrid import HybridSearch

CHANNEL_ID = "REDACTED_CHANNEL_ID"
DB_PATH = os.environ.get("MEMOREEI_DB_PATH", "./memoreei.db")


async def main() -> bool:
    # 1. Remove existing DB
    db_file = Path(DB_PATH)
    if db_file.exists():
        db_file.unlink()
        print(f"Removed existing {db_file}")

    # 2. Create Database, connect, create embedder
    db = Database(DB_PATH)
    await db.connect()

    embedder = get_provider()

    # 3. Create DiscordConnector and sync
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        print("FAIL: DISCORD_BOT_TOKEN not set")
        await db.close()
        return False

    connector = DiscordConnector(token=token, db=db, embedder=embedder)
    print(f"Syncing channel {CHANNEL_ID}...")
    count = await connector.sync_channel(CHANNEL_ID)

    # 4. Print count
    print(f"Messages synced: {count}")

    # 5. Verify count > 20
    if count <= 20:
        print(f"FAIL: Expected > 20 messages, got {count}")
        await db.close()
        return False

    # 6. Run hybrid search
    searcher = HybridSearch(db=db, embedder=embedder)
    results = await searcher.search("hello conversation", limit=3)

    # 7. Print top 3 results
    print(f"\nTop {len(results)} search results:")
    for i, r in enumerate(results[:3], 1):
        content = r.get("content", "")[:100]
        score = r.get("score", 0)
        print(f"  {i}. [{score:.4f}] {content}")

    await db.close()
    return True


if __name__ == "__main__":
    try:
        ok = asyncio.run(main())
        if ok:
            print("\nPASS")
            sys.exit(0)
        else:
            print("\nFAIL")
            sys.exit(1)
    except Exception as e:
        print(f"FAIL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
