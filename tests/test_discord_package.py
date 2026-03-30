"""Tests for the Discord Data Package (GDPR export) connector."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from memoreei.connectors.discord_package_connector import (
    _load_index,
    _parse_discord_timestamp,
    import_discord_package,
    parse_discord_package,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_package(tmp_path: Path, channels: dict[str, list[dict]], index: dict[str, str] | None = None) -> Path:
    """Create a minimal fake Discord data package in tmp_path.

    Args:
        channels: {channel_dir_name: [msg_dict, ...]}  e.g. {"c111": [{...}]}
        index:    optional custom index.json content (defaults to {id: id})
    """
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir(parents=True)

    # Build default index from channel directory names
    if index is None:
        index = {}
        for dir_name in channels:
            chan_id = dir_name[1:] if dir_name.startswith("c") else dir_name
            index[chan_id] = chan_id

    (messages_dir / "index.json").write_text(json.dumps(index), encoding="utf-8")

    for dir_name, msgs in channels.items():
        ch_dir = messages_dir / dir_name
        ch_dir.mkdir()
        (ch_dir / "messages.json").write_text(json.dumps(msgs), encoding="utf-8")

    return tmp_path


def _make_msg(id: str = "100", ts: str = "2024-01-15 12:00:00+00:00", contents: str = "hello", attachments: str = "") -> dict:
    return {"ID": id, "Timestamp": ts, "Contents": contents, "Attachments": attachments}


# ---------------------------------------------------------------------------
# Unit: timestamp parsing
# ---------------------------------------------------------------------------

def test_parse_timestamp_iso_with_offset():
    ts = _parse_discord_timestamp("2024-01-15 12:00:00+00:00")
    assert ts == 1705320000


def test_parse_timestamp_empty_string():
    ts = _parse_discord_timestamp("")
    assert isinstance(ts, int)
    assert ts > 0


def test_parse_timestamp_invalid():
    ts = _parse_discord_timestamp("not-a-date")
    assert isinstance(ts, int)


# ---------------------------------------------------------------------------
# Unit: index loading
# ---------------------------------------------------------------------------

def test_load_index(tmp_path):
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir()
    (messages_dir / "index.json").write_text(
        json.dumps({"111": "general", "222": "random"}), encoding="utf-8"
    )
    index = _load_index(tmp_path)
    assert index == {"111": "general", "222": "random"}


def test_load_index_missing_file(tmp_path):
    index = _load_index(tmp_path)
    assert index == {}


def test_load_index_corrupt_json(tmp_path):
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir()
    (messages_dir / "index.json").write_text("NOT JSON", encoding="utf-8")
    index = _load_index(tmp_path)
    assert index == {}


# ---------------------------------------------------------------------------
# Unit: parse_discord_package (iterator)
# ---------------------------------------------------------------------------

def test_parse_basic_messages(tmp_path):
    pkg = _make_package(
        tmp_path,
        channels={"c111": [_make_msg("1", contents="First message"), _make_msg("2", contents="Second message")]},
        index={"111": "general"},
    )
    items = list(parse_discord_package(pkg))
    assert len(items) == 2
    assert "First message" in items[0].content
    assert "Second message" in items[1].content


def test_parse_channel_name_from_index(tmp_path):
    pkg = _make_package(
        tmp_path,
        channels={"c999": [_make_msg("10", contents="hi")]},
        index={"999": "my-cool-channel"},
    )
    items = list(parse_discord_package(pkg))
    assert len(items) == 1
    assert items[0].source == "discord-export:my-cool-channel"


def test_parse_channel_id_fallback_when_not_in_index(tmp_path):
    pkg = _make_package(
        tmp_path,
        channels={"c777": [_make_msg("5", contents="test")]},
        index={},
    )
    items = list(parse_discord_package(pkg))
    assert len(items) == 1
    # Should fall back to channel_id
    assert "777" in items[0].source


def test_parse_source_id_format(tmp_path):
    pkg = _make_package(
        tmp_path,
        channels={"c111": [_make_msg("42")]},
        index={"111": "general"},
    )
    items = list(parse_discord_package(pkg))
    assert items[0].source_id == "discord-export:111:42"


def test_parse_skips_empty_content(tmp_path):
    pkg = _make_package(
        tmp_path,
        channels={"c111": [
            _make_msg("1", contents=""),
            _make_msg("2", contents="real message"),
        ]},
        index={"111": "general"},
    )
    items = list(parse_discord_package(pkg))
    assert len(items) == 1
    assert "real message" in items[0].content


def test_parse_attachment_only_message(tmp_path):
    pkg = _make_package(
        tmp_path,
        channels={"c111": [_make_msg("1", contents="", attachments="image.png")]},
        index={"111": "general"},
    )
    items = list(parse_discord_package(pkg))
    assert len(items) == 1
    assert "attachment" in items[0].content


def test_parse_multiple_channels(tmp_path):
    pkg = _make_package(
        tmp_path,
        channels={
            "c111": [_make_msg("1", contents="msg in general")],
            "c222": [_make_msg("2", contents="msg in random")],
        },
        index={"111": "general", "222": "random"},
    )
    items = list(parse_discord_package(pkg))
    assert len(items) == 2
    sources = {i.source for i in items}
    assert "discord-export:general" in sources
    assert "discord-export:random" in sources


def test_parse_nonexistent_path(tmp_path):
    items = list(parse_discord_package(tmp_path / "does_not_exist"))
    assert items == []


def test_parse_zip_file(tmp_path):
    """parse_discord_package should accept a .zip file and extract it."""
    pkg_dir = tmp_path / "pkg"
    _make_package(
        pkg_dir,
        channels={"c111": [_make_msg("1", contents="zipped message")]},
        index={"111": "general"},
    )
    zip_path = tmp_path / "export.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in pkg_dir.rglob("*"):
            zf.write(f, f.relative_to(pkg_dir))

    items = list(parse_discord_package(zip_path))
    assert len(items) == 1
    assert "zipped message" in items[0].content


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_dedup_within_single_parse(tmp_path):
    """Duplicate message IDs within a channel should yield only one item."""
    pkg = _make_package(
        tmp_path,
        channels={"c111": [
            _make_msg("1", contents="first"),
            _make_msg("1", contents="duplicate"),
        ]},
        index={"111": "general"},
    )
    items = list(parse_discord_package(pkg))
    assert len(items) == 1


def test_dedup_via_existing_ids(tmp_path):
    """Messages whose source_ids are in existing_ids should be skipped."""
    pkg = _make_package(
        tmp_path,
        channels={"c111": [
            _make_msg("1", contents="already imported"),
            _make_msg("2", contents="new message"),
        ]},
        index={"111": "general"},
    )
    existing = {"discord-export:111:1"}
    items = list(parse_discord_package(pkg, existing_ids=existing))
    assert len(items) == 1
    assert "new message" in items[0].content


# ---------------------------------------------------------------------------
# Integration: import_discord_package (with mock DB + embedder)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_discord_package_success(tmp_path, temp_db, mock_embedder):
    pkg = _make_package(
        tmp_path / "pkg",
        channels={"c111": [
            _make_msg("1", contents="Hello from Discord"),
            _make_msg("2", contents="Another message"),
        ]},
        index={"111": "general"},
    )
    result = await import_discord_package(str(pkg), db=temp_db, embedder=mock_embedder)
    assert result["ingested"] == 2
    assert result["channels"] == 1
    assert "errors" not in result


@pytest.mark.asyncio
async def test_import_discord_package_dedup_on_reimport(tmp_path, temp_db, mock_embedder):
    """Re-importing the same package should not increase message count."""
    pkg = _make_package(
        tmp_path / "pkg",
        channels={"c111": [_make_msg("1", contents="once")]},
        index={"111": "general"},
    )
    r1 = await import_discord_package(str(pkg), db=temp_db, embedder=mock_embedder)
    assert r1["ingested"] == 1

    r2 = await import_discord_package(str(pkg), db=temp_db, embedder=mock_embedder)
    assert r2["ingested"] == 0


@pytest.mark.asyncio
async def test_import_discord_package_missing_path(tmp_path, temp_db, mock_embedder):
    result = await import_discord_package(str(tmp_path / "no_such_dir"), db=temp_db, embedder=mock_embedder)
    assert "error" in result
    assert result["ingested"] == 0


@pytest.mark.asyncio
async def test_import_discord_package_multiple_channels(tmp_path, temp_db, mock_embedder):
    pkg = _make_package(
        tmp_path / "pkg",
        channels={
            "c111": [_make_msg("1", contents="ch1 msg1"), _make_msg("2", contents="ch1 msg2")],
            "c222": [_make_msg("3", contents="ch2 msg1")],
        },
        index={"111": "general", "222": "random"},
    )
    result = await import_discord_package(str(pkg), db=temp_db, embedder=mock_embedder)
    assert result["ingested"] == 3
    assert result["channels"] == 2
