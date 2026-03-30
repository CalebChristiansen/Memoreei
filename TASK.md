# Memoreei — Build Task for Claude Code

You are building **Memoreei**, an open-source MCP server that acts as a personal memory backend. It ingests personal data (WhatsApp exports, Discord messages) into a searchable hybrid database (keyword + vector) and exposes it via MCP tools.

## What to Build

### Project Structure
```
memoreei/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── data/
│   └── samples/
│       ├── whatsapp_printer_conspiracy.txt
│       ├── whatsapp_pizza_wars.txt
│       └── whatsapp_donut_heist.txt
├── src/
│   └── memoreei/
│       ├── __init__.py
│       ├── server.py              # MCP server entry point
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── database.py        # SQLite + FTS5 + vector
│       │   └── models.py          # Data models (dataclasses)
│       ├── search/
│       │   ├── __init__.py
│       │   ├── embeddings.py      # Embedding client (fastembed default, openai optional)
│       │   └── hybrid.py          # Hybrid search + RRF fusion
│       ├── connectors/
│       │   ├── __init__.py
│       │   ├── whatsapp.py        # WhatsApp .txt export parser
│       │   └── discord_connector.py  # Discord channel polling
│       └── tools/
│           ├── __init__.py
│           └── memory_tools.py    # MCP tool definitions
├── tests/
│   ├── test_storage.py
│   ├── test_search.py
│   ├── test_whatsapp_parser.py
│   └── test_discord.py
└── scripts/
    └── seed_data.py               # Seed DB with sample WhatsApp data
```

### Tech Stack
- **Python 3.10+**
- **mcp[cli]** — MCP Python SDK for the server
- **fastembed** — local ONNX embeddings (default, no API key needed)
- **openai** — optional embedding backend
- **sqlite3** + **sqlite-vec** — storage + vector search
- **discord.py** — Discord channel polling
- **python-dotenv** — env config

### pyproject.toml
Use a proper pyproject.toml with `[project]` metadata. The package should be installable with `pip install -e .`. Entry point for the MCP server should work with `python -m memoreei.server` or via the mcp CLI.

---

## Objective 1: Fake WhatsApp Export Files

Create 3 hilarious fake WhatsApp chat exports in `data/samples/`. Use the real WhatsApp export format:
```
[MM/DD/YY, HH:MM:SS] Name: message
```

### Chat 1: `whatsapp_printer_conspiracy.txt`
A group chat where Alice becomes convinced the office printer is sentient. She provides increasingly unhinged evidence. Bob is skeptical. Charlie just wants to print his TPS reports. The printer starts "responding" via printed pages. 30-50 messages.

### Chat 2: `whatsapp_pizza_wars.txt`
An argument about pizza toppings that escalates from casual preference to full diplomatic crisis. Someone brings up pineapple and it goes nuclear. Alliances form. A UN-style peace accord is drafted. 30-50 messages.

### Chat 3: `whatsapp_donut_heist.txt`
A group planning an elaborate heist to steal the last conference room donut. They assign roles (lookout, distraction, extraction specialist). The plan gets increasingly Mission Impossible. Someone eats the donut during planning. 30-50 messages.

Make them genuinely funny. These are for a hackathon demo.

---

## Objective 2: Storage Layer

### SQLite Schema
```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT,
    content TEXT NOT NULL,
    summary TEXT,
    participants TEXT,  -- JSON array of strings
    ts INTEGER NOT NULL,  -- unix epoch
    ingested_at INTEGER NOT NULL,
    metadata TEXT,  -- JSON object
    embedding BLOB
);

CREATE UNIQUE INDEX idx_memories_dedup ON memories(source, source_id);
CREATE INDEX idx_memories_ts ON memories(ts DESC);
CREATE INDEX idx_memories_source ON memories(source);

CREATE VIRTUAL TABLE memories_fts USING fts5(content, summary, content=memories, content_rowid=rowid);
```

For vector search, use **sqlite-vec** extension if available. If sqlite-vec fails to load (it can be tricky), fall back to storing embeddings as JSON arrays in the embedding column and doing numpy cosine similarity in Python. The fallback MUST work — don't let the demo break on a C extension issue.

### Database class (`storage/database.py`)
- `insert_memory(memory: MemoryItem) -> str` — insert with dedup
- `search_fts(query: str, limit: int = 10) -> list[MemoryItem]` — FTS5 search
- `search_vector(embedding: list[float], limit: int = 10) -> list[MemoryItem]` — vector similarity
- `get_by_id(id: str) -> MemoryItem | None`
- `get_context(memory_id: str, before: int = 5, after: int = 5) -> list[MemoryItem]` — thread context
- `list_sources() -> dict[str, int]` — source counts
- `delete_by_source(source: str)` — bulk delete
- `init_db()` — create tables

### Models (`storage/models.py`)
Use Python dataclasses:
```python
@dataclass
class MemoryItem:
    id: str  # ULID or UUID
    source: str
    source_id: str | None
    content: str
    summary: str | None
    participants: list[str]
    ts: int  # unix epoch
    ingested_at: int
    metadata: dict
    embedding: list[float] | None = None
```

---

## Objective 3: Embedding Pipeline

### `search/embeddings.py`
Create an abstract base + two implementations:

```python
class EmbeddingProvider:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...
    async def embed_query(self, text: str) -> list[float]:
        ...

class FastEmbedProvider(EmbeddingProvider):
    # Uses fastembed with "BAAI/bge-small-en-v1.5" model
    # Local, no API key needed

class OpenAIProvider(EmbeddingProvider):
    # Uses openai text-embedding-3-small
    # Requires OPENAI_API_KEY env var
```

Select provider based on config/env. Default to FastEmbed.

---

## Objective 4: Hybrid Search + RRF

### `search/hybrid.py`

Implement Reciprocal Rank Fusion:
1. Run FTS5 query → get ranked results
2. Run vector similarity query → get ranked results
3. Merge with RRF: `score = sum(1 / (k + rank))` where k=60

Support metadata filters:
- `source` — filter by data source
- `participant` — filter by participant name
- `after` / `before` — unix timestamp range
- `limit` — max results

---

## Objective 5: WhatsApp Export Parser

### `connectors/whatsapp.py`

Parse the standard WhatsApp export `.txt` format:
```
[MM/DD/YY, HH:MM:SS] Sender Name: message content
```

Handle:
- Multiline messages (continuation lines don't start with `[`)
- System messages (joined, left, changed subject) — skip these
- Media placeholders (`<Media omitted>`) — store as metadata note

Return list of `MemoryItem` objects ready for insertion.

---

## Objective 6: Discord Live Connector

### `connectors/discord_connector.py`

Read messages from a specific Discord channel using discord.py.

**Channel ID:** REDACTED_CHANNEL_ID (`#memoreei-demo` on Karamja server)

**Bot token access:** The token is stored in Bitwarden. For the build, read it from the `DISCORD_BOT_TOKEN` env var in `.env`.

To get the token for .env setup, run:
```bash
source ~/.config/bw/credentials
export BW_CLIENTID BW_CLIENTSECRET
MASTER=$(cat ~/.config/bw/master_password)
BW_SESSION=$(~/.local/bin/bw unlock "$MASTER" --raw)
~/.local/bin/bw get password "discord elliot" --session "$BW_SESSION"
```

**Implementation:**
- On startup or `sync_discord()` call: fetch messages since last checkpoint
- Store checkpoint (last message ID) in a small SQLite table or file
- Convert Discord messages to MemoryItem format
- Embed and store

**Important:** The Discord connector should work as a one-shot sync (not a persistent bot). Call `sync_discord()` → it fetches new messages, ingests them, returns count. No persistent websocket connection needed for the MCP server.

Use the Discord HTTP API directly (via `aiohttp` or `discord.py`'s HTTP client) rather than running a full bot gateway. This keeps the MCP server simple.

---

## Objective 7: MCP Server

### `server.py`

Use the `mcp` Python SDK to create a stdio-based MCP server.

**Tools to expose:**

#### `search_memory`
```python
@server.tool()
async def search_memory(
    query: str,
    limit: int = 10,
    source: str | None = None,
    participant: str | None = None,
    after: str | None = None,  # ISO date string
    before: str | None = None  # ISO date string
) -> list[dict]:
    """Search your personal memories using hybrid keyword + semantic search."""
```

#### `get_context`
```python
@server.tool()
async def get_context(
    memory_id: str,
    before: int = 5,
    after: int = 5
) -> list[dict]:
    """Get surrounding messages/context for a specific memory."""
```

#### `add_memory`
```python
@server.tool()
async def add_memory(
    content: str,
    source: str = "manual",
    metadata: dict | None = None
) -> dict:
    """Add a manual memory/note to your personal memory store."""
```

#### `list_sources`
```python
@server.tool()
async def list_sources() -> dict:
    """List all data sources and their message counts."""
```

#### `ingest_whatsapp`
```python
@server.tool()
async def ingest_whatsapp(file_path: str) -> dict:
    """Import a WhatsApp chat export .txt file into memory."""
```

#### `sync_discord`
```python
@server.tool()
async def sync_discord(channel_id: str | None = None) -> dict:
    """Sync recent Discord messages from the configured channel."""
```

### Server config
- Read `.env` for `OPENAI_API_KEY`, `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`
- Default embedding provider: fastembed
- If `OPENAI_API_KEY` is set and `EMBEDDING_PROVIDER=openai`, use OpenAI
- DB path: `./memoreei.db` (configurable via `MEMOREEI_DB_PATH`)

### Running the server
The server should be runnable as:
```bash
python -m memoreei.server
# or
mcp run src/memoreei/server.py
```

---

## Objective 8: Tests

Write basic tests in `tests/`. They don't need to be exhaustive — just enough to verify core functionality works:

- `test_storage.py` — insert, search FTS, search vector, dedup
- `test_search.py` — hybrid search returns results, RRF ranking works
- `test_whatsapp_parser.py` — parses sample files correctly
- Run with: `python -m pytest tests/`

---

## Objective 9: README

Write a professional README.md:
- Project name + tagline
- What it does (2-3 sentences)
- ASCII architecture diagram
- Quick start (install, configure, run)
- MCP tool documentation
- Supported data sources
- Configuration (.env options)
- Privacy note (local-first, your data stays yours)
- License: MIT

---

## Objective 10: seed_data.py script

A script that:
1. Parses all WhatsApp sample files from `data/samples/`
2. Embeds and inserts them into the database
3. Prints counts

Run as: `python scripts/seed_data.py`

---

## Build Order

1. Create project structure + pyproject.toml + .gitignore + .env.example
2. Write fake WhatsApp exports
3. Build storage layer + models
4. Build embedding pipeline
5. Build hybrid search
6. Build WhatsApp parser
7. Build Discord connector
8. Build MCP server
9. Write tests + run them
10. Write seed_data.py + run it
11. Write README
12. Commit everything with clear commit messages

## IMPORTANT NOTES

- Use `ulid` or `uuid` for IDs — install `python-ulid` if using ULID
- All async where possible
- Type hints on everything
- The server MUST work via stdio transport for Claude Code
- If sqlite-vec won't install/load, implement a numpy fallback for cosine similarity
- Commit after each major objective with a descriptive message
- Push to origin/main after each commit

## Git Config (already set)
```
user.email = REDACTED_EMAIL
user.name = Fi (Elliot's Build Bot)
remote = origin -> git@github.com:CalebChristiansen/Memoreei.git
```

When completely finished, run this command to notify me:
openclaw system event --text "Done: Memoreei MCP server built — all objectives complete. WhatsApp parser, Discord connector, hybrid search, MCP tools all working. Ready for Claude Code integration testing." --mode now
