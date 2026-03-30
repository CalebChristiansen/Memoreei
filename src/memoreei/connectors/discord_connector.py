from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

import aiohttp

from ulid import ULID

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

DISCORD_API_BASE = "https://discord.com/api/v10"
SOURCE_PREFIX = "discord"
FETCH_LIMIT = 100  # Discord max per request
_MIN_REQUEST_INTERVAL: float = 1.0  # Never more than 1 req/sec

# Module-level rate limit tracking (shared across all connector instances)
_last_request_time: float = 0.0
_rate_limit_remaining: int = 5
_rate_limit_reset: float = 0.0


async def _enforce_rate_limit() -> None:
    """Sleep as needed to stay under 1 req/sec and respect X-RateLimit headers."""
    global _last_request_time, _rate_limit_remaining, _rate_limit_reset

    # Enforce minimum 1-second gap between requests
    elapsed = time.monotonic() - _last_request_time
    if elapsed < _MIN_REQUEST_INTERVAL:
        await asyncio.sleep(_MIN_REQUEST_INTERVAL - elapsed)

    # If rate limit bucket is nearly empty, wait until it resets
    if _rate_limit_remaining <= 1:
        reset_in = _rate_limit_reset - time.time()
        if reset_in > 0:
            print(
                f"[discord] Rate limit low ({_rate_limit_remaining} remaining), "
                f"sleeping {reset_in:.1f}s until reset",
                file=sys.stderr,
            )
            await asyncio.sleep(reset_in + 0.2)


def _update_rate_limit_headers(resp: aiohttp.ClientResponse) -> None:
    global _rate_limit_remaining, _rate_limit_reset
    try:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            _rate_limit_remaining = int(remaining)
    except (ValueError, TypeError):
        pass
    try:
        reset_ts = resp.headers.get("X-RateLimit-Reset")
        if reset_ts is not None:
            _rate_limit_reset = float(reset_ts)
    except (ValueError, TypeError):
        pass


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
        global _last_request_time

        await _enforce_rate_limit()

        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
        params: dict[str, Any] = {"limit": FETCH_LIMIT}
        if after:
            params["after"] = after

        backoff = 1.0
        for attempt in range(5):
            _last_request_time = time.monotonic()
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self._headers, params=params) as resp:
                    _update_rate_limit_headers(resp)

                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        retry_after_str = resp.headers.get("Retry-After", str(backoff))
                        try:
                            retry_after = float(retry_after_str)
                        except (ValueError, TypeError):
                            retry_after = backoff
                        print(
                            f"[discord] 429 rate limited on channel {channel_id}, "
                            f"retry after {retry_after:.1f}s (attempt {attempt + 1}/5)",
                            file=sys.stderr,
                        )
                        await asyncio.sleep(retry_after)
                        backoff = min(backoff * 2, 60.0)
                        continue
                    elif resp.status == 401:
                        raise ValueError("Discord bot token is invalid or unauthorized")
                    elif resp.status == 403:
                        raise ValueError(f"Bot does not have access to channel {channel_id}")
                    elif resp.status == 404:
                        raise ValueError(f"Channel {channel_id} not found")
                    else:
                        text = await resp.text()
                        raise RuntimeError(f"Discord API error {resp.status}: {text}")

        raise RuntimeError(f"Discord API: too many rate limit retries for channel {channel_id}")

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

    target_channel = channel_id or os.environ.get("DISCORD_CHANNEL_ID", "")
    if not target_channel:
        return {"error": "No channel_id provided and DISCORD_CHANNEL_ID not set in environment", "synced": 0}

    connector = DiscordConnector(token=token, db=db, embedder=embedder)
    try:
        count = await connector.sync_channel(target_channel)
        return {"synced": count, "channel_id": target_channel}
    except Exception as e:
        return {"error": str(e), "synced": 0, "channel_id": target_channel}
