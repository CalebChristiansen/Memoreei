# Deployment Guide

## Bare Metal (pip install)

### Requirements

- Python 3.10+
- SQLite 3.35+ (ships with Python)

### Install

```bash
pip install memoreei
```

Or from source:

```bash
git clone https://github.com/CalebChristiansen/Memoreei.git
cd Memoreei
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Configure

Run the interactive setup to configure your connectors:

```bash
memoreei setup
```

This walks you through choosing an embedding provider, background sync and connectors,
and writes everything to `~/.memoreei/config.env` (mode 600). At the end it offers to
create the first API key for the network server.

You can also configure a single connector directly:

```bash
memoreei setup gmail
memoreei setup discord
```

Or edit `~/.memoreei/config.env` by hand (see `.env.example` for all variables).

### Home directory

Everything lives in one directory, `~/.memoreei/`:

| File | What |
|------|------|
| `config.env` | settings and connector credentials |
| `memoreei.db` | the memories, API key hashes and the list of registered import files |
| `memoreei.log` | the service log, on macOS |

Use another directory with `memoreei --home <dir> …` or `MEMOREEI_HOME=<dir>`. Back up
this directory and you have backed up memoreei.

### Run as a local MCP server (stdio)

For a client on the same machine. It starts memoreei itself, and gets every tool:

```json
{
  "mcpServers": {
    "memoreei": {
      "command": "/path/to/.venv/bin/memoreei",
      "args": ["serve"]
    }
  }
}
```

### Run as a network server (Streamable HTTP)

For clients on other machines. Create a key per client, then serve:

```bash
memoreei key create laptop     # prints the key once, plus client config
memoreei serve --http          # 0.0.0.0:3679, endpoint /mcp
```

The server refuses to start with no keys. Over the network it offers only
`search_memory`, `get_context`, `list_sources` and an argument-free `sync`. See the
README's [Run it as a network server](../README.md#run-it-as-a-network-server) for
connecting clients and HTTPS.

### Run CLI

```bash
memoreei setup    # interactive connector setup (first time)
memoreei serve    # start MCP server (stdio)
memoreei serve --http   # start the network server
memoreei sync     # sync configured sources and changed registered import files
memoreei search "what did zezima say about the meeting"
memoreei status   # show source counts
memoreei config   # show active configuration
memoreei key list # API keys and when each was last used
```

### Background service (launchd / systemd)

```bash
memoreei service install
```

On macOS this writes `~/Library/LaunchAgents/cafe.caleb.Memoreei.service.plist`, which runs
`memoreei serve --http` directly (no wrapper script, so that Full Disk Access is granted
to Python rather than to a shell). On Linux it writes `~/.config/systemd/user/memoreei.service`:

```ini
[Unit]
Description=Memoreei MCP server

[Service]
EnvironmentFile=%h/.memoreei/config.env
Environment=MEMOREEI_HOME=%h/.memoreei
ExecStart=/path/to/.venv/bin/memoreei serve --http --port 3679
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

On a headless Linux box, `loginctl enable-linger $USER` makes the user service start at
boot. `memoreei service status`, `logs` and `uninstall` do what they say.

---

## Docker

The image sets `MEMOREEI_HOME=/data`, so `config.env` and `memoreei.db` both live in the
one volume, and runs `memoreei serve --http` on port 3679.

### docker-compose

```bash
docker compose run --rm memoreei setup
docker compose run --rm memoreei key create laptop
docker compose up -d
```

Set `MEMOREEI_PUBLIC_URL` in `docker-compose.yml` to the URL clients will use: inside
the container memoreei can't see the host's addresses. Everything is persisted in
`./data/` on the host.

### Without compose

```bash
docker build -t memoreei .
docker run --rm -it -v $(pwd)/data:/data memoreei key create laptop
docker run -d -p 3679:3679 -v $(pwd)/data:/data memoreei
```

---

## Configuration Reference

All configuration is via environment variables. They are read from the environment,
then a `.env` in the current directory (for development), then
`$MEMOREEI_HOME/config.env`; earlier sources win.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMOREEI_HOME` | `~/.memoreei` | Directory holding `config.env` and `memoreei.db` |
| `MEMOREEI_DB_PATH` | `$MEMOREEI_HOME/memoreei.db` | Path to SQLite database file |
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` (local) or `openai` |
| `OPENAI_API_KEY` | — | Required if `EMBEDDING_PROVIDER=openai` |
| `AUTO_SYNC` | `false` | Enable background sync on server start |
| `AUTO_SYNC_INTERVAL` | `300` | Background sync interval in seconds |

### Network server

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMOREEI_HOST` | `0.0.0.0` | Bind address for `serve --http` |
| `MEMOREEI_PORT` | `3679` | Port for `serve --http` |
| `MEMOREEI_PUBLIC_URL` | — | URL clients use (e.g. behind a reverse proxy); printed by `key create` |
| `MEMOREEI_TLS_CERT` | — | TLS certificate (PEM) to serve HTTPS directly |
| `MEMOREEI_TLS_KEY` | — | TLS private key (PEM) |

### Discord

| Variable | Description |
|----------|-------------|
| `DISCORD_BOT_TOKEN` | Bot token (from Discord Developer Portal) |
| `DISCORD_CHANNEL_ID` | Default channel ID to sync |

### Telegram

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token (from @BotFather) |
| `TELEGRAM_CHAT_ID` | Default chat ID to sync |

### Matrix

| Variable | Description |
|----------|-------------|
| `MATRIX_HOMESERVER` | Homeserver URL, e.g. `https://matrix.org` |
| `MATRIX_ACCESS_TOKEN` | Access token from your Matrix client |
| `MATRIX_ROOM_ID` | Default room ID to sync |

### Slack

| Variable | Description |
|----------|-------------|
| `SLACK_BOT_TOKEN` | Bot OAuth token (`xoxb-...`) |
| `SLACK_CHANNEL_ID` | Default channel ID to sync |

### Gmail / IMAP

| Variable | Description |
|----------|-------------|
| `GMAIL_EMAIL` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | App password (requires 2FA enabled) |

### Mastodon

| Variable | Description |
|----------|-------------|
| `MASTODON_INSTANCE` | Instance URL, e.g. `https://mastodon.social` |
| `MASTODON_HASHTAG` | Hashtag to follow (without `#`) |
| `MASTODON_ACCESS_TOKEN` | Optional — for authenticated requests |
