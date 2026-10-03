from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import aiosqlite
import numpy as np

from memoreei.storage.models import MemoryItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT,
    content TEXT NOT NULL,
    summary TEXT,
    participants TEXT,
    ts INTEGER NOT NULL,
    ingested_at INTEGER NOT NULL,
    metadata TEXT,
    embedding BLOB
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_dedup ON memories(source, source_id)
    WHERE source_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_memories_ts ON memories(ts DESC);
CREATE INDEX IF NOT EXISTS idx_memories_source ON memories(source);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    summary,
    content=memories,
    content_rowid=rowid
);

CREATE TABLE IF NOT EXISTS discord_checkpoint (
    channel_id TEXT PRIMARY KEY,
    last_message_id TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_checkpoint (
    chat_id TEXT PRIMARY KEY,
    last_update_id INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS matrix_checkpoint (
    room_id TEXT PRIMARY KEY,
    prev_batch TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS slack_checkpoint (
    channel_id TEXT PRIMARY KEY,
    last_ts TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS email_checkpoint (
    key TEXT PRIMARY KEY,
    last_uid TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS imessage_checkpoint (
    chat_id TEXT PRIMARY KEY,
    last_rowid INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS whatsapp_checkpoint (
    reader TEXT PRIMARY KEY,
    position INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_checkpoint (
    conversation_id TEXT PRIMARY KEY,
    last_rowid INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Every Signal message read so far, and how long its json and body were then: when
-- that changes, someone reacted, edited or deleted it, and its memoreei is brought up
-- to date. Lengths only, never content.
CREATE TABLE IF NOT EXISTS signal_seen (
    message_id TEXT PRIMARY KEY,
    size INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    name TEXT PRIMARY KEY,
    key_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER
);

-- The dashboard's logins: one-time login links and the sessions they start.
-- Hashes only, like api_keys, and never valid as MCP keys.
CREATE TABLE IF NOT EXISTS admin_tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('login', 'session')),
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS import_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '{}',
    added_at INTEGER NOT NULL,
    last_synced_at INTEGER,
    last_mtime REAL,
    UNIQUE(kind, path)
);

CREATE TABLE IF NOT EXISTS contacts (
    identifier TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    updated_at INTEGER NOT NULL
);
"""

FTS_TRIGGER_INSERT = """
CREATE TRIGGER IF NOT EXISTS memories_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, summary) VALUES (new.rowid, new.content, COALESCE(new.summary, ''));
END;
"""

FTS_TRIGGER_DELETE = """
CREATE TRIGGER IF NOT EXISTS memories_fts_delete AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, summary) VALUES ('delete', old.rowid, old.content, COALESCE(old.summary, ''));
END;
"""

FTS_TRIGGER_UPDATE = """
CREATE TRIGGER IF NOT EXISTS memories_fts_update AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, summary) VALUES ('delete', old.rowid, old.content, COALESCE(old.summary, ''));
    INSERT INTO memories_fts(rowid, content, summary) VALUES (new.rowid, new.content, COALESCE(new.summary, ''));
END;
"""


def _stored_to_vector(stored: object) -> np.ndarray | None:
    """An embedding as stored (float32 bytes, or JSON from older versions) as a vector."""
    if isinstance(stored, (bytes, bytearray)) and stored:
        return np.frombuffer(stored, dtype=np.float32)
    if isinstance(stored, str) and stored:
        return np.asarray(json.loads(stored), dtype=np.float32)
    return None


# Rows handled per step when building an index in a worker thread. A single C call over
# 100k rows (fetchall, vstack) holds the GIL for a second or more on an Intel MacBook Air,
# and the server's event loop stalls behind it; between steps, the loop gets a turn.
_STEP = 4096


def _norms(matrix: np.ndarray) -> np.ndarray:
    norms = np.empty(len(matrix), np.float32)
    for start in range(0, len(matrix), _STEP):
        norms[start:start + _STEP] = np.linalg.norm(matrix[start:start + _STEP], axis=1)
    return norms


class _VectorIndex:
    """The embeddings of the memories table, as a matrix, with ids and sources alongside."""

    def __init__(self, ids: list[str], sources: np.ndarray, matrix: np.ndarray,
                 count: int, max_rowid: int, norms: np.ndarray | None = None) -> None:
        self.ids, self.sources, self.matrix = ids, sources, matrix
        self.norms = _norms(matrix) if norms is None else norms
        self.count, self.max_rowid = count, max_rowid
        self.data_version = 0  # SQLite's, when this was read; set by the Database

    @classmethod
    def empty(cls) -> "_VectorIndex":
        return cls([], np.array([], dtype=object), np.zeros((0, 0), np.float32), 0, 0)

    def extend(self, rows: list, count: int, max_rowid: int) -> "_VectorIndex":
        """A new index with *rows* (rowid, id, source, embedding) added."""
        decoded = [(memory_id, source, _stored_to_vector(stored))
                   for _rowid, memory_id, source, stored in rows]
        decoded = [(i, s, v) for i, s, v in decoded if v is not None]
        if len(self.matrix):
            dim = self.matrix.shape[1]
        elif decoded:  # the embedding model most rows came from
            dim = Counter(v.shape[0] for _, _, v in decoded).most_common(1)[0][0]
        else:
            dim = 0
        # another embedding model's vectors are not comparable
        decoded = [(i, s, v) for i, s, v in decoded if v.shape[0] == dim]
        if not decoded:
            return _VectorIndex(self.ids, self.sources, self.matrix, count, max_rowid, self.norms)

        old = len(self.matrix)
        matrix = np.empty((old + len(decoded), dim), np.float32)
        for start in range(0, old, _STEP):
            matrix[start:start + _STEP] = self.matrix[start:start + _STEP]
        for n, (_, _, vector) in enumerate(decoded, old):
            matrix[n] = vector
        norms = np.concatenate([self.norms, _norms(matrix[old:])])
        return _VectorIndex(self.ids + [i for i, _, _ in decoded],
                            np.concatenate([self.sources, np.array([s for _, s, _ in decoded], dtype=object)]),
                            matrix, count, max_rowid, norms)

    def without_source(self, source: str, count: int, max_rowid: int) -> "_VectorIndex":
        """A new index without *source*'s rows."""
        keep = self.sources != source
        index = _VectorIndex([i for i, k in zip(self.ids, keep) if k], self.sources[keep],
                             self.matrix[keep], count, max_rowid, self.norms[keep])
        index.data_version = self.data_version
        return index

    def rank(self, query: np.ndarray, limit: int, source_filter: str | None) -> list[str]:
        """The ids of the *limit* rows nearest *query* by cosine similarity, nearest first."""
        if not self.ids or self.matrix.shape[1] != query.shape[0]:
            return []
        candidates = np.flatnonzero(self.sources == source_filter) if source_filter else None
        matrix = self.matrix if candidates is None else self.matrix[candidates]
        norms = (self.norms if candidates is None else self.norms[candidates]) * np.linalg.norm(query)
        if not len(matrix):
            return []
        with np.errstate(divide="ignore", invalid="ignore"):
            scores = np.where(norms > 0, (matrix @ query) / norms, 0.0)
        k = min(limit, len(scores))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top], kind="stable")]
        if candidates is not None:
            top = candidates[top]
        return [self.ids[n] for n in top]


def _read_vectors(db_path: str, base: _VectorIndex | None) -> _VectorIndex | None:
    """Read embeddings into an index, on a connection of its own; runs in a worker thread.

    With *base*, only the rows after its ``max_rowid`` are read and added, and None comes
    back if the table changed in any way besides those appends. The rows, the count and
    ``max(rowid)`` are read in one transaction, so the index describes one snapshot. WAL
    mode means this read holds up neither the server's connection nor its writers.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN")
        count, max_rowid = conn.execute(
            "SELECT count(*), coalesce(max(rowid), 0) FROM memories").fetchone()
        cursor = conn.execute(
            "SELECT rowid, id, source, embedding FROM memories WHERE rowid > ? ORDER BY rowid",
            (base.max_rowid if base else 0,),
        )
        rows: list = []
        while chunk := cursor.fetchmany(_STEP):
            rows.extend(chunk)
        conn.rollback()
    finally:
        conn.close()
    if base is not None and base.count + len(rows) != count:
        return None
    index = (base or _VectorIndex.empty()).extend(rows, count, max_rowid)
    if base is not None:
        index.data_version = base.data_version
    return index


def _report_build_failure(task: "asyncio.Task[_VectorIndex]") -> None:
    if not task.cancelled() and task.exception() is not None:
        print(f"[database] Vector index build failed: {task.exception()!r}", file=sys.stderr)


def _embedding_to_blob(embedding: list[float]) -> bytes:
    return np.array(embedding, dtype=np.float32).tobytes()


class Database:
    def __init__(self, db_path: str = "./memoreei.db") -> None:
        self.db_path = str(Path(db_path).resolve())
        self._db: aiosqlite.Connection | None = None
        self._vectors: _VectorIndex | None = None
        self._vector_build: asyncio.Task[_VectorIndex] | None = None
        self._vector_epoch = 0  # bumped by this connection's deletes

    async def connect(self) -> None:
        parent = Path(self.db_path).parent
        if not parent.exists():
            parent.mkdir(parents=True, mode=0o700)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self.init_db()

    async def close(self) -> None:
        if self._vector_build is not None:
            self._vector_build.cancel()
        if self._db:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> "Database":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def init_db(self) -> None:
        assert self._db is not None
        for statement in SCHEMA.strip().split(";"):
            s = statement.strip()
            if s:
                await self._db.execute(s)
        for trigger in (FTS_TRIGGER_INSERT, FTS_TRIGGER_DELETE, FTS_TRIGGER_UPDATE):
            await self._db.execute(trigger)
        await self._db.commit()

    async def insert_memory(self, memory: MemoryItem) -> str:
        assert self._db is not None
        embedding_blob = _embedding_to_blob(memory.embedding) if memory.embedding else None
        try:
            await self._db.execute(
                """
                INSERT INTO memories
                    (id, source, source_id, content, summary, participants, ts, ingested_at, metadata, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, source_id) WHERE source_id IS NOT NULL DO NOTHING
                """,
                (
                    memory.id,
                    memory.source,
                    memory.source_id,
                    memory.content,
                    memory.summary,
                    json.dumps(memory.participants),
                    memory.ts,
                    memory.ingested_at,
                    json.dumps(memory.metadata),
                    embedding_blob,
                ),
            )
            await self._db.commit()
        except aiosqlite.IntegrityError:
            pass
        return memory.id

    async def bulk_insert(self, memories: list[MemoryItem]) -> int:
        inserted = 0
        for m in memories:
            await self.insert_memory(m)
            inserted += 1
        return inserted

    async def search_fts(self, query: str, limit: int = 10) -> list[MemoryItem]:
        assert self._db is not None
        # Sanitize query for FTS5 — strip characters that break the parser
        import re as _re
        # Remove apostrophes, quotes, and other FTS5 syntax chars
        safe_query = _re.sub(r"[\"'()*^{}~]", " ", query)
        # Collapse whitespace
        safe_query = " ".join(safe_query.split())
        if not safe_query.strip():
            return []
        async with self._db.execute(
            """
            SELECT m.*, memories_fts.rank as fts_rank
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.rowid
            WHERE memories_fts MATCH ?
            ORDER BY memories_fts.rank
            LIMIT ?
            """,
            (safe_query, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [MemoryItem.from_row(dict(row)) for row in rows]

    async def search_vector(
        self, embedding: list[float], limit: int = 10, source_filter: str | None = None
    ) -> list[MemoryItem]:
        """The memories nearest *embedding* by cosine similarity.

        Scored in one matrix product over every embedding, held in memory between
        searches (see _vector_index); whole rows are fetched for the winners alone.
        """
        assert self._db is not None
        index = await self._vector_index()
        query = np.asarray(embedding, dtype=np.float32)
        winners = await asyncio.to_thread(index.rank, query, limit, source_filter)
        if not winners:
            return []

        placeholders = ",".join("?" * len(winners))
        async with self._db.execute(
            f"SELECT * FROM memories WHERE id IN ({placeholders})", winners
        ) as cursor:
            by_id = {row["id"]: MemoryItem.from_row(dict(row)) for row in await cursor.fetchall()}
        return [by_id[memory_id] for memory_id in winners if memory_id in by_id]

    async def warm_vectors(self) -> None:
        """Build the vector index now, or bring it up to date, and wait until it is."""
        await self._vector_index()
        while self._vector_build is not None and not self._vector_build.done():
            await asyncio.shield(self._vector_build)
            await self._vector_index()

    async def _vector_index(self) -> "_VectorIndex":
        """Every embedding as one matrix, kept up to date without making searches wait.

        Reading 100k-odd embeddings out of SQLite takes 5-20 s on an Intel MacBook Air (they
        share pages with the message text); scoring them takes 20 ms. So the matrix stays
        in memory, about 190 MB at that size, and each call costs two cheap queries.

        This connection's inserts only append, so new rows are read and added before the
        search. Its deletes are taken out of the index directly (delete_by_source).
        Anything else, such as a commit from another process (a CLI import beside the
        server, which changes ``data_version``), or a count that doesn't add up because
        rowids can be reused after a delete, rebuilds the index in the background while
        searches go on using the old one. Only the very first build makes a search wait.
        """
        assert self._db is not None
        async with self._db.execute("PRAGMA data_version") as cursor:
            (data_version,) = await cursor.fetchone()
        index = self._vectors
        if index is None:
            return await asyncio.shield(self._rebuild_vectors())
        if index.data_version != data_version:
            self._rebuild_vectors()
            return index
        async with self._db.execute("SELECT count(*), coalesce(max(rowid), 0) FROM memories") as cursor:
            count, max_rowid = await cursor.fetchone()
        if (count, max_rowid) == (index.count, index.max_rowid) or self._rebuilding():
            return index
        if max_rowid > index.max_rowid:
            epoch = self._vector_epoch
            grown = await asyncio.to_thread(_read_vectors, self.db_path, index)
            if grown is not None:
                if self._vectors is index and self._vector_epoch == epoch:
                    self._vectors = grown
                return self._vectors or grown
        self._rebuild_vectors()
        return index

    def _rebuilding(self) -> bool:
        return self._vector_build is not None and not self._vector_build.done()

    def _rebuild_vectors(self) -> "asyncio.Task[_VectorIndex]":
        """Start reading the whole index again, unless that is already under way."""
        if not self._rebuilding():
            self._vector_build = asyncio.create_task(self._build_vectors())
            self._vector_build.add_done_callback(_report_build_failure)
        assert self._vector_build is not None
        return self._vector_build

    async def _build_vectors(self) -> "_VectorIndex":
        assert self._db is not None
        while True:
            epoch = self._vector_epoch
            async with self._db.execute("PRAGMA data_version") as cursor:
                (data_version,) = await cursor.fetchone()
            index = await asyncio.to_thread(_read_vectors, self.db_path, None)
            assert index is not None
            if epoch == self._vector_epoch:
                break  # otherwise one of our deletes landed mid-read: read again
        index.data_version = data_version  # from before the read: a commit since reads again
        self._vectors = index
        return index

    async def get_by_id(self, memory_id: str) -> MemoryItem | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ) as cursor:
            row = await cursor.fetchone()
        return MemoryItem.from_row(dict(row)) if row else None

    async def get_context(
        self, memory_id: str, before: int = 5, after: int = 5
    ) -> list[MemoryItem]:
        assert self._db is not None
        # Get the target memory first
        target = await self.get_by_id(memory_id)
        if not target:
            return []

        async with self._db.execute(
            """
            SELECT * FROM memories
            WHERE source = ?
              AND ts BETWEEN ? AND ?
            ORDER BY ts ASC
            LIMIT ?
            """,
            (
                target.source,
                target.ts - (before * 300),  # 5 min windows
                target.ts + (after * 300),
                before + after + 1,
            ),
        ) as cursor:
            rows = await cursor.fetchall()
        return [MemoryItem.from_row(dict(row)) for row in rows]

    async def list_sources(self) -> dict[str, int]:
        assert self._db is not None
        async with self._db.execute(
            "SELECT source, COUNT(*) as cnt FROM memories GROUP BY source ORDER BY cnt DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["source"]: row["cnt"] for row in rows}

    async def newest_ts(self, kind: str) -> int | None:
        """The newest message time among sources of one kind ("whatsapp" for "whatsapp:…")."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT MAX(ts) AS ts FROM memories WHERE source = ? OR source LIKE ? ESCAPE '\\'",
            (kind, kind.replace("_", "\\_").replace("%", "\\%") + ":%"),
        ) as cursor:
            row = await cursor.fetchone()
        return row["ts"] if row else None

    async def delete_by_source(self, source: str) -> int:
        assert self._db is not None
        async with self._db.execute(
            "SELECT COUNT(*) as cnt FROM memories WHERE source = ?", (source,)
        ) as cursor:
            row = await cursor.fetchone()
        count = row["cnt"] if row else 0
        await self._db.execute("DELETE FROM memories WHERE source = ?", (source,))
        await self._db.commit()
        self._vector_epoch += 1
        if self._vectors is not None:
            async with self._db.execute(
                "SELECT count(*), coalesce(max(rowid), 0) FROM memories"
            ) as cursor:
                remaining, max_rowid = await cursor.fetchone()
            self._vectors = self._vectors.without_source(source, remaining, max_rowid)
        return count

    async def get_discord_checkpoint(self, channel_id: str) -> str | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT last_message_id FROM discord_checkpoint WHERE channel_id = ?",
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["last_message_id"] if row else None

    async def set_discord_checkpoint(self, channel_id: str, last_message_id: str) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO discord_checkpoint (channel_id, last_message_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                last_message_id = excluded.last_message_id,
                updated_at = excluded.updated_at
            """,
            (channel_id, last_message_id, int(time.time())),
        )
        await self._db.commit()

    async def get_telegram_checkpoint(self, chat_id: str) -> int | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT last_update_id FROM telegram_checkpoint WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["last_update_id"] if row else None

    async def set_telegram_checkpoint(self, chat_id: str, last_update_id: int) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO telegram_checkpoint (chat_id, last_update_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                last_update_id = excluded.last_update_id,
                updated_at = excluded.updated_at
            """,
            (chat_id, last_update_id, int(time.time())),
        )
        await self._db.commit()

    async def get_matrix_checkpoint(self, room_id: str) -> str | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT prev_batch FROM matrix_checkpoint WHERE room_id = ?",
            (room_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["prev_batch"] if row else None

    async def set_matrix_checkpoint(self, room_id: str, prev_batch: str) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO matrix_checkpoint (room_id, prev_batch, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(room_id) DO UPDATE SET
                prev_batch = excluded.prev_batch,
                updated_at = excluded.updated_at
            """,
            (room_id, prev_batch, int(time.time())),
        )
        await self._db.commit()

    async def get_slack_checkpoint(self, channel_id: str) -> str | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT last_ts FROM slack_checkpoint WHERE channel_id = ?",
            (channel_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["last_ts"] if row else None

    async def set_slack_checkpoint(self, channel_id: str, last_ts: str) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO slack_checkpoint (channel_id, last_ts, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                last_ts = excluded.last_ts,
                updated_at = excluded.updated_at
            """,
            (channel_id, last_ts, int(time.time())),
        )
        await self._db.commit()

    async def get_email_checkpoint(self, email_addr: str, folder: str) -> str | None:
        assert self._db is not None
        key = f"{email_addr}:{folder}"
        async with self._db.execute(
            "SELECT last_uid FROM email_checkpoint WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["last_uid"] if row else None

    async def set_email_checkpoint(self, email_addr: str, folder: str, last_uid: str) -> None:
        assert self._db is not None
        key = f"{email_addr}:{folder}"
        await self._db.execute(
            """
            INSERT INTO email_checkpoint (key, last_uid, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                last_uid = excluded.last_uid,
                updated_at = excluded.updated_at
            """,
            (key, last_uid, int(time.time())),
        )
        await self._db.commit()

    async def get_imessage_checkpoint(self, chat_id: str) -> int | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT last_rowid FROM imessage_checkpoint WHERE chat_id = ?",
            (chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["last_rowid"] if row else None

    async def set_imessage_checkpoint(self, chat_id: str, last_rowid: int) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO imessage_checkpoint (chat_id, last_rowid, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                last_rowid = excluded.last_rowid,
                updated_at = excluded.updated_at
            """,
            (chat_id, last_rowid, int(time.time())),
        )
        await self._db.commit()

    async def get_whatsapp_checkpoint(self, reader: str) -> int | None:
        """How far a WhatsApp reader has got, in its own position units."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT position FROM whatsapp_checkpoint WHERE reader = ?", (reader,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["position"] if row else None

    async def set_whatsapp_checkpoint(self, reader: str, position: int) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO whatsapp_checkpoint (reader, position, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(reader) DO UPDATE SET
                position = excluded.position,
                updated_at = excluded.updated_at
            """,
            (reader, position, int(time.time())),
        )
        await self._db.commit()

    async def get_signal_checkpoint(self, conversation_id: str) -> int | None:
        assert self._db is not None
        async with self._db.execute(
            "SELECT last_rowid FROM signal_checkpoint WHERE conversation_id = ?",
            (conversation_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row["last_rowid"] if row else None

    async def set_signal_checkpoint(self, conversation_id: str, last_rowid: int) -> None:
        assert self._db is not None
        await self._db.execute(
            """
            INSERT INTO signal_checkpoint (conversation_id, last_rowid, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
                last_rowid = excluded.last_rowid,
                updated_at = excluded.updated_at
            """,
            (conversation_id, last_rowid, int(time.time())),
        )
        await self._db.commit()

    async def get_signal_seen(self) -> dict[str, int]:
        assert self._db is not None
        async with self._db.execute("SELECT message_id, size FROM signal_seen") as cursor:
            return {row[0]: row[1] for row in await cursor.fetchall()}

    async def set_signal_seen(self, sizes: dict[str, int]) -> None:
        assert self._db is not None
        await self._db.executemany(
            "INSERT INTO signal_seen (message_id, size) VALUES (?, ?) "
            "ON CONFLICT(message_id) DO UPDATE SET size = excluded.size",
            list(sizes.items()),
        )
        await self._db.commit()

    async def get_by_source_ids(self, source: str, source_ids: list[str]) -> dict[str, MemoryItem]:
        """The memories with these source_ids in one source, by source_id."""
        assert self._db is not None
        found: dict[str, MemoryItem] = {}
        for start in range(0, len(source_ids), 500):
            chunk = source_ids[start:start + 500]
            async with self._db.execute(
                f"SELECT * FROM memories WHERE source = ? AND source_id IN ({','.join('?' * len(chunk))})",
                (source, *chunk),
            ) as cursor:
                for row in await cursor.fetchall():
                    item = MemoryItem.from_row(dict(row))
                    found[item.source_id or ""] = item
        return found

    async def update_memory(self, memory: MemoryItem, reembedded: bool) -> None:
        """Rewrite a stored memory's words and metadata in place, found by source and source_id.

        The FTS index follows by trigger. With *reembedded*, the embedding changes too,
        which the in-memory vector index can't see from here (no row was added and no
        other connection committed), so it is rebuilt in the background.
        """
        assert self._db is not None
        await self._db.execute(
            "UPDATE memories SET content = ?, participants = ?, metadata = ?, "
            "embedding = coalesce(?, embedding) WHERE source = ? AND source_id = ?",
            (
                memory.content,
                json.dumps(memory.participants),
                json.dumps(memory.metadata),
                _embedding_to_blob(memory.embedding) if reembedded and memory.embedding else None,
                memory.source,
                memory.source_id,
            ),
        )
        await self._db.commit()
        if reembedded:
            self._vector_epoch += 1
            if self._vectors is not None:
                self._vectors.data_version = -1  # stale: the next search rebuilds it

    async def upsert_contacts(self, contacts: list[tuple[str, str, str]]) -> int:
        """Upsert a batch of (identifier, display_name, source) tuples. Returns count written."""
        assert self._db is not None
        now = int(time.time())
        await self._db.executemany(
            """
            INSERT INTO contacts (identifier, display_name, source, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(identifier) DO UPDATE SET
                display_name = excluded.display_name,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            [(ident, name, src, now) for ident, name, src in contacts],
        )
        await self._db.commit()
        return len(contacts)

    async def get_contacts(self) -> dict[str, str]:
        """Return mapping of identifier → display_name for all contacts."""
        assert self._db is not None
        async with self._db.execute("SELECT identifier, display_name FROM contacts") as cursor:
            rows = await cursor.fetchall()
        return {row["identifier"]: row["display_name"] for row in rows}

    async def resolve_identifier(self, identifier: str) -> str | None:
        """Look up a single identifier, return display_name or None."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT display_name FROM contacts WHERE identifier = ?", (identifier,)
        ) as cursor:
            row = await cursor.fetchone()
        return row["display_name"] if row else None

    async def count_memories(self) -> int:
        assert self._db is not None
        async with self._db.execute("SELECT COUNT(*) AS cnt FROM memories") as cursor:
            row = await cursor.fetchone()
        return row["cnt"] if row else 0

    # ── API keys ────────────────────────────────────────────────────────────
    # Only sha256 hashes are stored; see memoreei.auth.

    async def add_api_key(self, name: str, key_hash: str) -> bool:
        """Store a key hash under *name*. Returns False if the name is taken."""
        assert self._db is not None
        try:
            await self._db.execute(
                "INSERT INTO api_keys (name, key_hash, created_at) VALUES (?, ?, ?)",
                (name, key_hash, int(time.time())),
            )
        except aiosqlite.IntegrityError:
            return False
        await self._db.commit()
        return True

    async def list_api_keys(self) -> list[dict[str, Any]]:
        """Name, created_at and last_used_at for every key. Never the hash."""
        assert self._db is not None
        async with self._db.execute(
            "SELECT name, created_at, last_used_at FROM api_keys ORDER BY created_at, name"
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def api_key_hashes(self) -> list[tuple[str, str]]:
        """(name, key_hash) pairs, for the auth check only."""
        assert self._db is not None
        async with self._db.execute("SELECT name, key_hash FROM api_keys") as cursor:
            rows = await cursor.fetchall()
        return [(row["name"], row["key_hash"]) for row in rows]

    async def revoke_api_key(self, name: str) -> bool:
        assert self._db is not None
        cursor = await self._db.execute("DELETE FROM api_keys WHERE name = ?", (name,))
        await self._db.commit()
        return cursor.rowcount > 0

    async def touch_api_key(self, name: str, when: int | None = None) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE name = ?",
            (when if when is not None else int(time.time()), name),
        )
        await self._db.commit()

    # ── Dashboard logins ────────────────────────────────────────────────────
    # Hashes of one-time login tokens and session tokens; see memoreei.admin.auth.

    async def add_admin_token(self, token_hash: str, kind: str, ttl: int) -> None:
        assert self._db is not None
        now = int(time.time())
        await self._db.execute("DELETE FROM admin_tokens WHERE expires_at <= ?", (now,))
        await self._db.execute(
            "INSERT INTO admin_tokens (token_hash, kind, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, kind, now, now + ttl),
        )
        await self._db.commit()

    async def admin_token_valid(self, token_hash: str, kind: str) -> bool:
        assert self._db is not None
        async with self._db.execute(
            "SELECT 1 FROM admin_tokens WHERE token_hash = ? AND kind = ? AND expires_at > ?",
            (token_hash, kind, int(time.time())),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def take_admin_token(self, token_hash: str, kind: str) -> bool:
        """Delete a token, returning whether it was there and unexpired: single use."""
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM admin_tokens WHERE token_hash = ? AND kind = ? AND expires_at > ?",
            (token_hash, kind, int(time.time())),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def delete_admin_token(self, token_hash: str) -> None:
        assert self._db is not None
        await self._db.execute("DELETE FROM admin_tokens WHERE token_hash = ?", (token_hash,))
        await self._db.commit()

    # ── Registered import files ─────────────────────────────────────────────
    # Files imported locally, so `sync` can re-read them without being told a path.

    async def register_import(
        self, kind: str, path: str, options: dict[str, Any], mtime: float | None
    ) -> int:
        """Add or update a registered import. Returns its id."""
        assert self._db is not None
        now = int(time.time())
        await self._db.execute(
            """
            INSERT INTO import_sources (kind, path, options_json, added_at, last_synced_at, last_mtime)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, path) DO UPDATE SET
                options_json = excluded.options_json,
                last_synced_at = excluded.last_synced_at,
                last_mtime = excluded.last_mtime
            """,
            (kind, path, json.dumps(options, sort_keys=True), now, now, mtime),
        )
        await self._db.commit()
        async with self._db.execute(
            "SELECT id FROM import_sources WHERE kind = ? AND path = ?", (kind, path)
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["id"])

    async def list_imports(self) -> list[dict[str, Any]]:
        assert self._db is not None
        async with self._db.execute("SELECT * FROM import_sources ORDER BY id") as cursor:
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["options"] = json.loads(d.pop("options_json") or "{}")
            result.append(d)
        return result

    async def mark_import_synced(self, import_id: int, mtime: float) -> None:
        assert self._db is not None
        await self._db.execute(
            "UPDATE import_sources SET last_synced_at = ?, last_mtime = ? WHERE id = ?",
            (int(time.time()), mtime, import_id),
        )
        await self._db.commit()

    async def forget_import(self, import_id: int) -> bool:
        assert self._db is not None
        cursor = await self._db.execute("DELETE FROM import_sources WHERE id = ?", (import_id,))
        await self._db.commit()
        return cursor.rowcount > 0
