# Adding a New Connector

This guide walks through adding support for a new message source. As an example we'll add a hypothetical `irc` connector.

## 1. Implement BaseConnector

Create `src/memoreei/connectors/irc_connector.py`:

```python
from __future__ import annotations

import time
from dataclasses import dataclass

from memoreei.connectors.base import BaseConnector, SyncResult
from memoreei.config import get_config
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem
from memoreei.search.embeddings import get_embedder


class IrcConnector(BaseConnector):
    name = "irc"

    def __init__(self, db: Database) -> None:
        self.db = db
        self.cfg = get_config()

    @classmethod
    def is_configured(cls) -> bool:
        cfg = get_config()
        return bool(cfg.irc_server and cfg.irc_channel)

    async def sync(self, channel: str | None = None, **kwargs) -> SyncResult:
        result = SyncResult(source="irc")

        channel = channel or self.cfg.irc_channel
        if not channel:
            result.errors.append("No IRC channel configured")
            return result

        # 1. Read checkpoint (last message we synced)
        checkpoint = await self.db.get_irc_checkpoint(channel)
        since_ts = int(checkpoint) if checkpoint else 0

        # 2. Fetch messages from the platform
        try:
            raw_messages = await self._fetch_messages(channel, since_ts)
        except Exception as e:
            result.errors.append(str(e))
            return result

        if not raw_messages:
            return result

        # 3. Convert to MemoryItem objects
        embedder = get_embedder()
        items: list[MemoryItem] = []
        for msg in raw_messages:
            items.append(MemoryItem(
                id="",                          # leave empty; DB assigns ULID
                source=f"irc:{channel}",
                source_id=f"{channel}:{msg['id']}",  # must be unique per source
                content=f"<{msg['nick']}> {msg['text']}",
                summary=None,
                participants=[msg["nick"]],
                ts=int(msg["timestamp"]),
                ingested_at=int(time.time()),
                metadata={"channel": channel, "server": self.cfg.irc_server},
                embedding=None,                 # filled in below
            ))

        # 4. Embed in batches
        texts = [item.content for item in items]
        embeddings = await embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        # 5. Bulk insert (upsert — safe to call repeatedly)
        await self.db.bulk_insert(items)
        result.synced = len(items)

        # 6. Update checkpoint to the latest message timestamp
        latest_ts = max(msg["timestamp"] for msg in raw_messages)
        await self.db.set_irc_checkpoint(channel, str(latest_ts))

        return result

    async def _fetch_messages(self, channel: str, since_ts: int) -> list[dict]:
        """Hit your platform's API here. Return raw message dicts."""
        # ... your API calls ...
        return []
```

### Key rules

- `name` must be unique across all connectors
- `source_id` must uniquely identify a message within its source (used for dedup)
- `source` should be `"platform:identifier"` when multiple channels are possible
- Always update the checkpoint at the end so reruns don't re-ingest

## 2. Add a Checkpoint Table

Add to `src/memoreei/storage/database.py` in `_init_schema()`:

```python
await conn.execute("""
    CREATE TABLE IF NOT EXISTS irc_checkpoint (
        channel TEXT PRIMARY KEY,
        last_ts  TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    )
""")
```

Add checkpoint getter/setter methods to the `Database` class:

```python
async def get_irc_checkpoint(self, channel: str) -> str | None:
    async with aiosqlite.connect(self.db_path) as conn:
        async with conn.execute(
            "SELECT last_ts FROM irc_checkpoint WHERE channel = ?", (channel,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def set_irc_checkpoint(self, channel: str, last_ts: str) -> None:
    async with aiosqlite.connect(self.db_path) as conn:
        await conn.execute(
            """INSERT INTO irc_checkpoint (channel, last_ts, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(channel) DO UPDATE SET last_ts=excluded.last_ts,
               updated_at=excluded.updated_at""",
            (channel, last_ts, int(time.time())),
        )
        await conn.commit()
```

## 3. Add Config Fields

In `src/memoreei/config.py`, add fields to the `Config` dataclass:

```python
@dataclass
class Config:
    # ... existing fields ...

    # IRC
    irc_server: str | None = None
    irc_channel: str | None = None
    irc_nick: str | None = None
```

Add the env var mappings in `load_config()`:

```python
irc_server=os.getenv("IRC_SERVER"),
irc_channel=os.getenv("IRC_CHANNEL"),
irc_nick=os.getenv("IRC_NICK"),
```

Update `configured_connectors()` to include irc:

```python
if self.irc_server and self.irc_channel:
    connectors.append("irc")
```

Also document the new vars in `.env.example`:

```bash
# IRC
# IRC_SERVER=irc.libera.chat
# IRC_CHANNEL=#mychannel
# IRC_NICK=memoreei_bot
```

## 4. Register in connectors/__init__.py

```python
from memoreei.connectors.irc_connector import IrcConnector

CONNECTOR_REGISTRY: dict[str, type[BaseConnector]] = {
    "discord": DiscordConnector,
    "telegram": TelegramConnector,
    # ... existing ...
    "irc": IrcConnector,
}
```

## 5. Add an MCP Tool

In `src/memoreei/server.py`, add a tool alongside the existing `sync_*` tools:

```python
@mcp.tool()
async def sync_irc(channel: str | None = None) -> dict:
    """Sync messages from an IRC channel into memory."""
    tools = await _get_tools()
    connector = IrcConnector(tools.db)
    if not IrcConnector.is_configured():
        return {"error": "IRC not configured. Set IRC_SERVER and IRC_CHANNEL in .env"}
    result = await connector.sync(channel=channel)
    return result.to_dict()
```

## 6. Add Tests

Create `tests/test_irc.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from memoreei.connectors.irc_connector import IrcConnector


@pytest.mark.asyncio
async def test_irc_sync_returns_results(temp_db):
    raw = [{"id": "1", "nick": "alice", "text": "hello", "timestamp": 1700000000}]

    with patch.object(IrcConnector, "_fetch_messages", new=AsyncMock(return_value=raw)):
        with patch("memoreei.connectors.irc_connector.get_config") as mock_cfg:
            mock_cfg.return_value.irc_server = "irc.libera.chat"
            mock_cfg.return_value.irc_channel = "#test"
            connector = IrcConnector(temp_db)
            result = await connector.sync(channel="#test")

    assert result.synced == 1
    assert result.ok


@pytest.mark.asyncio
async def test_irc_dedup(temp_db):
    """Syncing same messages twice should not double-count."""
    raw = [{"id": "1", "nick": "alice", "text": "hello", "timestamp": 1700000000}]

    with patch.object(IrcConnector, "_fetch_messages", new=AsyncMock(return_value=raw)):
        with patch("memoreei.connectors.irc_connector.get_config") as mock_cfg:
            mock_cfg.return_value.irc_server = "irc.libera.chat"
            mock_cfg.return_value.irc_channel = "#test"
            connector = IrcConnector(temp_db)
            await connector.sync(channel="#test")
            result2 = await connector.sync(channel="#test")

    sources = await temp_db.list_sources()
    total = sum(sources.values())
    assert total == 1  # not 2


def test_irc_not_configured_without_env():
    with patch("memoreei.connectors.irc_connector.get_config") as mock_cfg:
        mock_cfg.return_value.irc_server = None
        mock_cfg.return_value.irc_channel = None
        assert IrcConnector.is_configured() is False
```
