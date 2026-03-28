from __future__ import annotations

import time

import pytest

from memoreei.connectors.discord_connector import DiscordConnector
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem


class MockEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 10 for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.1] * 10


def make_discord_message(message_id: str, content: str, username: str = "testuser") -> dict:
    """Build a minimal Discord API message dict."""
    # Discord snowflake: encode a timestamp-like ID
    snowflake = (int(time.time() * 1000) - 1420070400000) << 22
    return {
        "id": message_id,
        "content": content,
        "author": {
            "id": "999888777",
            "username": username,
            "global_name": username,
        },
        "attachments": [],
        "embeds": [],
    }


@pytest.fixture
async def db(tmp_path):
    database = Database(db_path=str(tmp_path / "test.db"))
    await database.connect()
    yield database
    await database.close()


def test_to_memory_item(db):
    """Test Discord message → MemoryItem conversion (sync, no network)."""
    import asyncio

    async def _run():
        connector = DiscordConnector(token="fake", db=db, embedder=MockEmbedder())
        msg = make_discord_message("123456789", "hello from discord", "Elliot")
        item = connector._to_memory_item(msg, "chan001")

        assert item.content == "Elliot: hello from discord"
        assert item.source == "discord:chan001"
        assert item.source_id == "discord:chan001:123456789"
        assert "Elliot" in item.participants
        assert item.metadata["channel_id"] == "chan001"

    asyncio.run(_run())


@pytest.mark.asyncio
async def test_checkpoint_roundtrip(db):
    await db.set_discord_checkpoint("chan001", "msg100")
    result = await db.get_discord_checkpoint("chan001")
    assert result == "msg100"

    await db.set_discord_checkpoint("chan001", "msg200")
    result = await db.get_discord_checkpoint("chan001")
    assert result == "msg200"


@pytest.mark.asyncio
async def test_no_token_returns_error(db):
    import os
    from memoreei.connectors.discord_connector import sync_discord

    # Ensure no token
    old = os.environ.pop("DISCORD_BOT_TOKEN", None)
    try:
        result = await sync_discord(db=db, embedder=MockEmbedder(), channel_id="123")
        assert "error" in result
        assert result["synced"] == 0
    finally:
        if old:
            os.environ["DISCORD_BOT_TOKEN"] = old
