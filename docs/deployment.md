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

This walks you through selecting connectors, entering credentials, and choosing a database location. Writes everything to `.env`.

You can also configure a single connector directly:

```bash
memoreei setup gmail
memoreei setup discord
```

Or edit `.env` manually (see `.env.example` for all variables).

### Run as MCP Server

The primary use case is running as an MCP server over stdio, configured in Claude Code or Claude Desktop:

```json
{
  "mcpServers": {
    "memoreei": {
      "command": "/path/to/.venv/bin/memoreei-server",
      "args": [],
      "env": {}
    }
  }
}
```

### Run CLI

```bash
memoreei setup    # interactive connector setup (first time)
memoreei serve    # start MCP server (stdio)
memoreei sync     # one-shot sync of all configured sources
memoreei search "what did alice say about the meeting"
memoreei status   # show source counts
memoreei config   # show active configuration
```

### systemd User Service

Create `~/.config/systemd/user/memoreei.service`:

```ini
[Unit]
Description=Memoreei MCP Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/memoreei
ExecStart=/path/to/.venv/bin/memoreei-server
Restart=on-failure
EnvironmentFile=/path/to/memoreei/.env

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user enable memoreei
systemctl --user start memoreei
systemctl --user status memoreei
```

---

## Docker

### Build and Run

```bash
docker build -t memoreei .
docker run -v $(pwd)/data:/data -v $(pwd)/.env:/app/.env:ro memoreei
```

### docker-compose

```bash
cp .env.example .env
$EDITOR .env
docker compose up -d
```

The database is persisted in `./data/memoreei.db` on the host.

---

## Configuration Reference

All configuration is via environment variables (loaded from `.env` if present).

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMOREEI_DB_PATH` | `./memoreei.db` | Path to SQLite database file |
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` (local) or `openai` |
| `OPENAI_API_KEY` | — | Required if `EMBEDDING_PROVIDER=openai` |
| `AUTO_SYNC` | `false` | Enable background sync on server start |
| `SYNC_INTERVAL` | `300` | Background sync interval in seconds |

### Discord

| Variable | Description |
|----------|-------------|
| `DISCORD_TOKEN` | Bot token (from Discord Developer Portal) |
| `DISCORD_CHANNEL_ID` | Default channel ID to sync |

### Telegram

| Variable | Description |
|----------|-------------|
| `TELEGRAM_TOKEN` | Bot token (from @BotFather) |
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
