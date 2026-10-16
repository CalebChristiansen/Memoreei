# Deployment Guide

On Linux, the packages on the [releases page](https://github.com/CalebChristiansen/Memoreei/releases/latest)
are the easy way: see the README's [On Linux: a package](../README.md#on-linux-a-package).
This guide is the pip route, which works anywhere Python does.

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
and writes settings to `config.env` in the home directory (mode 600) and tokens and
passwords into the encrypted database. At the end it offers to create the first API key
for the network server.

You can also configure a single connector directly:

```bash
memoreei setup gmail
memoreei setup discord
```

Or edit `config.env` by hand (see `.env.example` for all variables). A token or password
written there moves into the encrypted database at the next start.

### Home directory

Everything lives in one directory: `~/.local/share/memoreei` on Linux
(`$XDG_DATA_HOME/memoreei`), `~/Library/Application Support/Memoreei` on macOS, and
`~/.memoreei` elsewhere.

| File | What |
|------|------|
| `config.env` | settings: ports, paths, flags |
| `memoreei.db` | the memories, connector tokens and passwords, API key hashes and the list of registered import files; encrypted (SQLCipher) |
| `memoreei.key` | the database's key, mode 600, unless it's in GNOME Keyring or KWallet (see below) |
| `memoreei.log` | the service log, on macOS |

Use another directory with `memoreei --home <dir> …` or `MEMOREEI_HOME=<dir>`. Back up
this directory and you have backed up memoreei, as long as the key is in it: on a Linux
desktop the key may be in the keyring instead, as "Memoreei database key", and a backup
of `memoreei.db` without its key is noise.

### The database key

`memoreei.db` is always encrypted, with a random 256-bit key made on first start. It's
looked for in `MEMOREEI_DB_KEY` (64 hex characters, for containers), then
`memoreei.key` beside the database, then the desktop keyring (Secret Service: GNOME
Keyring, KWallet). A new key goes into the keyring when there is one and it's unlocked,
and into `memoreei.key` otherwise: on a Mac, a headless Linux box, and elsewhere.
`memoreei config` says where it is. A database from an earlier memoreei is encrypted on
its first start.

If the key can't be found, or is the wrong one, memoreei stops and says where it
looked. A locked keyring is one cause: the service started before anyone logged in. The
Linux unit restarts until it can read it.

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
`search_memoreei`, `get_context`, `list_sources` and an argument-free `sync`. See the
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
EnvironmentFile=%h/.local/share/memoreei/config.env
Environment=MEMOREEI_HOME=%h/.local/share/memoreei
ExecStart=/path/to/.venv/bin/memoreei serve --http --port 3679
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

The Linux packages ship their own unit, and there `service install` enables it rather
than writing one. On a headless Linux box, `loginctl enable-linger $USER` makes the user
service start at boot. `memoreei service status`, `logs` and `uninstall` do what they say.

---

## Configuration Reference

All configuration is via environment variables. They are read from the environment,
then a `.env` in the current directory (for development), then
`$MEMOREEI_HOME/config.env` and the tokens and passwords kept in the database; earlier
sources win.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMOREEI_HOME` | `~/.local/share/memoreei` on Linux, `~/Library/Application Support/Memoreei` on macOS, `~/.memoreei` elsewhere | Directory holding `config.env`, `memoreei.db` and `memoreei.key` |
| `MEMOREEI_DB_PATH` | `$MEMOREEI_HOME/memoreei.db` | Path to the (encrypted) SQLite database file; its key file sits beside it |
| `MEMOREEI_DB_KEY` | — | The database key, 64 hex characters, in place of `memoreei.key` or the keyring |
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` (local) or `openai` |
| `OPENAI_API_KEY` | — | Required if `EMBEDDING_PROVIDER=openai` |
| `AUTO_SYNC` | `false` | Enable background sync on server start |
| `AUTO_SYNC_INTERVAL` | `300` | Background sync interval in seconds |
| `MEMOREEI_ME` | the account's full name | Names, numbers or addresses that mean the owner, comma-separated; `participant` resolves them to `me` |

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

### iMessage (macOS)

| Variable | Description |
|----------|-------------|
| `IMESSAGE_DB_PATH` | Messages database, normally `~/Library/Messages/chat.db`. Needs Full Disk Access |

### WhatsApp

| Variable | Description |
|----------|-------------|
| `WHATSAPP_DB_PATH` | WhatsApp for Mac's `ChatStorage.sqlite` (`~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/`), or the copy in an iPhone backup |

### Signal Desktop (macOS and Linux)

| Variable | Description |
|----------|-------------|
| `SIGNAL_DB_KEY` | Signal Desktop's database key. Written by *Connect* (Sources, or `memoreei setup signal`), which reads it from the system keyring once and keeps it in the encrypted database |
| `SIGNAL_DIR` | Signal Desktop's data folder, if it isn't in its usual place |
