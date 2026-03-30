"""Signal Desktop connector — reads the local encrypted database (read-only).

Signal Desktop stores messages in an SQLCipher-encrypted SQLite database.
The encryption key is stored in config.json in the same Signal directory.

Requires pysqlcipher3 for encrypted database access. Falls back to the
sqlcipher CLI if pysqlcipher3 is not installed. Returns a clear error
if neither is available.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from ulid import ULID

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

SOURCE_PREFIX = "signal"

# Signal Desktop message types to ingest (skip system messages)
_INGEST_TYPES = {"incoming", "outgoing", None}


# ---------------------------------------------------------------------------
# OS / path detection
# ---------------------------------------------------------------------------


def _get_signal_dir() -> Path:
    """Return the Signal Desktop data directory for the current OS."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Signal"
    elif sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Signal"
    else:
        # Linux (and anything else)
        return Path.home() / ".config" / "Signal"


def _get_db_path() -> str:
    """Return Signal DB path from SIGNAL_DB_PATH env var or OS default."""
    default = str(_get_signal_dir() / "sql" / "db.sqlite")
    return os.environ.get("SIGNAL_DB_PATH", default)


def _get_config_path() -> str:
    """Return Signal config.json path from SIGNAL_CONFIG_PATH env var or OS default."""
    default = str(_get_signal_dir() / "config.json")
    return os.environ.get("SIGNAL_CONFIG_PATH", default)


# ---------------------------------------------------------------------------
# Key extraction
# ---------------------------------------------------------------------------


def _read_key(config_path: str) -> str:
    """Extract the SQLCipher encryption key from Signal's config.json.

    The key is a 64-character hex string used as a raw SQLCipher key.
    """
    with open(config_path, "r") as f:
        config = json.load(f)
    key = config.get("key")
    if not key:
        raise RuntimeError(
            f"No 'key' field found in Signal config.json at {config_path}"
        )
    return key


# ---------------------------------------------------------------------------
# Backend availability checks
# ---------------------------------------------------------------------------


def _has_pysqlcipher3() -> bool:
    """Return True if pysqlcipher3 is importable."""
    try:
        import pysqlcipher3  # noqa: F401
        return True
    except ImportError:
        return False


def _has_sqlcipher_cli() -> bool:
    """Return True if the sqlcipher CLI is available on PATH."""
    try:
        result = subprocess.run(
            ["sqlcipher", "--version"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


# ---------------------------------------------------------------------------
# Low-level DB access
# ---------------------------------------------------------------------------


def _open_with_pysqlcipher3(db_path: str, key: str) -> Any:
    """Open the Signal SQLCipher DB using pysqlcipher3."""
    from pysqlcipher3 import dbapi2 as sqlcipher  # type: ignore[import]

    conn = sqlcipher.connect(db_path)
    conn.row_factory = sqlcipher.Row
    # Use raw hex key (Signal stores key as hex, not passphrase)
    conn.execute(f"PRAGMA key=\"x'{key}'\"")
    # Signal Desktop 5+ uses SQLCipher 4 defaults
    conn.execute("PRAGMA cipher_page_size = 4096")
    conn.execute("PRAGMA kdf_iter = 64000")
    conn.execute("PRAGMA cipher_hmac_algorithm = HMAC_SHA512")
    conn.execute("PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512")
    # Verify we can read the DB
    conn.execute("SELECT count(*) FROM sqlite_master")
    return conn


def _open_with_sqlcipher_cli(db_path: str, key: str) -> sqlite3.Connection:
    """Decrypt the Signal DB to a temp file using the sqlcipher CLI and open it."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        commands = (
            f"PRAGMA key=\"x'{key}'\";\n"
            "PRAGMA cipher_page_size = 4096;\n"
            "PRAGMA kdf_iter = 64000;\n"
            "PRAGMA cipher_hmac_algorithm = HMAC_SHA512;\n"
            "PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;\n"
            f"ATTACH DATABASE '{tmp_path}' AS plain KEY '';\n"
            "SELECT sqlcipher_export('plain');\n"
            "DETACH DATABASE plain;\n"
        )
        result = subprocess.run(
            ["sqlcipher", db_path],
            input=commands,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"sqlcipher CLI failed (exit {result.returncode}): {result.stderr.strip()}"
            )

        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        # Clean up temp file on error
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _open_signal_db(db_path: str, key: str) -> tuple[Any, str | None]:
    """Open the Signal DB using the best available backend.

    Returns (connection, temp_path_to_cleanup).
    temp_path_to_cleanup is set when sqlcipher CLI was used (temp file to delete).
    Raises RuntimeError if neither backend is available or DB cannot be opened.
    """
    if _has_pysqlcipher3():
        return _open_with_pysqlcipher3(db_path, key), None

    if _has_sqlcipher_cli():
        # The CLI creates a temp file; we track it for cleanup
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_path = tmp.name
        tmp.close()

        commands = (
            f"PRAGMA key=\"x'{key}'\";\n"
            "PRAGMA cipher_page_size = 4096;\n"
            "PRAGMA kdf_iter = 64000;\n"
            "PRAGMA cipher_hmac_algorithm = HMAC_SHA512;\n"
            "PRAGMA cipher_kdf_algorithm = PBKDF2_HMAC_SHA512;\n"
            f"ATTACH DATABASE '{tmp_path}' AS plain KEY '';\n"
            "SELECT sqlcipher_export('plain');\n"
            "DETACH DATABASE plain;\n"
        )
        result = subprocess.run(
            ["sqlcipher", db_path],
            input=commands,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise RuntimeError(
                f"sqlcipher CLI failed (exit {result.returncode}): {result.stderr.strip()}"
            )

        conn = sqlite3.connect(tmp_path)
        conn.row_factory = sqlite3.Row
        return conn, tmp_path

    raise RuntimeError(
        "Signal connector requires pysqlcipher3: pip install pysqlcipher3\n"
        "Alternatively, install the sqlcipher CLI (e.g. apt install sqlcipher)."
    )


# ---------------------------------------------------------------------------
# Connector class
# ---------------------------------------------------------------------------


class SignalConnector:
    """Read-only connector for the Signal Desktop SQLCipher database."""

    _CONVERSATIONS_QUERY = """
        SELECT
            id,
            COALESCE(name, profileFullName, profileName, e164, uuid, id) AS display_name,
            type,
            e164,
            uuid
        FROM conversations
        ORDER BY active_at DESC
    """

    _CONVERSATIONS_FILTERED_QUERY = """
        SELECT
            id,
            COALESCE(name, profileFullName, profileName, e164, uuid, id) AS display_name,
            type,
            e164,
            uuid
        FROM conversations
        WHERE id = ? OR name = ? OR profileFullName = ? OR profileName = ? OR e164 = ?
    """

    _MESSAGES_QUERY = """
        SELECT
            rowid,
            id,
            conversationId,
            body,
            sent_at,
            source,
            sourceUuid,
            type
        FROM messages
        WHERE conversationId = ? AND rowid > ? AND body IS NOT NULL AND body != ''
        ORDER BY rowid ASC
    """

    def __init__(
        self,
        db: Database,
        embedder: Any,
        db_path: str | None = None,
        config_path: str | None = None,
    ) -> None:
        self.db = db
        self.embedder = embedder
        self.db_path = db_path or _get_db_path()
        self.config_path = config_path or _get_config_path()

    async def sync(self, conversation_id: str | None = None) -> int:
        """Sync messages from the Signal DB into the memory database.

        Args:
            conversation_id: Optional filter — only sync this conversation.

        Returns:
            Number of new messages stored.
        """
        key = _read_key(self.config_path)
        conn, tmp_path = _open_signal_db(self.db_path, key)
        try:
            return await self._do_sync(conn, conversation_id)
        finally:
            conn.close()
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    async def _do_sync(self, conn: Any, conv_filter: str | None) -> int:
        conversations = self._list_conversations(conn, conv_filter)
        if not conversations:
            return 0
        total = 0
        for conv_id, display_name, conv_type, e164, uuid in conversations:
            total += await self._sync_conversation(
                conn, conv_id, display_name, conv_type, e164, uuid
            )
        return total

    def _list_conversations(
        self, conn: Any, conv_filter: str | None
    ) -> list[tuple[str, str, str, str | None, str | None]]:
        """Return list of (id, display_name, type, e164, uuid)."""
        if conv_filter:
            cursor = conn.execute(
                self._CONVERSATIONS_FILTERED_QUERY,
                (conv_filter, conv_filter, conv_filter, conv_filter, conv_filter),
            )
        else:
            cursor = conn.execute(self._CONVERSATIONS_QUERY)
        return [
            (
                str(row["id"]),
                str(row["display_name"]),
                str(row["type"]),
                row["e164"] if row["e164"] else None,
                row["uuid"] if row["uuid"] else None,
            )
            for row in cursor.fetchall()
        ]

    async def _sync_conversation(
        self,
        conn: Any,
        conv_id: str,
        display_name: str,
        conv_type: str,
        e164: str | None,
        uuid: str | None,
    ) -> int:
        last_rowid = await self.db.get_signal_checkpoint(conv_id) or 0

        cursor = conn.execute(self._MESSAGES_QUERY, (conv_id, last_rowid))
        rows = cursor.fetchall()
        if not rows:
            return 0

        items: list[MemoryItem] = []
        newest_rowid = last_rowid
        for row in rows:
            rowid = int(row["rowid"])
            if rowid > newest_rowid:
                newest_rowid = rowid
            item = self._to_memory_item(row, conv_id, display_name, conv_type)
            if item is not None:
                items.append(item)

        await self.db.set_signal_checkpoint(conv_id, newest_rowid)

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
        row: Any,
        conv_id: str,
        display_name: str,
        conv_type: str,
    ) -> MemoryItem | None:
        body = row["body"]
        if not body or not str(body).strip():
            return None

        msg_type = row["type"]
        if msg_type not in _INGEST_TYPES:
            return None

        is_outgoing = msg_type == "outgoing"
        sender = "me" if is_outgoing else (row["source"] or row["sourceUuid"] or "unknown")

        # sent_at is milliseconds since Unix epoch
        sent_at_ms = row["sent_at"] or 0
        ts = int(sent_at_ms) // 1000

        source = f"{SOURCE_PREFIX}:{conv_id}"
        source_id = f"{source}:{row['id']}"

        return MemoryItem(
            id=str(ULID()),
            source=source,
            source_id=source_id,
            content=f"{sender}: {str(body).strip()}",
            summary=None,
            participants=[sender],
            ts=ts,
            ingested_at=int(time.time()),
            metadata={
                "conversation_id": conv_id,
                "conversation_name": display_name,
                "conversation_type": conv_type,
                "message_id": str(row["id"]),
                "type": msg_type,
                "is_outgoing": is_outgoing,
            },
            embedding=None,
        )


# ---------------------------------------------------------------------------
# Top-level sync function (used by MCP server tool)
# ---------------------------------------------------------------------------


async def sync_signal(
    db: Database,
    embedder: Any,
    conversation_id: str | None = None,
) -> dict:
    """Sync Signal Desktop messages from the local encrypted database.

    Returns a dict with 'synced' count on success or 'error' on failure.
    Never raises — errors are returned as dict so the MCP tool stays alive.
    """
    if not _has_pysqlcipher3() and not _has_sqlcipher_cli():
        return {
            "error": (
                "Signal connector requires pysqlcipher3: pip install pysqlcipher3\n"
                "Alternatively, install the sqlcipher CLI (e.g. apt install sqlcipher)."
            ),
            "synced": 0,
        }

    db_path = _get_db_path()
    config_path = _get_config_path()
    connector = SignalConnector(
        db=db, embedder=embedder, db_path=db_path, config_path=config_path
    )
    try:
        count = await connector.sync(conversation_id=conversation_id)
        return {"synced": count, "db_path": db_path}
    except Exception as exc:
        return {"error": str(exc), "synced": 0, "db_path": db_path}
