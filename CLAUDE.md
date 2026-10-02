# CLAUDE.md — Memoreei Project Context

## What This Is
Memoreei is an MCP server for personal memory search. It ingests messages from iMessage, Discord, Telegram, Matrix, Slack, Gmail, and more — stores them in a hybrid search database (keyword + vector) — and exposes them via MCP tools.

## MCP Tools Available
You have these tools via the `memoreei` MCP server:

| Tool | What it does |
|------|-------------|
| `sync_discord` | Pull messages from Discord channel into memory DB |
| `search_memory` | Hybrid keyword + semantic search across all sources |
| `get_context` | Get surrounding messages around a search hit |
| `add_memory` | Manually store a note or fact |
| `list_sources` | Show all ingested sources with message counts |

## How to Use
- **To search:** Use `search_memory` with a natural language query. It does hybrid BM25 + vector search with RRF fusion.
- **To sync Discord:** Use `sync_discord` — it reads the bot token and channel from the config automatically.
- **To get context around a result:** Use `get_context` with the memory ID from search results.
- **To see what's ingested:** Use `list_sources`.

## DO NOT
- Do NOT try to access Discord APIs directly or unlock Bitwarden — the MCP tools handle everything.
- Do NOT use `bw` commands. The server has its own credentials in its config.

## Project Structure
```
src/memoreei/
├── server.py              # MCP servers: local (stdio, every tool), network (HTTP, 4 tools)
├── auth.py                # API keys, bearer-auth middleware, client config printout
├── imports.py             # registered import files, re-read by `sync`
├── catalog.py             # what can be set up (setup wizard and dashboard share it)
├── admin/                 # the /admin dashboard and its login rules
├── storage/database.py    # SQLite + FTS5 + vector search
├── search/hybrid.py       # Hybrid search with RRF fusion
├── connectors/            # iMessage, Discord, Telegram, Matrix, Slack, Email, …
└── tools/memory_tools.py  # MCP tool implementations
```

## Key Paths
- **Home:** `~/.local/share/memoreei` on Linux, `~/Library/Application Support/Memoreei` on macOS, `~/.memoreei` elsewhere (or `--home` / `MEMOREEI_HOME`): `config.env` + `memoreei.db`
- **Mac app:** `macos/` (Swift menu-bar app, build script, lock file); see `macos/README.md`
- **Linux packages:** `linux/` (.deb, .rpm, tarball of one `/opt/memoreei` tree; build, lock, smoke test); see `linux/README.md`
- **Dashboard:** `src/memoreei/admin/` (`/admin`, Jinja + htmx, loopback-only; `ssh -L` for a headless box)
- **Dev config:** a `.env` in the checkout overrides `config.env` while you work here
- **Venv:** `.venv/bin/python`, with memoreei installed editable (`pip install -e '.[dev]'`)
