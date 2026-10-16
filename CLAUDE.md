# CLAUDE.md — Memoreei Project Context

## What This Is
Memoreei is an MCP server for personal memory search. It ingests messages from iMessage, WhatsApp, Signal, Discord, Telegram, Matrix, Slack, Gmail, and more — stores them in a hybrid search database (keyword + vector) — and exposes them via MCP tools.

## MCP Tools Available
You have these tools via the `memoreei` MCP server:

| Tool | What it does |
|------|-------------|
| `sync_discord` | Pull messages from Discord channel into memory DB |
| `search_memoreei` | Hybrid keyword + semantic search across all sources |
| `get_context` | Get surrounding messages around a search hit |
| `add_memoreei` | Manually store a note or fact |
| `list_sources` | Show all ingested sources with message counts |

## How to Use
- **To search:** Use `search_memoreei` with a natural language query. It does hybrid BM25 + vector search with RRF fusion.
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
├── public/                # the public listener: /mcp for the internet, the gate, access log
├── oauth/                 # memoreei's own OAuth sign-in: approve chatbots on the computer
├── cloud/                 # Memoreei Cloud (optional): enroll, certificate, tunnel to the relay
├── storage/database.py    # SQLite (SQLCipher-encrypted) + FTS5 + vector search
├── storage/key.py         # the database key, and every connection to memoreei.db
├── storage/vault.py       # sources' secrets, in the database's secrets table
├── search/hybrid.py       # Hybrid search with RRF fusion
├── connectors/            # iMessage, WhatsApp, Signal, Discord, Telegram, Slack, Email, …
└── tools/memory_tools.py  # MCP tool implementations
```

## Key Paths
- **Home:** `~/.local/share/memoreei` on Linux, `~/Library/Application Support/Memoreei` on macOS, `~/.memoreei` elsewhere (or `--home` / `MEMOREEI_HOME`): `config.env` (settings) + `memoreei.db` (encrypted; sources' secrets too) + usually `memoreei.key`
- **Database key:** `MEMOREEI_DB_KEY` › `memoreei.key` beside the db (mode 600) › GNOME Keyring/KWallet (item "Memoreei database key"). New keys go to the keyring on a Linux desktop with it unlocked, else the file (Mac included, until a Developer ID). Open memoreei.db only through `storage.key.connect` / `connect_async`; plain `sqlite3` sees noise. Tests keep the key in a file beside each test db (conftest `no_keyring`)
- **Mac app:** `macos/` (Swift menu-bar app, build script, lock file); see `macos/README.md`
- **Linux packages:** `linux/` (.deb, .rpm, tarball of one `/opt/memoreei` tree; build, lock, smoke test); see `linux/README.md`
- **Dashboard:** `src/memoreei/admin/` (`/admin`, Jinja + htmx; loopback-only and `ssh -L` for a headless box, unless it has a password and "from anywhere" is on: then also the LAN and the public listener, to the password. The rules are in `admin/auth.py` and `admin/account.py`)
- **Dev config:** a `.env` in the checkout overrides `config.env` while you work here
- **Venv:** `.venv/bin/python`, with memoreei installed editable (`pip install -e '.[dev]'`)
