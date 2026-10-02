"""WhatsApp, read from where the app keeps its own chats on this computer.

The shared half (messages.py, sync.py) takes named messages from any reader. The one
reader so far is ChatStorage.sqlite: WhatsApp for Mac's database, which an iPhone
backup also contains. WhatsApp on Windows and Linux is WhatsApp Web, whose local store
is encrypted with a key from WhatsApp's servers, so it has no reader.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from memoreei.connectors.whatsapp.chatstorage import ChatStorageReader
from memoreei.connectors.whatsapp.sync import SOURCE_PREFIX, sync_reader
from memoreei.storage.database import Database

__all__ = ["DEFAULT_MAC_PATH", "SOURCE_PREFIX", "ChatStorageReader", "get_db_path", "sync_whatsapp"]

DEFAULT_MAC_PATH = "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"


def get_db_path() -> str:
    """WHATSAPP_DB_PATH, or where WhatsApp for Mac keeps its chats."""
    return str(Path(os.environ.get("WHATSAPP_DB_PATH") or DEFAULT_MAC_PATH).expanduser())


def mac_app_present() -> bool:
    """Whether WhatsApp for Mac has a database on this Mac."""
    return sys.platform == "darwin" and Path(DEFAULT_MAC_PATH).expanduser().exists()


def _cant_read(db_path: str, exc: Exception) -> str:
    """Why the database can't be read, without its path: the network sync shows this."""
    if not Path(db_path).exists():
        return (
            "No WhatsApp database found. Install WhatsApp for Mac and link it to your "
            "phone, or set WHATSAPP_DB_PATH."
        )
    message = f"Can't open the WhatsApp database ({exc})."
    if sys.platform == "darwin":
        from memoreei.service._macos_access import how_to_grant

        message += f" Memoreei may need Full Disk Access: {how_to_grant()}."
    return message


async def sync_whatsapp(db: Database, embedder: Any, db_path: str | None = None) -> dict:
    """Sync new WhatsApp messages. Never raises: errors come back as a dict."""
    db_path = db_path or get_db_path()
    reader = ChatStorageReader(db_path)
    try:
        reader.open()
    except sqlite3.Error as exc:
        return {"error": _cant_read(db_path, exc), "synced": 0, "db_path": db_path}
    try:
        count = await sync_reader(db, embedder, reader)
        return {"synced": count, "db_path": db_path}
    except Exception as exc:
        return {"error": str(exc), "synced": 0, "db_path": db_path}
    finally:
        reader.close()
