from __future__ import annotations

import json
import time
from pathlib import Path

from ulid import ULID

from memoreei.search.embeddings import EmbeddingProvider
from memoreei.storage.database import Database
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


async def import_instagram(
    data_path: str,
    db: Database,
    embedder: EmbeddingProvider,
    *,
    batch_size: int = 100,
) -> dict:
    """Import an Instagram data download into the memory database.

    Args:
        data_path:  Path to extracted Instagram data folder
                    (the folder containing your_instagram_activity/)
        db:         Open Database instance
        embedder:   Embedding provider for semantic search
        batch_size: Number of messages to embed and insert per batch
    """
    path = Path(data_path)
    if not path.exists():
        return {"error": f"Path not found: {data_path}", "ingested": 0}
    if not path.is_dir():
        return {"error": f"Expected a directory, got: {data_path}", "ingested": 0}

    inbox = path / "your_instagram_activity" / "messages" / "inbox"
    if not inbox.exists():
        return {
            "error": (
                f"No inbox found at {inbox}. "
                "Point data_path at the root of the Instagram export "
                "(the folder that contains your_instagram_activity/)."
            ),
            "ingested": 0,
        }

    before_sources = await db.list_sources()
    before_total = sum(before_sources.values())

    all_items = parse_instagram_export(path)

    errors: list[str] = []
    conversations_seen: set[str] = set()

    for i in range(0, len(all_items), batch_size):
        batch = all_items[i : i + batch_size]
        texts = [item.content for item in batch]
        try:
            embeddings = await embedder.embed(texts)
            for mem, emb in zip(batch, embeddings):
                mem.embedding = emb
            await db.bulk_insert(batch)
            for mem in batch:
                conversations_seen.add(mem.source)
        except Exception as e:
            errors.append(str(e))

    after_sources = await db.list_sources()
    after_total = sum(after_sources.values())
    newly_inserted = after_total - before_total

    result: dict = {
        "ingested": newly_inserted,
        "data_path": str(path),
        "conversations": len(conversations_seen),
    }
    if errors:
        result["errors"] = errors
    return result
