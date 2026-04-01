from __future__ import annotations

import os
import time
from typing import Any

try:
    import aiohttp
except ImportError:
    aiohttp = None  # type: ignore[assignment]

from ulid import ULID

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

SLACK_API_BASE = "https://slack.com/api"
SOURCE_PREFIX = "slack"
FETCH_LIMIT = 200  # Slack max per request for conversations.history


class SlackConnector:
    """One-shot Slack channel sync via Web API. Requires bot token with channels:history scope."""

    def __init__(self, token: str, db: Database, embedder: Any) -> None:
        self.token = token
        self.db = db
        self.embedder = embedder
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def sync_channel(self, channel_id: str) -> int:
        """Fetch new messages since last checkpoint, embed and store. Returns count."""
        last_ts = await self.db.get_slack_checkpoint(channel_id)

        messages = await self._fetch_messages(channel_id, oldest=last_ts)
        if not messages:
            return 0

        # Resolve user IDs to display names
        user_cache: dict[str, str] = {}
        items = [
            await self._to_memory_item(msg, channel_id, user_cache)
            for msg in messages
        ]

        # Embed all messages
        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        await self.db.bulk_insert(items)

        # Checkpoint = the newest message timestamp (messages are oldest-first)
        newest_ts = messages[-1]["ts"]
        await self.db.set_slack_checkpoint(channel_id, newest_ts)

        return len(items)

    async def _fetch_messages(
        self, channel_id: str, oldest: str | None = None
    ) -> list[dict]:
        """Fetch messages from Slack channel. Returns list oldest-first."""
        url = f"{SLACK_API_BASE}/conversations.history"
        params: dict[str, Any] = {
            "channel": channel_id,
            "limit": FETCH_LIMIT,
        }
        if oldest:
            params["oldest"] = oldest

        all_messages: list[dict] = []

        async with aiohttp.ClientSession() as session:
            while True:
                async with session.get(url, headers=self._headers, params=params) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(f"Slack API HTTP error {resp.status}: {text}")
                    data = await resp.json()

                if not data.get("ok"):
                    error = data.get("error", "unknown_error")
                    if error == "not_in_channel":
                        raise ValueError(
                            f"Bot is not in channel {channel_id}. "
                            "Invite the bot with /invite @botname"
                        )
                    elif error in ("invalid_auth", "not_authed", "token_revoked"):
                        raise ValueError("Slack bot token is invalid or revoked")
                    elif error == "channel_not_found":
                        raise ValueError(f"Channel {channel_id} not found")
                    else:
                        raise RuntimeError(f"Slack API error: {error}")

                messages = data.get("messages", [])
                # Filter out subtypes (channel_join, bot_message without text, etc.)
                messages = [
                    m for m in messages
                    if not m.get("subtype") or m.get("text")
                ]
                all_messages.extend(messages)

                if not data.get("has_more"):
                    break

                next_cursor = data.get("response_metadata", {}).get("next_cursor")
                if not next_cursor:
                    break
                params["cursor"] = next_cursor

        # Slack returns newest-first; reverse to oldest-first
        all_messages.reverse()
        return all_messages

    async def _resolve_user(
        self, user_id: str, session: aiohttp.ClientSession, cache: dict[str, str]
    ) -> str:
        """Resolve a Slack user ID to a display name."""
        if user_id in cache:
            return cache[user_id]

        url = f"{SLACK_API_BASE}/users.info"
        try:
            async with session.get(
                url, headers=self._headers, params={"user": user_id}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        profile = data["user"].get("profile", {})
                        name = (
                            profile.get("display_name")
                            or profile.get("real_name")
                            or data["user"].get("name")
                            or user_id
                        )
                        cache[user_id] = name
                        return name
        except Exception:
            pass

        cache[user_id] = user_id
        return user_id

    async def _to_memory_item(
        self, msg: dict, channel_id: str, user_cache: dict[str, str]
    ) -> MemoryItem:
        text = msg.get("text", "").strip()
        if not text:
            if msg.get("files"):
                text = "[file]"
            elif msg.get("attachments"):
                text = "[attachment]"
            else:
                text = "[empty message]"

        # Resolve author
        user_id = msg.get("user") or msg.get("bot_id", "unknown")
        async with aiohttp.ClientSession() as session:
            username = await self._resolve_user(user_id, session, user_cache)

        # Slack ts is a float string like "1234567890.000100"
        ts_float = float(msg.get("ts", "0"))
        ts = int(ts_float)

        source = f"{SOURCE_PREFIX}:{channel_id}"
        source_id = f"{source}:{msg['ts']}"

        return MemoryItem(
            id=str(ULID()),
            source=source,
            source_id=source_id,
            content=f"{username}: {text}",
            summary=None,
            participants=[username],
            ts=ts,
            ingested_at=int(time.time()),
            metadata={
                "channel_id": channel_id,
                "slack_ts": msg["ts"],
                "user_id": user_id,
                "has_files": bool(msg.get("files")),
                "thread_ts": msg.get("thread_ts"),
            },
            embedding=None,
        )


async def sync_slack(
    db: Database, embedder: Any, channel_id: str | None = None
) -> dict:
    """Top-level sync function used by the MCP server tool."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        return {"error": "SLACK_BOT_TOKEN not set in environment", "synced": 0}

    target_channel = channel_id or os.environ.get("SLACK_CHANNEL_ID", "")
    if not target_channel:
        return {
            "error": "No channel_id provided and SLACK_CHANNEL_ID not set in environment",
            "synced": 0,
        }

    connector = SlackConnector(token=token, db=db, embedder=embedder)
    try:
        count = await connector.sync_channel(target_channel)
        return {"synced": count, "channel_id": target_channel}
    except Exception as e:
        return {"error": str(e), "synced": 0, "channel_id": target_channel}
