# 🧠 Memoreei

**All your AIs should know you**

Memoreei is a local-first MCP server that gives Claude (and any MCP-compatible agent) persistent, searchable memory across your conversations, chats, and notes. It's not a chatbot wrapper — it's **memory infrastructure** for AI agents.

```
"What did I say about the printer last week?"
"Remind me of everything from that Discord thread about the API redesign."
"What were my notes on the React migration?"
```

Claude can now answer these. Without Memoreei, it can't.

---

## What It Does

- **Ingests** conversations from WhatsApp, Discord, Telegram, and manual notes
- **Embeds** them locally using ONNX-based vector models (no cloud required)
- **Indexes** everything in SQLite with full-text search (BM25 via FTS5)
- **Fuses** keyword + semantic results using Reciprocal Rank Fusion
- **Exposes** 7 MCP tools for any Claude client to query memory in real-time

No SaaS. No mandatory API keys. Your data stays on your machine.

---

## Architecture

```
 ┌─────────────────────────────────────────────────────────────────┐
 │                        Your Data Sources                        │
 │                                                                 │
 │  WhatsApp .txt export  Discord channel  Telegram bot  Manual notes  │
 │         │                    │                │              │      │
 └─────────┼────────────────────┼────────────────┼──────────────┼─────┘
           │                    │                │              │
           ▼                    ▼                ▼              ▼
 ┌─────────────────────────────────────────────────────────────────┐
 │                     Memoreei MCP Server                         │
 │                                                                 │
 │  ┌──────────────┐  ┌─────────────────┐  ┌──────────────────┐   │
 │  │  Connectors  │  │  Hybrid Search  │  │   MCP Tools      │   │
 │  │              │  │                 │  │                  │   │
 │  │  whatsapp.py │  │  FTS5 (BM25)    │  │  search_memory   │   │
 │  │  discord.py  │  │  + vector cosine│  │  get_context     │   │
 │  │  telegram.py │  │  + RRF fusion   │  │  add_memory      │   │
 │  │  manual add  │  │                 │  │  list_sources    │   │
 │  └──────┬───────┘  └────────┬────────┘  │  ingest_whatsapp │   │
 │         │                   │           │  sync_discord    │   │
 │         │                   │           │  sync_telegram   │   │
 │  ┌─────────────────────────────────┐    └────────┬─────────┘   │
 │  │         SQLite Database         │             │             │
 │  │  memories + FTS5 index          │             │             │
 │  │  embeddings (BLOB)              │             │             │
 │  │  discord sync checkpoint        │             │             │
 │  └─────────────────────────────────┘             │             │
 └──────────────────────────────────────────────────┼────────────┘
                                                    │ stdio / JSON-RPC
                                                    ▼
                                         ┌─────────────────────┐
                                         │   Claude Clients    │
                                         │                     │
                                         │   Claude Code       │
                                         │   Claude Desktop    │
                                         │   Any MCP client    │
                                         └─────────────────────┘
```

---

## Quick Start

### 1. Install

```bash
git clone <repo-url>
cd memoreei
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env — defaults work out of the box with local embeddings
```

### 3. Connect to Claude

**Claude Code** — add to `.mcp.json`:

```json
{
  "mcpServers": {
    "memoreei": {
      "command": "/path/to/memoreei/.venv/bin/python",
      "args": ["-m", "memoreei.server"],
      "cwd": "/path/to/memoreei"
    }
  }
}
```

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memoreei": {
      "command": "/path/to/memoreei/.venv/bin/python",
      "args": ["-m", "memoreei.server"]
    }
  }
}
```

### 4. Seed sample data (optional)

```bash
python scripts/seed_data.py
```

### 5. Start using memory

```
# In Claude, via MCP:
> ingest_whatsapp("/path/to/WhatsApp Chat Export.txt")
> sync_discord()
> add_memory("The staging DB password rotates every 90 days")
> search_memory("printer issues")
```

---

## MCP Tools

All 7 tools are available to any connected MCP client.

### `search_memory`

Hybrid keyword + semantic search across all your memories.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | **required** | Natural language search query |
| `limit` | int | 10 | Max results to return |
| `source` | string | null | Filter by source: `whatsapp:chat_name`, `discord:ID`, `manual` |
| `participant` | string | null | Filter by sender name (case-insensitive) |
| `after` | string | null | ISO date lower bound, e.g. `2026-01-01` |
| `before` | string | null | ISO date upper bound |

Returns ranked results with scores, content, participants, timestamps, and source metadata.

---

### `get_context`

Fetch surrounding messages for a memory — essential for understanding what was actually happening in a conversation.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `memory_id` | string | **required** | Memory ID (ULID) from search results |
| `before` | int | 5 | Messages to fetch before the target |
| `after` | int | 5 | Messages to fetch after the target |

---

### `add_memory`

Manually store a note, fact, or anything worth remembering.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | **required** | Text to remember |
| `source` | string | `"manual"` | Source label |
| `metadata` | dict | null | Optional key-value pairs |

Auto-embeds the content for semantic search immediately.

---

### `list_sources`

Inventory all ingested data sources and message counts.

```
# Example return:
{
  "whatsapp:printer_conspiracy": 1842,
  "discord:REDACTED_CHANNEL_ID": 391,
  "manual": 12
}
```

---

### `ingest_whatsapp`

Import a WhatsApp chat export `.txt` file.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_path` | string | Absolute path to the exported `.txt` file |

Handles multi-line messages, media placeholders, system messages, and deduplication on re-import.

---

### `sync_discord`

Pull new messages from a Discord channel into memory. Uses checkpoint-based incremental sync — only fetches messages you haven't seen yet.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `channel_id` | string | `DISCORD_CHANNEL_ID` env var | Discord channel ID to sync |

---

### `sync_telegram`

Pull new messages received by the Telegram bot into memory via the Bot API (`getUpdates`). Uses checkpoint-based incremental sync — only fetches messages the bot has received since the last sync.

Requires the bot to be a member of the target group, or for users to have sent direct messages to the bot.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `chat_id` | string | `TELEGRAM_CHAT_ID` env var | Telegram chat ID to filter (positive = DM, negative = group). Syncs all chats if omitted. |

---

## Supported Sources

| Source | How to Ingest | Source ID Format |
|--------|---------------|------------------|
| **WhatsApp** | Export chat → `.txt` → `ingest_whatsapp` | `whatsapp:<chat_name>` |
| **Discord** | Set bot token + channel ID → `sync_discord` | `discord:<channel_id>` |
| **Telegram** | Set bot token → `sync_telegram` | `telegram:<chat_id>` |
| **Manual** | `add_memory` tool | `manual` (or custom label) |

More connectors are easy to add — each is a standalone parser that produces `MemoryItem` objects.

---

## Configuration

Copy `.env.example` to `.env`:

```bash
# Embedding provider: "fastembed" (local, default) or "openai"
EMBEDDING_PROVIDER=fastembed

# Required only if EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...

# Discord bot token (required for sync_discord)
DISCORD_BOT_TOKEN=your_bot_token_here

# Discord channel to sync (default for sync_discord)
DISCORD_CHANNEL_ID=1234567890123456789

# Telegram bot token for syncing messages via sync_telegram
TELEGRAM_BOT_TOKEN=your_bot_token_here

# Default Telegram chat ID for sync_telegram (positive = DM, negative = group)
# TELEGRAM_CHAT_ID=

# SQLite database path
MEMOREEI_DB_PATH=./memoreei.db
```

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` (local ONNX) or `openai` |
| `OPENAI_API_KEY` | — | Required only if using OpenAI embeddings |
| `DISCORD_BOT_TOKEN` | — | Discord bot token for `sync_discord` |
| `DISCORD_CHANNEL_ID` | — | Default channel for `sync_discord` |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for `sync_telegram` (create via @BotFather) |
| `TELEGRAM_CHAT_ID` | — | Default chat/group ID for `sync_telegram` |
| `MEMOREEI_DB_PATH` | `./memoreei.db` | SQLite database path |

---

## How Hybrid Search Works

Memoreei runs **two searches in parallel** and fuses the results:

```
Query: "that weird printer incident"
         │
         ├──▶ FTS5 BM25 keyword search
         │    Matches "printer", "incident" — fast, exact
         │    Returns ranked list of IDs
         │
         └──▶ Vector search (cosine similarity)
              Matches "the HP wouldn't connect", "paper jam saga"
              Returns ranked list of IDs
                  │
                  ▼
         Reciprocal Rank Fusion (RRF)
         ─────────────────────────────
         score(item) = Σ  1 / (60 + rank_i)
                        i ∈ {keyword_rank, vector_rank}

         Items in BOTH result sets get summed scores → boosted.
         Items in only one set still contribute.
         Top N returned, then filtered by source/participant/date.
```

**Why RRF?** Rank-based — no score normalization needed across different scales. Empirically outperforms weighted linear combinations. Constant `k=60` is the standard default from the original paper.

**Default embedding model:** `BAAI/bge-small-en-v1.5` via FastEmbed — 384-dimensional vectors, ~23MB ONNX model, runs fully offline, no API keys.

---

## Privacy

**Local-first by design.**

- All data stored in a single SQLite file on your machine
- Default embedding model runs locally via ONNX — zero network calls
- OpenAI embeddings are opt-in only (`EMBEDDING_PROVIDER=openai`)
- No telemetry, no analytics, no cloud sync
- Your conversations never leave your machine in the default configuration

The `.env` file and `memoreei.db` are in `.gitignore`.

---

## Project Structure

```
memoreei/
├── src/memoreei/
│   ├── server.py              # MCP server entry point, all 6 tool definitions
│   ├── storage/
│   │   ├── database.py        # SQLite + FTS5 virtual table + vector storage
│   │   └── models.py          # MemoryItem dataclass
│   ├── search/
│   │   ├── embeddings.py      # FastEmbed (local ONNX) + OpenAI providers
│   │   └── hybrid.py          # RRF fusion of BM25 + vector results
│   └── connectors/
│       ├── whatsapp.py           # WhatsApp .txt export parser
│       ├── discord_connector.py  # Discord REST API + checkpoint sync
│       └── telegram_connector.py # Telegram Bot API + checkpoint sync
├── tests/                     # Unit + integration tests
├── data/samples/              # Sample WhatsApp exports for testing
├── scripts/
│   └── seed_data.py           # Load sample data
├── .env.example               # Config template
└── pyproject.toml             # Package metadata
```

---

## License

MIT

```
Copyright (c) 2026 Memoreei Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
