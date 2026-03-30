"""Discord Data Package (GDPR export) connector.

Parses the data export ZIP or extracted folder that users request at
Discord Settings > Privacy & Safety > Request All of My Data.

Package structure:
  messages/
    index.json          — {"channel_id": "channel_name", ...}
    c{channel_id}/
      messages.json     — [{"ID": ..., "Timestamp": ..., "Contents": ..., "Attachments": ...}, ...]
      channel.json      — optional channel metadata
  account/
    user.json           — account owner info (optional)
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from ulid import ULID

from memoreei.search.embeddings import EmbeddingProvider
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem


def _parse_discord_timestamp(ts_str: str) -> int:
    """Parse Discord export timestamp to unix epoch."""
    if not ts_str:
        return int(time.time())
    try:
        # Format: "2024-01-15 12:34:56.789000+00:00"
        dt = datetime.fromisoformat(ts_str.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return int(time.time())


def _load_index(package_dir: Path) -> dict[str, str]:
    """Load the channel ID → name mapping from messages/index.json."""
    index_path = package_dir / "messages" / "index.json"
    if not index_path.exists():
        return {}
    try:
        with index_path.open("r", encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _get_account_name(package_dir: Path) -> str | None:
    """Try to get the account owner's username from user.json."""
    for candidate in [
        package_dir / "account" / "user.json",
        package_dir / "user.json",
    ]:
        if candidate.exists():
            try:
                with candidate.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("username") or data.get("name")
            except (json.JSONDecodeError, OSError, KeyError):
                pass
    return None


def _iter_channel_messages(
    package_dir: Path,
    index: dict[str, str],
) -> Iterator[tuple[str, str, dict]]:
    """Yield (channel_id, channel_name, message_dict) for every message in the package.

    Reads one messages.json at a time to avoid loading the whole export into memory.
    """
    messages_dir = package_dir / "messages"
    if not messages_dir.exists():
        return

    for channel_dir in sorted(messages_dir.iterdir()):
        if not channel_dir.is_dir():
            continue
        messages_file = channel_dir / "messages.json"
        if not messages_file.exists():
            continue

        # Directory names are like "c1234567890" — strip leading 'c'
        dir_name = channel_dir.name
        channel_id = dir_name[1:] if dir_name.startswith("c") else dir_name
        channel_name = index.get(channel_id) or index.get(dir_name) or channel_id

        try:
            with messages_file.open("r", encoding="utf-8", errors="replace") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        # Support both top-level array and {"messages": [...]} wrapper
        if isinstance(data, dict):
            data = data.get("messages", [])
        if not isinstance(data, list):
            continue

        for msg in data:
            if isinstance(msg, dict):
                yield channel_id, channel_name, msg


def parse_discord_package(
    package_path: str | Path,
    *,
    existing_ids: set[str] | None = None,
) -> Iterator[MemoryItem]:
    """Parse a Discord data package and yield MemoryItems.

    Handles both extracted folders and ZIP files.  Yields one item at a time
    so callers can process in batches without holding the full export in RAM.

    Args:
        package_path: Path to extracted folder or .zip file
        existing_ids: Set of source_ids already in the DB (used for in-process dedup).
                      The DB unique constraint handles cross-run dedup automatically.
    """
    path = Path(package_path)
    extract_dir: Path | None = None

    if path.is_file() and path.suffix.lower() == ".zip":
        extract_dir = Path(tempfile.mkdtemp(prefix="memoreei_discord_"))
        with zipfile.ZipFile(path, "r") as zf:
            zf.extractall(extract_dir)
        package_dir = extract_dir
    elif path.is_dir():
        package_dir = path
    else:
        return  # Unknown path type — caller should validate

    try:
        index = _load_index(package_dir)
        account_name = _get_account_name(package_dir)
        seen: set[str] = set(existing_ids) if existing_ids else set()

        for channel_id, channel_name, msg in _iter_channel_messages(package_dir, index):
            msg_id = str(msg.get("ID", "")).strip()
            if not msg_id:
                continue

            source_id = f"discord-export:{channel_id}:{msg_id}"
            if source_id in seen:
                continue
            seen.add(source_id)

            contents = str(msg.get("Contents", "")).strip()
            attachments = str(msg.get("Attachments", "")).strip()

            if not contents and not attachments:
                continue

            if not contents and attachments:
                contents = f"[attachment: {attachments}]"

            ts = _parse_discord_timestamp(str(msg.get("Timestamp", "")))

            # Author: check message dict first, then fall back to account name
            author = (
                str(msg.get("Author", "")).strip()
                or str(msg.get("author", "")).strip()
                or account_name
                or "User"
            )

            source = f"discord-export:{channel_name}"
            display_content = f"{author}: {contents}"

            yield MemoryItem(
                id=str(ULID()),
                source=source,
                source_id=source_id,
                content=display_content,
                summary=None,
                participants=[author],
                ts=ts,
                ingested_at=int(time.time()),
                metadata={
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "message_id": msg_id,
                    "attachments": attachments,
                },
                embedding=None,
            )
    finally:
        if extract_dir is not None:
            shutil.rmtree(extract_dir, ignore_errors=True)


async def import_discord_package(
    package_path: str,
    db: Database,
    embedder: EmbeddingProvider,
    *,
    batch_size: int = 100,
) -> dict:
    """Import a Discord data package into the memory database.

    Args:
        package_path: Path to extracted folder or ZIP file
        db:           Open Database instance
        embedder:     Embedding provider for semantic search
        batch_size:   Number of messages to embed and insert per batch
    """
    path = Path(package_path)
    if not path.exists():
        return {"error": f"Path not found: {package_path}", "ingested": 0}
    if path.is_file() and path.suffix.lower() != ".zip":
        return {"error": f"Expected a directory or .zip file, got: {path.suffix}", "ingested": 0}

    batch: list[MemoryItem] = []
    texts: list[str] = []
    errors: list[str] = []
    channels_seen: set[str] = set()

    # Count rows before import so we can report only *newly* inserted rows.
    before_sources = await db.list_sources()
    before_total = sum(before_sources.values())

    try:
        for item in parse_discord_package(path):
            channels_seen.add(item.source)
            batch.append(item)
            texts.append(item.content)

            if len(batch) >= batch_size:
                try:
                    embeddings = await embedder.embed(texts)
                    for mem, emb in zip(batch, embeddings):
                        mem.embedding = emb
                    await db.bulk_insert(batch)
                except Exception as e:
                    errors.append(str(e))
                finally:
                    batch = []
                    texts = []

        if batch:
            try:
                embeddings = await embedder.embed(texts)
                for mem, emb in zip(batch, embeddings):
                    mem.embedding = emb
                await db.bulk_insert(batch)
            except Exception as e:
                errors.append(str(e))

    except Exception as e:
        errors.append(str(e))

    after_sources = await db.list_sources()
    after_total = sum(after_sources.values())
    newly_inserted = after_total - before_total

    result: dict = {
        "ingested": newly_inserted,
        "package_path": str(path),
        "channels": len(channels_seen),
    }
    if errors:
        result["errors"] = errors
    return result
