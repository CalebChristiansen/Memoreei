# Memoreei Architecture

## Overview

Memoreei is an MCP (Model Context Protocol) server that aggregates personal messages from multiple platforms into a single searchable database. It exposes search and sync tools over stdio, making it directly usable from Claude Code and Claude Desktop.

```
┌─────────────────┐     stdio      ┌──────────────────────────────┐
│   Claude Code   │ ◄────────────► │     MCP Server (FastMCP)     │
│  Claude Desktop │                │     src/memoreei/server.py   │
└─────────────────┘                └──────────────┬───────────────┘
                                                  │
                          ┌───────────────────────┼────────────────────────┐
                          │                       │                        │
               ┌──────────▼──────────┐ ┌──────────▼──────────┐  ┌────────▼────────┐
               │   Hybrid Search     │ │   Connectors         │  │    CLI          │
               │  search/hybrid.py   │ │  connectors/         │  │   cli.py        │
               └──────────┬──────────┘ └──────────┬──────────┘  └─────────────────┘
                          │                       │
               ┌──────────▼───────────────────────▼──────────┐
               │              Storage Layer                    │
               │          storage/database.py                  │
               │    SQLite + FTS5 (keyword) + float32 blobs   │
               └──────────────────────────────────────────────┘
```

## Hybrid Search

Search combines two ranking methods fused via Reciprocal Rank Fusion (RRF).

### BM25 (Keyword Search)

SQLite's FTS5 virtual table provides BM25 ranking over message `content` and `summary` fields. A trigger-based sync keeps the FTS index in lockstep with the `memories` table.

### Vector Search

Each memory is embedded using one of two providers:
- **FastEmbed** (default) — `BAAI/bge-small-en-v1.5`, runs locally via ONNX, 384-dimensional
- **OpenAI** — `text-embedding-3-small`, 1536-dimensional, requires `OPENAI_API_KEY`

Embeddings are stored as raw float32 binary blobs. At query time, cosine similarity is computed in Python with numpy.

### RRF Fusion

```
score(id) = Σ  1 / (60 + rank_i)
```

Both rankers return `max(limit * 3, 30)` candidates. Each result gets a score that sums its reciprocal ranks across the two rankers. Items appearing in both sets get naturally boosted. The constant `k=60` is the standard from the original RRF paper.

Post-fusion filters (`source`, `participant`, `after`, `before`) are applied to the merged ranked list before returning the top N results.

## Database Schema

### Core Table

```sql
CREATE TABLE memories (
    id          TEXT PRIMARY KEY,   -- ULID
    source      TEXT NOT NULL,      -- "discord", "whatsapp:Chat Name", "manual"
    source_id   TEXT,               -- platform message ID (dedup key)
    content     TEXT NOT NULL,      -- full message text
    summary     TEXT,               -- optional short summary
    participants TEXT,              -- JSON array ["Alice", "Bob"]
    ts          INTEGER NOT NULL,   -- unix timestamp (message time)
    ingested_at INTEGER NOT NULL,   -- when added to DB
    metadata    TEXT,               -- JSON object (custom fields per source)
    embedding   BLOB                -- float32 vector as numpy tobytes()
);
```

Deduplication is enforced by a unique index on `(source, source_id)` for rows where `source_id IS NOT NULL`. This means syncing the same source twice is safe — existing messages are skipped.

### FTS5 Index

```sql
CREATE VIRTUAL TABLE memories_fts USING fts5(
    content, summary,
    content=memories,
    content_rowid=rowid
);
```

Three triggers (`INSERT`, `DELETE`, `UPDATE`) keep `memories_fts` current.

### Connector Checkpoints

Each connector has its own checkpoint table tracking the last successfully synced position:

| Table | Key | Checkpoint value |
|-------|-----|-----------------|
| `discord_checkpoint` | `channel_id` | last Discord message snowflake ID |
| `telegram_checkpoint` | `chat_id` | last Telegram update ID |
| `matrix_checkpoint` | `room_id` | pagination token (`prev_batch`) |
| `slack_checkpoint` | `channel_id` | last message timestamp |
| `email_checkpoint` | `"email:folder"` | last IMAP UID |

On the next sync, the connector fetches only messages newer than its checkpoint.

## Connector Architecture

All connectors implement `BaseConnector`:

```python
class BaseConnector(ABC):
    name: str = "unknown"

    @abstractmethod
    async def sync(self, **kwargs) -> SyncResult:
        """Run incremental sync. Returns SyncResult(synced, source, errors)."""

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Return True if required env vars/files are present."""
```

A connector's `sync()` method:
1. Reads its checkpoint from the database
2. Fetches messages from the platform API since that checkpoint
3. Converts them to `MemoryItem` objects (with `source_id` set for dedup)
4. Calls the embedder in batches
5. Bulk-inserts into the database (upsert on conflict)
6. Updates the checkpoint

The connector registry in `connectors/__init__.py` maps connector names to classes and supports lazy instantiation.

## MCP Tool Flow

```
Claude calls search_memory("what did Alice say about the meeting")
        │
        ▼
server.py: _get_tools() → lazy-init Database + HybridSearch
        │
        ▼
memory_tools.py: search()
        │
        ├── db.search_fts(query, limit=30)      → BM25 ranked list
        ├── embedder.embed([query])              → query vector
        └── db.search_vector(vector, limit=30)  → cosine ranked list
        │
        ▼
hybrid.py: RRF fusion → top 10 results
        │
        ▼
Returns list of dicts: [{id, source, content, participants, ts, score}]
```

Tools are registered on the `FastMCP` instance at module load time and dispatched via stdio JSON-RPC.
