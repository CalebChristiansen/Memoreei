from __future__ import annotations

import pytest
from pathlib import Path

from memoreei.connectors.whatsapp import parse_whatsapp_export


SAMPLES_DIR = Path(__file__).parent.parent / "data" / "samples"


def test_parse_printer_conspiracy():
    path = SAMPLES_DIR / "whatsapp_printer_conspiracy.txt"
    items = parse_whatsapp_export(path)

    assert len(items) >= 30
    assert all(item.content for item in items)
    assert all(item.ts > 0 for item in items)
    assert all(item.source.startswith("whatsapp:") for item in items)

    # Check that at least Alice, Bob, Charlie appear
    all_content = " ".join(item.content for item in items)
    assert "Alice:" in all_content
    assert "Bob:" in all_content
    assert "Charlie:" in all_content


def test_parse_pizza_wars():
    path = SAMPLES_DIR / "whatsapp_pizza_wars.txt"
    items = parse_whatsapp_export(path)
    assert len(items) >= 30
    all_content = " ".join(item.content for item in items)
    assert "pineapple" in all_content.lower()


def test_parse_donut_heist():
    path = SAMPLES_DIR / "whatsapp_donut_heist.txt"
    items = parse_whatsapp_export(path)
    assert len(items) >= 30
    all_content = " ".join(item.content for item in items)
    assert "donut" in all_content.lower()


def test_multiline_message(tmp_path):
    chat = tmp_path / "test.txt"
    chat.write_text(
        "[03/15/26, 09:00:00] Alice: first line\ncontinued on second line\n"
        "[03/15/26, 09:01:00] Bob: next message\n"
    )
    items = parse_whatsapp_export(chat)
    assert len(items) == 2
    assert "second line" in items[0].content


def test_source_id_uniqueness(tmp_path):
    chat = tmp_path / "test.txt"
    chat.write_text(
        "[03/15/26, 09:00:00] Alice: hello\n"
        "[03/15/26, 09:01:00] Bob: world\n"
        "[03/15/26, 09:02:00] Alice: again\n"
    )
    items = parse_whatsapp_export(chat)
    source_ids = [item.source_id for item in items]
    assert len(source_ids) == len(set(source_ids))


def test_participants_recorded(tmp_path):
    chat = tmp_path / "test.txt"
    chat.write_text(
        "[03/15/26, 09:00:00] Alice: hello\n"
        "[03/15/26, 09:01:00] Bob: world\n"
    )
    items = parse_whatsapp_export(chat)
    # Each message has the sender in participants
    assert "Alice" in items[0].participants
    # Chat participants metadata contains all senders
    chat_participants = items[0].metadata.get("chat_participants", [])
    assert "Alice" in chat_participants
    assert "Bob" in chat_participants
