# Memoreei

**Remember every conversation you've ever had.**

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![CI](https://github.com/CalebChristiansen/Memoreei/actions/workflows/ci.yml/badge.svg)](https://github.com/CalebChristiansen/Memoreei/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/memoreei)](https://pypi.org/project/memoreei/)
[![codecov](https://codecov.io/gh/CalebChristiansen/Memoreei/graph/badge.svg)](https://codecov.io/gh/CalebChristiansen/Memoreei)

Memoreei is an open-source MCP server that gives AI assistants a searchable memory of your entire personal communication history. It connects to 13 platforms so far — Discord, WhatsApp, Telegram, Signal, iMessage, Gmail, Slack, Instagram, and more — ingests your messages, and indexes them with a hybrid search engine combining keyword and semantic vector search. Any AI client that supports the [Model Context Protocol](https://modelcontextprotocol.io/) can then query your memories as naturally as asking a question.

**The problem it solves:** every AI assistant starts each conversation with no knowledge of who you are, what you've discussed, or what matters to you. Memoreei changes that by turning years of your personal conversations into a living, searchable knowledge base — surfaced exactly when your AI needs it.

```
"What's my friend's favorite restaurant?"
"What did my sister say she wanted for her birthday?"
"How many times have I asked Dory to send that link again?"
```

Your AI can answer these now. Without Memoreei, it can't.

---

## It's Also a Platform

Memoreei isn't just a memory server — any app can be built on top of it. Two of our favorites:

- **Movie Ring** — Ranks movies based on what your friends are actually talking about in the group chat that never shuts up.
- **Contact Dossier** — A personal CRM that builds itself from your conversations. No data entry required.

---

## Key Features

- **Local-first** — all data stays in a single SQLite file on your machine
- **13 sources and counting** — WhatsApp, Discord, Telegram, Slack, Matrix, iMessage, Signal, Gmail, Instagram, Mastodon, and more
- **MCP-native** — 19 tools exposed via the Model Context Protocol, usable by any MCP client
- **Hybrid search** — BM25 keyword search + vector semantic search, fused with Reciprocal Rank Fusion
- **No mandatory cloud** — default embedding model runs fully offline via ONNX
- **CLI + Docker** — `pip install memoreei` and you're running in under a minute

---

## Supported Sources

| Source | Type | Status |
|--------|------|--------|
| WhatsApp (`.txt` export) | File import | ✅ Stable |
| Discord (bot API) | Live sync | ✅ Stable |
| Discord Data Package (GDPR export) | File import | ✅ Stable |
| Telegram (bot API) | Live sync | ✅ Stable |
| Slack (Web API) | Live sync | ✅ Stable |
| Matrix (Client-Server API) | Live sync | ✅ Stable |
| Mastodon (REST API) | Live sync | ✅ Stable |
| Gmail (IMAP) | Live sync | ✅ Stable |
| Instagram DMs (GDPR export) | File import | ✅ Stable |
| Facebook Messenger (GDPR export) | File import | ✅ Stable |
| SMS Backup & Restore XML | File import | ✅ Stable |
| Generic JSON / JSON-lines | File import | ✅ Stable |
| Generic CSV / TSV | File import | ✅ Stable |
| iMessage (macOS) | Live sync | 🧪 Beta |
| Signal Desktop | Live sync | 🧪 Beta |
| Manual notes (`add_memory`) | MCP tool | ✅ Stable |

**File import** — one-time or repeated ingest from an exported file.
**Live sync** — incremental sync via API, checkpoint-based (only fetches new messages).

---

## Quick Start

```bash
pip install memoreei
memoreei setup           # interactive — pick connectors, enter credentials
memoreei sync            # pull messages from configured sources
memoreei serve           # start the MCP server
```

Or from source:

```bash
git clone https://github.com/CalebChristiansen/Memoreei.git
cd Memoreei
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

### Connect to Your AI

Add to your MCP client config (e.g. `.mcp.json`, `claude_desktop_config.json`, or wherever your client reads MCP server definitions):

```json
{
  "mcpServers": {
    "memoreei": {
      "command": "/path/to/memoreei/.venv/bin/python",
      "args": ["-m", "memoreei.server"],
      "cwd": "/path/to/memoreei"
    }
  }
}
```

If you installed via `pip install memoreei`, the `memoreei-server` command is also available:

```json
{
  "mcpServers": {
    "memoreei": {
      "command": "memoreei-server"
    }
  }
}
```

---

## MCP Tools

All 19 tools are available to any connected MCP client.

### Search & Retrieval

#### `search_memory`
Hybrid keyword + semantic search across all ingested memories.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | **required** | Natural language search query |
| `limit` | int | 10 | Max results to return |
| `source` | string | — | Filter by source, e.g. `whatsapp:friends`, `discord:1234567890` |
| `participant` | string | — | Filter by sender name (case-insensitive) |
| `after` | string | — | ISO date lower bound, e.g. `2026-01-01` |
| `before` | string | — | ISO date upper bound |

#### `get_context`
Fetch surrounding messages for a specific memory — essential for understanding the conversation around a result.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `memory_id` | string | **required** | Memory ID (ULID) from search results |
| `before` | int | 5 | Messages to include before the target |
| `after` | int | 5 | Messages to include after the target |

#### `add_memory`
Manually store a note, fact, or anything worth remembering. Auto-embeds content immediately.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `content` | string | **required** | Text to remember |
| `source` | string | `"manual"` | Source label |
| `metadata` | dict | — | Optional key-value pairs |

#### `list_sources`
Inventory all ingested sources with message counts.

```json
{
  "whatsapp:friends": 1842,
  "discord:1234567890": 391,
  "telegram:-100987654321": 227,
  "manual": 12
}
```

---

### File Import Tools

#### `ingest_whatsapp`
Import a WhatsApp chat export `.txt` file. Handles multi-line messages, media placeholders, and deduplication on re-import.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_path` | string | Path to the WhatsApp `.txt` export file |

#### `import_discord_package`
Import a Discord GDPR data export (all channels and DMs). Accepts a ZIP file or extracted folder.

Request your data at: **Discord Settings → Privacy & Safety → Request All of My Data**

| Parameter | Type | Description |
|-----------|------|-------------|
| `package_path` | string | Path to extracted folder or ZIP file |

#### `import_messenger`
Import Facebook Messenger messages from a GDPR data download (JSON format).

Download at: **Facebook Settings → Your Information → Download Your Information**

| Parameter | Type | Description |
|-----------|------|-------------|
| `data_path` | string | Path to the extracted folder containing `messages/inbox/` |

#### `import_instagram`
Import Instagram DMs from a GDPR data download (JSON format).

Download at: **Instagram Settings → Accounts Center → Your Information → Download Your Information**

| Parameter | Type | Description |
|-----------|------|-------------|
| `data_path` | string | Path to the extracted folder containing `your_instagram_activity/` |

#### `import_sms_backup`
Import SMS/MMS messages from an Android [SMS Backup & Restore](https://play.google.com/store/apps/details?id=com.riteshsahu.SMSBackupRestore) XML file.

| Parameter | Type | Description |
|-----------|------|-------------|
| `file_path` | string | Path to the XML backup file |

#### `import_json_file`
Import messages from any JSON file. Supports JSON arrays, JSON-lines, and wrapped objects. Covers Google Chat takeout, Google Hangouts, LinkedIn data, and custom formats.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | string | **required** | Path to the JSON or JSON-lines file |
| `content_field` | string | **required** | Field name containing the message text |
| `sender_field` | string | — | Field name for sender name |
| `timestamp_field` | string | — | Field name for timestamp (auto-detects format) |
| `source_label` | string | `"json-import"` | Tag for imported messages |

#### `import_csv_file`
Import messages from any CSV or TSV file. Auto-detects delimiter (comma, tab, semicolon). Covers LinkedIn exports and any custom spreadsheet format.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | string | **required** | Path to the CSV/TSV file |
| `content_column` | string | **required** | Column name for message text |
| `sender_column` | string | — | Column name for sender |
| `timestamp_column` | string | — | Column name for timestamp |
| `source_label` | string | `"csv-import"` | Tag for imported messages |

---

### Live Sync Tools

All sync tools use checkpoint-based incremental sync — only new messages are fetched on subsequent runs.

#### `sync_discord`
Sync messages from a Discord channel via the bot API.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `channel_id` | string | `DISCORD_CHANNEL_ID` env var | Discord channel ID |

#### `sync_telegram`
Sync messages received by a Telegram bot via `getUpdates`. Bot must be a member of the target group or have received DMs.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `chat_id` | string | `TELEGRAM_CHAT_ID` env var | Chat ID (positive = DM, negative = group). Syncs all if omitted. |

#### `sync_matrix`
Sync messages from a Matrix room using the Client-Server API.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `room_id` | string | `MATRIX_ROOM_ID` env var | Matrix room ID, e.g. `!abc123:matrix.org` |

#### `sync_slack`
Sync messages from a Slack channel via the Web API (`conversations.history`). Requires bot token with `channels:history` and `users:read` scopes.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `channel_id` | string | `SLACK_CHANNEL_ID` env var | Slack channel ID, e.g. `C1234567890` |

#### `sync_email`
Sync Gmail messages via IMAP. Uses per-folder UID checkpointing.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `folder` | string | `"INBOX"` | IMAP folder, e.g. `[Gmail]/Sent Mail` |
| `max_emails` | int | 200 | Maximum emails per sync |

#### `sync_mastodon`
Sync Mastodon posts. Public and hashtag timelines require no authentication.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `instance` | string | `MASTODON_INSTANCE` env var | Instance URL, e.g. `https://fosstodon.org` |
| `hashtag` | string | `MASTODON_HASHTAG` env var | Hashtag without `#`, or omit for public timeline |
| `access_token` | string | `MASTODON_ACCESS_TOKEN` env var | OAuth token (optional, for home timeline) |

#### `sync_imessage`
> 🧪 Beta — macOS only. Requires Full Disk Access for Terminal in System Settings → Privacy & Security.

Sync iMessage/SMS conversations from `~/Library/Messages/chat.db` (read-only).

| Parameter | Type | Description |
|-----------|------|-------------|
| `chat_name` | string | Optional — filter by contact name or identifier (e.g. `+1234567890`) |

#### `sync_signal`
> 🧪 Beta — requires `pysqlcipher3`. Signal Desktop must be installed.

Sync Signal Desktop messages from the local encrypted SQLCipher database.
Default paths: `~/.config/Signal/sql/db.sqlite` (Linux), `~/Library/Application Support/Signal/sql/db.sqlite` (macOS).

| Parameter | Type | Description |
|-----------|------|-------------|
| `conversation_id` | string | Optional — filter by conversation ID, name, or phone number |

---

### Utility Tools

#### `refresh_memory`
Trigger an immediate sync of all configured sources. Returns count of new messages.

#### `sync_all`
Sync every configured connector and return counts per source.

---

## CLI Reference

```bash
# Interactive setup — configure connectors, writes to .env
memoreei setup             # pick from a list (spacebar to select, enter to confirm)
memoreei setup gmail       # configure a specific connector directly

# Start the MCP server (stdio transport, default)
memoreei serve

# Start with SSE transport (for HTTP clients)
memoreei serve --sse --port 8080

# Show DB stats: message counts, sources, last sync times
memoreei status

# Sync all configured sources
memoreei sync

# Sync a specific source
memoreei sync discord
memoreei sync telegram
memoreei sync matrix
memoreei sync slack
memoreei sync email
memoreei sync mastodon

# Search from the terminal
memoreei search "API redesign notes"
memoreei search "printer issue" --limit 5 --source whatsapp:friends

# Import files
memoreei import-whatsapp /path/to/WhatsApp\ Chat.txt
memoreei import-sms /path/to/sms-backup.xml
memoreei import-discord-package /path/to/discord-package.zip

# Show current configuration (tokens masked)
memoreei config
```

---

## Architecture

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │                          Your Data Sources                           │
 │                                                                      │
 │  File Imports                          Live Sync (API)               │
 │  ─────────────────────────────         ──────────────────────────    │
 │  WhatsApp .txt  Instagram JSON         Discord    Telegram           │
 │  Messenger JSON SMS Backup XML         Slack      Matrix             │
 │  Discord ZIP    Generic JSON/CSV       Gmail      Mastodon           │
 │                                        iMessage   Signal             │
 └──────────────┬───────────────────────────────┬─────────────────────┘
                │                               │
                ▼                               ▼
 ┌──────────────────────────────────────────────────────────────────────┐
 │                        Memoreei MCP Server                           │
 │                                                                      │
 │  ┌────────────────────┐  ┌──────────────────┐  ┌─────────────────┐  │
 │  │    Connectors      │  │  Hybrid Search   │  │   MCP Tools     │  │
 │  │                    │  │                  │  │                 │  │
 │  │  13 sources        │  │  FTS5 (BM25)     │  │  search_memory  │  │
 │  │  file + live sync  │  │  + vector cosine │  │  get_context    │  │
 │  │  checkpoint-based  │  │  + RRF fusion    │  │  add_memory     │  │
 │  │  dedup on import   │  │                  │  │  list_sources   │  │
 │  │                    │  └────────┬─────────┘  │  ingest_*       │  │
 │  │                    │           │            │  import_*       │  │
 │  │                    │           │            │  sync_*         │  │
 │  │                    │  ┌────────▼──────────┐ │  refresh_memory │  │
 │  │                    │  │  SQLite Database  │ │  sync_all       │  │
 │  │                    │  │  memories + FTS5  │ └────────┬────────┘  │
 │  └────────────────────┘  │  embeddings BLOB  │          │           │
 │                          │  sync checkpoints │          │           │
 │                          └───────────────────┘          │           │
 └────────────────────────────────────────────────────────┼────────────┘
 │                                                          │ stdio / SSE
                                                          ▼
                                               ┌─────────────────────┐
                                               │    MCP Clients      │
                                               │                     │
                                               │  Any AI assistant   │
                                               │  that speaks MCP    │
                                               └─────────────────────┘
```

---

## How Hybrid Search Works

Memoreei runs two searches in parallel and fuses the results:

```
Query: "that weird API rate limit issue"
         │
         ├──▶ FTS5 BM25 keyword search
         │    Matches "API", "rate", "limit" — fast, exact
         │    Returns ranked list of IDs
         │
         └──▶ Vector search (cosine similarity)
              Matches "throttling", "429 errors", "backoff"
              Returns ranked list of IDs
                  │
                  ▼
         Reciprocal Rank Fusion (RRF)
         ─────────────────────────────
         score(item) = Σ  1 / (60 + rank_i)
                        i ∈ {keyword_rank, vector_rank}

         Items in BOTH result sets are boosted.
         Items in only one set still contribute.
         Top N returned, then filtered by source/participant/date.
```

**Why RRF?** Rank-based fusion requires no score normalization across different scales. The constant `k=60` is the standard default from the original paper and empirically outperforms weighted linear combinations.

**Default embedding model:** `BAAI/bge-small-en-v1.5` via FastEmbed — 384-dimensional vectors, ~23 MB ONNX model, runs fully offline.

---

## Configuration

Copy `.env.example` to `.env` and fill in the credentials for the sources you want to use. Unused connectors can be left blank. Or just run `memoreei setup` and it'll walk you through it.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` (local ONNX, no API key) or `openai` |
| `OPENAI_API_KEY` | — | Required only if `EMBEDDING_PROVIDER=openai` |
| `MEMOREEI_DB_PATH` | `./memoreei.db` | SQLite database path |
| `AUTO_SYNC` | `false` | Enable background sync loop on server start |
| `AUTO_SYNC_INTERVAL` | `3600` | Background sync interval in seconds |

### Discord

| Variable | Description |
|----------|-------------|
| `DISCORD_BOT_TOKEN` | Bot token from Discord Developer Portal |
| `DISCORD_CHANNEL_ID` | Default channel ID for `sync_discord` |

### Telegram

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Default chat ID (positive = DM, negative = group) |

### Matrix

| Variable | Description |
|----------|-------------|
| `MATRIX_HOMESERVER` | Homeserver URL, e.g. `https://matrix.org` |
| `MATRIX_ACCESS_TOKEN` | User access token |
| `MATRIX_ROOM_ID` | Default room ID, e.g. `!abc123:matrix.org` |

### Slack

| Variable | Description |
|----------|-------------|
| `SLACK_BOT_TOKEN` | Bot token (`xoxb-...`), requires `channels:history` + `users:read` |
| `SLACK_CHANNEL_ID` | Default channel ID, e.g. `C1234567890` |

### Gmail

| Variable | Description |
|----------|-------------|
| `GMAIL_EMAIL` | Gmail address |
| `GMAIL_APP_PASSWORD` | [App Password](https://myaccount.google.com/apppasswords) (required if 2FA enabled) |

### Mastodon

| Variable | Default | Description |
|----------|---------|-------------|
| `MASTODON_INSTANCE` | `https://mastodon.social` | Instance URL |
| `MASTODON_HASHTAG` | — | Default hashtag (without `#`) |
| `MASTODON_ACCESS_TOKEN` | — | OAuth token (optional, for home timeline) |

### iMessage (macOS only)

| Variable | Default | Description |
|----------|---------|-------------|
| `IMESSAGE_DB_PATH` | `~/Library/Messages/chat.db` | Override path to `chat.db` |

### Signal Desktop

| Variable | Description |
|----------|-------------|
| `SIGNAL_DB_PATH` | Override path to Signal's `db.sqlite` |
| `SIGNAL_CONFIG_PATH` | Override path to Signal's `config.json` |

---

## Privacy

**Local-first by design.**

- All data stored in a single SQLite file on your machine
- Default embedding model (FastEmbed) runs entirely offline via ONNX — zero network calls
- OpenAI embeddings are strictly opt-in (`EMBEDDING_PROVIDER=openai`)
- No telemetry, no analytics, no cloud sync
- Your messages never leave your machine in the default configuration

**What requires network access:**

- Live sync connectors (Discord, Telegram, Slack, Matrix, Gmail, Mastodon) make outbound API calls to those services
- `EMBEDDING_PROVIDER=openai` sends message text to OpenAI's API for embedding

The `.env` file and `memoreei.db` are in `.gitignore`.

---

## Docker

```bash
docker build -t memoreei .
docker run -v ./data:/data -e MEMOREEI_DB_PATH=/data/memoreei.db memoreei serve
```

Or with docker-compose:

```bash
docker-compose up
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on adding new connectors, running tests, and submitting pull requests.

---

## License

MIT
