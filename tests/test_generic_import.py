"""Tests for the generic JSON and CSV importers."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from memoreei.connectors.generic_connector import (
    _parse_timestamp,
    import_csv,
    import_json,
)
from memoreei.tools.memory_tools import MemoryTools


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def test_parse_timestamp_unix_seconds():
    ts = 1700000000
    assert _parse_timestamp(ts) == ts


def test_parse_timestamp_unix_millis():
    ts_ms = 1700000000000
    assert _parse_timestamp(ts_ms) == 1700000000


def test_parse_timestamp_iso8601():
    result = _parse_timestamp("2024-01-15T12:30:00Z")
    assert result == pytest.approx(1705320600, abs=86400)


def test_parse_timestamp_iso8601_with_offset():
    result = _parse_timestamp("2024-01-15T12:30:00+00:00")
    assert result == pytest.approx(1705320600, abs=86400)


def test_parse_timestamp_date_only():
    result = _parse_timestamp("2024-01-15")
    assert isinstance(result, int)
    assert result > 0


def test_parse_timestamp_none_returns_now():
    before = int(time.time())
    result = _parse_timestamp(None)
    after = int(time.time())
    assert before <= result <= after


def test_parse_timestamp_empty_string():
    before = int(time.time())
    result = _parse_timestamp("")
    after = int(time.time())
    assert before <= result <= after


def test_parse_timestamp_numeric_string():
    assert _parse_timestamp("1700000000") == 1700000000


# ---------------------------------------------------------------------------
# JSON import — unit tests (no DB)
# ---------------------------------------------------------------------------

SAMPLE_JSON_ARRAY = [
    {"message": "Hello world", "author": "Alice", "date": "2024-01-01T10:00:00Z"},
    {"message": "How are you?", "author": "Bob", "date": "2024-01-01T10:01:00Z"},
    {"message": "Empty", "author": "Charlie", "date": "2024-01-01T10:02:00Z"},
]

SAMPLE_JSONL = "\n".join([
    '{"body": "First line", "from": "Alice", "ts": 1700000000}',
    '{"body": "Second line", "from": "Bob", "ts": 1700000060}',
    "",  # blank line — should be skipped
    '{"body": "Third line", "from": "Charlie", "ts": 1700000120}',
])


def test_import_json_array(tmp_path):
    f = tmp_path / "messages.json"
    f.write_text(json.dumps(SAMPLE_JSON_ARRAY), encoding="utf-8")

    items, errors = import_json(
        f,
        field_mapping={"content": "message", "sender": "author", "timestamp": "date"},
        source_label="test-json",
    )

    assert len(errors) == 0
    assert len(items) == 3
    assert items[0].content == "Alice: Hello world"
    assert items[1].content == "Bob: How are you?"
    assert items[0].source == "test-json"
    assert items[0].participants == ["Alice"]


def test_import_json_no_sender(tmp_path):
    data = [{"text": "msg1"}, {"text": "msg2"}]
    f = tmp_path / "data.json"
    f.write_text(json.dumps(data), encoding="utf-8")

    items, errors = import_json(f, field_mapping={"content": "text"})

    assert len(items) == 2
    assert items[0].content == "msg1"
    assert items[0].participants == []


def test_import_jsonlines(tmp_path):
    f = tmp_path / "messages.jsonl"
    f.write_text(SAMPLE_JSONL, encoding="utf-8")

    items, errors = import_json(
        f,
        field_mapping={"content": "body", "sender": "from", "timestamp": "ts"},
        source_label="jsonl-test",
    )

    assert len(items) == 3
    assert items[0].content == "Alice: First line"
    assert items[0].ts == 1700000000


def test_import_json_missing_content_field(tmp_path):
    data = [{"message": "hello"}]
    f = tmp_path / "data.json"
    f.write_text(json.dumps(data), encoding="utf-8")

    items, errors = import_json(f, field_mapping={"content": "body"})

    assert len(items) == 0
    assert any("body" in e for e in errors)


def test_import_json_file_not_found():
    items, errors = import_json(
        "/nonexistent/file.json",
        field_mapping={"content": "message"},
    )
    assert len(items) == 0
    assert any("not found" in e.lower() for e in errors)


def test_import_json_empty_content_rows_skipped(tmp_path):
    data = [
        {"msg": "hello"},
        {"msg": ""},
        {"msg": None},
        {"msg": "world"},
    ]
    f = tmp_path / "data.json"
    f.write_text(json.dumps(data), encoding="utf-8")

    items, _ = import_json(f, field_mapping={"content": "msg"})
    assert len(items) == 2


def test_import_json_no_content_mapping(tmp_path):
    f = tmp_path / "data.json"
    f.write_text(json.dumps([{"x": "y"}]), encoding="utf-8")

    items, errors = import_json(f, field_mapping={"sender": "name"})
    assert len(items) == 0
    assert any("content" in e for e in errors)


def test_import_json_wrapped_object(tmp_path):
    data = {"messages": [{"text": "hi"}, {"text": "bye"}]}
    f = tmp_path / "data.json"
    f.write_text(json.dumps(data), encoding="utf-8")

    items, errors = import_json(f, field_mapping={"content": "text"})
    assert len(items) == 2


# ---------------------------------------------------------------------------
# CSV import — unit tests (no DB)
# ---------------------------------------------------------------------------

SAMPLE_CSV = """from,message,timestamp
Alice,Hello world,2024-01-01T10:00:00Z
Bob,How are you?,2024-01-01T10:01:00Z
Charlie,Fine thanks,2024-01-01T10:02:00Z
"""

SAMPLE_TSV = "sender\tbody\tdate\nAlice\tTab separated\t1700000000\nBob\tSecond row\t1700000060\n"

SAMPLE_SEMICOLON = "name;content;ts\nAlice;Semicolon row;1700000000\n"


def test_import_csv_basic(tmp_path):
    f = tmp_path / "messages.csv"
    f.write_text(SAMPLE_CSV, encoding="utf-8")

    items, errors = import_csv(
        f,
        field_mapping={"content": "message", "sender": "from", "timestamp": "timestamp"},
        source_label="csv-test",
    )

    assert len(errors) == 0
    assert len(items) == 3
    assert items[0].content == "Alice: Hello world"
    assert items[0].source == "csv-test"
    assert items[1].participants == ["Bob"]


def test_import_tsv(tmp_path):
    f = tmp_path / "messages.tsv"
    f.write_text(SAMPLE_TSV, encoding="utf-8")

    items, errors = import_csv(
        f,
        field_mapping={"content": "body", "sender": "sender", "timestamp": "date"},
    )

    assert len(items) == 2
    assert items[0].content == "Alice: Tab separated"
    assert items[0].ts == 1700000000


def test_import_csv_semicolon_delimiter(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text(SAMPLE_SEMICOLON, encoding="utf-8")

    items, errors = import_csv(
        f,
        field_mapping={"content": "content", "sender": "name", "timestamp": "ts"},
    )

    assert len(items) == 1
    assert items[0].content == "Alice: Semicolon row"


def test_import_csv_missing_column(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text(SAMPLE_CSV, encoding="utf-8")

    items, errors = import_csv(f, field_mapping={"content": "body"})

    assert len(items) == 0
    assert any("body" in e for e in errors)


def test_import_csv_file_not_found():
    items, errors = import_csv(
        "/nonexistent/file.csv",
        field_mapping={"content": "message"},
    )
    assert len(items) == 0
    assert any("not found" in e.lower() for e in errors)


def test_import_csv_no_header(tmp_path):
    data = "Alice,Hello world,1700000000\nBob,Goodbye,1700000060\n"
    f = tmp_path / "data.csv"
    f.write_text(data, encoding="utf-8")

    items, errors = import_csv(
        f,
        field_mapping={"sender": "0", "content": "1", "timestamp": "2"},
        has_header=False,
    )

    assert len(items) == 2
    assert items[0].content == "Alice: Hello world"
    assert items[0].ts == 1700000000


def test_import_csv_no_content_mapping(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text(SAMPLE_CSV, encoding="utf-8")

    items, errors = import_csv(f, field_mapping={"sender": "from"})
    assert len(items) == 0
    assert any("content" in e for e in errors)


def test_import_csv_latin1_encoding(tmp_path):
    # Write a file with latin-1 characters
    content = "sender,message\nAlice,Caf\xe9 chat\nBob,na\xefve talk\n"
    f = tmp_path / "latin1.csv"
    f.write_bytes(content.encode("latin-1"))

    items, errors = import_csv(f, field_mapping={"content": "message", "sender": "sender"})
    assert len(items) == 2


# ---------------------------------------------------------------------------
# Integration tests — via MemoryTools (with DB + embedder)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_memory_tools_import_json(temp_db, mock_embedder, tmp_path):
    data = [
        {"text": "Hello", "user": "Alice"},
        {"text": "World", "user": "Bob"},
    ]
    f = tmp_path / "data.json"
    f.write_text(json.dumps(data), encoding="utf-8")

    tools = MemoryTools(db=temp_db, embedder=mock_embedder)
    result = await tools.import_json_file(
        file_path=str(f),
        content_field="text",
        sender_field="user",
        source_label="test-json",
    )

    assert result["ingested"] == 2
    assert result["source"] == "test-json"
    assert "error" not in result


@pytest.mark.asyncio
async def test_memory_tools_import_csv(temp_db, mock_embedder, tmp_path):
    csv_content = "author,body,when\nAlice,Hi there,1700000000\nBob,Hey,1700000060\n"
    f = tmp_path / "messages.csv"
    f.write_text(csv_content, encoding="utf-8")

    tools = MemoryTools(db=temp_db, embedder=mock_embedder)
    result = await tools.import_csv_file(
        file_path=str(f),
        content_column="body",
        sender_column="author",
        timestamp_column="when",
        source_label="test-csv",
    )

    assert result["ingested"] == 2
    assert result["source"] == "test-csv"
    assert "error" not in result


@pytest.mark.asyncio
async def test_memory_tools_import_json_file_not_found(temp_db, mock_embedder):
    tools = MemoryTools(db=temp_db, embedder=mock_embedder)
    result = await tools.import_json_file(
        file_path="/nonexistent/file.json",
        content_field="text",
    )
    assert result["ingested"] == 0
    assert "error" in result


@pytest.mark.asyncio
async def test_memory_tools_import_csv_bad_column(temp_db, mock_embedder, tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("col1,col2\na,b\n", encoding="utf-8")

    tools = MemoryTools(db=temp_db, embedder=mock_embedder)
    result = await tools.import_csv_file(
        file_path=str(f),
        content_column="nonexistent",
    )
    assert result["ingested"] == 0
    assert "error" in result


@pytest.mark.asyncio
async def test_memory_tools_deduplication(temp_db, mock_embedder, tmp_path):
    """Importing the same file twice should not create duplicate rows in the DB."""
    data = [{"msg": "hello"}, {"msg": "world"}]
    f = tmp_path / "data.json"
    f.write_text(json.dumps(data), encoding="utf-8")

    tools = MemoryTools(db=temp_db, embedder=mock_embedder)
    r1 = await tools.import_json_file(str(f), content_field="msg")
    await tools.import_json_file(str(f), content_field="msg")

    assert r1["ingested"] == 2
    # DB should only have 2 unique rows despite double import (ON CONFLICT DO NOTHING)
    sources = await temp_db.list_sources()
    assert sum(sources.values()) == 2
