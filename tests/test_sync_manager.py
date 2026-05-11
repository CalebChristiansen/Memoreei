"""Tests for SyncManager — sync_source dispatch and refresh_all."""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from memoreei.sync_manager import SyncManager


class _FakeTools:
    """Minimal MemoryTools stand-in with controllable results."""

    def __init__(self, imessage_result: dict | None = None):
        self._imessage_result = imessage_result or {"synced": 5}

    async def sync_imessage_tool(self, chat_name: str | None = None) -> dict:
        return self._imessage_result

    async def sync_discord_tool(self, channel_id=None):
        return {"synced": 0}

    async def sync_telegram_tool(self, chat_id=None):
        return {"synced": 0}

    async def sync_matrix_tool(self, room_id=None):
        return {"synced": 0}

    async def sync_slack_tool(self, channel_id=None):
        return {"synced": 0}

    async def sync_email_tool(self, folder="INBOX", max_emails=200):
        return {"synced": 0}

    async def sync_mastodon_tool(self, instance=None, hashtag=None, access_token=None):
        return {"synced": 0}


# ---------------------------------------------------------------------------
# sync_source — iMessage dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_source_imessage_returns_count():
    manager = SyncManager()
    tools = _FakeTools(imessage_result={"synced": 42})
    count = await manager.sync_source("imessage", tools)
    assert count == 42


@pytest.mark.asyncio
async def test_sync_source_imessage_error_dict_returns_zero(capsys):
    manager = SyncManager()
    tools = _FakeTools(imessage_result={"synced": 0, "error": "Operation not permitted"})
    count = await manager.sync_source("imessage", tools)
    assert count == 0


@pytest.mark.asyncio
async def test_sync_source_imessage_error_logged_to_stderr(capsys):
    manager = SyncManager()
    tools = _FakeTools(imessage_result={"synced": 0, "error": "Full Disk Access required"})
    await manager.sync_source("imessage", tools)
    captured = capsys.readouterr()
    assert "Full Disk Access required" in captured.err


@pytest.mark.asyncio
async def test_sync_source_imessage_exception_returns_zero(capsys):
    """If sync_imessage_tool raises unexpectedly, returns 0 without propagating."""
    manager = SyncManager()

    class RaisingTools(_FakeTools):
        async def sync_imessage_tool(self, chat_name=None):
            raise RuntimeError("unexpected crash")

    count = await manager.sync_source("imessage", RaisingTools())
    assert count == 0
    assert "unexpected crash" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_sync_source_unknown_returns_zero(capsys):
    manager = SyncManager()
    count = await manager.sync_source("nonexistent_source", _FakeTools())
    assert count == 0


# ---------------------------------------------------------------------------
# refresh_all — iMessage included when configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_all_includes_imessage_when_configured():
    manager = SyncManager()
    tools = _FakeTools(imessage_result={"synced": 7})

    from memoreei.config import Config
    cfg = Config(imessage_db_path="/some/chat.db")

    with patch.object(sys, "platform", "darwin"):
        with patch("memoreei.sync_manager.SyncManager.sync_source", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = 7
            with patch("memoreei.config.Config.configured_connectors", return_value=["imessage"]):
                total = await manager.refresh_all(tools)

    mock_sync.assert_called_once_with("imessage", tools)
    assert total == 7


@pytest.mark.asyncio
async def test_refresh_all_excludes_imessage_when_not_configured():
    manager = SyncManager()
    tools = _FakeTools()

    with patch("memoreei.config.Config.configured_connectors", return_value=[]):
        with patch.object(manager, "sync_source", new_callable=AsyncMock) as mock_sync:
            total = await manager.refresh_all(tools)

    mock_sync.assert_not_called()
    assert total == 0
