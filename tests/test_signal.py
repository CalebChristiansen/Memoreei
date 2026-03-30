"""Tests for the Signal Desktop connector.

All tests use a synthetic (unencrypted) SQLite DB in a temp directory.
No real Signal Desktop installation is required. pysqlcipher3 is not
required — DB access is patched to use a plain sqlite3 connection.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memoreei.connectors.signal_connector import (
    SOURCE_PREFIX,
    SignalConnector,
    _get_config_path,
    _get_db_path,
    _get_signal_dir,
    _has_pysqlcipher3,
    _has_sqlcipher_cli,
    _read_key,
    sync_signal,
)
from memoreei.storage.database import Database

# ---------------------------------------------------------------------------
# Synthetic Signal DB schema (mirrors the real Signal Desktop schema)
# ---------------------------------------------------------------------------

_SIGNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    json TEXT,
    active_at INTEGER,
    type TEXT,
    members TEXT,
    name TEXT,
    profileName TEXT,
    profileFamilyName TEXT,
    profileFullName TEXT,
    e164 TEXT,
    uuid TEXT,
    groupId TEXT,
    profileLastFetchedAt INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    json TEXT,
    readStatus INTEGER,
    expires_at INTEGER,
    sent_at INTEGER,
    schemaVersion INTEGER,
    conversationId TEXT,
    received_at INTEGER,
    source TEXT,
    sourceUuid TEXT,
    sourceDevice INTEGER,
    hasAttachments INTEGER,
    hasFileAttachments INTEGER,
    hasVisualMediaAttachments INTEGER,
    expireTimer INTEGER,
    expirationStartTimestamp INTEGER,
    type TEXT,
    body TEXT,
    messageTimer INTEGER,
    messageTimerStart INTEGER,
    messageTimerExpiresAt INTEGER,
    serverTimestamp INTEGER,
    serverGuid TEXT,
    unread INTEGER,
    targetOfThisMessage TEXT
);
"""


def _make_signal_db(path: str) -> None:
    """Create a minimal Signal-like SQLite DB with test data."""
    conn = sqlite3.connect(path)
    conn.executescript(_SIGNAL_SCHEMA)

    # Conversations
    conn.execute(
        "INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("conv-alice", None, int(time.time()), "private", None, None,
         "Alice", None, "Alice Smith", "+15550001111", "uuid-alice", None, None),
    )
    conn.execute(
        "INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("conv-group", None, int(time.time()), "group", None, "Book Club",
         None, None, None, None, None, "group-id-1", None),
    )

    # Messages — sent_at in milliseconds
    base_ms = 1700000000000  # ~Nov 2023
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("msg-1", None, 0, None, base_ms, 0, "conv-alice", base_ms, "+15550001111",
         "uuid-alice", 1, 0, 0, 0, 0, None, "incoming", "Hello!", None, None, None,
         base_ms, None, 0, None),
    )
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("msg-2", None, 0, None, base_ms + 1000, 0, "conv-alice", base_ms + 1000,
         None, None, 1, 0, 0, 0, 0, None, "outgoing", "Hey Alice!", None, None,
         None, base_ms + 1000, None, 0, None),
    )
    # Empty body (should be skipped)
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("msg-3", None, 0, None, base_ms + 2000, 0, "conv-alice", base_ms + 2000,
         "+15550001111", "uuid-alice", 1, 0, 0, 0, 0, None, "incoming", "",
         None, None, None, base_ms + 2000, None, 0, None),
    )
    # NULL body (should be skipped)
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("msg-4", None, 0, None, base_ms + 3000, 0, "conv-alice", base_ms + 3000,
         "+15550001111", "uuid-alice", 1, 0, 0, 0, 0, None, "incoming", None,
         None, None, None, base_ms + 3000, None, 0, None),
    )
    # System message (type not in incoming/outgoing — should be skipped)
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("msg-5", None, 0, None, base_ms + 4000, 0, "conv-alice", base_ms + 4000,
         None, None, 0, 0, 0, 0, 0, None, "keychange", "Safety number changed.",
         None, None, None, base_ms + 4000, None, 0, None),
    )
    # Group message
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("msg-6", None, 0, None, base_ms + 5000, 0, "conv-group", base_ms + 5000,
         "+15550002222", "uuid-bob", 1, 0, 0, 0, 0, None, "incoming", "What are we reading?",
         None, None, None, base_ms + 5000, None, 0, None),
    )

    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def signal_db_path(tmp_path) -> str:
    path = str(tmp_path / "db.sqlite")
    _make_signal_db(path)
    return path


@pytest.fixture
def signal_config_path(tmp_path) -> str:
    """Write a fake config.json with a dummy hex key."""
    config = {"key": "a" * 64}  # 64-char hex key
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return str(path)


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
# Helper: patch _open_signal_db to return a plain sqlite3 connection
# ---------------------------------------------------------------------------


def _make_plain_conn(db_path: str):
    """Open the synthetic DB as a plain sqlite3 connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Unit tests — OS detection & path logic
# ---------------------------------------------------------------------------


def test_get_signal_dir_linux():
    with patch.object(sys, "platform", "linux"):
        d = _get_signal_dir()
    assert str(d).endswith(".config/Signal")


def test_get_signal_dir_macos():
    with patch.object(sys, "platform", "darwin"):
        d = _get_signal_dir()
    assert "Application Support/Signal" in str(d)


def test_get_signal_dir_windows():
    with patch.dict(os.environ, {"APPDATA": "C:\\Users\\Test\\AppData\\Roaming"}):
        with patch.object(sys, "platform", "win32"):
            d = _get_signal_dir()
    assert "Signal" in str(d)


def test_get_db_path_default_linux():
    with patch.object(sys, "platform", "linux"):
        os.environ.pop("SIGNAL_DB_PATH", None)
        path = _get_db_path()
    assert path.endswith(".config/Signal/sql/db.sqlite")


def test_get_db_path_from_env(tmp_path):
    custom = str(tmp_path / "signal.db")
    with patch.dict(os.environ, {"SIGNAL_DB_PATH": custom}):
        assert _get_db_path() == custom


def test_get_config_path_default_linux():
    with patch.object(sys, "platform", "linux"):
        os.environ.pop("SIGNAL_CONFIG_PATH", None)
        path = _get_config_path()
    assert path.endswith(".config/Signal/config.json")


def test_get_config_path_from_env(tmp_path):
    custom = str(tmp_path / "config.json")
    with patch.dict(os.environ, {"SIGNAL_CONFIG_PATH": custom}):
        assert _get_config_path() == custom


# ---------------------------------------------------------------------------
# Unit tests — key extraction
# ---------------------------------------------------------------------------


def test_read_key_success(tmp_path):
    key = "deadbeef" * 8  # 64-char hex
    config = {"key": key, "other_field": "ignored"}
    config_path = str(tmp_path / "config.json")
    Path(config_path).write_text(json.dumps(config))
    assert _read_key(config_path) == key


def test_read_key_missing_key_field(tmp_path):
    config = {"other_field": "no key here"}
    config_path = str(tmp_path / "config.json")
    Path(config_path).write_text(json.dumps(config))
    with pytest.raises(RuntimeError, match="No 'key' field"):
        _read_key(config_path)


def test_read_key_file_not_found():
    with pytest.raises(FileNotFoundError):
        _read_key("/nonexistent/path/config.json")


# ---------------------------------------------------------------------------
# Unit tests — missing backend returns clear error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_signal_no_backend_returns_error(mem_db, embedder):
    """When neither pysqlcipher3 nor sqlcipher CLI is available, return clear error."""
    with (
        patch("memoreei.connectors.signal_connector._has_pysqlcipher3", return_value=False),
        patch("memoreei.connectors.signal_connector._has_sqlcipher_cli", return_value=False),
    ):
        result = await sync_signal(db=mem_db, embedder=embedder)
    assert "error" in result
    assert result["synced"] == 0
    assert "pysqlcipher3" in result["error"]


# ---------------------------------------------------------------------------
# Integration tests — parsing with mock Signal DB
# ---------------------------------------------------------------------------


def _patch_open_signal_db(signal_db_path: str):
    """Return a context manager that patches _open_signal_db.

    Uses side_effect so each call gets a fresh connection (needed for
    idempotency tests where sync() is called twice).
    """
    return patch(
        "memoreei.connectors.signal_connector._open_signal_db",
        side_effect=lambda db_path, key: (_make_plain_conn(signal_db_path), None),
    )


@pytest.mark.asyncio
async def test_sync_ingests_messages(mem_db, embedder, signal_db_path, signal_config_path):
    connector = SignalConnector(
        db=mem_db, embedder=embedder,
        db_path=signal_db_path, config_path=signal_config_path,
    )
    with _patch_open_signal_db(signal_db_path):
        count = await connector.sync()

    # msg-1 (incoming), msg-2 (outgoing), msg-6 (group incoming) = 3
    # msg-3 (empty), msg-4 (null), msg-5 (keychange) are skipped
    assert count == 3


@pytest.mark.asyncio
async def test_sync_source_labels(mem_db, embedder, signal_db_path, signal_config_path):
    connector = SignalConnector(
        db=mem_db, embedder=embedder,
        db_path=signal_db_path, config_path=signal_config_path,
    )
    with _patch_open_signal_db(signal_db_path):
        await connector.sync()

    sources = await mem_db.list_sources()
    assert f"{SOURCE_PREFIX}:conv-alice" in sources
    assert f"{SOURCE_PREFIX}:conv-group" in sources
    assert sources[f"{SOURCE_PREFIX}:conv-alice"] == 2
    assert sources[f"{SOURCE_PREFIX}:conv-group"] == 1


@pytest.mark.asyncio
async def test_sync_sender_attribution(mem_db, embedder, signal_db_path, signal_config_path):
    connector = SignalConnector(
        db=mem_db, embedder=embedder,
        db_path=signal_db_path, config_path=signal_config_path,
    )
    with _patch_open_signal_db(signal_db_path):
        await connector.sync()

    results = await mem_db.search_fts("Hello", limit=5)
    assert len(results) == 1
    assert "+15550001111: Hello!" in results[0].content

    results = await mem_db.search_fts("Hey Alice", limit=5)
    assert len(results) == 1
    assert "me: Hey Alice!" in results[0].content


@pytest.mark.asyncio
async def test_sync_timestamp_ms_to_seconds(mem_db, embedder, signal_db_path, signal_config_path):
    connector = SignalConnector(
        db=mem_db, embedder=embedder,
        db_path=signal_db_path, config_path=signal_config_path,
    )
    with _patch_open_signal_db(signal_db_path):
        await connector.sync()

    results = await mem_db.search_fts("Hello", limit=1)
    # sent_at = 1700000000000ms → ts = 1700000000s
    assert results[0].ts == 1700000000


@pytest.mark.asyncio
async def test_sync_conversation_filter(mem_db, embedder, signal_db_path, signal_config_path):
    connector = SignalConnector(
        db=mem_db, embedder=embedder,
        db_path=signal_db_path, config_path=signal_config_path,
    )
    with _patch_open_signal_db(signal_db_path):
        count = await connector.sync(conversation_id="conv-alice")

    assert count == 2
    sources = await mem_db.list_sources()
    assert f"{SOURCE_PREFIX}:conv-alice" in sources
    assert f"{SOURCE_PREFIX}:conv-group" not in sources


@pytest.mark.asyncio
async def test_sync_conversation_filter_by_name(mem_db, embedder, signal_db_path, signal_config_path):
    connector = SignalConnector(
        db=mem_db, embedder=embedder,
        db_path=signal_db_path, config_path=signal_config_path,
    )
    with _patch_open_signal_db(signal_db_path):
        count = await connector.sync(conversation_id="Book Club")

    assert count == 1
    sources = await mem_db.list_sources()
    assert f"{SOURCE_PREFIX}:conv-group" in sources


# ---------------------------------------------------------------------------
# Checkpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_set_and_get(mem_db):
    await mem_db.set_signal_checkpoint("conv-1", 42)
    result = await mem_db.get_signal_checkpoint("conv-1")
    assert result == 42


@pytest.mark.asyncio
async def test_checkpoint_update(mem_db):
    await mem_db.set_signal_checkpoint("conv-1", 10)
    await mem_db.set_signal_checkpoint("conv-1", 99)
    assert await mem_db.get_signal_checkpoint("conv-1") == 99


@pytest.mark.asyncio
async def test_checkpoint_none_for_unknown(mem_db):
    result = await mem_db.get_signal_checkpoint("nonexistent-conv")
    assert result is None


@pytest.mark.asyncio
async def test_sync_idempotent(mem_db, embedder, signal_db_path, signal_config_path):
    """Second sync should return 0 new messages (checkpoint prevents re-ingestion)."""
    connector = SignalConnector(
        db=mem_db, embedder=embedder,
        db_path=signal_db_path, config_path=signal_config_path,
    )
    with _patch_open_signal_db(signal_db_path):
        first = await connector.sync()
        second = await connector.sync()

    assert first == 3
    assert second == 0


@pytest.mark.asyncio
async def test_sync_incremental_new_message(mem_db, embedder, tmp_path, signal_config_path):
    """Messages added after first sync are picked up on the second sync."""
    db_path = str(tmp_path / "signal_inc.db")
    _make_signal_db(db_path)

    connector = SignalConnector(
        db=mem_db, embedder=embedder,
        db_path=db_path, config_path=signal_config_path,
    )
    with _patch_open_signal_db(db_path):
        first = await connector.sync()

    assert first == 3

    # Add a new message to conv-alice
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("msg-new", None, 0, None, 1700000010000, 0, "conv-alice", 1700000010000,
         "+15550001111", "uuid-alice", 1, 0, 0, 0, 0, None, "incoming", "New message!",
         None, None, None, 1700000010000, None, 0, None),
    )
    conn.commit()
    conn.close()

    with _patch_open_signal_db(db_path):
        second = await connector.sync()

    assert second == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_signal_config_not_found_returns_error(mem_db, embedder, signal_db_path):
    """Missing config.json returns error dict, not exception."""
    with (
        patch("memoreei.connectors.signal_connector._has_pysqlcipher3", return_value=True),
        patch("memoreei.connectors.signal_connector._has_sqlcipher_cli", return_value=False),
    ):
        result = await sync_signal(
            db=mem_db,
            embedder=embedder,
            conversation_id=None,
        )
    # config.json at default path doesn't exist on this machine
    assert "error" in result
    assert result["synced"] == 0
