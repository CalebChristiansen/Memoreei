from __future__ import annotations

import json
from pathlib import Path

import pytest

from memoreei.connectors.messenger_connector import parse_messenger_export, _fix_encoding


def _make_export(tmp_path: Path, conversations: dict[str, list[dict]]) -> Path:
    """Create a minimal Messenger export folder structure."""
    inbox = tmp_path / "messages" / "inbox"
    for conv_name, parts in conversations.items():
        conv_dir = inbox / conv_name
        conv_dir.mkdir(parents=True, exist_ok=True)
        for idx, part_data in enumerate(parts, start=1):
            (conv_dir / f"message_{idx}.json").write_text(
                json.dumps(part_data), encoding="utf-8"
            )
    return tmp_path


def _simple_part(participants: list[str], messages: list[dict]) -> dict:
    return {
        "participants": [{"name": p} for p in participants],
        "messages": messages,
    }


def _msg(
    sender: str,
    content: str,
    timestamp_ms: int = 1_700_000_000_000,
    msg_type: str = "Generic",
) -> dict:
    return {
        "sender_name": sender,
        "content": content,
        "timestamp_ms": timestamp_ms,
        "type": msg_type,
    }


# ── Basic parsing ─────────────────────────────────────────────────────────────

def test_parses_basic_messages(tmp_path: Path) -> None:
    export = _make_export(tmp_path, {
        "alice_xyz": [_simple_part(
            ["You", "Alice"],
            [
                _msg("Alice", "Hey there!", 1_700_000_000_000),
                _msg("You", "Hi!", 1_700_000_001_000),
            ],
        )]
    })
    items = parse_messenger_export(export)
    assert len(items) == 2
    assert any("Alice: Hey there!" in i.content for i in items)
    assert any("You: Hi!" in i.content for i in items)


def test_source_format(tmp_path: Path) -> None:
    export = _make_export(tmp_path, {
        "bob_abc": [_simple_part(["You", "Bob"], [_msg("Bob", "Hello")])]
    })
    items = parse_messenger_export(export)
    assert all(i.source == "messenger:bob_abc" for i in items)


def test_timestamp_conversion(tmp_path: Path) -> None:
    ts_ms = 1_700_000_000_000
    export = _make_export(tmp_path, {
        "conv": [_simple_part(["A", "B"], [_msg("A", "x", ts_ms)])]
    })
    items = parse_messenger_export(export)
    assert items[0].ts == ts_ms // 1000


# ── Encoding quirk ────────────────────────────────────────────────────────────

def test_fix_encoding_roundtrip() -> None:
    mangled = "caf\u00c3\u00a9"
    assert _fix_encoding(mangled) == "café"


def test_encoding_applied_to_content(tmp_path: Path) -> None:
    mangled_content = "h\u00c3\u00a9llo"
    mangled_sender = "Mar\u00c3\u00ada"

    inbox = tmp_path / "messages" / "inbox" / "conv"
    inbox.mkdir(parents=True)
    data = {
        "participants": [{"name": mangled_sender}, {"name": "You"}],
        "messages": [
            {
                "sender_name": mangled_sender,
                "content": mangled_content,
                "timestamp_ms": 1_700_000_000_000,
                "type": "Generic",
            }
        ],
    }
    (inbox / "message_1.json").write_text(json.dumps(data), encoding="utf-8")

    items = parse_messenger_export(tmp_path)
    assert len(items) == 1
    assert "María: héllo" in items[0].content


# ── Multi-part handling ───────────────────────────────────────────────────────

def test_multi_part_messages_combined(tmp_path: Path) -> None:
    part1 = _simple_part(["You", "Alice"], [_msg("Alice", "Part1 msg", 1_700_000_000_000)])
    part2 = _simple_part(["You", "Alice"], [_msg("You", "Part2 msg", 1_700_000_001_000)])
    export = _make_export(tmp_path, {"conv": [part1, part2]})

    items = parse_messenger_export(export)
    assert len(items) == 2
    contents = [i.content for i in items]
    assert any("Part1 msg" in c for c in contents)
    assert any("Part2 msg" in c for c in contents)


def test_multi_part_participants_from_first_file(tmp_path: Path) -> None:
    part1 = _simple_part(["You", "Alice"], [_msg("Alice", "hello", 1_700_000_000_000)])
    part2 = {"messages": [_msg("You", "hi", 1_700_000_001_000)]}  # no participants key

    inbox = tmp_path / "messages" / "inbox" / "conv"
    inbox.mkdir(parents=True)
    (inbox / "message_1.json").write_text(json.dumps(part1), encoding="utf-8")
    (inbox / "message_2.json").write_text(json.dumps(part2), encoding="utf-8")

    items = parse_messenger_export(tmp_path)
    assert len(items) == 2
    for item in items:
        assert "Alice" in item.metadata["chat_participants"]


# ── Participant extraction ─────────────────────────────────────────────────────

def test_participants_in_metadata(tmp_path: Path) -> None:
    export = _make_export(tmp_path, {
        "conv": [_simple_part(["You", "Alice", "Bob"], [_msg("Alice", "hey")])]
    })
    items = parse_messenger_export(export)
    assert set(items[0].metadata["chat_participants"]) == {"You", "Alice", "Bob"}


def test_sender_in_participants_field(tmp_path: Path) -> None:
    export = _make_export(tmp_path, {
        "conv": [_simple_part(["You", "Alice"], [_msg("Alice", "hey")])]
    })
    items = parse_messenger_export(export)
    assert "Alice" in items[0].participants


# ── Group chats ───────────────────────────────────────────────────────────────

def test_group_chat_multiple_participants(tmp_path: Path) -> None:
    export = _make_export(tmp_path, {
        "groupchat_xyz": [_simple_part(
            ["You", "Alice", "Bob", "Carol"],
            [
                _msg("Alice", "hey everyone"),
                _msg("Bob", "hi all"),
                _msg("Carol", "hello"),
            ],
        )]
    })
    items = parse_messenger_export(export)
    assert len(items) == 3
    for item in items:
        assert len(item.metadata["chat_participants"]) == 4


# ── Media / non-text messages ──────────────────────────────────────────────────

def test_media_message_no_content_marked_as_media(tmp_path: Path) -> None:
    msg = {"sender_name": "Alice", "timestamp_ms": 1_700_000_000_000, "type": "Generic", "photos": [{"uri": "photo.jpg"}]}
    export = _make_export(tmp_path, {
        "conv": [_simple_part(["You", "Alice"], [msg])]
    })
    items = parse_messenger_export(export)
    assert len(items) == 1
    assert "[media]" in items[0].content
    assert items[0].metadata.get("media") is True


def test_sticker_message(tmp_path: Path) -> None:
    msg = {
        "sender_name": "Alice",
        "timestamp_ms": 1_700_000_000_000,
        "type": "Generic",
        "sticker": {"uri": "sticker.png"},
    }
    export = _make_export(tmp_path, {
        "conv": [_simple_part(["You", "Alice"], [msg])]
    })
    items = parse_messenger_export(export)
    assert len(items) == 1
    assert "[sticker]" in items[0].content


def test_share_type_message(tmp_path: Path) -> None:
    msg = {
        "sender_name": "Alice",
        "timestamp_ms": 1_700_000_000_000,
        "type": "Share",
        "share": {"link": "https://example.com"},
    }
    export = _make_export(tmp_path, {
        "conv": [_simple_part(["You", "Alice"], [msg])]
    })
    items = parse_messenger_export(export)
    assert len(items) == 1
    assert "[share]" in items[0].content


def test_unknown_type_skipped(tmp_path: Path) -> None:
    msg = {
        "sender_name": "Alice",
        "timestamp_ms": 1_700_000_000_000,
        "type": "Unsubscribe",
    }
    export = _make_export(tmp_path, {
        "conv": [_simple_part(["You", "Alice"], [msg])]
    })
    items = parse_messenger_export(export)
    assert len(items) == 0


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_missing_inbox_returns_empty(tmp_path: Path) -> None:
    items = parse_messenger_export(tmp_path)
    assert items == []


def test_empty_conversation_dir(tmp_path: Path) -> None:
    inbox = tmp_path / "messages" / "inbox" / "empty_conv"
    inbox.mkdir(parents=True)
    items = parse_messenger_export(tmp_path)
    assert items == []


def test_multiple_conversations(tmp_path: Path) -> None:
    export = _make_export(tmp_path, {
        "alice_1": [_simple_part(["You", "Alice"], [_msg("Alice", "hi")])],
        "bob_2": [_simple_part(["You", "Bob"], [_msg("Bob", "hello")])],
    })
    items = parse_messenger_export(export)
    assert len(items) == 2
    sources = {i.source for i in items}
    assert sources == {"messenger:alice_1", "messenger:bob_2"}
