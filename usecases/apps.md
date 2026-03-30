# Memoreei Use Case Apps

**Status:** 3/5 apps complete — Movie Ring, Memory Bot, and Contact Dossier are done and deployable.

Each app is standalone — no imports from the main memoreei package. They connect to the MCP server or directly to the SQLite DB.

---

## Apps

### 1. 🎬 Movie Ring
**What:** Web app that scans your conversations for movie mentions, shows them with posters, friend quotes, and links. "What movies are my friends talking about?"
**Tech:** Python (Flask), TMDB API for posters, Pico CSS, self-hosted via systemd
**URL:** http://localhost:5050
**Status:** ✅ Done

**Files:** `usecases/moviering/`
- `app.py` — Flask server, port 5050
- `templates/index.html` — Cinema-themed UI (Pico CSS dark)
- `moviering.service` — systemd user service
- `caddy.conf` — Caddy config options

**Setup:**
```bash
# 1. Install deps (already in project venv)
.venv/bin/pip install flask requests

# 2. Add TMDB key to .env for movie posters
echo "TMDB_API_KEY=your_key_here" >> .env
# Get free key at: https://www.themoviedb.org/settings/api

# 3. Install + start systemd service
cp usecases/moviering/moviering.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now moviering

# 4. Access at http://localhost:5050
```

### 2. 🤖 Memory Bot (Discord)
**What:** A Discord bot you can @ or use `!remember <query>` to search Memoreei memories and get formatted results in-channel.
**Tech:** discord.py, connects to memoreei.db directly (hybrid FTS + vector search)
**Status:** ✅ Done

**Files:** `usecases/memorybot/`
- `bot.py` — Discord bot, responds to mentions and `!remember` commands

**Setup:**
```bash
# Token already in .env as DISCORD_BOT_TOKEN
python usecases/memorybot/bot.py
```

**Usage in Discord:**
```
@MemoryBot what did we talk about last week?
!remember printer issues
!remember movies caleb mentioned
```

**Response format:** source, timestamp, participants, truncated content (up to 5 results)

### 3. 📊 People Map
**What:** Web page showing who you talk to most, what topics come up, relationship graph visualization. Interactive.
**Tech:** Python + D3.js or similar, self-hosted
**URL:** TBD
**Status:** ❌ Skipped — deferred post-hackathon

### 4. 📅 On This Day
**What:** Discord bot or web page that shows "what were you talking about on this date" — a conversational time capsule.
**Tech:** Python, cron-driven or on-demand
**Status:** ❌ Skipped — deferred post-hackathon

### 5. 🕵️ Contact Dossier
**What:** Pick any person and get a full dossier — every conversation across all platforms, key topics, timeline, sentiment. A CRM for your actual relationships.
**Tech:** Python (Flask), Pico CSS dark, self-hosted via systemd
**URL:** http://localhost:5051
**Status:** ✅ Done

**Files:** `usecases/dossier/`
- `app.py` — Flask server, port 5051
- `templates/index.html` — Dark Pico CSS UI, split-panel layout
- `dossier.service` — systemd user service
- `caddy.conf` — Caddy config options

**Features:**
- Person sidebar with search + message counts + platform dots
- Stats grid: message count, platform count, first/last seen dates
- Sentiment vibe (positive/neutral/negative with animated bar)
- Key topics tag cloud (word frequency, sized by count)
- Platform breakdown with colored bars
- Recent messages feed + full timeline grouped by source
- All data from memoreei.db, no extra dependencies

**Setup:**
```bash
# 1. Install deps (already in project venv)
.venv/bin/pip install flask

# 2. Install + start systemd service
cp usecases/dossier/dossier.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now dossier

# 3. Access at http://localhost:5051
# Or run directly:
.venv/bin/python usecases/dossier/app.py
```

---

## Hosting Notes
- Self-hosted on your-server via Caddy reverse proxy (like Jellyfin)
- Apps run as systemd services or just background processes for the demo
