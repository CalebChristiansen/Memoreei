# Memoreei Use Case Apps

**Status:** Not started — nice-to-have, built after core is DONE.

Each app is standalone — no imports from the main memoreei package. They connect to the MCP server or directly to the SQLite DB.

---

## Apps

### 1. 🎬 Movie Ring
**What:** Web app that scans your conversations for movie mentions, shows them with posters, friend quotes, and links. "What movies are my friends talking about?"
**Tech:** Python (Flask or FastAPI), TMDB API for posters, self-hosted via Caddy
**URL:** TBD (will be hosted at something like https://caleb.cafe/moviering)
**Status:** ⬜ Not started

### 2. 🤖 Memory Bot (Discord)
**What:** A Discord bot you can @ and ask "what did we talk about last week?" or "who mentioned the printer?" Queries Memoreei and responds in-channel.
**Tech:** discord.py, connects to memoreei.db directly
**Channel:** TBD
**Status:** ⬜ Not started

### 3. 📊 People Map
**What:** Web page showing who you talk to most, what topics come up, relationship graph visualization. Interactive.
**Tech:** Python + D3.js or similar, self-hosted
**URL:** TBD
**Status:** ⬜ Not started

### 4. 📅 On This Day
**What:** Discord bot or web page that shows "what were you talking about on this date" — a conversational time capsule.
**Tech:** Python, cron-driven or on-demand
**Status:** ⬜ Not started

### 5. 🔍 Memory Search UI
**What:** Clean web search interface for your memories. Type a query, see results with sources, timestamps, participants, and context. Like a personal Google for your life.
**Tech:** FastAPI + HTMX or simple HTML/JS, self-hosted
**URL:** TBD
**Status:** ⬜ Not started

---

## Hosting Notes
- Self-hosted on karamja via Caddy reverse proxy (like Jellyfin)
- Tailscale for access: REDACTED_IP
- Apps run as systemd services or just background processes for the demo
