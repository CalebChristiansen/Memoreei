from __future__ import annotations

import os
import time
from typing import Any

import aiohttp

from ulid import ULID

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

DISCORD_API_BASE = "https://discord.com/api/v10"
DEFAULT_CHANNEL_ID = "REDACTED_CHANNEL_ID"
SOURCE_PREFIX = "discord"
FETCH_LIMIT = 100  # Discord max per request


class DiscordConnector:
    """One-shot Discord channel sync via REST API. No persistent gateway connection."""

    def __init__(self, token: str, db: Database, embedder: Any) -> None:
        self.token = token
        self.db = db
        self.embedder = embedder
        self._headers = {
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
        }

    async def sync_channel(self, channel_id: str) -> int:
        """Fetch new messages since last checkpoint, embed and store. Returns count."""
        last_id = await self.db.get_discord_checkpoint(channel_id)

        messages = await self._fetch_messages(channel_id, after=last_id)
        if not messages:
            return 0

        items = [self._to_memory_item(msg, channel_id) for msg in messages]

        # Embed all messages
        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        # Store and update checkpoint
        await self.db.bulk_insert(items)

        # Checkpoint = the newest message ID (messages are newest-first from Discord)
        newest_id = messages[0]["id"]
        await self.db.set_discord_checkpoint(channel_id, newest_id)

        return len(items)

    async def _fetch_messages(
        self, channel_id: str, after: str | None = None
    ) -> list[dict]:
        """Fetch messages from Discord channel. Returns list newest-first."""
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
        params: dict[str, Any] = {"limit": FETCH_LIMIT}
        if after:
            params["after"] = after

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 401:
                    raise ValueError("Discord bot token is invalid or unauthorized")
                elif resp.status == 403:
                    raise ValueError(f"Bot does not have access to channel {channel_id}")
                elif resp.status == 404:
                    raise ValueError(f"Channel {channel_id} not found")
                else:
                    text = await resp.text()
                    raise RuntimeError(f"Discord API error {resp.status}: {text}")

    def _to_memory_item(self, msg: dict, channel_id: str) -> MemoryItem:
        author = msg.get("author", {})
        username = author.get("global_name") or author.get("username", "unknown")
        content = msg.get("content", "").strip()

        # Handle embeds/attachments
        if not content:
            if msg.get("embeds"):
                content = "[embed]"
            elif msg.get("attachments"):
                content = "[attachment]"
            else:
                content = "[empty message]"

        # Discord snowflake → approximate timestamp
        snowflake = int(msg["id"])
        ts = int((snowflake >> 22) / 1000 + 1420070400)

        source = f"{SOURCE_PREFIX}:{channel_id}"
        source_id = f"{source}:{msg['id']}"

        return MemoryItem(
            id=str(ULID()),
            source=source,
            source_id=source_id,
            content=f"{username}: {content}",
            summary=None,
            participants=[username],
            ts=ts,
            ingested_at=int(time.time()),
            metadata={
                "channel_id": channel_id,
                "message_id": msg["id"],
                "author_id": author.get("id"),
                "has_attachments": bool(msg.get("attachments")),
            },
            embedding=None,
        )


async def sync_discord(
    db: Database, embedder: Any, channel_id: str | None = None
) -> dict:
    """Top-level sync function used by the MCP server tool."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        return {"error": "DISCORD_BOT_TOKEN not set in environment", "synced": 0}

    target_channel = channel_id or os.environ.get("DISCORD_CHANNEL_ID", DEFAULT_CHANNEL_ID)

    connector = DiscordConnector(token=token, db=db, embedder=embedder)
    try:
        count = await connector.sync_channel(target_channel)
        return {"synced": count, "channel_id": target_channel}
    except Exception as e:
        return {"error": str(e), "synced": 0, "channel_id": target_channel}
