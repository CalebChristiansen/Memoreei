from __future__ import annotations

import json
import time
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

CREATE TABLE IF NOT EXISTS signal_checkpoint (
    conversation_id TEXT PRIMARY KEY,
    last_rowid INTEGER NOT NULL,
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


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))


def _embedding_to_blob(embedding: list[float]) -> bytes:
    return np.array(embedding, dtype=np.float32).tobytes()


class Database:
    def __init__(self, db_path: str = "./memoreei.db") -> None:
        self.db_path = str(Path(db_path).resolve())
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self.init_db()

    async def close(self) -> None:
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
        assert self._db is not None
        # Numpy cosine similarity fallback (sqlite-vec optional)
        if source_filter:
            async with self._db.execute(
                "SELECT * FROM memories WHERE embedding IS NOT NULL AND source = ?",
                (source_filter,),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with self._db.execute(
                "SELECT * FROM memories WHERE embedding IS NOT NULL"
            ) as cursor:
                rows = await cursor.fetchall()

        if not rows:
            return []

        scored: list[tuple[float, MemoryItem]] = []
        for row in rows:
            item = MemoryItem.from_row(dict(row))
            if item.embedding:
                sim = _cosine_similarity(embedding, item.embedding)
                scored.append((sim, item))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:limit]]

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

    async def delete_by_source(self, source: str) -> int:
        assert self._db is not None
        async with self._db.execute(
            "SELECT COUNT(*) as cnt FROM memories WHERE source = ?", (source,)
        ) as cursor:
            row = await cursor.fetchone()
        count = row["cnt"] if row else 0
        await self._db.execute("DELETE FROM memories WHERE source = ?", (source,))
        await self._db.commit()
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
