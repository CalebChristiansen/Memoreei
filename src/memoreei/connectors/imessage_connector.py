"""iMessage connector — reads the local macOS Messages database (read-only).

Only works on macOS. On Linux/Windows the top-level sync function returns a
clear error dict rather than raising, so the MCP server degrades gracefully.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from ulid import ULID

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

SOURCE_PREFIX = "imessage"
APPLE_EPOCH_OFFSET = 978307200  # seconds between Unix epoch (1970) and Apple epoch (2001)
NANOSECOND_THRESHOLD = 1_000_000_000_000  # values above this are nanoseconds (macOS 13+)


def is_macos() -> bool:
    return sys.platform == "darwin"


def _get_db_path() -> str:
    """Return the iMessage DB path from env var or the macOS default."""
    default = str(Path.home() / "Library" / "Messages" / "chat.db")
    return os.environ.get("IMESSAGE_DB_PATH", default)


def _apple_date_to_unix(apple_date: int) -> int:
    """Convert an Apple Core Data timestamp to a Unix timestamp.

    macOS 13+ stores dates as nanoseconds since 2001-01-01.
    Older macOS stores them as seconds since 2001-01-01.
    """
    if apple_date > NANOSECOND_THRESHOLD:
        return int(apple_date / 1_000_000_000) + APPLE_EPOCH_OFFSET
    return apple_date + APPLE_EPOCH_OFFSET


class IMessageConnector:
    """Read-only connector for the macOS Messages SQLite database (chat.db)."""

    _MESSAGES_QUERY = """
        SELECT
            m.rowid,
            m.guid,
            m.text,
            m.date,
            m.is_from_me,
            m.service,
            h.id AS sender_handle
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.rowid
        LEFT JOIN handle h ON h.rowid = m.handle_id
        WHERE cmj.chat_id = ? AND m.rowid > ?
        ORDER BY m.rowid ASC
    """

    _CHATS_QUERY = """
        SELECT rowid, chat_identifier,
               COALESCE(display_name, room_name, chat_identifier) AS name
        FROM chat
    """

    _CHATS_FILTERED_QUERY = """
        SELECT rowid, chat_identifier,
               COALESCE(display_name, room_name, chat_identifier) AS name
        FROM chat
        WHERE chat_identifier = ? OR display_name = ? OR room_name = ?
    """

    def __init__(self, db: Database, embedder: Any, db_path: str | None = None) -> None:
        self.db = db
        self.embedder = embedder
        self.db_path = db_path or _get_db_path()

    def _open_chat_db(self) -> sqlite3.Connection:
        """Open chat.db in read-only mode (raises if not accessible)."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    async def sync(self, chat_name: str | None = None) -> int:
        """Sync messages from chat.db into the memory database.

        Args:
            chat_name: Optional filter — only sync this chat/contact identifier
                       or display name.

        Returns:
            Number of new messages stored.
        """
        if not is_macos():
            raise RuntimeError(
                f"iMessage connector is only supported on macOS. "
                f"Current platform: {sys.platform}"
            )

        try:
            conn = self._open_chat_db()
        except sqlite3.OperationalError as exc:
            raise RuntimeError(
                f"Cannot open iMessage database at {self.db_path}: {exc}. "
                "Make sure Terminal (or your app) has Full Disk Access in "
                "System Settings → Privacy & Security."
            ) from exc

        try:
            return await self._do_sync(conn, chat_name)
        finally:
            conn.close()

    async def _do_sync(
        self, conn: sqlite3.Connection, chat_filter: str | None
    ) -> int:
        chats = self._list_chats(conn, chat_filter)
        if not chats:
            return 0
        total = 0
        for chat_rowid, chat_identifier, chat_name in chats:
            total += await self._sync_chat(conn, chat_rowid, chat_identifier, chat_name)
        return total

    def _list_chats(
        self, conn: sqlite3.Connection, chat_filter: str | None
    ) -> list[tuple[int, str, str]]:
        """Return list of (rowid, chat_identifier, display_name)."""
        if chat_filter:
            cursor = conn.execute(
                self._CHATS_FILTERED_QUERY, (chat_filter, chat_filter, chat_filter)
            )
        else:
            cursor = conn.execute(self._CHATS_QUERY)
        return [(int(row[0]), str(row[1]), str(row[2])) for row in cursor.fetchall()]

    async def _sync_chat(
        self,
        conn: sqlite3.Connection,
        chat_rowid: int,
        chat_identifier: str,
        chat_name: str,
    ) -> int:
        checkpoint_key = str(chat_rowid)
        last_rowid = await self.db.get_imessage_checkpoint(checkpoint_key) or 0

        cursor = conn.execute(self._MESSAGES_QUERY, (chat_rowid, last_rowid))
        rows = cursor.fetchall()
        if not rows:
            return 0

        items: list[MemoryItem] = []
        newest_rowid = last_rowid
        for row in rows:
            rowid = int(row["rowid"])
            if rowid > newest_rowid:
                newest_rowid = rowid
            item = self._to_memory_item(row, chat_identifier, chat_name)
            if item is not None:
                items.append(item)

        # Always advance checkpoint even if all messages were empty
        await self.db.set_imessage_checkpoint(checkpoint_key, newest_rowid)

        if not items:
            return 0

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        await self.db.bulk_insert(items)
        return len(items)

    def _to_memory_item(
        self,
        row: sqlite3.Row,
        chat_identifier: str,
        chat_name: str,
    ) -> MemoryItem | None:
        text = row["text"]
        if not text or not text.strip():
            return None

        is_from_me = bool(row["is_from_me"])
        sender = "me" if is_from_me else (row["sender_handle"] or "unknown")
        service = row["service"] or "iMessage"
        apple_date = row["date"] or 0
        ts = _apple_date_to_unix(int(apple_date))

        source = f"{SOURCE_PREFIX}:{chat_identifier}"
        source_id = f"{source}:{row['guid']}"

        return MemoryItem(
            id=str(ULID()),
            source=source,
            source_id=source_id,
            content=f"{sender}: {text.strip()}",
            summary=None,
            participants=[sender],
            ts=ts,
            ingested_at=int(time.time()),
            metadata={
                "chat_identifier": chat_identifier,
                "chat_name": chat_name,
                "service": service,
                "is_from_me": is_from_me,
                "message_rowid": int(row["rowid"]),
            },
            embedding=None,
        )


async def sync_imessage(
    db: Database,
    embedder: Any,
    chat_name: str | None = None,
) -> dict:
    """Top-level sync function used by the MCP server tool.

    Returns a dict with 'synced' count on success or 'error' on failure.
    Never raises — errors are returned as dict so the MCP tool stays alive.
    """
    if not is_macos():
        return {
            "error": (
                f"iMessage connector is only supported on macOS. "
                f"Current platform: {sys.platform}"
            ),
            "synced": 0,
        }

    db_path = _get_db_path()
    connector = IMessageConnector(db=db, embedder=embedder, db_path=db_path)
    try:
        count = await connector.sync(chat_name=chat_name)
        return {"synced": count, "db_path": db_path}
    except Exception as exc:
        return {"error": str(exc), "synced": 0, "db_path": db_path}
