from __future__ import annotations

import asyncio
import json
import sqlcipher3
import sys
import time
from pathlib import Path
from typing import Any

import aiosqlite
import numpy as np

from memoreei.search.people import GROUP_SQL, PHONE_SQL, SENDER_SQL
from memoreei.storage.key import connect, connect_async
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

-- The dashboard's one account (memoreei.admin.account): an optional password (an scrypt
-- hash), optional 2FA (an authenticator's secret, and the last step used, so a code
-- works once), and whether the dashboard answers away from this computer.
CREATE TABLE IF NOT EXISTS admin_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT,
    password_set_at INTEGER,
    totp_secret TEXT,
    totp_pending TEXT,
    totp_last_step INTEGER,
    totp_set_at INTEGER,
    remote INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

-- Recovery codes for a lost authenticator: hashes, each good once.
CREATE TABLE IF NOT EXISTS admin_recovery_codes (
    code_hash TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    used_at INTEGER
);

-- Dashboard sessions. 'link' came from a one-time link (admin-url, the Mac app's menu)
-- and works only on this computer, 'password' from the password, anywhere it's allowed.
-- Sessions from before this table are in admin_tokens, and count as 'link'.
CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash TEXT PRIMARY KEY,
    how TEXT NOT NULL CHECK (how IN ('link', 'password')),
    peer TEXT,
    user_agent TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

-- Browsers that have signed in with the password before. They skip the limit on
-- everyone's failed sign-ins, so a stranger's guessing can't keep the owner out.
CREATE TABLE IF NOT EXISTS admin_browsers (
    token_hash TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

-- Sign-ins to the dashboard, good and bad, for the Security page. Kept 90 days.
CREATE TABLE IF NOT EXISTS admin_signins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    peer TEXT,
    how TEXT NOT NULL,
    ok INTEGER NOT NULL,
    why TEXT
);

-- Every authenticated call to the public listener (memoreei.public): who, from where,
-- what. Never the arguments or the answer. Kept 90 days.
CREATE TABLE IF NOT EXISTS access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    peer TEXT,
    kind TEXT NOT NULL,
    client TEXT,
    subject TEXT,
    method TEXT,
    tool TEXT,
    status INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS access_log_at ON access_log(at);

-- Each OAuth client or key the public listener has let in, and when it first did.
CREATE TABLE IF NOT EXISTS access_clients (
    kind TEXT NOT NULL,
    client TEXT NOT NULL,
    first_seen_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, client)
);

-- memoreei's own OAuth sign-in for chatbots and agents (memoreei.oauth).
-- A CIMD client is its document's URL, cached here, and one that registered itself (DCR)
-- gets an id of ours. metadata is the JSON as checked.
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('cimd', 'dcr')),
    metadata TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

-- A chatbot or agent asking to connect, waiting for the person at this computer to
-- approve it. 'authorize' came from a browser (and only that browser, by a cookie's
-- hash, can collect the answer), and 'device' from an agent polling with a device code.
-- user_code is shown on both sides, so the person can tell which request is which.
CREATE TABLE IF NOT EXISTS oauth_requests (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('authorize', 'device')),
    user_code TEXT NOT NULL,
    client_id TEXT NOT NULL,
    client_name TEXT NOT NULL,
    client_host TEXT NOT NULL,
    self_registered INTEGER NOT NULL,
    peer TEXT,
    resource TEXT NOT NULL,
    redirect_uri TEXT,
    state TEXT,
    code_challenge TEXT,
    browser_hash TEXT,
    device_code_hash TEXT UNIQUE,
    interval INTEGER,
    last_poll_at REAL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    decided_at INTEGER,
    approved INTEGER,
    finished_at INTEGER,
    grant_id TEXT
);

-- One yes to one chatbot. Its codes, access tokens and refresh tokens hang off it,
-- and removing it ends them all at once. refresh_generation counts rotations: only
-- the newest refresh token works (and the one before, for 30 seconds).
CREATE TABLE IF NOT EXISTS oauth_grants (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    client_name TEXT NOT NULL,
    client_host TEXT NOT NULL,
    self_registered INTEGER NOT NULL,
    resource TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_used_at INTEGER,
    refresh_generation INTEGER NOT NULL DEFAULT 0,
    refreshed_at INTEGER,
    revoked_at INTEGER,
    revoked_why TEXT
);

-- Authorization codes, a minute each. The grant is made when one is first redeemed.
CREATE TABLE IF NOT EXISTS oauth_codes (
    code_hash TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    used_at INTEGER,
    grant_id TEXT
);

-- Access tokens: opaque, ten minutes, hashes only, like api_keys.
CREATE TABLE IF NOT EXISTS oauth_access_tokens (
    token_hash TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);

-- private_key_jwt assertions seen, so none is accepted twice.
CREATE TABLE IF NOT EXISTS oauth_jtis (
    client_id TEXT NOT NULL,
    jti TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (client_id, jti)
);

-- The key refresh tokens are signed with. Inside the encrypted database, like everything.
CREATE TABLE IF NOT EXISTS oauth_keys (
    id TEXT PRIMARY KEY,
    secret BLOB NOT NULL,
    created_at INTEGER NOT NULL
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


# Words too common to narrow a keyword search on their own (Database.search_fts).
_COMMON_WORDS = frozenset("""
a about after again all also am an and any are as at be because been before being but by
can could did do does doing don done for from get got had has have having he her here him
his how i if in into is it its just like me more most my no not now of on one or our out
over said say says she should so some than that the their them then there these they this
to too u up us very was we were what when where which who why will with would yes you your
going gonna want wanted tell told ask asked think know
""".split())


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


class _Rows:
    """One embedding model's rows for an index, each vector copied into the matrix as it
    arrives.

    Holding every row until the end kept each embedding twice, once as SQLite's bytes and
    once in the matrix, and the allocator never handed the bytes' half back: about 250 MB
    at 155k rows, which a 4 GB MacBook Air felt. The matrix is allocated for every row that
    might come; the system only gives it memory for the rows that do.
    """

    def __init__(self, dim: int, capacity: int) -> None:
        self.matrix = np.empty((capacity, dim), np.float32)
        self.ids: list[str] = []
        self.columns: tuple[list, ...] = ([], [], [], [], [])  # source, sender, phone, ts, group

    def add(self, row: tuple, vector: np.ndarray) -> None:
        n = len(self.ids)
        if n == len(self.matrix):  # more rows than counted: grow rather than drop
            grown = np.empty((max(2 * n, 1), self.matrix.shape[1]), np.float32)
            grown[:n] = self.matrix
            self.matrix = grown
        self.matrix[n] = vector
        self.ids.append(row[1])
        for column, value in zip(self.columns, row[2:3] + row[4:]):
            column.append(value)


class _Batch:
    """Rows read for an index, by embedding dimension."""

    def __init__(self, capacity: int, dim: int | None = None) -> None:
        self.capacity, self.dim = capacity, dim
        self.by_dim: dict[int, _Rows] = {}
        self.read = 0  # rows seen, with an embedding or not

    def add(self, rows: list) -> None:
        """Take (rowid, id, source, embedding, sender, phone, ts, group) rows."""
        self.read += len(rows)
        for row in rows:
            vector = _stored_to_vector(row[3])
            if vector is None or self.dim not in (None, vector.shape[0]):
                continue
            dim = vector.shape[0]
            if dim not in self.by_dim:
                self.by_dim[dim] = _Rows(dim, self.capacity)
            self.by_dim[dim].add(row, vector)

    def best(self) -> _Rows | None:
        """The rows from the embedding model most rows came from: another model's vectors
        are not comparable."""
        return max(self.by_dim.values(), key=lambda rows: len(rows.ids), default=None)


class _VectorIndex:
    """The embeddings of the memories table, as a matrix, with ids and sources alongside.

    Beside each row it also keeps what a search can be narrowed by (sender, the sender's
    number, time, whether the chat is a group), so filters mask the matrix before it is
    scored rather than throwing away winners afterwards (see search.people.Filter).
    """

    def __init__(self, ids: list[str], sources: np.ndarray, matrix: np.ndarray,
                 count: int, max_rowid: int, norms: np.ndarray | None = None,
                 senders: np.ndarray | None = None, phones: np.ndarray | None = None,
                 ts: np.ndarray | None = None, groups: np.ndarray | None = None) -> None:
        n = len(ids)
        self.ids, self.sources, self.matrix = ids, sources, matrix
        self.norms = _norms(matrix) if norms is None else norms
        self.senders = np.full(n, "", dtype=object) if senders is None else senders
        self.phones = np.full(n, "", dtype=object) if phones is None else phones
        self.ts = np.zeros(n, np.int64) if ts is None else ts
        self.groups = np.zeros(n, bool) if groups is None else groups
        self.count, self.max_rowid = count, max_rowid
        self.data_version = 0  # SQLite's, when this was read; set by the Database
        self._sender_counts: tuple[list[str], list[int]] | None = None
        self._codes: dict[str, tuple[dict[str, int], np.ndarray]] = {}
        self.directory: Any = None  # search.people.Directory, cached by MemoryTools

    @classmethod
    def empty(cls) -> "_VectorIndex":
        return cls([], np.array([], dtype=object), np.zeros((0, 0), np.float32), 0, 0)

    def _columns(self) -> tuple[np.ndarray, ...]:
        return self.sources, self.senders, self.phones, self.ts, self.groups

    def append(self, batch: "_Batch", count: int, max_rowid: int) -> "_VectorIndex":
        """A new index with *batch*'s rows added."""
        rows = batch.best()
        if rows is None:
            return _VectorIndex(self.ids, self.sources, self.matrix, count, max_rowid, self.norms,
                                self.senders, self.phones, self.ts, self.groups)

        fresh = rows.matrix[:len(rows.ids)]
        # A first build takes the batch's matrix as it is: no second copy of every embedding
        matrix = np.concatenate([self.matrix, fresh]) if len(self.matrix) else fresh
        norms = np.concatenate([self.norms, _norms(fresh)])
        added = rows.columns
        new = (np.array(added[0], dtype=object),
               np.array([s or "" for s in added[1]], dtype=object),
               np.array([p or "" for p in added[2]], dtype=object),
               np.array([t or 0 for t in added[3]], dtype=np.int64),
               np.array([bool(g) for g in added[4]], dtype=bool))
        sources, senders, phones, ts, groups = (
            np.concatenate([mine, theirs]) for mine, theirs in zip(self._columns(), new))
        index = _VectorIndex(self.ids + rows.ids, sources, matrix, count,
                             max_rowid, norms, senders, phones, ts, groups)
        # Carry the codes forward, adding only the new rows', rather than recode them all
        for column, added_values in (("sources", new[0]), ("senders", new[1]), ("phones", new[2])):
            if column in self._codes:
                vocab, codes = self._codes[column]
                vocab = dict(vocab)
                extra = np.fromiter((vocab.setdefault(v, len(vocab)) for v in added_values.tolist()),
                                    np.int32, len(added_values))
                index._codes[column] = (vocab, np.concatenate([codes, extra]))
        return index

    def without_source(self, source: str, count: int, max_rowid: int) -> "_VectorIndex":
        """A new index without *source*'s rows."""
        keep = self.sources != source
        index = _VectorIndex([i for i, k in zip(self.ids, keep) if k], self.sources[keep],
                             self.matrix[keep], count, max_rowid, self.norms[keep],
                             *(column[keep] for column in self._columns()[1:]))
        index.data_version = self.data_version
        return index

    def _coded(self, column: str) -> tuple[dict[str, int], np.ndarray]:
        """*column* as integer codes and the value each stands for, computed once."""
        if column not in self._codes:
            values, codes = np.unique(getattr(self, column).astype(str), return_inverse=True)
            self._codes[column] = ({v: n for n, v in enumerate(values.tolist())},
                                   codes.astype(np.int32))
        return self._codes[column]

    def prepare(self) -> "_VectorIndex":
        """Code every filter column now, in the worker thread, not in the first search."""
        for column in ("sources", "senders", "phones"):
            self._coded(column)
        return self

    def vocab(self, column: str) -> dict[str, int]:
        """Every distinct value of *column*."""
        return self._coded(column)[0]

    def isin(self, column: str, values: set[str]) -> np.ndarray:
        """A mask of the rows whose *column* is one of *values*."""
        vocab, codes = self._coded(column)
        wanted = [vocab[v] for v in values if v in vocab]
        if not wanted:
            return np.zeros(len(codes), bool)
        return np.isin(codes, np.array(wanted, np.int32))

    def sender_counts(self) -> tuple[list[str], list[int]]:
        """Each sender as stored, and how many rows it sent; computed once per index."""
        if self._sender_counts is None:
            vocab, codes = self._coded("senders")
            counts = np.bincount(codes, minlength=len(vocab)).tolist() if len(codes) else []
            self._sender_counts = (list(vocab), counts)
        return self._sender_counts

    def rank(self, query: np.ndarray, limit: int, keep: np.ndarray | None = None) -> list[str]:
        """The ids of the *limit* rows nearest *query* by cosine similarity, nearest first.

        With *keep* (a mask over the rows), only those rows are scored.
        """
        if not self.ids or self.matrix.shape[1] != query.shape[0]:
            return []
        candidates = np.flatnonzero(keep) if keep is not None else None
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
    after = base.max_rowid if base else 0
    conn = connect(db_path)
    try:
        conn.execute("BEGIN")
        count, max_rowid = conn.execute(
            "SELECT count(*), coalesce(max(rowid), 0) FROM memories").fetchone()
        if base is not None and len(base.matrix):
            batch = _Batch(max(count - base.count, 0), base.matrix.shape[1])
        else:
            batch = _Batch(count)
        cursor = conn.execute(
            f"SELECT rowid, id, source, embedding, {SENDER_SQL}, {PHONE_SQL}, ts, {GROUP_SQL} "
            "FROM memories WHERE rowid > ? ORDER BY rowid",
            (after,),
        )
        while chunk := cursor.fetchmany(_STEP):
            batch.add(chunk)
        conn.rollback()
    finally:
        conn.close()
    if base is not None and base.count + batch.read != count:
        return None
    index = (base or _VectorIndex.empty()).append(batch, count, max_rowid).prepare()
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
        self._db = await connect_async(self.db_path)
        self._db.row_factory = sqlcipher3.Row
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
        except sqlcipher3.IntegrityError:
            pass
        return memory.id

    async def bulk_insert(self, memories: list[MemoryItem]) -> int:
        inserted = 0
        for m in memories:
            await self.insert_memory(m)
            inserted += 1
        return inserted

    async def search_fts(self, query: str, limit: int = 10,
                         where: tuple[str, list] | None = None, any_words: bool = False,
                         stats: dict | None = None) -> list[MemoryItem]:
        """Keyword matches, best first; *where* (SQL over memories, and its parameters)
        narrows them before the limit is applied.

        Messages with every word come first. With *any_words*, messages with only some
        of them fill the rest of the limit, ranked by BM25, which weighs rare words most;
        so a word the model guessed ("ordered food") doesn't hide the message that has
        the other ("ordered a sensor"). *stats* gets ``all_words``: how many had every word.
        """
        assert self._db is not None
        # Words only, each quoted: FTS5 reads punctuation (a path's "/", a URL's ":", a
        # hyphen) and bare AND/OR/NOT as syntax, and a syntax error fails the search.
        import re as _re
        words = _re.findall(r"\w+", query)
        if not words:
            return []
        quoted = [f'"{w}"' for w in words]
        clause, params = where or ("1", [])
        sql = f"""
            SELECT m.*, memories_fts.rank as fts_rank
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.rowid
            WHERE memories_fts MATCH ? AND ({clause})
            ORDER BY memories_fts.rank
            LIMIT ?
            """
        async with self._db.execute(sql, (" ".join(quoted), *params, limit)) as cursor:
            rows = list(await cursor.fetchall())
        if stats is not None:
            stats["all_words"] = len(rows)
        # Common words left out: "to" OR "on" matches most of an archive, and ranking that
        # many rows took seconds on 155k messages, for words that rank nothing anyway.
        rare = [f'"{w}"' for w in words if w.lower() not in _COMMON_WORDS]
        if any_words and len(quoted) > 1 and rare and len(rows) < limit:
            have = {row["id"] for row in rows}
            async with self._db.execute(sql, (" OR ".join(rare), *params, limit + len(rows))) as cursor:
                rows += [row for row in await cursor.fetchall() if row["id"] not in have][:limit - len(rows)]
        return [MemoryItem.from_row(dict(row)) for row in rows]

    async def search_vector(
        self, embedding: list[float], limit: int = 10, keep: Any = None
    ) -> list[MemoryItem]:
        """The memories nearest *embedding* by cosine similarity.

        Scored in one matrix product over every embedding, held in memory between
        searches (see _vector_index); whole rows are fetched for the winners alone.
        *keep*, a callable given the index, returns a mask of the rows that may win.
        """
        assert self._db is not None
        index = await self._vector_index()
        query = np.asarray(embedding, dtype=np.float32)
        mask = keep(index) if keep is not None else None
        winners = await asyncio.to_thread(index.rank, query, limit, mask)
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
        """The memory, with up to *before* messages before it and *after* after it, in
        its own chat, oldest first."""
        assert self._db is not None
        target = await self.get_by_id(memory_id)
        if not target:
            return []
        async with self._db.execute(
            "SELECT rowid FROM memories WHERE id = ?", (memory_id,)
        ) as cursor:
            (rowid,) = await cursor.fetchone()
        # Ordered by time, then rowid for messages sent in the same second.
        async with self._db.execute(
            "SELECT * FROM memories WHERE source = ? AND (ts < ? OR (ts = ? AND rowid < ?)) "
            "ORDER BY ts DESC, rowid DESC LIMIT ?",
            (target.source, target.ts, target.ts, rowid, max(before, 0)),
        ) as cursor:
            earlier = [MemoryItem.from_row(dict(r)) for r in await cursor.fetchall()]
        async with self._db.execute(
            "SELECT * FROM memories WHERE source = ? AND (ts > ? OR (ts = ? AND rowid > ?)) "
            "ORDER BY ts ASC, rowid ASC LIMIT ?",
            (target.source, target.ts, target.ts, rowid, max(after, 0)),
        ) as cursor:
            later = [MemoryItem.from_row(dict(r)) for r in await cursor.fetchall()]
        return earlier[::-1] + [target] + later

    async def list_sources(self) -> dict[str, int]:
        assert self._db is not None
        async with self._db.execute(
            "SELECT source, COUNT(*) as cnt FROM memories GROUP BY source ORDER BY cnt DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["source"]: row["cnt"] for row in rows}

    async def source_summary(self) -> list[dict[str, Any]]:
        """Each source with its message count, whether it is a group chat and its name, largest first.

        Group and name come from each source's newest row, so this costs one indexed
        GROUP BY and a row per source, not a JSON parse of every message.
        """
        assert self._db is not None
        async with self._db.execute(
            "SELECT m.source, c.cnt, m.metadata FROM "
            "(SELECT source, COUNT(*) AS cnt, MAX(rowid) AS last FROM memories GROUP BY source) c "
            "JOIN memories m ON m.rowid = c.last ORDER BY c.cnt DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        summary = []
        for r in rows:
            try:
                meta = json.loads(r["metadata"]) if r["metadata"] else {}
            except ValueError:
                meta = {}
            group = bool(meta.get("is_group")) or r["source"].startswith("imessage:chat")
            summary.append({"source": r["source"], "count": r["cnt"], "group": group,
                            "chat_name": meta.get("chat_name") or None})
        return summary

    async def platforms(self) -> list[str]:
        """The kinds of source stored ('imessage' for 'imessage:…'), alphabetically."""
        return sorted({s.partition(":")[0] for s in await self.list_sources()})

    async def search_index(self) -> "_VectorIndex":
        """The in-memory index, as searches see it (for resolving who a filter means)."""
        return await self._vector_index()

    def invalidate_vectors(self) -> None:
        """Rebuild the vector index on the next search: rows changed in ways it can't see."""
        self._vector_epoch += 1
        if self._vectors is not None:
            self._vectors.data_version = -1

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

    async def forget_signal_seen(self, message_ids: list[str]) -> None:
        assert self._db is not None
        await self._db.executemany(
            "DELETE FROM signal_seen WHERE message_id = ?", [(m,) for m in message_ids]
        )
        await self._db.commit()

    async def mark_deleted(self, source_prefix: str, message_ids: list[str]) -> int:
        """Set metadata.deleted on the memories whose source_id ends in these message ids.

        For messages that left their app altogether, whose chat is no longer known.
        Returns how many memories were marked.
        """
        assert self._db is not None
        marked = 0
        for message_id in message_ids:
            cursor = await self._db.execute(
                "UPDATE memories SET metadata = json_set(coalesce(metadata, '{}'), '$.deleted', json('true')) "
                "WHERE source LIKE ? AND source_id = source || ':' || ? "
                "AND coalesce(json_extract(metadata, '$.deleted'), 0) = 0",
                (f"{source_prefix}:%", message_id),
            )
            marked += cursor.rowcount
        await self._db.commit()
        return marked

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
        except sqlcipher3.IntegrityError:
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

    # ── The public listener's access log ────────────────────────────────────
    # See memoreei.public.

    async def record_access(self, entry: dict[str, Any], keep_seconds: int = 90 * 86400,
                            when: int | None = None) -> None:
        assert self._db is not None
        now = when if when is not None else int(time.time())
        client = entry.get("client") or "?"
        await self._db.execute(
            "INSERT INTO access_log (at, peer, kind, client, subject, method, tool, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now, entry.get("peer"), entry["kind"], client, entry.get("subject"),
             entry.get("method"), entry.get("tool"), int(entry.get("status") or 0)),
        )
        await self._db.execute(
            "INSERT INTO access_clients (kind, client, first_seen_at, last_seen_at, calls) "
            "VALUES (?, ?, ?, ?, 1) ON CONFLICT(kind, client) DO UPDATE SET "
            "last_seen_at = excluded.last_seen_at, calls = calls + 1",
            (entry["kind"], client, now, now),
        )
        if now - getattr(self, "_access_pruned_at", 0) >= 3600:  # an hourly sweep is plenty
            self._access_pruned_at = now
            await self._db.execute("DELETE FROM access_log WHERE at < ?", (now - keep_seconds,))
        await self._db.commit()

    async def list_access(self, limit: int = 100) -> list[dict[str, Any]]:
        assert self._db is not None
        async with self._db.execute(
            "SELECT at, peer, kind, client, subject, method, tool, status FROM access_log "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def list_access_clients(self) -> list[dict[str, Any]]:
        assert self._db is not None
        async with self._db.execute(
            "SELECT kind, client, first_seen_at, last_seen_at, calls FROM access_clients "
            "ORDER BY last_seen_at DESC"
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

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
