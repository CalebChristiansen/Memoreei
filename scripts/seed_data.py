#!/usr/bin/env python3
"""Seed the Memoreei database with sample WhatsApp chat exports."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv

load_dotenv()

from memoreei.connectors.whatsapp import parse_whatsapp_export
from memoreei.search.embeddings import get_provider
from memoreei.storage.database import Database

SAMPLES_DIR = Path(__file__).parent.parent / "data" / "samples"


async def seed() -> None:
    db_path = os.environ.get("MEMOREEI_DB_PATH", "./memoreei.db")
    print(f"Database: {db_path}")

    embedder = get_provider()
    print(f"Embedding provider: {type(embedder).__name__}")

    async with Database(db_path=db_path) as db:
        sample_files = sorted(SAMPLES_DIR.glob("whatsapp_*.txt"))
        if not sample_files:
            print(f"No sample files found in {SAMPLES_DIR}")
            return

        total_inserted = 0
        for path in sample_files:
            print(f"\nParsing {path.name}...")
            items = parse_whatsapp_export(path)
            print(f"  Parsed {len(items)} messages")

            if not items:
                continue

            # Embed in batches of 50
            batch_size = 50
            all_embeddings = []
            for i in range(0, len(items), batch_size):
                batch = items[i : i + batch_size]
                texts = [item.content for item in batch]
                embeddings = await embedder.embed(texts)
                all_embeddings.extend(embeddings)
                print(f"  Embedded batch {i // batch_size + 1}/{(len(items) + batch_size - 1) // batch_size}")

            for item, emb in zip(items, all_embeddings):
                item.embedding = emb

            inserted = await db.bulk_insert(items)
            print(f"  Inserted {inserted} items (source: {items[0].source})")
            total_inserted += inserted

        print(f"\nDone. Total inserted: {total_inserted}")

        sources = await db.list_sources()
        print("\nSources in database:")
        for source, count in sources.items():
            print(f"  {source}: {count} messages")


if __name__ == "__main__":
    asyncio.run(seed())
