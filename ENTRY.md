# Memoreei — Entry Point (Context Recovery Document)

**Read this first if you have no memory of this project.**

## What Is This
Memoreei is an open-source MCP server for personal memory search. It ingests messages from Discord, WhatsApp, Telegram, Matrix, Slack, Gmail, and Mastodon — stores them in a hybrid search database (keyword + vector) — and exposes them via MCP tools to any LLM client.

**Repo:** git@github.com:CalebChristiansen/Memoreei.git
**Local path:** `/home/fi/.openclaw/workspace-elliot/projects/memoreei/`
**Venv:** `.venv/bin/python` (created, deps installed)
**DB:** `./memoreei.db` (SQLite + FTS5 + numpy cosine similarity)

## Current State (as of 2026-03-29)

**BUILD COMPLETE.** 22/22 tasks finished autonomously via ralph loop. All committed and pushed.

### What's built and working
- Core MCP server (FastMCP, stdio transport, 12 MCP tools)
- Hybrid search: BM25 (FTS5) + fastembed vectors + RRF fusion
- Connectors: Discord, WhatsApp, Telegram, Matrix, Slack, Gmail, Mastodon
- Auto-sync: background sync every 60s + sync on startup + `refresh_memory` tool
- 21+ unit tests passing
- `.mcp.json` and `~/.claude/settings.json` configured for Claude Code

### Use Case Apps (all running)
- **Movie Ring** — http://REDACTED_IP:5050 (Flask, TMDB posters, ranked by mentions)
- **Contact Dossier** — http://REDACTED_IP:5051 (Flask, ranked contacts, sentiment, timeline)
- **Discord Memory Bot** — `usecases/memorybot/bot.py`

### Web Apps
To restart if down:
```bash
cd /home/fi/.openclaw/workspace-elliot/projects/memoreei
nohup .venv/bin/python usecases/moviering/app.py > /tmp/moviering.log 2>&1 &
nohup .venv/bin/python usecases/dossier/app.py > /tmp/dossier.log 2>&1 &
```

### Discord Watcher (auto-sync on Caleb's messages)
```bash
nohup .venv/bin/python -u scripts/watch_and_sync.py > /tmp/watch_sync.log 2>&1 &
```

## 12 MCP Tools
`search_memory` · `get_context` · `add_memory` · `list_sources` · `refresh_memory` · `ingest_whatsapp` · `sync_discord` · `sync_telegram` · `sync_matrix` · `sync_slack` · `sync_email` · `sync_mastodon`

## Key IDs
| Thing | ID |
|-------|-----|
| Discord server (Karamja) | REDACTED_SERVER_ID |
| #memoreei-demo channel | REDACTED_CHANNEL_ID |
| #project-updates channel | REDACTED_CHANNEL_ID |
| #project-live channel | REDACTED_CHANNEL_ID |
| #projects channel | REDACTED_CHANNEL_ID_4 |
| Elliot bot user | REDACTED_BOT_ID |
| Caleb user | REDACTED_USER_ID |

## Bot Tokens
All in Bitwarden: `discord elliot`, `discord fi`, `discord navi`, `discord ford`
```bash
source ~/.config/bw/credentials
export BW_CLIENTID BW_CLIENTSECRET
MASTER=$(cat ~/.config/bw/master_password)
BW_SESSION=$(~/.local/bin/bw unlock "$MASTER" --raw)
~/.local/bin/bw get password "discord elliot" --session "$BW_SESSION"
```

## .env (not committed)
- `DISCORD_BOT_TOKEN` — Elliot's bot token
- `DISCORD_CHANNEL_ID` — REDACTED_CHANNEL_ID
- `EMBEDDING_PROVIDER` — fastembed
- `MEMOREEI_DB_PATH` — ./memoreei.db
- `TMDB_API_KEY` — for Movie Ring posters
- `TMDB_READ_TOKEN` — TMDB v4 token

## SSH / Git
- SSH key: `/home/fi/.ssh/id_ed25519_github` (deploy key)
- SSH config: `/home/fi/.ssh/config` (maps github.com to that key)
- Git user: `REDACTED_EMAIL` / `Fi (Elliot's Build Bot)`

## Project Structure
```
memoreei/
├── src/memoreei/
│   ├── server.py              # MCP server (FastMCP, stdio)
│   ├── storage/database.py    # SQLite + FTS5 + vector
│   ├── search/embeddings.py   # FastEmbed (ONNX) + OpenAI
│   ├── search/hybrid.py       # HybridSearch with RRF fusion
│   ├── connectors/            # discord, whatsapp, telegram, matrix, slack, email, mastodon
│   └── tools/memory_tools.py  # MCP tool implementations
├── usecases/
│   ├── moviering/app.py       # Movie Ring web app (port 5050)
│   ├── dossier/app.py         # Contact Dossier web app (port 5051)
│   └── memorybot/bot.py       # Discord memory bot
├── scripts/
│   ├── seed_data.py           # WhatsApp sample seeder
│   ├── seed_discord.py        # Discord bot conversation seeder
│   ├── refresh.sh             # Manual DB sync
│   ├── watch_and_sync.py      # Auto-sync on Caleb's Discord messages
│   ├── discord_post.sh        # Post to #project-live
│   ├── demo_setup.sh          # Demo prep script
│   └── test_*.py              # Various test scripts
├── data/samples/              # WhatsApp sample exports
├── tests/                     # 21+ unit tests
├── run_tasks.sh               # Ralph loop task runner
├── tasks.json                 # Task queue (22 tasks, all done)
├── ralph.env                  # Runner config (if exists)
├── PLAN.md                    # Original project plan
├── PROGRESS.md                # Build progress tracker
├── DEMO.md                    # 3-minute demo script
├── CLAUDE.md                  # Claude Code context (MCP tools, don't use BW)
├── ENTRY.md                   # THIS FILE
└── .mcp.json                  # MCP server config for Claude Code
```

## How to Run Things

### Start MCP server
```bash
.venv/bin/python -m memoreei.server
```

### Run tests
```bash
.venv/bin/python -m pytest tests/ -v
```

### Manual sync
```bash
bash scripts/refresh.sh
```

### Launch Claude Code with MCP
```bash
cd /home/fi/.openclaw/workspace-elliot/projects/memoreei && claude
```

## Known Issues
- Movie Ring: single-word movie titles only detected if in `KNOWN_SINGLE_WORD_MOVIES` set in `usecases/moviering/app.py`. Add new titles there.
- Discord connector needs bot token with message read permissions
- TMDB API key needed for Movie Ring posters (in .env)

## Hackathon Context
- Built for Ralphthon SF at W&B Office (March 28, 2026)
- 22/22 tasks completed autonomously with 1 escalation (self-healed)
- Demo: Discord-first, real-time ingestion, multi-source search
- Frame as "MCP infrastructure" NOT "RAG chatbot"
