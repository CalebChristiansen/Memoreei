# Memoreei

**Your personal memory, searchable by AI.**

Memoreei is an open-source MCP server that ingests your conversations — WhatsApp exports, Discord messages, or manual notes — into a local hybrid database (keyword + vector) and exposes them as MCP tools. Ask any MCP-compatible AI (Claude Code, Claude Desktop, etc.) to search your memories and it just works.

---

## Architecture

```
  Your Data                  Memoreei MCP Server              AI Client
  ─────────                  ───────────────────              ─────────
  WhatsApp .txt  ──ingest──▶  ┌─────────────────┐
  Discord msgs   ──sync────▶  │   MCP Tools      │ ◀──stdio──▶ Claude Code
  Manual notes   ──add─────▶  │                  │             Claude Desktop
                              │  ┌────────────┐  │             Any MCP client
                              │  │  Hybrid    │  │
                              │  │  Search    │  │
                              │  │ FTS5 + Vec │  │
                              │  └────┬───────┘  │
                              │       │ RRF       │
                              │  ┌────▼───────┐  │
                              │  │  SQLite DB  │  │
                              │  │ + Embeddings│  │
                              │  └────────────┘  │
                              └─────────────────┘
```

**Hybrid search** combines BM25 keyword matching (SQLite FTS5) with semantic vector similarity (fastembed, local ONNX), merged via Reciprocal Rank Fusion (RRF). Best of both worlds: exact keyword hits + fuzzy semantic matching.

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/CalebChristiansen/Memoreei.git
cd Memoreei
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — no API key required for default fastembed mode
```

### 3. Seed with sample data

```bash
python scripts/seed_data.py
```

### 4. Run the MCP server

```bash
python -m memoreei.server
# or
mcp run src/memoreei/server.py
```

### 5. Configure Claude Code

Add to your `.mcp.json` or Claude Code MCP settings:

```json
{
  "mcpServers": {
    "memoreei": {
      "command": "python",
      "args": ["-m", "memoreei.server"],
      "cwd": "/path/to/Memoreei"
    }
  }
}
```

---

## MCP Tools

### `search_memory`
Search your memories with hybrid keyword + semantic search.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | Natural language search query |
| `limit` | int | 10 | Max results |
| `source` | string | null | Filter by source (e.g. `whatsapp:whatsapp_pizza_wars`) |
| `participant` | string | null | Filter by sender name |
| `after` | string | null | ISO date lower bound (e.g. `2026-01-01`) |
| `before` | string | null | ISO date upper bound |

### `get_context`
Get surrounding messages around a specific memory for conversation context.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `memory_id` | string | required | Memory ID from search results |
| `before` | int | 5 | Messages before target |
| `after` | int | 5 | Messages after target |

### `add_memory`
Manually add a note or memory.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | required | Text to remember |
| `source` | string | `"manual"` | Source label |
| `metadata` | dict | null | Optional key-value metadata |

### `list_sources`
List all ingested data sources and message counts.

### `ingest_whatsapp`
Import a WhatsApp chat export `.txt` file.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_path` | string | Path to WhatsApp `.txt` export |

### `sync_discord`
Sync new messages from a Discord channel.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `channel_id` | string | env var | Discord channel ID to sync |

---

## Supported Data Sources

| Source | How to ingest |
|--------|---------------|
| WhatsApp | Export chat → `.txt` → `ingest_whatsapp` tool |
| Discord | Set `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` → `sync_discord` tool |
| Manual notes | `add_memory` tool |

---

## Configuration

All configuration via `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` (local) or `openai` |
| `OPENAI_API_KEY` | — | Required if `EMBEDDING_PROVIDER=openai` |
| `DISCORD_BOT_TOKEN` | — | Discord bot token for channel sync |
| `DISCORD_CHANNEL_ID` | `REDACTED_CHANNEL_ID` | Default Discord channel to sync |
| `MEMOREEI_DB_PATH` | `./memoreei.db` | SQLite database path |

---

## Tech Stack

- **Python 3.10+**
- **mcp[cli]** — MCP Python SDK (stdio transport)
- **fastembed** — Local ONNX embeddings (`BAAI/bge-small-en-v1.5`, no API key needed)
- **sqlite3 + FTS5** — Full-text search
- **numpy** — Vector cosine similarity
- **aiosqlite** — Async SQLite
- **discord.py / aiohttp** — Discord REST API
- **python-dotenv** — Config

---

## Privacy

Memoreei is **local-first**. Your data never leaves your machine:
- All embeddings are computed locally via fastembed (ONNX, no network calls)
- SQLite database stored on your filesystem
- No telemetry, no cloud sync
- Optional: use OpenAI for embeddings (your data goes to OpenAI's API)

---

## License

MIT
