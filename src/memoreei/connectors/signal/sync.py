"""Signal messages into memory, and kept up to date when they change afterwards.

New messages are found by rowid, after a checkpoint, as WhatsApp's are. Signal also
changes old messages in place: a reaction, an edit, a "delete for everyone". signal_seen
holds the length of each message's json and body as last read; a different length
means a change, and that message's memoreei is rewritten:

    reactions  metadata only ({"emoji", "from"} each)
    an edit    the original stays, each later version follows on its own line as
               "[edited] …", so either wording finds it; re-embedded
    a delete   the memoreei stays as it was, with metadata "deleted": true. Deleted for
               everyone, the message stays in Signal marked so; deleted any other way
               (for me, on another device, the whole chat), it simply isn't there

Disappearing messages are never stored: whoever sent one chose that it shouldn't last.
"""
from __future__ import annotations

import sys
import time
from typing import Any

from ulid import ULID

from memoreei.connectors.signal.reader import CHAT_TYPES, SignalMessage, SignalReader
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

SOURCE_PREFIX = "signal"
CHECKPOINT = "signal-desktop"  # the one reader so far, as signal_checkpoint names it
DISAPPEARING_SKIPPED = "disappearing-skipped"  # a running count, kept beside it for Status
BATCH_SIZE = 100


def source_for(chat_id: str) -> str:
    return f"{SOURCE_PREFIX}:{chat_id}"


def content(msg: SignalMessage) -> str:
    """The words a message is searched by: the sender, then what was said.

    Group changes, calls and timer changes are already sentences with their subject in
    them, so they stand alone.
    """
    words = msg.text
    for edit in msg.edits:
        words += f"\n[edited] {edit}"
    if msg.kind in CHAT_TYPES or msg.kind == "story":
        return f"{msg.sender_name}: {words}"
    return words


def metadata(msg: SignalMessage) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "chat_id": msg.chat.id,
        "chat_name": msg.chat.name,
        "is_group": msg.chat.is_group,
        "is_from_me": msg.from_me,
        "message_id": msg.message_id,
        "kind": msg.kind,
    }
    if msg.edits:
        meta["edits"] = len(msg.edits)
    if msg.deleted:
        meta["deleted"] = True
    if msg.reactions:
        meta["reactions"] = msg.reactions
    return meta


def to_memory_item(msg: SignalMessage) -> MemoryItem:
    source = source_for(msg.chat.id)
    return MemoryItem(
        id=str(ULID()),
        source=source,
        source_id=f"{source}:{msg.message_id}",
        content=content(msg),
        summary=None,
        participants=[msg.sender_name],
        ts=msg.ts,
        ingested_at=int(time.time()),
        metadata=metadata(msg),
        embedding=None,
    )


async def sync_reader(db: Database, embedder: Any, reader: SignalReader) -> dict[str, int]:
    """Store what's new, update what changed. Counts come back; content never does."""
    await db.upsert_contacts(
        [(source_for(chat.id), chat.name, SOURCE_PREFIX) for chat in reader.chats()]
    )
    after = await db.get_signal_checkpoint(CHECKPOINT) or 0
    upto = reader.end()
    counts = {"synced": 0, "updated": 0, "skipped_disappearing": 0}

    if after:
        await _bring_up_to_date(db, embedder, reader, after, counts)
    if upto > after:
        await _store_new(db, embedder, reader, after, upto, counts)
    # Past what was skipped too, so it isn't re-read every round.
    await db.set_signal_checkpoint(CHECKPOINT, upto)
    if counts["skipped_disappearing"]:
        total = await db.get_signal_checkpoint(DISAPPEARING_SKIPPED) or 0
        await db.set_signal_checkpoint(DISAPPEARING_SKIPPED, total + counts["skipped_disappearing"])
    return counts


async def _store_new(db: Database, embedder: Any, reader: SignalReader,
                     after: int, upto: int, counts: dict[str, int]) -> None:
    from tqdm import tqdm

    batch: list[SignalMessage] = []

    async def flush() -> None:
        items = [to_memory_item(m) for m in batch]
        if items:
            embeddings = await embedder.embed([item.content for item in items])
            for item, emb in zip(items, embeddings):
                item.embedding = emb
            await db.bulk_insert(items)
            counts["synced"] += len(items)
        await db.set_signal_seen({m.message_id: m.size for m in batch})
        # After each batch, so a crash or cancel resumes here rather than from scratch.
        await db.set_signal_checkpoint(CHECKPOINT, batch[-1].rowid)
        batch.clear()

    # No chat names in the progress bar: they are personal data, and stderr is the log.
    with tqdm(desc="  Signal", unit="msg", file=sys.stderr, disable=None) as progress:
        for msg in reader.messages(after, upto):
            if msg.disappearing:
                counts["skipped_disappearing"] += 1
                await db.set_signal_seen({msg.message_id: msg.size})
                continue
            if msg.deleted and not msg.text:
                continue  # deleted before it was ever read: nothing left to keep
            batch.append(msg)
            if len(batch) >= BATCH_SIZE:
                progress.update(len(batch))
                await flush()
        if batch:
            progress.update(len(batch))
            await flush()


async def _bring_up_to_date(db: Database, embedder: Any, reader: SignalReader,
                            after: int, counts: dict[str, int]) -> None:
    seen = await db.get_signal_seen()
    sizes = reader.sizes(after)
    gone = [mid for mid in seen if mid not in sizes]
    if gone:
        counts["updated"] += await db.mark_deleted(SOURCE_PREFIX, gone)
        await db.forget_signal_seen(gone)
    changed = [mid for mid, size in sizes.items() if mid in seen and seen[mid] != size]
    if not changed:
        return
    fresh = list(reader.by_ids(changed))
    by_source: dict[str, list[SignalMessage]] = {}
    for msg in fresh:
        by_source.setdefault(source_for(msg.chat.id), []).append(msg)

    for source, msgs in by_source.items():
        stored = await db.get_by_source_ids(source, [f"{source}:{m.message_id}" for m in msgs])
        for msg in msgs:
            if msg.disappearing:
                continue
            item = to_memory_item(msg)
            old = stored.get(item.source_id or "")
            if old is None:
                if msg.text and not msg.deleted:  # had no words before (an edit gave it some)
                    item.embedding = (await embedder.embed([item.content]))[0]
                    await db.bulk_insert([item])
                    counts["synced"] += 1
                continue
            if msg.deleted:
                item.content = old.content  # what it said, before it was taken back
            reembed = item.content != old.content
            if not reembed and item.metadata == old.metadata:
                continue
            if reembed:
                item.embedding = (await embedder.embed([item.content]))[0]
            await db.update_memory(item, reembedded=reembed)
            counts["updated"] += 1
    await db.set_signal_seen({mid: sizes[mid] for mid in changed})
