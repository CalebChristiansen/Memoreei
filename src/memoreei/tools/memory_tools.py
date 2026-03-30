from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from ulid import ULID

from memoreei.connectors.discord_connector import sync_discord
from memoreei.connectors.generic_connector import import_json, import_csv
from memoreei.connectors.discord_package_connector import import_discord_package as _import_discord_package
from memoreei.connectors.imessage_connector import sync_imessage
from memoreei.connectors.signal_connector import sync_signal
from memoreei.connectors.email_connector import sync_email
from memoreei.connectors.mastodon_connector import sync_mastodon
from memoreei.connectors.matrix_connector import sync_matrix
from memoreei.connectors.slack_connector import sync_slack
from memoreei.connectors.telegram_connector import sync_telegram
from memoreei.connectors.instagram_connector import parse_instagram_export
from memoreei.connectors.messenger_connector import parse_messenger_export
from memoreei.connectors.sms_connector import parse_sms_backup
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

    async def sync_telegram_tool(self, chat_id: str | None = None) -> dict[str, Any]:
        return await sync_telegram(db=self.db, embedder=self.embedder, chat_id=chat_id)

    async def sync_matrix_tool(self, room_id: str | None = None) -> dict[str, Any]:
        return await sync_matrix(db=self.db, embedder=self.embedder, room_id=room_id)

    async def sync_slack_tool(self, channel_id: str | None = None) -> dict[str, Any]:
        return await sync_slack(db=self.db, embedder=self.embedder, channel_id=channel_id)

    async def sync_email_tool(
        self, folder: str = "INBOX", max_emails: int = 200
    ) -> dict[str, Any]:
        return await sync_email(db=self.db, embedder=self.embedder, folder=folder, max_emails=max_emails)

    async def sync_mastodon_tool(
        self,
        instance: str | None = None,
        hashtag: str | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        return await sync_mastodon(
            db=self.db,
            embedder=self.embedder,
            instance=instance,
            hashtag=hashtag,
            access_token=access_token,
        )

    async def sync_imessage_tool(self, chat_name: str | None = None) -> dict[str, Any]:
        return await sync_imessage(db=self.db, embedder=self.embedder, chat_name=chat_name)

    async def sync_signal_tool(self, conversation_id: str | None = None) -> dict[str, Any]:
        return await sync_signal(db=self.db, embedder=self.embedder, conversation_id=conversation_id)

    async def import_sms_backup(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}", "ingested": 0}
        if path.suffix.lower() not in (".xml",):
            return {"error": f"Expected a .xml file, got: {path.suffix}", "ingested": 0}

        items = parse_sms_backup(path)
        if not items:
            return {"error": "No messages parsed from file", "ingested": 0}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        sources = list({item.source for item in items})
        return {
            "ingested": count,
            "file": str(path),
            "sources": sources,
        }

    async def import_discord_package_tool(self, package_path: str) -> dict[str, Any]:
        return await _import_discord_package(package_path=package_path, db=self.db, embedder=self.embedder)

    async def import_instagram(self, data_path: str) -> dict[str, Any]:
        path = Path(data_path)
        if not path.exists():
            return {"error": f"Path not found: {data_path}", "ingested": 0}

        items = parse_instagram_export(path)
        if not items:
            return {"error": "No messages parsed from export", "ingested": 0}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        sources = list({item.source for item in items})
        return {
            "ingested": count,
            "path": str(path),
            "sources": sources,
        }

    async def import_json_file(
        self,
        file_path: str,
        content_field: str,
        sender_field: str = "",
        timestamp_field: str = "",
        source_label: str = "json-import",
    ) -> dict[str, Any]:
        field_mapping: dict[str, str] = {"content": content_field}
        if sender_field:
            field_mapping["sender"] = sender_field
        if timestamp_field:
            field_mapping["timestamp"] = timestamp_field

        items, errors = import_json(file_path, field_mapping, source_label)
        if not items:
            return {"error": errors[0] if errors else "No messages parsed", "ingested": 0, "errors": errors}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        result: dict[str, Any] = {"ingested": count, "file": str(file_path), "source": source_label}
        if errors:
            result["parse_errors"] = errors
        return result

    async def import_csv_file(
        self,
        file_path: str,
        content_column: str,
        sender_column: str = "",
        timestamp_column: str = "",
        source_label: str = "csv-import",
    ) -> dict[str, Any]:
        field_mapping: dict[str, str] = {"content": content_column}
        if sender_column:
            field_mapping["sender"] = sender_column
        if timestamp_column:
            field_mapping["timestamp"] = timestamp_column

        items, errors = import_csv(file_path, field_mapping, source_label)
        if not items:
            return {"error": errors[0] if errors else "No messages parsed", "ingested": 0, "errors": errors}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        result: dict[str, Any] = {"ingested": count, "file": str(file_path), "source": source_label}
        if errors:
            result["parse_errors"] = errors
        return result

    async def import_messenger(self, data_path: str) -> dict[str, Any]:
        path = Path(data_path)
        if not path.exists():
            return {"error": f"Path not found: {data_path}", "ingested": 0}

        items = parse_messenger_export(path)
        if not items:
            return {"error": "No messages parsed from export", "ingested": 0}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        sources = list({item.source for item in items})
        return {
            "ingested": count,
            "path": str(path),
            "sources": sources,
        }
