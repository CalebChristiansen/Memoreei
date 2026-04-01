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

TELEGRAM_API_BASE = "https://api.telegram.org"
SOURCE_PREFIX = "telegram"
FETCH_LIMIT = 100  # updates per getUpdates call


class TelegramConnector:
    """Fetch Telegram messages received by the bot using getUpdates (Bot API).

    The Bot API only delivers messages the bot has received since its last
    acknowledged update. Each call to getUpdates with an ``offset`` marks
    previous updates as consumed, so the connector stores the highest
    ``update_id`` seen as a per-chat checkpoint and uses it to avoid
    re-ingesting the same messages on subsequent syncs.

    Limitation: messages sent *before* the bot was added to a chat, or
    consumed by another process (e.g. OpenClaw's gateway), are not visible
    via getUpdates.
    """

    def __init__(self, token: str, db: Database, embedder: Any) -> None:
        self.token = token
        self.db = db
        self.embedder = embedder
        self._base = f"{TELEGRAM_API_BASE}/bot{token}"

    async def sync_chat(self, chat_id: str | None = None) -> dict[str, Any]:
        """Fetch new updates, filter to ``chat_id`` if given, embed and store.

        Returns a dict with ``synced`` count and ``chat_ids`` processed.
        """
        # Determine the global offset from the lowest stored checkpoint so we
        # don't re-request updates we've already ingested.
        global_offset = await self._global_offset()

        updates = await self._fetch_updates(offset=global_offset)
        if not updates:
            return {"synced": 0, "chat_ids": []}

        # Group by chat_id
        by_chat: dict[str, list[dict]] = {}
        max_update_id: dict[str, int] = {}

        for upd in updates:
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue  # skip non-message updates (inline queries, etc.)
            cid = str(msg["chat"]["id"])
            if chat_id and cid != str(chat_id):
                continue
            by_chat.setdefault(cid, []).append(msg)
            uid = upd["update_id"]
            if uid > max_update_id.get(cid, -1):
                max_update_id[cid] = uid

        if not by_chat:
            # Acknowledge updates even if none matched the filter
            last_update_id = max(u["update_id"] for u in updates)
            await self._ack_updates(last_update_id)
            return {"synced": 0, "chat_ids": []}

        # Convert and embed
        all_items: list[MemoryItem] = []
        for cid, messages in by_chat.items():
            items = [self._to_memory_item(msg) for msg in messages]
            all_items.extend(items)

        texts = [item.content for item in all_items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(all_items, embeddings):
            item.embedding = emb

        await self.db.bulk_insert(all_items)

        # Update per-chat checkpoints and global ack
        for cid, uid in max_update_id.items():
            await self.db.set_telegram_checkpoint(cid, uid)

        last_update_id = max(u["update_id"] for u in updates)
        await self._ack_updates(last_update_id)

        return {"synced": len(all_items), "chat_ids": list(by_chat.keys())}

    async def _global_offset(self) -> int | None:
        """Return offset = (lowest stored checkpoint) so we re-request nothing already stored."""
        # We don't have a cross-chat offset table; use None to let Telegram
        # return only unacknowledged updates.
        return None

    async def _fetch_updates(self, offset: int | None = None) -> list[dict]:
        """Call getUpdates and return the list of update objects."""
        url = f"{self._base}/getUpdates"
        params: dict[str, Any] = {"limit": FETCH_LIMIT, "timeout": 0}
        if offset is not None:
            params["offset"] = offset

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        return data["result"]
                    raise RuntimeError(f"Telegram API error: {data.get('description')}")
                elif resp.status == 401:
                    raise ValueError("Telegram bot token is invalid or unauthorized")
                else:
                    text = await resp.text()
                    raise RuntimeError(f"Telegram API error {resp.status}: {text}")

    async def _ack_updates(self, last_update_id: int) -> None:
        """Acknowledge updates up to last_update_id by setting offset = last + 1."""
        url = f"{self._base}/getUpdates"
        params = {"offset": last_update_id + 1, "limit": 1, "timeout": 0}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params):
                pass  # response not needed; side-effect is the ack

    def _to_memory_item(self, msg: dict) -> MemoryItem:
        sender = msg.get("from", {})
        username = (
            sender.get("username")
            or sender.get("first_name", "")
            + (" " + sender.get("last_name", "") if sender.get("last_name") else "")
        ).strip() or "unknown"

        text = msg.get("text") or msg.get("caption", "")
        if not text:
            if msg.get("photo"):
                text = "[photo]"
            elif msg.get("document"):
                text = "[document]"
            elif msg.get("sticker"):
                text = "[sticker]"
            elif msg.get("voice"):
                text = "[voice message]"
            elif msg.get("video"):
                text = "[video]"
            else:
                text = "[unsupported message type]"

        chat = msg["chat"]
        chat_id = str(chat["id"])
        message_id = str(msg["message_id"])
        ts = int(msg.get("date", time.time()))

        source = f"{SOURCE_PREFIX}:{chat_id}"
        source_id = f"{source}:{message_id}"

        chat_title = chat.get("title") or chat.get("username") or chat_id

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
                "chat_id": chat_id,
                "chat_title": chat_title,
                "chat_type": chat.get("type", "unknown"),
                "message_id": message_id,
                "sender_id": str(sender.get("id", "")),
                "has_media": bool(
                    msg.get("photo")
                    or msg.get("document")
                    or msg.get("voice")
                    or msg.get("video")
                ),
            },
            embedding=None,
        )


async def sync_telegram(
    db: Database, embedder: Any, chat_id: str | None = None
) -> dict:
    """Top-level sync function used by the MCP server tool."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return {"error": "TELEGRAM_BOT_TOKEN not set in environment", "synced": 0}

    target_chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    connector = TelegramConnector(token=token, db=db, embedder=embedder)
    try:
        result = await connector.sync_chat(chat_id=target_chat)
        return result
    except Exception as e:
        return {"error": str(e), "synced": 0}
