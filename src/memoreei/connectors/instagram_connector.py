from __future__ import annotations

import json
import time
from pathlib import Path

from ulid import ULID

from memoreei.storage.models import MemoryItem


def _fix_encoding(text: str) -> str:
    """Instagram JSON uses UTF-8 but represents special chars as latin-1 escaped."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def _parse_conversation_dir(conv_dir: Path, conv_name: str) -> list[MemoryItem]:
    """Parse all message_N.json files in a conversation directory."""
    source = f"instagram:{conv_name}"
    items: list[MemoryItem] = []

    # Collect all participants across all parts
    all_participants: list[str] = []

    # Find all message_N.json parts, sorted by number
    msg_files = sorted(
        conv_dir.glob("message_*.json"),
        key=lambda p: int(p.stem.split("_")[1]) if p.stem.split("_")[1].isdigit() else 0,
    )

    if not msg_files:
        return items

    raw_messages: list[dict] = []

    for msg_file in msg_files:
        try:
            data = json.loads(msg_file.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue

        # Extract participants from first file that has them
        if not all_participants and "participants" in data:
            all_participants = [
                _fix_encoding(p.get("name", "")) for p in data.get("participants", [])
            ]

        raw_messages.extend(data.get("messages", []))

    for msg in raw_messages:
        msg_type = msg.get("type", "")
        if msg_type != "Generic":
            continue

        sender_raw = msg.get("sender_name", "")
        sender = _fix_encoding(sender_raw)
        timestamp_ms = msg.get("timestamp_ms", 0)
        ts = int(timestamp_ms // 1000) if timestamp_ms else int(time.time())

        content_raw = msg.get("content")
        if content_raw is None:
            # No text content — media message
            content = "[media]"
            metadata: dict = {"media": True}
        else:
            content = _fix_encoding(content_raw).strip()
            metadata = {}

        if not content:
            continue

        source_id = f"{source}:{ts}:{sender}"

        item = MemoryItem(
            id=str(ULID()),
            source=source,
            source_id=source_id,
            content=f"{sender}: {content}",
            summary=None,
            participants=[sender] if sender else [],
            ts=ts,
            ingested_at=int(time.time()),
            metadata={
                **metadata,
                "conversation": conv_name,
                "chat_participants": all_participants,
            },
            embedding=None,
        )
        items.append(item)

    return items


def parse_instagram_export(data_path: str | Path) -> list[MemoryItem]:
    """Parse an Instagram GDPR data export into a list of MemoryItems.

    Expects the extracted data folder containing your_instagram_activity/messages/inbox/.
    Each conversation subfolder may contain multiple message_N.json files.
    """
    root = Path(data_path)
    inbox = root / "your_instagram_activity" / "messages" / "inbox"

    if not inbox.exists():
        return []

    items: list[MemoryItem] = []

    for conv_dir in sorted(inbox.iterdir()):
        if not conv_dir.is_dir():
            continue
        conv_name = conv_dir.name
        conv_items = _parse_conversation_dir(conv_dir, conv_name)
        items.extend(conv_items)

    return items
