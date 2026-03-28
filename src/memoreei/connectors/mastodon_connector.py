from __future__ import annotations

import os
import time
from typing import Any

import aiohttp

from ulid import ULID

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

DEFAULT_INSTANCE = "https://mastodon.social"
SOURCE_PREFIX = "mastodon"
FETCH_LIMIT = 40  # Mastodon default max per request


class MastodonConnector:
    """One-shot Mastodon sync via REST API.

    Supports public timeline and hashtag timeline — no auth required.
    Optionally uses an access token for home timeline or private accounts.
    """

    def __init__(
        self,
        db: Database,
        embedder: Any,
        instance: str = DEFAULT_INSTANCE,
        access_token: str | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.instance = instance.rstrip("/")
        self._headers: dict[str, str] = {}
        if access_token:
            self._headers["Authorization"] = f"Bearer {access_token}"

    async def sync_public(self, hashtag: str | None = None) -> int:
        """Fetch recent posts from public/hashtag timeline. Returns count stored."""
        source_key = f"mastodon:{hashtag or 'public'}"
        last_id = await self.db.get_discord_checkpoint(source_key)  # reuse checkpoint table

        statuses = await self._fetch_statuses(hashtag=hashtag, since_id=last_id)
        if not statuses:
            return 0

        items = [self._to_memory_item(s, hashtag) for s in statuses]

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        await self.db.bulk_insert(items)

        newest_id = statuses[0]["id"]
        await self.db.set_discord_checkpoint(source_key, newest_id)

        return len(items)

    async def _fetch_statuses(
        self, hashtag: str | None, since_id: str | None
    ) -> list[dict]:
        if hashtag:
            url = f"{self.instance}/api/v1/timelines/tag/{hashtag}"
        else:
            url = f"{self.instance}/api/v1/timelines/public"

        params: dict[str, Any] = {"limit": FETCH_LIMIT}
        if since_id:
            params["since_id"] = since_id

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 401:
                    raise ValueError("Invalid Mastodon access token")
                elif resp.status == 404:
                    raise ValueError(f"Hashtag or endpoint not found: {url}")
                else:
                    text = await resp.text()
                    raise RuntimeError(f"Mastodon API error {resp.status}: {text}")

    def _to_memory_item(self, status: dict, hashtag: str | None) -> MemoryItem:
        account = status.get("account", {})
        username = account.get("acct") or account.get("username", "unknown")

        # Strip HTML tags from content
        import re
        raw_content = status.get("content", "")
        plain = re.sub(r"<[^>]+>", "", raw_content).strip()
        if not plain:
            plain = "[empty post]"

        # ISO timestamp → unix
        created_at = status.get("created_at", "")
        try:
            from datetime import datetime, timezone
            ts = int(datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp())
        except Exception:
            ts = int(time.time())

        source_name = f"mastodon:{hashtag or 'public'}"
        source_id = f"{source_name}:{status['id']}"

        return MemoryItem(
            id=str(ULID()),
            source=source_name,
            source_id=source_id,
            content=f"{username}: {plain}",
            summary=None,
            participants=[username],
            ts=ts,
            ingested_at=int(time.time()),
            metadata={
                "status_id": status["id"],
                "instance": self.instance,
                "hashtag": hashtag,
                "url": status.get("url"),
                "favourites_count": status.get("favourites_count", 0),
                "reblogs_count": status.get("reblogs_count", 0),
            },
            embedding=None,
        )


async def sync_mastodon(
    db: Database,
    embedder: Any,
    instance: str | None = None,
    hashtag: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Top-level sync function used by the MCP server tool."""
    target_instance = instance or os.environ.get("MASTODON_INSTANCE", DEFAULT_INSTANCE)
    token = access_token or os.environ.get("MASTODON_ACCESS_TOKEN")
    target_hashtag = hashtag or os.environ.get("MASTODON_HASHTAG")

    connector = MastodonConnector(
        db=db,
        embedder=embedder,
        instance=target_instance,
        access_token=token,
    )
    try:
        count = await connector.sync_public(hashtag=target_hashtag)
        return {
            "synced": count,
            "instance": target_instance,
            "hashtag": target_hashtag or "public",
        }
    except Exception as e:
        return {"error": str(e), "synced": 0, "instance": target_instance}
