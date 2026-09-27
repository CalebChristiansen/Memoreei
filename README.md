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
- **MCP-native** — every tool over stdio for a local client; a key-protected, read-only surface over the network for everything else
- **Hybrid search** — BM25 keyword search + vector semantic search, fused with Reciprocal Rank Fusion
- **No mandatory cloud** — default embedding model runs fully offline via ONNX
- **Runs anywhere** — a package for Linux, an app for the Mac, `pip install memoreei` elsewhere; reach it from any machine on your network

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

**On Linux?** [Download the package](#on-linux-a-package) for your distribution and
open Memoreei from your applications. Python and the search model are inside.

**On a Mac?** [Memoreei.app](#on-a-mac-memoreeiapp) is the easy way: nothing to
install first, no Terminal, and it's the only way to read iMessage without granting
Full Disk Access to Python itself.

**Anywhere else** (Windows, for now), with Python 3.10 or newer:

```bash
pip install memoreei
memoreei setup           # interactive — pick connectors, enter credentials
memoreei sync            # pull messages from configured sources
```

Or from source:

```bash
git clone https://github.com/CalebChristiansen/Memoreei.git
cd Memoreei
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

Everything memoreei knows lives in one directory: `config.env` for settings and
credentials, `memoreei.db` for the memories. It's `~/Library/Application
Support/Memoreei` on macOS (shared with Memoreei.app), `~/.local/share/memoreei` on
Linux and `~/.memoreei` everywhere else. Every command finds it from wherever you run
it. Point it elsewhere with `--home <dir>` or `MEMOREEI_HOME`.

Then connect an AI client, either on the same machine or over the network.

### On the same machine (stdio)

The client starts memoreei itself and talks to it over stdio. No network, no key,
and every tool, including the ones that import files. Add to your MCP client config
(`.mcp.json`, `claude_desktop_config.json`, or wherever your client keeps them):

```json
{
  "mcpServers": {
    "memoreei": {
      "command": "memoreei",
      "args": ["serve"]
    }
  }
}
```

Use the full path to `memoreei` (`which memoreei`) if it's installed in a virtualenv
your client doesn't know about. For Claude Code:
`claude mcp add memoreei -- memoreei serve`.

### From other machines (network server)

See [Run it as a network server](#run-it-as-a-network-server), next.

---

## On Linux: a package

The whole server in one download, Python and the search model included. It runs as a
service for you, not as root, with the same dashboard as the Mac app. Ubuntu 20.04,
Debian 11, RHEL and Rocky 8, current Fedora, or newer; x86_64 or ARM64.

1. **Download** from the [latest release](https://github.com/CalebChristiansen/Memoreei/releases/latest):
   - Ubuntu, Debian: `memoreei_X.Y.Z_amd64.deb` (`arm64` on a Raspberry Pi or other ARM machine)
   - Fedora, RHEL, Rocky: `memoreei-X.Y.Z.x86_64.rpm` (`aarch64` on ARM)
   - anything else, or without root: `memoreei-X.Y.Z-linux-x86_64.tar.gz` (`aarch64` on ARM)
2. **Install it.** Double-click the file to open it in your software center, or:
   ```bash
   sudo apt install ./memoreei_*.deb        # Ubuntu, Debian
   sudo dnf install ./memoreei-*.rpm        # Fedora, RHEL, Rocky
   tar xzf memoreei-*-linux-*.tar.gz && memoreei-*-linux-*/install.sh   # into ~/.local, no root
   ```
3. **Open Memoreei** from your applications. It starts the server, sets it to start
   when you log in, and opens the dashboard in your browser. Under **Clients**, create a
   key for each machine or app that will search your memories. The key is shown once,
   with ready-to-paste setup for Claude Code and `.mcp.json`.
4. **Sources** are set up in a terminal for now: `memoreei setup` for Gmail, Discord,
   Slack, Telegram, Matrix and Mastodon, and `memoreei import …` for chat exports.
   Signal Desktop isn't supported yet: it now keeps its database key in the system
   keyring, where Memoreei can't read it.

**A server with no desktop.** The same package, from a terminal:

```bash
memoreei service install     # starts it now and at every login, and offers to start it at boot
memoreei key create laptop   # a key per client; see "Connect a client" below
memoreei admin-url           # how to reach the dashboard from your own computer
```

The dashboard only answers on the machine itself. With no display, `admin-url` prints
the `ssh -L 3679:localhost:3679 you@server` that brings it to your laptop, and a
one-time link to open there.

**The dashboard** shows whether the server is running, switches *Start at login* and
*Start at boot* on and off, shows the log, and stops the server. It also says when
there's a new release. Download that and install it the same way; a running Memoreei
restarts on the new version by itself.

**Where things are.** Data in `~/.local/share/memoreei`, caches in `~/.cache/memoreei`,
the program in `/opt/memoreei` (or `~/.local/opt/memoreei` from the tarball). The server
is the systemd user unit `memoreei.service`: `memoreei service status`, `memoreei service
logs`. Settings go in `config.env`, except the few needed before memoreei can find it
(`MEMOREEI_HOME`), which go in `~/.config/memoreei/env`; the service and the command
both read that.

**Two people, one computer.** Each runs their own Memoreei, and the first to start gets
port 3679. The second needs another: `MEMOREEI_PORT=3680` in their `config.env`. Clients
of theirs use that port too.

**Coming from pip?** Versions before 0.4 kept data in `~/.memoreei` on Linux, and nothing
moves it: the dashboard says where it is, and `MEMOREEI_HOME=~/.memoreei` in
`~/.config/memoreei/env` keeps using it there. `memoreei service install` from the
package sets aside the unit the pip install wrote.

**Uninstall** with `sudo apt remove memoreei`, `sudo dnf remove memoreei`, or
`install.sh --uninstall` from the tarball's folder. Your data stays where it is.

---

## On a Mac: Memoreei.app

A menu-bar app with the whole server inside it, Python included. It runs the network
server, asks for nothing to be installed first, and sets itself up through a dashboard
in your browser.

1. **Download** the DMG for your Mac from the
   [latest release](https://github.com/CalebChristiansen/Memoreei/releases/latest):
   `Memoreei-arm64.dmg` for Apple silicon (Apple menu → About This Mac says *Chip
   Apple M…*), `Memoreei-x86_64.dmg` for Intel. macOS 12 or newer.
2. **Drag Memoreei into Applications.**
3. **Open it the first time from Finder**, in the Applications folder. The app isn't
   signed by Apple yet, so macOS won't open it with a double-click, and opening it from
   Launchpad or Spotlight only says it can't be opened, with no way past. On macOS
   12–14, **right-click Memoreei → Open**, then **Open** in the dialog. On macOS 15 and
   later, double-click it once, then go to System Settings → Privacy & Security, scroll
   down, and click **Open Anyway** next to the message about Memoreei. You do this once
   per download.
4. **Full Disk Access.** A window walks you through it: macOS gives apps no way to ask,
   so you drag Memoreei's icon into the Full Disk Access list yourself. The window
   notices when it's done. If macOS offers to *Quit & Reopen* Memoreei, go ahead.
5. **The firewall** may ask whether *Memoreei Server* may accept incoming connections.
   Click **Allow**, or other machines can't reach it.
6. **The dashboard** opens in your browser. Under **Sources**, set up iMessage; under
   **Clients**, create a key for each machine or app that will search your memories. The
   key is shown once, with ready-to-paste setup for Claude Code and `.mcp.json`.

Memoreei lives in the menu bar from then on and starts at login (switch that off in
its menu). **Open Memoreei…** signs you in to the dashboard. **Quit** stops the server
too, so clients lose access until you open it again.

**Updates.** The menu shows *Update Available* when there's a new release. Download the
new DMG and replace the app. Until Memoreei is signed, macOS treats each new version as
a stranger and switches its Full Disk Access off; the setup window reopens by itself,
and Memoreei is still in the list, so you just switch it back on.

**Where things are.** Data in `~/Library/Application Support/Memoreei`, logs in
`~/Library/Logs/Memoreei` (**Show Log** in the menu). A `memoreei` installed with pip
on the same Mac uses the same data, so `memoreei key list` in Terminal shows the app's
keys. Don't run both servers at once: they'd want the same port, and the app will say
so rather than fight over it.

---

## Run it as a network server

Run memoreei on one always-on machine (a home server, a desktop, a Mac that never
sleeps) and query your memories from every other one. It serves MCP's Streamable HTTP
transport at `/mcp`, and every request needs an API key.

This section installs it with pip, which works anywhere Python does. On Linux the
[package](#on-linux-a-package) does steps 1 and 4 for you, and on a Mac the
[app](#on-a-mac-memoreeiapp) does; the rest is the same.

### 1. Install

```bash
python3 -m venv ~/memoreei-venv
source ~/memoreei-venv/bin/activate
pip install memoreei
```

Python 3.10 or newer. On Debian and Ubuntu, `sudo apt install python3-venv` first, or
the first line fails. On macOS, `python3 --version` first; the system Python may be older.
To try a release candidate, `pip install --pre memoreei`.

### 2. Configure

```bash
memoreei setup
```

The wizard asks which embedding provider to use, whether to sync in the background,
and which connectors to configure. It writes `config.env` in the home directory (readable only by
you), then offers to create the first API key. Say yes, and name it after the machine
that will use it.

Pull in what you have:

```bash
memoreei sync                          # every connector you configured
memoreei import whatsapp chat.txt      # and any exports you have lying around
```

### 3. Create a key per client

```bash
memoreei key create laptop
```

The key is printed once and never again; memoreei keeps only a hash of it. Alongside it
you get ready-to-paste client config with this machine's address filled in: a
`claude mcp add` command and a `.mcp.json` snippet. If the machine has several
addresses (Wi-Fi, Ethernet, a VPN), you get one URL per address; use whichever the
client can reach.

One key per client means that losing a laptop costs you one `memoreei key revoke
laptop`, not a round of changing every other client. `memoreei key list` shows when
each key was last used.

### 4. Start it, and keep it running

```bash
memoreei service install
```

This registers a background service (launchd on macOS, a systemd user unit on Linux)
that starts at login, restarts if it crashes, and runs `memoreei serve --http` on port
3679. To look after it:

```bash
memoreei service status     # is it running?
memoreei service logs       # tail the log; each request is logged with its key's name
memoreei service uninstall  # remove it
```

To run it in the foreground instead: `memoreei serve --http`. Until at least one key
exists it refuses every client; there is no way to run the network server open.

Port 3679 spells DORY on a phone keypad. It is officially registered to the Apple
Newton's dock sync, a device discontinued in 1998, which is not expected to object.
Change it with `--port` or `MEMOREEI_PORT`.

On a Linux server with no desktop login, run `loginctl enable-linger $USER` once so
the user service starts at boot rather than at your first SSH login.

**macOS firewall:** if it's on (System Settings → Network → Firewall), other machines
can't connect until you allow the Python that runs memoreei to accept incoming
connections. macOS asks with a dialog on the Mac's screen the first time the server
starts; click **Allow**. Missed it? Firewall → Options → **+**, and add the interpreter
`memoreei service grant-access` points you at (the same file, for the same reason). The
symptom is a client that times out while `curl http://127.0.0.1:3679/mcp` on the Mac
itself answers `401`.

**macOS and iMessage:** reading `~/Library/Messages/chat.db` needs **Full Disk Access**,
and macOS gives apps no way to ask for it. `memoreei service install` offers to walk
you through it, or at any time:

```bash
memoreei service grant-access
```

A dialog explains each step, then opens System Settings at Full Disk Access and a
Finder window with the exact file to add already selected: the real Python interpreter
behind your virtualenv, usually buried somewhere nobody finds by hand. Drag it into
the list, switch it on, click **Done**, and memoreei restarts the service and tells you
whether it can now read your messages. It has to be done in person, at the Mac. The
service log also says `Full Disk Access: ok` or `missing` each time it starts.

### The dashboard

The server has a web dashboard at `/admin` for what setup and `key` do on the command
line: status, sources, and client keys. It answers only on the machine the server runs
on, and only after you sign in with a one-time link:

```bash
memoreei admin-url     # prints http://localhost:3679/admin/login?token=…
```

Open the link in a browser on that machine. It works once, within five minutes, and
signs that browser in for a month. The dashboard never accepts API keys, and API keys
never open it. On a machine with no display, `admin-url` also prints the `ssh -L` that
brings the dashboard to the computer you're sitting at.

### 5. Connect a client

**Claude Code** — paste the command `key create` printed:

```bash
claude mcp add --transport http memoreei http://<server-ip>:3679/mcp \
  --header "Authorization: Bearer <key>"
```

**A project `.mcp.json`** — keep the key out of the file and in an environment variable,
which Claude Code expands:

```json
{
  "mcpServers": {
    "memoreei": {
      "type": "http",
      "url": "http://<server-ip>:3679/mcp",
      "headers": { "Authorization": "Bearer ${MEMOREEI_KEY}" }
    }
  }
}
```

**claude.ai** (custom connectors) — these call your server from Anthropic's servers,
not from your browser, so they can't reach an address on your home network or VPN.
They need a public HTTPS URL (see below). Add the connector with that URL, and put the
key in an `Authorization: Bearer <key>` request header in the connector's settings.

Any other MCP client that supports Streamable HTTP and custom headers works the same
way: URL, plus that header.

### HTTPS

memoreei speaks plain HTTP by default, like Jellyfin, the *arr apps and Home Assistant.
That's fine on a home network or a VPN, where the network is the thing keeping
strangers out and the key is the thing keeping everyone else out. For anything
public, put HTTPS in front.

**A reverse proxy (recommended for a public URL).** [Caddy](https://caddyserver.com)
gets and renews a certificate by itself:

```
memories.example.com {
    reverse_proxy <server-ip>:3679
}
```

Then tell memoreei the URL clients should use, so `key create` prints it:
`MEMOREEI_PUBLIC_URL=https://memories.example.com` in `config.env`.

**Or let memoreei do it**, if you already have a certificate:

```bash
memoreei serve --http --tls-cert /path/to/cert.pem --tls-key /path/to/key.pem
```

or set `MEMOREEI_TLS_CERT` and `MEMOREEI_TLS_KEY` in `config.env`, which the service
picks up.

### What the network can do

Over the network memoreei is **read-only, plus one refresh button**. It offers
`search_memory`, `get_context`, `list_sources` and `sync`, and nothing else. It can't
be told to import a file, fetch from a new account or store a note; several local tools
take a path on the server, and a key holder shouldn't be able to point one at your SSH
keys and search them back out. Filling the database is a local job: `memoreei setup`,
`memoreei import …`, or a stdio client on the server itself.

`sync` takes no arguments. It runs the connectors configured on the server and
re-reads any import files registered there (see `memoreei import list`) that changed
since the last read.

### Updating

```bash
source ~/memoreei-venv/bin/activate
pip install --upgrade memoreei
memoreei service install   # rewrites the service for the new version and restarts it
```

**Upgrading on Linux from before 0.4:** the home directory moved from `~/.memoreei` to
`~/.local/share/memoreei`, and nothing moves your data. Before `service install`, either
`mv ~/.memoreei ~/.local/share/memoreei`, or `export MEMOREEI_HOME=~/.memoreei` (and keep
it in your shell's profile), or the service starts over on an empty home.

---

## MCP Tools

A local (stdio) client gets every tool below. A network client gets only the four
marked **network**: see [What the network can do](#what-the-network-can-do).

| Tool | Local | Network |
|------|:-----:|:-------:|
| `search_memory`, `get_context`, `list_sources` | ✅ | ✅ |
| `sync` | ✅ | ✅ |
| `add_memory` | ✅ | — |
| `ingest_whatsapp`, `import_*` | ✅ | — |
| `sync_<source>`, `sync_all`, `refresh_memory`, `sync_contacts` | ✅ | — |

### Search & Retrieval

#### `search_memory` · network
Hybrid keyword + semantic search across all ingested memories.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | **required** | Natural language search query |
| `limit` | int | 10 | Max results to return |
| `source` | string | — | Filter by source, e.g. `whatsapp:friends`, `discord:1234567890` |
| `participant` | string | — | Filter by sender name (case-insensitive) |
| `after` | string | — | ISO date lower bound, e.g. `2026-01-01` |
| `before` | string | — | ISO date upper bound |

#### `get_context` · network
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

#### `list_sources` · network
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
> 🧪 Beta — macOS only. Requires Full Disk Access for the program running memoreei: [Memoreei.app](#on-a-mac-memoreeiapp) walks you through it, and `memoreei service grant-access` does for a pip install.

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

#### `sync` · network
Refresh from everything configured on the server: every connector in `config.env`, plus
registered import files that changed since they were last read. Takes no arguments.
Returns counts per connector and per import file.

#### `refresh_memory`
Trigger an immediate sync of all configured sources. Returns count of new messages.

#### `sync_all`
Sync every configured connector and return counts per source.

---

## CLI Reference

```bash
# Every command takes --home to use a different home directory
memoreei --home /srv/memoreei status

# Interactive setup — writes config.env in the home directory, offers the first API key
memoreei setup             # pick from a list (spacebar to select, enter to confirm)
memoreei setup gmail       # configure a specific connector directly

# Start the MCP server for a local client (stdio)
memoreei serve

# Start the network server (Streamable HTTP at /mcp, port 3679, key required)
memoreei serve --http
memoreei serve --http --host 0.0.0.0 --port 3679
memoreei serve --http --tls-cert cert.pem --tls-key key.pem

# API keys for network clients: one per client, shown once
memoreei key create laptop
memoreei key list
memoreei key revoke laptop

# A one-time link that signs a browser in to the dashboard (/admin)
memoreei admin-url

# Run the network server in the background (launchd / systemd)
memoreei service install [--port 3679]
memoreei service status | logs | uninstall

# Show DB stats: message counts, sources
memoreei status

# Sync everything: configured connectors, plus changed registered import files
memoreei sync

# Sync one connector
memoreei sync discord        # telegram, matrix, slack, email, mastodon, imessage

# Search from the terminal
memoreei search "API redesign notes"
memoreei search "printer issue" --limit 5 --source whatsapp:friends

# Import files (each one is remembered, and re-read by `memoreei sync` when it changes)
memoreei import whatsapp "WhatsApp Chat.txt"
memoreei import sms sms-backup.xml
memoreei import discord-package discord-package.zip
memoreei import messenger ~/Downloads/facebook-export/messages
memoreei import instagram ~/Downloads/instagram-export
memoreei import json chat.json --content-field text --sender-field from --timestamp-field ts
memoreei import csv chat.csv --content-column body --sender-column who
memoreei import contacts contacts.vcf

# The remembered import files
memoreei import list
memoreei import forget 3     # stop re-reading it; what was imported stays

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
 │                                                          │ stdio / HTTP
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

`memoreei setup` (or the dashboard) writes `config.env` in the home directory and is
the easy way. To edit it by hand,
[`.env.example`](.env.example) lists every setting. Settings are read from, in order of
precedence:

1. the environment
2. a `.env` in the current directory (handy when developing memoreei itself)
3. `config.env` in memoreei's home directory

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMOREEI_HOME` | `~/Library/Application Support/Memoreei` on macOS, `$XDG_DATA_HOME/memoreei` (`~/.local/share/memoreei`) on Linux, `~/.memoreei` elsewhere | Home directory, holding `config.env` and `memoreei.db`; `--home` overrides it |
| `MEMOREEI_DB_PATH` | `$MEMOREEI_HOME/memoreei.db` | SQLite database path |
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` (local ONNX, no API key) or `openai` |
| `OPENAI_API_KEY` | — | Required only if `EMBEDDING_PROVIDER=openai` |
| `AUTO_SYNC` | `false` | Run `sync` in the background while the server runs |
| `AUTO_SYNC_INTERVAL` | `300` | Background sync interval in seconds |

### Network server

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMOREEI_HOST` | `0.0.0.0` | Address `serve --http` binds to |
| `MEMOREEI_PORT` | `3679` | Port `serve --http` listens on |
| `MEMOREEI_PUBLIC_URL` | — | The URL clients use, e.g. behind a reverse proxy. Only used to print client config |
| `MEMOREEI_TLS_CERT` | — | TLS certificate (PEM), to serve HTTPS directly |
| `MEMOREEI_TLS_KEY` | — | TLS private key (PEM) |

API keys aren't settings: they live, hashed, in the database. See `memoreei key`.

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
- The network server is off unless you start it, never runs without an API key, and
  stores only hashes of its keys. Over the network it can search what is stored, not
  add to it or read files

**What requires network access:**

- Live sync connectors (Discord, Telegram, Slack, Matrix, Gmail, Mastodon) make outbound API calls to those services
- `EMBEDDING_PROVIDER=openai` sends message text to OpenAI's API for embedding

The home directory is created readable only by you, and `config.env` is written mode 600.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on adding new connectors, running tests, and submitting pull requests.

---

## License

MIT
