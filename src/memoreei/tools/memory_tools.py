from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ulid import ULID

from memoreei.connectors.discord_connector import sync_discord
from memoreei.connectors.whatsapp import parse_whatsapp_export
from memoreei.search.embeddings import EmbeddingProvider
from memoreei.search.hybrid import HybridSearch
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem


class MemoryTools:
    def __init__(self, db: Database, embedder: EmbeddingProvider) -> None:
        self.db = db
        self.embedder = embedder
        self.search = HybridSearch(db=db, embedder=embedder)

    async def search_memory(
        self,
        query: str,
        limit: int = 10,
        source: str | None = None,
        participant: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.search.search(
            query=query,
            limit=limit,
            source=source,
            participant=participant,
            after=after,
            before=before,
        )

    async def get_context(
        self, memory_id: str, before: int = 5, after: int = 5
    ) -> list[dict[str, Any]]:
        items = await self.db.get_context(memory_id, before=before, after=after)
        return [item.to_dict() for item in items]

    async def add_memory(
        self,
        content: str,
        source: str = "manual",
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        embedding = await self.embedder.embed_query(content)
        item = MemoryItem(
            id=str(ULID()),
            source=source,
            source_id=None,
            content=content,
            summary=None,
            participants=[],
            ts=int(time.time()),
            ingested_at=int(time.time()),
            metadata=metadata or {},
            embedding=embedding,
        )
        memory_id = await self.db.insert_memory(item)
        return {"id": memory_id, "source": source, "content": content}

    async def list_sources(self) -> dict[str, Any]:
        sources = await self.db.list_sources()
        total = sum(sources.values())
        return {"sources": sources, "total": total}

    async def ingest_whatsapp(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}", "ingested": 0}
        if not path.suffix.lower() == ".txt":
            return {"error": f"Expected a .txt file, got: {path.suffix}", "ingested": 0}

        items = parse_whatsapp_export(path)
        if not items:
            return {"error": "No messages parsed from file", "ingested": 0}

        # Embed in batches
        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        return {
            "ingested": count,
            "file": str(path),
            "source": items[0].source if items else None,
        }

    async def sync_discord_tool(self, channel_id: str | None = None) -> dict[str, Any]:
        return await sync_discord(db=self.db, embedder=self.embedder, channel_id=channel_id)
