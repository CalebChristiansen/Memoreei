"""Signal, read from Signal Desktop's database on this computer.

Signal Desktop keeps every message it has in an SQLCipher database whose key is sealed
by the operating system's secret store (keys.py). Connecting reads that key once, when
someone chooses Connect on the Sources page, and keeps it in config.env as
SIGNAL_DB_KEY. Syncs then open the database with it and never touch the secret store,
so nothing prompts again unless Signal's key changes (Signal reinstalled or relinked),
and then the sync says to reconnect.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from memoreei.connectors.signal.keys import KeyUnavailable, read_db_key
from memoreei.connectors.signal.reader import SchemaChanged, SignalReader
from memoreei.connectors.signal.sync import SOURCE_PREFIX, sync_reader
from memoreei.storage.database import Database

__all__ = ["SOURCE_PREFIX", "KeyUnavailable", "app_present", "connect", "signal_dir", "sync_signal"]

RECONNECT = (
    "Signal Desktop's database key has changed (Signal was reinstalled or linked again). "
    "Reconnect Signal on the Sources page."
)


def signal_dir() -> Path:
    """SIGNAL_DIR, or where Signal Desktop keeps its data on this OS."""
    override = os.environ.get("SIGNAL_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Signal"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "Signal"


def app_present() -> bool:
    """Whether Signal Desktop has been set up here. Windows keeps its key with DPAPI,
    which nothing here reads, so it isn't offered there."""
    return sys.platform != "win32" and (signal_dir() / "config.json").exists()


def _db_path() -> Path:
    return signal_dir() / "sql" / "db.sqlite"


def connect() -> str:
    """Read Signal's database key and prove it opens the database. Returns the key.

    This is the only place the secret store is asked, so the only place a prompt can
    appear. It blocks until whoever is at the screen answers; run it off the event loop.
    Raises KeyUnavailable with a reason to show them.
    """
    key = read_db_key(signal_dir())
    try:
        with SignalReader(_db_path(), key):
            pass
    except SchemaChanged as exc:
        raise KeyUnavailable(str(exc)) from None
    except Exception as exc:
        raise KeyUnavailable(f"The key was read, but Signal's database didn't open with it ({exc}).") from None
    return key


async def sync_signal(db: Database, embedder: Any, key: str | None = None) -> dict:
    """Sync Signal Desktop with the key saved at Connect. Never raises: errors come back as a dict."""
    if key is None:
        from memoreei.config import get_config

        key = get_config().signal_db_key or ""
    if not key:
        return {"error": "Signal isn't connected. Connect it on the Sources page.", "synced": 0}
    if not _db_path().exists():
        return {"error": "Signal Desktop's database isn't there any more. Is Signal Desktop still installed?",
                "synced": 0}
    reader = SignalReader(_db_path(), key)
    try:
        reader.open()
    except SchemaChanged as exc:
        return {"error": str(exc), "synced": 0}
    except Exception:
        return {"error": RECONNECT, "synced": 0}
    try:
        return await sync_reader(db, embedder, reader)
    finally:
        reader.close()
