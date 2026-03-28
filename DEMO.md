# Memoreei — Live Demo Script (3 minutes)

## Pre-Demo Setup

Run this before going on screen:

```bash
bash scripts/demo_setup.sh
```

It will seed the database if empty, verify the MCP server starts, and print a readiness checklist.

**What should be visible:**
- Claude Code open with Memoreei MCP server connected (`.mcp.json` in project root)
- Discord `#memoreei-demo` channel visible
- Terminal ready for ad-hoc commands if needed

---

## The Demo

### Opening (15 sec)

> "This is Memoreei — a personal memory server. It ingests your conversations from anywhere — Discord, Telegram, WhatsApp, email — and makes them searchable by any AI model through MCP."

### Architecture (15 sec)

> "It's not a chatbot. It's infrastructure. Any MCP client connects to it — Claude Code, Cursor, your own apps. Your data stays local. Embeddings run locally via ONNX. No cloud, no API keys required."

### Live Discord Demo (60 sec)

1. Switch to Discord — show `#memoreei-demo` with existing bot conversation
2. **Type a new message** in the channel:
   > *"Hey everyone, I just found out that the secret ingredient in grandma's cookies is actually cardamom"*
3. Switch to Claude Code
4. Say: **"Sync my Discord and tell me what people have been talking about"**
   - Claude calls `sync_discord` → picks up all messages including the one just typed
   - Claude calls `search_memory` → summarizes the conversation
5. Say: **"What was the secret ingredient I just mentioned?"**
   - Returns: *"cardamom"* — with the exact message and timestamp
   - *This is the money shot — real-time memory from a message typed 30 seconds ago*

### Multi-Source Search (45 sec)

6. Say: **"Search all my memories for anything about movies"**
   - Returns results across Discord, WhatsApp, and manual notes — multiple sources, one query
7. Say: **"Who have I been talking to the most this week?"**
   - Returns participant stats fused across sources

### Show Sources (15 sec)

8. Say: **"List all my memory sources"**
   - Shows each source with message counts: `discord:...`, `whatsapp:...`, `manual`
   > *"One server. Every conversation. Any AI client."*

### Use Case Flash (if time — 15 sec)

- Quick flash of the Movie Ring web app or Memory Search UI
> "Because it's an MCP server, anything can build on top of it. This web app scans conversations for movie mentions and shows what my friends are recommending."

### Close (15 sec)

> "Memoreei. Your AI's long-term memory. Local-first, open source, and on GitHub right now."
>
> Drop the repo link.

---

## Backup Plans

| Problem | Recovery |
|---------|----------|
| Claude Code is slow | Have pre-run query results ready to paste |
| Discord sync fails | WhatsApp data is already seeded — search `whatsapp:printer_conspiracy` |
| Embeddings slow on first sync | "First sync downloads the model — here's one I prepared earlier" → show pre-populated results |
| MCP server not connected | Run `bash scripts/demo_setup.sh` and reconnect in Claude Code settings |

---

## MCP Tools Reference

| Tool | What it does |
|------|-------------|
| `sync_discord` | Pull new messages from Discord channel into memory |
| `search_memory` | Hybrid keyword + semantic search across all sources |
| `get_context` | Fetch surrounding messages around a memory hit |
| `add_memory` | Manually store a note or fact |
| `list_sources` | Show all ingested sources with message counts |
| `ingest_whatsapp` | Import a WhatsApp chat export `.txt` file |

---

## Key Phrases to Hit

- "Not a chatbot — it's infrastructure"
- "Any MCP client can connect"
- "Your data stays local"
- "Real-time ingestion"
- "Open source"

## Words to Avoid

- "RAG" — just show it working
- "Basic" anything
- Explaining vector databases — the demo speaks for itself
