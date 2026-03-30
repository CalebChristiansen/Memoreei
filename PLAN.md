# Memoreei — Hackathon Build Plan

**Project:** Open-source MCP server for personal memory search
**Repo:** git@github.com:CalebChristiansen/Memoreei.git
**Stack:** Python, SQLite + FTS5 + sqlite-vec, OpenAI embeddings, MCP Python SDK
**Deadline:** 3:25 PM PDT, Saturday March 28, 2026
**Demo client:** Claude Code (local CLI)

---

## PART A — What I Need From Caleb (before 12:30 PM)

### A1. OpenAI API Key
I don't have one in Bitwarden. I need you to either:
- Add it to Bitwarden as `openai-api-key` and tell me when it's synced
- Or paste it and I'll store it in a `.env` file (not committed)

### A2. GitHub SSH Access
I can't push to the repo — no SSH key on this machine. Options:
- **Option 1 (fastest):** Generate an SSH key here, you add it as a deploy key on the repo with write access
- **Option 2:** You clone it locally and I work in the directory, you push manually
- **Option 3:** You create a personal access token, I push via HTTPS

I'd go with Option 1. I'll generate the key, you paste the public key into GitHub repo → Settings → Deploy keys.

### A3. Discord Channel for Live Demo
I need you to:
1. Create a new channel on the Karamja server called `#memoreei-demo`
2. Give my bot (Elliot) access to read it
3. We'll populate it with funny fake conversations between the bots

**Or** — I can create the channel myself using the Discord API with Elliot's bot token if you give me the go-ahead. (I have access to the token via Bitwarden.)

### A4. Confirm: Can I Use Elliot's Bot Token for Discord Reading?
The Memoreei server will use a Discord bot token to poll a channel for new messages. I can use Elliot's existing token for the hackathon demo. Confirm that's OK.

---

## PART B — Autonomous Build Plan

### Timeline

| Time | Phase | Status |
|------|-------|--------|
| 10:30 - 11:00 | Setup: repo, env, deps, SSH key | With Caleb |
| 11:00 - 11:30 | Generate fake data, create Discord channel | With Caleb |
| 11:30 - 12:30 | Core build: storage, ingestion, MCP server | With Caleb (available) |
| 12:30 - 1:30 | WhatsApp parser + Discord connector | Autonomous |
| 1:30 - 2:15 | MCP tools + hybrid search | Autonomous |
| 2:15 - 2:45 | Claude Code integration testing | Autonomous |
| 2:45 - 3:15 | Polish: README, demo script, edge cases | Autonomous |
| 3:15 - 3:25 | Final commit, push, verify | Autonomous |

### Heartbeat Protocol
- Heartbeat fires every 60 seconds
- Check: am I currently running a build task? If not, resume next incomplete objective
- Check: is it past 3:25 PM? If yes, stop all work, commit what exists, push
- Track progress in `projects/memoreei/PROGRESS.md`

---

## Build Objectives (Ordered)

Each objective has a **concrete test** so I know when it's done.

### Objective 1: Project Scaffold
**Do:**
- Init git repo locally
- Create Python project structure (pyproject.toml, src/, tests/)
- Set up virtual env, install deps
- Create `.env` for OpenAI key
- `.gitignore` for .env, __pycache__, *.db, .venv

**Test:** `cd projects/memoreei && python -c "import mcp; print('ok')"` succeeds

**Deps:**
```
mcp[cli]
openai
sqlite-vec
aiosqlite
python-dotenv
discord.py
```

---

### Objective 2: Fake WhatsApp Export Files
**Do:**
- Generate 2-3 hilarious fake WhatsApp chat exports
- Use real WhatsApp export format: `[MM/DD/YY, HH:MM:SS] Name: message`
- Topics: conspiracy theories about the office printer, an argument about the best pizza topping that escalates absurdly, planning a heist to steal the last conference room donut

**Test:** Files exist at `data/samples/` and are parseable

**Sample format:**
```
[03/15/26, 09:15:23] Alice: guys the printer is sentient
[03/15/26, 09:15:45] Bob: not this again
[03/15/26, 09:16:02] Alice: it ONLY jams when I print anything about AI
[03/15/26, 09:16:30] Charlie: that's because you print 200 pages at once
[03/15/26, 09:17:01] Alice: explain why it printed "I SEE YOU" on a blank page then
```

---

### Objective 3: Storage Layer (SQLite + FTS5 + Vector)
**Do:**
- SQLite database with memories table
- FTS5 virtual table for keyword search
- sqlite-vec for vector storage
- CRUD operations: insert, search, delete
- Dedup on (source, source_id)

**Schema:**
```sql
CREATE TABLE memories (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_id TEXT,
  content TEXT NOT NULL,
  summary TEXT,
  participants TEXT,  -- JSON array
  ts INTEGER NOT NULL,
  ingested_at INTEGER NOT NULL,
  metadata TEXT,  -- JSON
  embedding BLOB
);
```

**Test:** Insert 10 records, run FTS search, run vector search, both return correct results.

---

### Objective 4: Embedding Pipeline
**Do:**
- OpenAI text-embedding-3-small integration
- Batch embedding (50 items at a time)
- Embed on ingest
- Store embedding as blob in sqlite-vec

**Test:** Embed "what's for lunch" → search for "food plans" → returns the record.

---

### Objective 5: Hybrid Search + RRF
**Do:**
- BM25 search via FTS5
- Vector cosine similarity via sqlite-vec
- Reciprocal Rank Fusion to merge results
- Metadata filters: source, participant, date range

**Test:**
- Search "printer sentient" → finds the WhatsApp conspiracy thread (BM25 wins)
- Search "office equipment becoming self-aware" → finds same thread (vector wins)
- Both produce same top result via RRF

---

### Objective 6: WhatsApp Export Parser
**Do:**
- Parse standard WhatsApp `.txt` export format
- Extract: timestamp, sender, message text
- Handle multiline messages
- Chunk into individual messages
- Ingest into storage layer

**Test:** Parse sample files → all messages in DB → searchable

---

### Objective 7: Discord Live Connector
**Do:**
- Use discord.py to read messages from `#memoreei-demo` channel
- Poll for new messages since last checkpoint
- Ingest new messages into storage layer
- Store checkpoint (last message ID) for incremental sync
- Background polling task (every 30 seconds)

**Test:**
1. Post a message in `#memoreei-demo`
2. Wait for next poll cycle
3. Search for the message content via MCP → found

---

### Objective 8: MCP Server
**Do:**
- Python MCP server using `mcp` SDK
- Transport: stdio (for Claude Code)
- Tools:
  - `search_memory(query, limit?, source?, participant?, after?, before?)` — hybrid search
  - `get_context(memory_id)` — get surrounding messages in thread
  - `add_memory(content, source?, metadata?)` — manual note
  - `list_sources()` — show available data sources and counts
  - `ingest_whatsapp(file_path)` — import a WhatsApp export
  - `sync_discord()` — trigger Discord sync now

**Test:** Run MCP server, call each tool via test script, all return expected results.

---

### Objective 9: Claude Code Integration
**Do:**
- Create MCP config for Claude Code at `~/.claude/` or project-level `.mcp.json`
- Configure Memoreei as an MCP server
- Test real queries through Claude Code

**Test sequence:**
1. Start Claude Code
2. Ask: "Search my memories for anything about a sentient printer"
3. Claude Code calls `search_memory` → returns WhatsApp results with snippets
4. Ask: "What's the latest message in the Discord demo channel?"
5. Claude Code calls `search_memory` with source=discord → returns result
6. Post a NEW message in Discord `#memoreei-demo`
7. Ask Claude Code: "sync discord and search for [new message content]"
8. Returns the new message ✅

---

### Objective 10: README + Demo Script
**Do:**
- Professional README.md with:
  - What it is
  - Architecture diagram (ASCII)
  - Quick start
  - MCP tool docs
  - Supported sources
  - Privacy note
- `demo.sh` or `demo.md` — step-by-step demo script for the hackathon

**Test:** Someone can clone the repo, follow README, get it running.

---

### Objective 11: Polish + Edge Cases
**Do:**
- Error handling on all tools
- Graceful handling of missing API key
- Rate limiting on embeddings
- Clean shutdown
- Type hints everywhere
- Docstrings on public functions

**Test:** Intentionally pass bad inputs to each MCP tool → get helpful error messages, not crashes.

---

## Project Structure

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
│       │   ├── database.py        # SQLite + FTS5 + sqlite-vec
│       │   └── models.py          # Data models
│       ├── search/
│       │   ├── __init__.py
│       │   ├── embeddings.py      # OpenAI embedding client
│       │   ├── hybrid.py          # Hybrid search + RRF
│       │   └── filters.py         # Metadata filtering
│       ├── connectors/
│       │   ├── __init__.py
│       │   ├── whatsapp.py        # WhatsApp export parser
│       │   └── discord_connector.py  # Discord live polling
│       └── tools/
│           ├── __init__.py
│           └── memory_tools.py    # MCP tool definitions
├── tests/
│   ├── test_storage.py
│   ├── test_search.py
│   ├── test_whatsapp_parser.py
│   └── test_discord.py
└── scripts/
    └── seed_data.py               # Seed DB with sample data
```

---

## Risk Mitigations

| Risk | Mitigation |
|------|-----------|
| OpenAI rate limits | Batch embeddings, cache, retry with backoff |
| Discord API limits | Poll every 30s, not per-second |
| sqlite-vec install issues | Fallback: numpy cosine similarity on raw blobs |
| Claude Code MCP config | Test early (Objective 9 before polish) |
| Time overrun | Objectives ordered by demo value — if I run out of time, first 8 objectives = working demo |

---

## Demo Flow (Hackathon Presentation)

1. Show the funny WhatsApp exports on screen
2. "Let's ask our AI what it remembers..."
3. Claude Code: "Search for the printer conspiracy"
4. → Returns funny WhatsApp messages with sources and dates
5. "But Memoreei also watches live channels..."
6. Someone types a message in `#memoreei-demo` on Discord
7. "Let's sync and search..."
8. Claude Code: "Sync Discord and find [that message]"
9. → Returns the live message that was just posted
10. "This is an MCP server. Any model, any app can use it."
11. Show the architecture diagram
12. Drop the GitHub link

---

## Progress Tracking

Progress will be tracked in `PROGRESS.md` in this directory.
Each objective gets marked: ⬜ TODO → 🔨 IN PROGRESS → ✅ DONE → ❌ BLOCKED
