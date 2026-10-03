# Memoreei

**Remember every conversation you've ever had.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![CI](https://github.com/CalebChristiansen/Memoreei/actions/workflows/ci.yml/badge.svg)](https://github.com/CalebChristiansen/Memoreei/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/memoreei)](https://pypi.org/project/memoreei/)
[![codecov](https://codecov.io/gh/CalebChristiansen/Memoreei/graph/badge.svg)](https://codecov.io/gh/CalebChristiansen/Memoreei)

Memoreei is an open-source [MCP](https://modelcontextprotocol.io/) server that gives
your AI assistant a searchable memory of your messages: iMessage, WhatsApp, Discord,
Telegram, Slack, Gmail, Instagram and more. It keeps them in one SQLite file on your
machine and searches them by keyword and by meaning at once.

```
"What's my friend's favorite restaurant?"
"What did my sister say she wanted for her birthday?"
"How many times have I asked Dory to send that link again?"
```

Every assistant starts each conversation knowing nothing about you. With Memoreei, it
can look.

- **Local-first.** One SQLite file. The default search model runs offline.
- **Hybrid search.** BM25 keywords and vector similarity, fused with Reciprocal Rank Fusion.
- **On your network.** One always-on machine serves every other one, each client with its own key.
- **Read-only to the network.** A key can search what's stored, never add to it or read a file.

---

## Install

Pick one. Each is the whole server; they differ in how it's packaged.

| You have | Get | Includes |
|---|---|---|
| a Mac | [Memoreei.app](#on-a-mac-memoreeiapp) | Python, the search model, a menu-bar app, iMessage |
| Linux | [a .deb, .rpm or tarball](#on-linux-a-package) | Python, the search model, a user service |
| anything else | `pip install memoreei` ([below](#anywhere-else-pip)) | Python 3.10 or newer, which you bring |

Everything Memoreei knows lives in one home directory: `config.env` for settings and
credentials, `memoreei.db` for the memories. That's `~/Library/Application
Support/Memoreei` on macOS, `~/.local/share/memoreei` on Linux and `~/.memoreei`
elsewhere. Point it somewhere else with `--home <dir>` or `MEMOREEI_HOME`.

### On a Mac: Memoreei.app

1. **Download** the DMG from the [latest release](https://github.com/CalebChristiansen/Memoreei/releases/latest):
   `Memoreei-arm64.dmg` for Apple silicon (About This Mac says *Chip Apple M…*),
   `Memoreei-x86_64.dmg` for Intel. macOS 12 or newer.
2. **Drag Memoreei into Applications.**
3. **Open it the first time from Finder.** The app isn't signed by Apple yet, so a
   double-click, Launchpad or Spotlight won't open it. On macOS 12–14, **right-click →
   Open**, then **Open**. On macOS 15 and later, double-click once, then System Settings
   → Privacy & Security → **Open Anyway**. Once per download.
4. **Full Disk Access**, which iMessage needs. macOS gives apps no way to ask, so a
   window walks you through dragging Memoreei into the list, and notices when it's done.
5. **The firewall** may ask whether *Memoreei Server* may accept incoming connections.
   **Allow**, or other machines can't reach it.
6. **The dashboard** opens in your browser. Set up iMessage (and WhatsApp, if WhatsApp
   for Mac is installed) under **Sources**, and
   create a key under **Clients** for each machine or app that will search.

Memoreei then lives in the menu bar and starts at login. **Open Dashboard** signs you in
to the dashboard; **Quit** stops the server too.

**Updates.** The menu says *Update to X…*. Download the new DMG and replace the
app. Until Memoreei is signed, macOS treats each new version as a stranger and switches
its Full Disk Access off; the setup window reopens, and you switch it back on.

Data is in `~/Library/Application Support/Memoreei`, logs in `~/Library/Logs/Memoreei`
(**Show Log** in the menu). A pip-installed `memoreei` on the same Mac shares that data,
so `memoreei key list` in Terminal shows the app's keys. Don't run both servers at once;
the app will tell you if you try.

### On Linux: a package

Ubuntu 20.04, Debian 11, RHEL and Rocky 8, current Fedora, or newer; x86_64 or ARM64.

1. **Download** from [Releases](https://github.com/CalebChristiansen/Memoreei/releases):
   - Ubuntu, Debian: `memoreei_X.Y.Z_amd64.deb` (`arm64` on ARM, such as a Raspberry Pi)
   - Fedora, RHEL, Rocky: `memoreei-X.Y.Z.x86_64.rpm` (`aarch64` on ARM)
   - anything else, or without root: `memoreei-X.Y.Z-linux-x86_64.tar.gz` (`aarch64` on ARM)
2. **Install it**, by double-clicking it or:
   ```bash
   sudo apt install ./memoreei_*.deb        # Ubuntu, Debian
   sudo dnf install ./memoreei-*.rpm        # Fedora, RHEL, Rocky
   tar xzf memoreei-*-linux-*.tar.gz && memoreei-*-linux-*/install.sh   # into ~/.local, no root
   ```
3. **Open Memoreei** from your applications. It starts the server, sets it to start at
   login, and opens the dashboard. Create a key under **Clients** for each machine or
   app that will search.
4. **Add sources** in a terminal, for now: `memoreei setup` for the live connectors,
   `memoreei import …` for chat exports.

**No desktop?** The same package, from a terminal:

```bash
memoreei service install     # start now and at every login; offers to start at boot too
memoreei key create laptop   # one key per client
memoreei admin-url           # how to reach the dashboard from your own computer
```

The server is the systemd user unit `memoreei.service`. Data is in
`~/.local/share/memoreei`, the program in `/opt/memoreei` (or `~/.local/opt/memoreei`
from the tarball). Settings go in `config.env`, except `MEMOREEI_HOME` itself, which
goes in `~/.config/memoreei/env`.

**Updates.** The dashboard says when there's a new release. Install it the same way; a
running Memoreei restarts on the new version by itself.

**Two people, one computer?** The first to start gets port 3679; the second sets
`MEMOREEI_PORT=3680` in their `config.env`.

**Coming from pip?** Before 0.4, Linux data lived in `~/.memoreei`, and nothing moves
it. Either `mv ~/.memoreei ~/.local/share/memoreei`, or put `MEMOREEI_HOME=~/.memoreei`
in `~/.config/memoreei/env`.

**Uninstall** with `sudo apt remove memoreei`, `sudo dnf remove memoreei`, or
`install.sh --uninstall`. Your data stays.

### Anywhere else: pip

```bash
python3 -m venv ~/memoreei-venv && source ~/memoreei-venv/bin/activate
pip install memoreei          # --pre for a release candidate
memoreei setup                # pick connectors, enter credentials, make the first key
memoreei sync                 # pull in messages
```

On Debian and Ubuntu, `sudo apt install python3-venv` first. To keep the network server
running in the background (launchd on macOS, a systemd user unit on Linux):

```bash
memoreei service install      # starts at login, restarts if it crashes
memoreei service status | logs | uninstall
```

On a headless Linux box, `loginctl enable-linger $USER` once, so it starts at boot rather
than at your first login. To update: `pip install --upgrade memoreei`, then
`memoreei service install` again.

**macOS via pip.** iMessage needs Full Disk Access for the Python behind your
virtualenv, which is buried somewhere nobody finds by hand. `memoreei service
grant-access` opens System Settings and a Finder window with the right file already
selected; drag it in, switch it on, and it checks the result. The firewall asks about
the same Python the first time the server starts: **Allow**. The app does all of this
for you, which is the argument for the app.

---

## Connect a client

### On the same machine

The client starts Memoreei itself and talks over stdio: no network, no key, and every
tool, including the imports. For Claude Code, `claude mcp add memoreei -- memoreei serve`.
Elsewhere (`.mcp.json`, `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "memoreei": { "command": "memoreei", "args": ["serve"] }
  }
}
```

Use the full path (`which memoreei`) if it's in a virtualenv your client doesn't know.

### Over the network

Create a key per client, in the dashboard or with `memoreei key create laptop`. The key
is shown once, with ready-to-paste setup and this machine's address filled in:

```bash
claude mcp add --transport http memoreei http://<server-ip>:3679/mcp \
  --header "Authorization: Bearer <key>"
```

or, in a project's `.mcp.json`, with the key kept in an environment variable:

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

Any MCP client with Streamable HTTP and custom headers works the same way. A lost laptop
costs one `memoreei key revoke laptop`; `memoreei key list` shows when each key was last
used.

**claude.ai connectors** call from Anthropic's servers, not your browser, so they can't
reach your home network. They need a public HTTPS URL (below), with the key in an
`Authorization: Bearer <key>` header.

---

## Run it as a network server

The network server speaks MCP's Streamable HTTP at `/mcp` on port 3679, and refuses
every request without a key. There is no way to run it open. The app, the Linux package
and `memoreei service install` all run it for you; by hand, it's `memoreei serve --http`.

Port 3679 spells DORY on a phone keypad. It is officially registered to the Apple
Newton's dock sync, a device discontinued in 1998, which is not expected to object.
Change it with `--port` or `MEMOREEI_PORT`.

### What the network can do

Search, and press one refresh button. A network client gets `search_memoreei`,
`get_context`, `list_sources` and `sync`, and nothing else. Several local tools take a
path on the server, and a key holder shouldn't be able to point one at your SSH keys and
search them back out. Filling the database is a local job.

`sync` takes no arguments: it runs the connectors configured on the server and re-reads
registered import files that have changed.

### The dashboard

`/admin` shows status, sources, client keys and the log, and switches start-at-login.
It answers only on the server itself, after a one-time sign-in link:

```bash
memoreei admin-url     # http://localhost:3679/admin/login?token=…
```

The link works once, within five minutes, and signs that browser in for a month. With no
display, `admin-url` also prints the `ssh -L` that brings the dashboard to your laptop.
Dashboard sessions and API keys are separate: neither opens the other.

### HTTPS

Plain HTTP is fine on a home network or a VPN. For anything public, put HTTPS in front.
[Caddy](https://caddyserver.com) gets its own certificate:

```
memories.example.com {
    reverse_proxy <server-ip>:3679
}
```

and `MEMOREEI_PUBLIC_URL=https://memories.example.com` in `config.env` makes
`key create` print that URL. Or, with a certificate already in hand,
`memoreei serve --http --tls-cert cert.pem --tls-key key.pem` (or `MEMOREEI_TLS_CERT` and
`MEMOREEI_TLS_KEY` in `config.env`).

---

## Sources

| Source | How | Status |
|---|---|---|
| iMessage (macOS) | live, from `chat.db` | 🧪 Beta |
| WhatsApp (WhatsApp for Mac, or an iPhone backup) | live, from `ChatStorage.sqlite` | 🧪 Beta |
| Gmail (IMAP) | live | ✅ |
| Discord (bot) | live | ✅ |
| Telegram (bot) | live | ✅ |
| Slack (bot) | live | ✅ |
| Matrix | live | ✅ |
| Mastodon | live | ✅ |
| Discord Data Package | import | ✅ |
| Facebook Messenger (data download) | import | ✅ |
| Instagram DMs (data download) | import | ✅ |
| Android SMS Backup & Restore (XML) | import | ✅ |
| Any JSON, JSON-lines, CSV or TSV | import | ✅ |
| Contacts (vCard, or macOS Contacts) | names for phone numbers | ✅ |
| Signal (Signal Desktop, macOS and Linux) | live | ✅ |

**WhatsApp** is read from where WhatsApp for Mac keeps the chats it has synced from your
phone: texts, photo and video captions, shared links and document names. Stickers, voice
notes and reactions have no words to search, so they're skipped. Its database has the
same layout as the one in an iPhone backup, so `WHATSAPP_DB_PATH` can point at that
instead, on any OS. WhatsApp on Windows and Linux is WhatsApp Web, whose local copy is
encrypted with a key held by WhatsApp's servers, so there is nothing there to read.

**Signal** is read from Signal Desktop's database on the same computer. Its key is sealed
by the system keyring (the Keychain on a Mac, GNOME Keyring or KWallet on Linux), so
connecting reads it once, when you choose *Connect* under Sources, and saves it; a Mac
asks first. Texts, captions, links, group changes and calls are kept. Reactions and edits
follow the message they belong to, with both wordings of an edit searchable, and a
message deleted for everyone is marked rather than forgotten. Photos and files become
labels, never copies. Disappearing messages are never stored.

**Live** sources sync incrementally, fetching only what's new. **Imports** are
remembered: `memoreei sync` re-reads a file when it changes.

Set up live sources with `memoreei setup` (or `memoreei setup gmail` for one), and import
with `memoreei import …`. `memoreei import --help` lists the formats.

---

## MCP tools

| Tool | Does | Network |
|---|---|:---:|
| `search_memoreei` | hybrid search, filtered by `source`, `participant`, `after`, `before` | ✅ |
| `get_context` | the messages around a search result | ✅ |
| `list_sources` | every source and its message count | ✅ |
| `sync` | refresh everything configured on the server; no arguments | ✅ |
| `add_memoreei` | store a note | — |
| `sync_discord`, `_telegram`, `_matrix`, `_slack`, `_email`, `_mastodon`, `_imessage`, `_whatsapp`, `_signal` | sync one connector | — |
| `sync_all`, `refresh_memoreei` | sync every configured connector, without import files | — |
| `import_discord_package`, `import_messenger`, `import_instagram`, `import_sms_backup`, `import_json_file`, `import_csv_file` | import an export file | — |
| `import_contacts_vcf`, `sync_contacts` | names for phone numbers, from a vCard or macOS Contacts | — |

A local (stdio) client gets all of them. Each tool's parameters are in its MCP
description, which your client shows it.

---

## CLI

```bash
memoreei setup [connector]          # configure connectors; offers the first API key
memoreei serve                      # stdio, for a local client
memoreei serve --http [--port 3679] [--tls-cert … --tls-key …]
memoreei open                       # the dashboard, starting Memoreei if needed
memoreei admin-url                  # a one-time dashboard sign-in link
memoreei key create | list | revoke <name>
memoreei service install | status | logs | uninstall | grant-access
memoreei status                     # message counts, sources, last sync times
memoreei config                     # settings, tokens masked
memoreei sync [source]              # everything, or one of discord, telegram, matrix,
                                    #   slack, email, mastodon, imessage, whatsapp
memoreei search "printer issue" --limit 5 --source imessage:+12025550142
memoreei import sms backup.xml      # also discord-package, messenger, instagram,
                                    #   json, csv, contacts
memoreei import list | forget <id>  # the files `sync` re-reads
```

Every command takes `--home <dir>`, and `--help`.

---

## How search works

Every query runs two searches at once, and fuses them:

```
"that weird API rate limit issue"
   ├─▶ keyword (SQLite FTS5, BM25)   matches "API", "rate", "limit"
   └─▶ vector (cosine similarity)    matches "throttling", "429 errors", "backoff"
          │
          ▼
   Reciprocal Rank Fusion: score = Σ 1 / (60 + rank)
```

Results that both searches found rise to the top; results only one found still count.
RRF works on ranks, so the two scores never need to agree on a scale.

The default embedding model is `BAAI/bge-small-en-v1.5` via FastEmbed: 384 dimensions,
about 67 MB of ONNX, fully offline. `EMBEDDING_PROVIDER=openai` swaps in OpenAI's.

---

## Configuration

`memoreei setup` and the dashboard write `config.env`; [`.env.example`](.env.example)
lists every setting for editing by hand. The environment wins over a `.env` in the
current directory, which wins over `config.env`.

| Variable | Default | |
|---|---|---|
| `MEMOREEI_HOME` | per platform, [above](#install) | holds `config.env` and `memoreei.db` |
| `MEMOREEI_DB_PATH` | `$MEMOREEI_HOME/memoreei.db` | |
| `EMBEDDING_PROVIDER` | `fastembed` | or `openai`, with `OPENAI_API_KEY` |
| `AUTO_SYNC` | `false` | sync in the background while the server runs |
| `AUTO_SYNC_INTERVAL` | `300` | seconds |
| `MEMOREEI_HOST` | `0.0.0.0` | network server address |
| `MEMOREEI_PORT` | `3679` | network server port |
| `MEMOREEI_PUBLIC_URL` | — | the URL `key create` prints, e.g. behind a proxy |
| `MEMOREEI_TLS_CERT`, `MEMOREEI_TLS_KEY` | — | serve HTTPS directly |

Connector settings:

| Connector | Variables |
|---|---|
| Discord | `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (positive for a DM, negative for a group) |
| Matrix | `MATRIX_HOMESERVER`, `MATRIX_ACCESS_TOKEN`, `MATRIX_ROOM_ID` |
| Slack | `SLACK_BOT_TOKEN` (scopes `channels:history`, `users:read`), `SLACK_CHANNEL_ID` |
| Gmail | `GMAIL_EMAIL`, `GMAIL_APP_PASSWORD` ([an app password](https://myaccount.google.com/apppasswords)) |
| Mastodon | `MASTODON_INSTANCE` (default `https://mastodon.social`), `MASTODON_HASHTAG`, `MASTODON_ACCESS_TOKEN` |
| iMessage | `IMESSAGE_DB_PATH` (default `~/Library/Messages/chat.db`) |
| WhatsApp | `WHATSAPP_DB_PATH` (default WhatsApp for Mac's, `~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite`) |
| Signal | `SIGNAL_DB_KEY`, written by *Connect*; `SIGNAL_DIR` if Signal Desktop's data isn't in its usual place |

API keys aren't settings. They live in the database, hashed.

---

## Privacy

- Everything is stored in one SQLite file on your machine. The home directory is
  readable only by you, and `config.env` is mode 600.
- The default search model runs offline. No telemetry, no analytics, no cloud.
- Network traffic happens only when you ask for it: live connectors call their own
  services, and `EMBEDDING_PROVIDER=openai` sends message text to OpenAI.
- The network server is off until you start it, never runs without a key, and stores
  only hashes of its keys. Over the network it can search, not add or read files.

---

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) covers building from source, running the tests and
adding a connector.

## License

MIT
