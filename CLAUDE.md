# CLAUDE.md — Memoreei Project Context

## What This Is
Memoreei is an MCP server for personal memory search. It ingests messages from Discord, WhatsApp, Telegram, Matrix, Slack, Gmail, and more — stores them in a hybrid search database (keyword + vector) — and exposes them via MCP tools.

## MCP Tools Available
You have these tools via the `memoreei` MCP server:

| Tool | What it does |
|------|-------------|
| `sync_discord` | Pull messages from Discord channel into memory DB |
| `search_memory` | Hybrid keyword + semantic search across all sources |
| `get_context` | Get surrounding messages around a search hit |
| `add_memory` | Manually store a note or fact |
| `list_sources` | Show all ingested sources with message counts |
| `ingest_whatsapp` | Import a WhatsApp chat export .txt file |

## How to Use
- **To search:** Use `search_memory` with a natural language query. It does hybrid BM25 + vector search with RRF fusion.
- **To sync Discord:** Use `sync_discord` — it reads the bot token and channel from .env automatically.
- **To get context around a result:** Use `get_context` with the memory ID from search results.
- **To see what's ingested:** Use `list_sources`.

## DO NOT
- Do NOT try to access Discord APIs directly or unlock Bitwarden — the MCP tools handle everything.
- Do NOT use `bw` commands. The server has its own credentials in `.env`.

## Project Structure
```
src/memoreei/
├── server.py              # MCP server (FastMCP, stdio)
├── storage/database.py    # SQLite + FTS5 + vector search
├── search/hybrid.py       # Hybrid search with RRF fusion
├── connectors/            # Discord, WhatsApp, Telegram, Matrix, Slack, Email
└── tools/memory_tools.py  # MCP tool implementations
```

## Key Paths
- **DB:** `./memoreei.db` (SQLite + FTS5)
- **Venv:** `.venv/bin/python`
- **Config:** `.env` (tokens, channel IDs)
- **MCP config:** `.mcp.json` (also in `~/.claude/settings.json`)
