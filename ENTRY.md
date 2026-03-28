# Memoreei — Entry Point (Context Recovery Document)

**Read this first if you have no memory of this project.**

## What Is This
Memoreei is an open-source MCP server for personal memory search. It ingests messages from Discord (and WhatsApp exports), stores them in a hybrid search database (keyword + vector), and exposes them via MCP tools to any LLM client (Claude Code, etc.).

**Repo:** git@github.com:CalebChristiansen/Memoreei.git
**Local path:** `/home/fi/.openclaw/workspace-elliot/projects/memoreei/`
**Venv:** `.venv/bin/python` (already created, deps installed)
**DB:** `./memoreei.db` (SQLite + FTS5 + numpy cosine similarity fallback)

## Who Asked For This
Caleb (sci8452, Discord ID REDACTED_USER_ID). He's at a hackathon. Deadline: **3:25 PM PDT, March 28, 2026.**

## Current State
- Core MCP server is built and working
- 6 MCP tools: search_memory, get_context, add_memory, list_sources, ingest_whatsapp, sync_discord
- Server starts via stdio, responds to JSON-RPC
- 3 funny WhatsApp sample exports seeded (172 messages, embedded)
- Discord connector works (tested against real channel)
- 21 unit tests passing
- Ralph loop task runner at `run_tasks.sh`
- Heartbeat cron (job ID: `063b927f-09cf-472d-abd4-f4042bfc2961`) — currently DISABLED, posts to #project-updates

## Key IDs
| Thing | ID |
|-------|-----|
| Discord server (Karamja) | REDACTED_SERVER_ID |
| #memoreei-demo channel | REDACTED_CHANNEL_ID |
| #project-updates channel | REDACTED_CHANNEL_ID |
| #project-live channel | REDACTED_CHANNEL_ID |
| #projects channel | REDACTED_CHANNEL_ID_4 |
| Elliot bot user | REDACTED_BOT_ID |
| Fi bot user | 1486569305968738434 |
| Navi bot user | 1486572574346576042 |
| Ford bot user | 1486573797485187153 |
| Caleb user | REDACTED_USER_ID |
| Heartbeat cron job | 063b927f-09cf-472d-abd4-f4042bfc2961 |

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
Located at project root. Contains:
- `DISCORD_BOT_TOKEN` — Elliot's bot token
- `DISCORD_CHANNEL_ID` — REDACTED_CHANNEL_ID
- `EMBEDDING_PROVIDER` — fastembed
- `MEMOREEI_DB_PATH` — ./memoreei.db

## SSH / Git
- SSH key: `/home/fi/.ssh/id_ed25519_github` (deploy key for the repo)
- SSH config: `/home/fi/.ssh/config` (maps github.com to that key)
- Git user: `REDACTED_EMAIL` / `Fi (Elliot's Build Bot)`

## Project Structure
```
memoreei/
├── src/memoreei/
│   ├── server.py              # MCP server (FastMCP, stdio transport)
│   ├── storage/database.py    # SQLite + FTS5 + vector (numpy fallback)
│   ├── storage/models.py      # MemoryItem dataclass
│   ├── search/embeddings.py   # FastEmbed (default) + OpenAI provider
│   ├── search/hybrid.py       # HybridSearch with RRF fusion
│   ├── connectors/whatsapp.py # WhatsApp .txt export parser
│   ├── connectors/discord_connector.py  # Discord REST API sync
│   └── tools/memory_tools.py  # MCP tool implementations
├── tests/                     # 21 passing tests
├── scripts/
│   ├── seed_data.py           # Seeds WhatsApp sample data
│   └── discord_post.sh        # Posts to #project-live
├── data/samples/              # 3 funny WhatsApp exports
├── run_tasks.sh               # Ralph loop task runner
├── tasks.json                 # Task queue for ralph loop
├── PLAN.md                    # Full project plan
├── PROGRESS.md                # Build progress tracker
├── TASK.md                    # Detailed build instructions
└── ENTRY.md                   # THIS FILE
```

## How to Run Things

### Start MCP server
```bash
cd /home/fi/.openclaw/workspace-elliot/projects/memoreei
.venv/bin/python -m memoreei.server
```

### Run tests
```bash
.venv/bin/python -m pytest tests/ -v
```

### Seed WhatsApp data
```bash
.venv/bin/python scripts/seed_data.py
```

### Test server via JSON-RPC
```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1.0"}}}' | timeout 10 .venv/bin/python -m memoreei.server
```

### Post to Discord channel
```bash
# Using any bot token:
curl -s -X POST "https://discord.com/api/v10/channels/CHANNEL_ID/messages" \
  -H "Authorization: Bot TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "message"}'
```

### Start ralph loop
```bash
cd /home/fi/.openclaw/workspace-elliot/projects/memoreei
bash run_tasks.sh &
```

### Enable/disable heartbeat
```bash
# Via cron tool: update job 063b927f-09cf-472d-abd4-f4042bfc2961, set enabled true/false
```

## Demo Plan (Current Focus)
The demo is Discord-first:
1. All 4 bots (Fi, Navi, Ford, Elliot) have a funny conversation in #memoreei-demo
2. Sync Discord → all messages ingested
3. Search for something from the conversation → found
4. Someone sends a NEW message live
5. Sync again → new message found
6. Show it working through Claude Code MCP

## Task Priorities
- **P0 (MUST):** Core demo — seed Discord, integration tests, MCP config, README, demo script, "DONE" commit
- **P1 (SHOULD):** Additional chat sources — Telegram, Matrix, Slack, Gmail, 5th service
- **P2 (NICE TO HAVE):** Use case apps — Movie Ring, Discord Memory Bot, Search UI
- When P0 is done: commit "DONE", post to #projects, then continue with P1/P2
- Stop at 3:25 PM regardless

## What's Left to Build
Check tasks.json and PROGRESS.md for current task queue status.

## Use Case Apps
Standalone apps in `usecases/`. No dependency on main package. Status tracked in `usecases/apps.md`.
- Movie Ring — web app showing movie mentions + posters + friend quotes
- Discord Memory Bot — @ mention to search memories
- Memory Search UI — web search interface

## Hackathon Rules
- Read /home/fi/docs/ralphthon-guide.md
- "Basic RAG" is on the disqualification list — frame as MCP infrastructure
- 3 min live demo, no slides
- Lobster Rule: fewer laptop touches = better score (20%)
- Need: public GitHub repo, 1-min demo video, live demo link

## Autonomy Infrastructure
- `run_tasks.sh` — bash loop, spawns fresh Claude Code per task
- Heartbeat cron — 60s isolated sessions, checks for .escalate files
- On failure: writes .escalate → heartbeat agent diagnoses and fixes
- Posts live updates to #project-live
- Posts status to #project-updates
- Stops at 3:25 PM PDT deadline
