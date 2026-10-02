"""WhatsApp messages from any reader, into memory."""
from __future__ import annotations

import sys
import time
from typing import Any

from ulid import ULID

from memoreei.connectors.whatsapp.messages import Reader, WhatsAppMessage
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

SOURCE_PREFIX = "whatsapp"
BATCH_SIZE = 100

_LABELS = {"photo": "[Photo]", "video": "[Video]", "gif": "[GIF]", "document": "[Document]"}


def source_for(chat_jid: str) -> str:
    return f"{SOURCE_PREFIX}:{chat_jid}"


def body(msg: WhatsAppMessage) -> str:
    """The words a message is searched by."""
    text = msg.text.strip()
    if msg.kind == "link":
        title = (msg.link_title or "").strip()
        return f"{text}\n[Link] {title}" if title and title not in text else text
    if msg.kind == "document":
        caption = (msg.caption or "").strip()
        return f"[Document] {text}" + (f"\n{caption}" if caption else "")
    label = _LABELS.get(msg.kind)
    return f"{label} {text}" if label else text


def to_memory_item(msg: WhatsAppMessage) -> MemoryItem:
    source = source_for(msg.chat.jid)
    return MemoryItem(
        id=str(ULID()),
        source=source,
        source_id=f"{source}:{msg.message_id}",
        content=f"{msg.sender_name}: {body(msg)}",
        summary=None,
        participants=[msg.sender_name],
        ts=msg.ts,
        ingested_at=int(time.time()),
        metadata={
            "chat_jid": msg.chat.jid,
            "chat_name": msg.chat.name,
            "is_group": msg.chat.is_group,
            "is_from_me": msg.from_me,
            "sender_jid": msg.sender_jid,
            "sender_phone": msg.sender_phone,
            "kind": msg.kind,
        },
        embedding=None,
    )


async def sync_reader(db: Database, embedder: Any, reader: Reader) -> int:
    """Store what's new since the reader's checkpoint. Returns how many were stored."""
    # Chats go into contacts under their JID, so "whatsapp:<jid>" shows by name in
    # search results and list_sources, the way "imessage:+1…" does from the AddressBook.
    await db.upsert_contacts(
        [(chat.jid, chat.name, SOURCE_PREFIX) for chat in reader.chats() if chat.name != chat.jid]
    )

    after = await db.get_whatsapp_checkpoint(reader.name) or 0
    upto = reader.end()
    if upto <= after:
        return 0

    from tqdm import tqdm

    stored = 0
    batch: list[WhatsAppMessage] = []

    async def flush() -> None:
        nonlocal stored
        items = [to_memory_item(m) for m in batch]
        embeddings = await embedder.embed([item.content for item in items])
        for item, emb in zip(items, embeddings):
            item.embedding = emb
        await db.bulk_insert(items)
        stored += len(items)
        # After each batch, so a crash or cancel resumes here rather than from scratch.
        await db.set_whatsapp_checkpoint(reader.name, batch[-1].position)
        batch.clear()

    # disable=None: no bar when stderr isn't a terminal, as under the Mac app. No chat
    # names in it either way: they are personal data, and stderr is the app's log.
    with tqdm(desc="  WhatsApp", unit="msg", file=sys.stderr, disable=None) as progress:
        for msg in reader.messages(after, upto):
            batch.append(msg)
            if len(batch) >= BATCH_SIZE:
                await flush()
                progress.update(BATCH_SIZE)
        if batch:
            progress.update(len(batch))
            await flush()

    # Past the skipped ones too (stickers, reactions…), so they aren't re-read every round.
    await db.set_whatsapp_checkpoint(reader.name, upto)
    return stored
