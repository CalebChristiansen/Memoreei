# Memoreei — Live Demo Script (3 minutes)

## Setup (before demo)
- Claude Code open with Memoreei MCP server connected
- Discord #memoreei-demo visible on screen
- Any additional chat services open in tabs (Telegram, etc.)
- Gmail inbox for REDACTED_ACCOUNT visible

---

## The Demo

### Opening (15 sec)
> "This is Memoreei — a personal memory server. It ingests your conversations from anywhere — Discord, Telegram, email, WhatsApp — and makes them searchable by any AI model through MCP."

### Show the Architecture (15 sec)
> "It's not a chatbot. It's infrastructure. Any MCP client connects to it — Claude Code, Cursor, your own apps. Your data stays local. Embeddings run locally. No cloud."

### Live Discord Demo (60 sec)
1. Switch to Discord — show #memoreei-demo with the bot conversation
2. **Type a new message** in the channel: *"Hey everyone, I just found out that the secret ingredient in grandma's cookies is actually cardamom"*
3. Switch to Claude Code
4. Say: **"Sync my Discord and tell me what people have been talking about"**
   - Claude calls `sync_discord` → picks up all messages including the one you just typed
   - Claude calls `search_memory` → summarizes the conversation
5. Say: **"What was the secret ingredient I just mentioned?"**
   - Returns: "cardamom" with your exact message and timestamp
   - *This is the money shot — real-time memory*

### Multi-Source Demo (45 sec)
6. Say: **"Search all my memories for anything about movies"**
   - Returns results from Discord, maybe Telegram, email — multiple sources
   - Show how it pulls context from different platforms into one answer
7. Say: **"Who have I been talking to the most this week?"**
   - Returns participant stats across sources

### Show Sources (15 sec)
8. Say: **"List all my memory sources"**
   - Shows: discord, telegram, email, whatsapp — with message counts
   - *"One server, every conversation, any AI client."*

### Use Case Flash (if time — 15 sec)
- Quick flash of the Movie Ring web app or Memory Search UI
> "And because it's an MCP server, you can build anything on top. This web app scans my conversations for movie mentions and shows what my friends are recommending."

### Close (15 sec)
> "Memoreei. Your AI's long-term memory. It's open source, it's local-first, and it's on GitHub right now."
> 
> Drop the repo link.

---

## Backup Plans
- If Claude Code is slow: have pre-run queries ready to show
- If Discord sync fails: WhatsApp data is already seeded, search that
- If embeddings are slow: "First sync takes a moment — here's one I prepared earlier" → show pre-populated results

## Key Phrases to Hit
- "Not a chatbot — it's infrastructure"
- "Any MCP client can connect"
- "Your data stays local"
- "Real-time ingestion"
- "Open source"

## Don't Say
- "RAG" (it's on the disqualification list framing)
- "Basic" anything
- Don't explain vector databases — just show it working
