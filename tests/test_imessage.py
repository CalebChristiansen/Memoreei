"""Tests for the iMessage connector.

All tests use a synthetic chat.db created in a temp directory — no real
macOS Messages database is required. Platform checks are mocked so the
connector logic can be exercised on Linux/Windows as well.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from memoreei.connectors.imessage_connector import (
    APPLE_EPOCH_OFFSET,
    NANOSECOND_THRESHOLD,
    IMessageConnector,
    _apple_date_to_unix,
    _get_db_path,
    is_macos,
    sync_imessage,
)
from memoreei.storage.database import Database

# ---------------------------------------------------------------------------
# Schema that mirrors the real macOS chat.db (simplified)
# ---------------------------------------------------------------------------

_CHAT_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS handle (
    rowid       INTEGER PRIMARY KEY,
    id          TEXT,
    country     TEXT,
    service     TEXT,
    person_centric_id TEXT
);

CREATE TABLE IF NOT EXISTS chat (
    rowid           INTEGER PRIMARY KEY,
    guid            TEXT,
    style           INTEGER,
    state           INTEGER,
    account_id      TEXT,
    chat_identifier TEXT,
    service_name    TEXT,
    room_name       TEXT,
    display_name    TEXT
);

CREATE TABLE IF NOT EXISTS message (
    rowid                   INTEGER PRIMARY KEY,
    guid                    TEXT,
    text                    TEXT,
    handle_id               INTEGER,
    service                 TEXT,
    account                 TEXT,
    date                    INTEGER,
    date_read               INTEGER,
    date_delivered          INTEGER,
    is_from_me              INTEGER,
    is_read                 INTEGER,
    cache_has_attachments   INTEGER
);

CREATE TABLE IF NOT EXISTS chat_message_join (
    chat_id      INTEGER,
    message_id   INTEGER,
    message_date INTEGER
);

CREATE TABLE IF NOT EXISTS chat_handle_join (
    chat_id   INTEGER,
    handle_id INTEGER
);
"""


def _make_chat_db(path: str) -> None:
    """Create a minimal chat.db with some test messages."""
    conn = sqlite3.connect(path)
    conn.executescript(_CHAT_DB_SCHEMA)

    # Handles
    conn.execute("INSERT INTO handle VALUES (1, '+15550001234', 'US', 'iMessage', NULL)")
    conn.execute("INSERT INTO handle VALUES (2, 'alice@example.com', 'US', 'iMessage', NULL)")

    # Chats
    # Individual chat with +15550001234
    conn.execute(
        "INSERT INTO chat VALUES (1, 'chat-guid-1', 45, 3, NULL, '+15550001234', 'iMessage', NULL, NULL)"
    )
    # Group chat
    conn.execute(
        "INSERT INTO chat VALUES (2, 'chat-guid-2', 43, 3, NULL, 'chat-group-id', 'iMessage', 'my-room', 'Book Club')"
    )

    # Messages — dates in seconds since Apple epoch (small values → seconds branch)
    # +15550001234 sends: date=730000000 (≈ Feb 2024)
    conn.execute(
        "INSERT INTO message VALUES (1, 'msg-guid-1', 'Hello there!', 1, 'iMessage', NULL, 730000000, 0, 0, 0, 1, 0)"
    )
    # me sends reply
    conn.execute(
        "INSERT INTO message VALUES (2, 'msg-guid-2', 'Hey! How are you?', 0, 'iMessage', NULL, 730001000, 0, 0, 1, 1, 0)"
    )
    # empty message (should be skipped)
    conn.execute(
        "INSERT INTO message VALUES (3, 'msg-guid-3', '', 1, 'iMessage', NULL, 730002000, 0, 0, 0, 1, 0)"
    )
    # NULL text (should be skipped)
    conn.execute(
        "INSERT INTO message VALUES (4, 'msg-guid-4', NULL, 1, 'iMessage', NULL, 730003000, 0, 0, 0, 1, 0)"
    )
    # Group chat message
    conn.execute(
        "INSERT INTO message VALUES (5, 'msg-guid-5', 'What are we reading?', 2, 'iMessage', NULL, 730004000, 0, 0, 0, 1, 0)"
    )

    # Joins
    conn.execute("INSERT INTO chat_message_join VALUES (1, 1, 730000000)")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 2, 730001000)")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 3, 730002000)")
    conn.execute("INSERT INTO chat_message_join VALUES (1, 4, 730003000)")
    conn.execute("INSERT INTO chat_message_join VALUES (2, 5, 730004000)")

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_db_path(tmp_path) -> str:
    path = str(tmp_path / "chat.db")
    _make_chat_db(path)
    return path


@pytest.fixture
async def mem_db(tmp_path) -> Database:
    db = Database(db_path=str(tmp_path / "memoreei.db"))
    await db.connect()
    yield db
    await db.close()


class MockEmbedder:
    dim = 4

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * self.dim


@pytest.fixture
def embedder() -> MockEmbedder:
    return MockEmbedder()


# ---------------------------------------------------------------------------
# Unit tests — timestamp conversion
# ---------------------------------------------------------------------------


def test_apple_date_seconds_conversion():
    # 730000000 seconds since 2001-01-01 → unix = 730000000 + 978307200
    result = _apple_date_to_unix(730000000)
    assert result == 730000000 + APPLE_EPOCH_OFFSET


def test_apple_date_nanoseconds_conversion():
    # Same absolute time expressed in nanoseconds
    ns = 730000000 * 1_000_000_000
    result = _apple_date_to_unix(ns)
    assert result == 730000000 + APPLE_EPOCH_OFFSET


def test_apple_date_nanosecond_threshold():
    # Values at the threshold boundary
    below = NANOSECOND_THRESHOLD - 1
    assert _apple_date_to_unix(below) == below + APPLE_EPOCH_OFFSET

    above = NANOSECOND_THRESHOLD + 1
    expected = int((NANOSECOND_THRESHOLD + 1) / 1_000_000_000) + APPLE_EPOCH_OFFSET
    assert _apple_date_to_unix(above) == expected


# ---------------------------------------------------------------------------
# Unit tests — platform detection
# ---------------------------------------------------------------------------


def test_is_macos_on_current_platform():
    result = is_macos()
    assert result == (sys.platform == "darwin")


def test_is_macos_mocked_darwin():
    with patch("memoreei.connectors.imessage_connector.sys") as mock_sys:
        mock_sys.platform = "darwin"
        assert is_macos() is True


def test_is_macos_mocked_linux():
    with patch("memoreei.connectors.imessage_connector.sys") as mock_sys:
        mock_sys.platform = "linux"
        assert is_macos() is False


# ---------------------------------------------------------------------------
# Unit tests — sync_imessage returns error on non-macOS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_imessage_non_macos_returns_error(mem_db, embedder):
    """On Linux, sync_imessage must return an error dict, not raise."""
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=False):
        result = await sync_imessage(db=mem_db, embedder=embedder)
    assert "error" in result
    assert result["synced"] == 0
    assert "macOS" in result["error"]


# ---------------------------------------------------------------------------
# Integration tests — parsing with mock chat.db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_ingests_messages(mem_db, embedder, chat_db_path):
    connector = IMessageConnector(db=mem_db, embedder=embedder, db_path=chat_db_path)
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        count = await connector.sync()

    # Messages 1, 2 (chat 1) and 5 (chat 2) have non-empty text; 3 and 4 are skipped
    assert count == 3


@pytest.mark.asyncio
async def test_sync_message_content(mem_db, embedder, chat_db_path):
    connector = IMessageConnector(db=mem_db, embedder=embedder, db_path=chat_db_path)
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        await connector.sync()

    sources = await mem_db.list_sources()
    assert "imessage:+15550001234" in sources
    assert "imessage:chat-group-id" in sources
    assert sources["imessage:+15550001234"] == 2
    assert sources["imessage:chat-group-id"] == 1


@pytest.mark.asyncio
async def test_sync_sender_attribution(mem_db, embedder, chat_db_path):
    connector = IMessageConnector(db=mem_db, embedder=embedder, db_path=chat_db_path)
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        await connector.sync()

    results = await mem_db.search_fts("Hello there", limit=5)
    assert len(results) == 1
    assert "+15550001234: Hello there!" in results[0].content

    results = await mem_db.search_fts("Hey", limit=5)
    assert len(results) == 1
    assert "me: Hey! How are you?" in results[0].content


@pytest.mark.asyncio
async def test_sync_timestamp_conversion(mem_db, embedder, chat_db_path):
    connector = IMessageConnector(db=mem_db, embedder=embedder, db_path=chat_db_path)
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        await connector.sync()

    results = await mem_db.search_fts("Hello there", limit=1)
    assert results[0].ts == 730000000 + APPLE_EPOCH_OFFSET


@pytest.mark.asyncio
async def test_sync_chat_filter(mem_db, embedder, chat_db_path):
    connector = IMessageConnector(db=mem_db, embedder=embedder, db_path=chat_db_path)
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        count = await connector.sync(chat_name="+15550001234")

    assert count == 2  # only the individual chat
    sources = await mem_db.list_sources()
    assert "imessage:+15550001234" in sources
    assert "imessage:chat-group-id" not in sources


@pytest.mark.asyncio
async def test_sync_chat_filter_by_display_name(mem_db, embedder, chat_db_path):
    connector = IMessageConnector(db=mem_db, embedder=embedder, db_path=chat_db_path)
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        count = await connector.sync(chat_name="Book Club")

    assert count == 1
    sources = await mem_db.list_sources()
    assert "imessage:chat-group-id" in sources


# ---------------------------------------------------------------------------
# Checkpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_set_and_get(mem_db):
    await mem_db.set_imessage_checkpoint("chat-1", 42)
    result = await mem_db.get_imessage_checkpoint("chat-1")
    assert result == 42


@pytest.mark.asyncio
async def test_checkpoint_update(mem_db):
    await mem_db.set_imessage_checkpoint("chat-1", 10)
    await mem_db.set_imessage_checkpoint("chat-1", 99)
    assert await mem_db.get_imessage_checkpoint("chat-1") == 99


@pytest.mark.asyncio
async def test_checkpoint_none_for_unknown(mem_db):
    result = await mem_db.get_imessage_checkpoint("nonexistent-chat")
    assert result is None


@pytest.mark.asyncio
async def test_sync_idempotent_with_checkpoint(mem_db, embedder, chat_db_path):
    """Second sync should ingest 0 new messages (checkpoint prevents re-ingestion)."""
    connector = IMessageConnector(db=mem_db, embedder=embedder, db_path=chat_db_path)
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        first = await connector.sync()
        second = await connector.sync()

    assert first == 3
    assert second == 0


@pytest.mark.asyncio
async def test_sync_incremental_new_message(mem_db, embedder, tmp_path):
    """Messages added after first sync are picked up on the second sync."""
    db_path = str(tmp_path / "chat.db")
    _make_chat_db(db_path)

    connector = IMessageConnector(db=mem_db, embedder=embedder, db_path=db_path)
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        first = await connector.sync()

    assert first == 3

    # Add a new message to the existing individual chat (chat rowid=1)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO message VALUES (6, 'msg-guid-6', 'New message!', 1, 'iMessage', NULL, 730010000, 0, 0, 0, 1, 0)"
    )
    conn.execute("INSERT INTO chat_message_join VALUES (1, 6, 730010000)")
    conn.commit()
    conn.close()

    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        second = await connector.sync()

    assert second == 1


# ---------------------------------------------------------------------------
# Config / env var tests
# ---------------------------------------------------------------------------


def test_get_db_path_default():
    """Default path should point to ~/Library/Messages/chat.db."""
    with patch.dict("os.environ", {}, clear=False):
        # Remove IMESSAGE_DB_PATH if present
        import os
        os.environ.pop("IMESSAGE_DB_PATH", None)
        path = _get_db_path()
    assert path.endswith("Library/Messages/chat.db")


def test_get_db_path_from_env(tmp_path):
    custom = str(tmp_path / "custom.db")
    with patch.dict("os.environ", {"IMESSAGE_DB_PATH": custom}):
        assert _get_db_path() == custom
