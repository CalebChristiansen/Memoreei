# Memoreei

**Remember every conversation you've ever had.**

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![CI](https://github.com/CalebChristiansen/Memoreei/actions/workflows/ci.yml/badge.svg)](https://github.com/CalebChristiansen/Memoreei/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/memoreei)](https://pypi.org/project/memoreei/)
[![codecov](https://codecov.io/gh/CalebChristiansen/Memoreei/graph/badge.svg)](https://codecov.io/gh/CalebChristiansen/Memoreei)

Memoreei is an open-source [MCP](https://modelcontextprotocol.io/) server that gives
your AI assistant a searchable memory of your messages: iMessage, WhatsApp, Signal,
Discord, Telegram, Slack, Gmail, Instagram and more. It keeps them in one encrypted SQLite
file on your machine and searches them by keyword and by meaning at once.

```
"What's my friend's favorite restaurant?"
"What did my sister say she wanted for her birthday?"
"How many times have I asked Dory to send that link again?"
```

Every assistant starts each conversation knowing nothing about you. With Memoreei, it
can look.

- **Local-first.** One SQLite file, encrypted. The default search model runs offline.
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

Everything Memoreei knows lives in one home directory: `config.env` for settings,
`memoreei.db` for the memories and the credentials, encrypted, and usually
`memoreei.key`, its key ([Privacy](#privacy)). That's `~/Library/Application
Support/Memoreei` on macOS, `~/.local/share/memoreei` on Linux and `~/.memoreei`
elsewhere. Point it somewhere else with `--home <dir>` or `MEMOREEI_HOME`.

### On a Mac: Memoreei.app

1. **Download** the DMG from the [latest release](https://github.com/CalebChristiansen/Memoreei/releases/latest):
   `Memoreei-arm64.dmg` for Apple silicon (About This Mac says *Chip Apple M…*),
   `Memoreei-x86_64.dmg` for Intel. macOS 11 or newer.
2. **Drag Memoreei into Applications.**
3. **Open it the first time from Finder.** The app isn't signed by Apple yet, so a
   double-click, Launchpad or Spotlight won't open it. On macOS 11–14, **right-click →
   Open**, then **Open**. On macOS 15 and later, double-click once, then System Settings
   → Privacy & Security → **Open Anyway**. Once per download.
4. **Full Disk Access**, which iMessage needs. macOS gives apps no way to ask, so a
   window walks you through dragging Memoreei into the list, and notices when it's done.
5. **The firewall** may ask whether *Memoreei Server* may accept incoming connections.
   **Allow**, or other machines can't reach it.
6. **The dashboard** opens in your browser. Set up iMessage under **Sources**, and
   WhatsApp or Signal too if WhatsApp for Mac or Signal Desktop is installed (Signal's
   *Connect* asks macOS once for its key: choose **Allow**). Then create a key under
   **Connections** for each machine or app that will search.

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
   login, and opens the dashboard. Create a key under **Connections** for each machine or
   app that will search.
4. **Add sources.** Signal, if Signal Desktop is set up, is under **Sources** in the
   dashboard: *Connect* reads its key from your desktop's keyring. The other live
   connectors are set up in a terminal for now, with `memoreei setup`, and chat exports
   with `memoreei import …`.

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

### From Claude.ai, ChatGPT and agents

Claude.ai and ChatGPT call from their own servers, not your browser, so they can't reach
your home network, and they sign in with OAuth rather than a pasted key. Memoreei has a
second, stricter front door for them, the **public listener**: it answers `/mcp`, the
sign-in, and the OAuth metadata that tells a chatbot where to sign in, and nothing else
(the dashboard only if you [open it from anywhere](#from-anywhere)). Agents that only send a header can use
your `mem_` keys there too. Failed attempts are rate-limited, and every call is listed on
the dashboard's Connections page: when, which chatbot or key, which tool, from where. It's off
until you choose one of the two ways below.

#### Approving a chatbot

Memoreei signs chatbots in itself, and **you approve each one on the computer memoreei
runs on**. Add memoreei's address as a connector in Claude or ChatGPT (or sign in from an
agent) and it opens a page with a short code, like `K7QM`, that waits for you. On the
computer, approve that code:

- on the dashboard's Connections page (every page shows a note while one waits), or
- with `memoreei chatbots approve K7QM`, or
- from the desktop notification, where there is a desktop, or
- away from the computer, if you've [opened the dashboard from anywhere](#from-anywhere):
  the chatbot's page offers **Sign in to approve it here**, and sends you back to the
  chatbot once you have.

The chatbot's page moves on by itself within a couple of seconds. Only approve a code you
just asked for: anyone can ask, and approving lets them read your messages. Compare the
code, and check the name and address beside it. A chatbot with a published client
document shows its own address (`claude.ai`, `chatgpt.com`); an app that registered
itself is marked "it named itself", because it could have called itself anything.
Requests that nobody answers lapse after ten minutes.

Agents without a browser (`hermes mcp login --flow device`) get the same kind of code to
approve the same way.

`memoreei chatbots list` shows who's waiting and who's connected; **Remove** on the Cloud
page, or `memoreei chatbots remove <id>`, cuts one off at once. Its next call fails, not
its next sign-in. Sign-in tokens are memoreei's own: access tokens last ten minutes,
refresh tokens are signed by memoreei, replaced on every use, and stop after a month
unused, and an old one turning up again (someone else has a copy) cancels that chatbot's
sign-in altogether. Nobody else, Memoreei Cloud included, can mint a token for your
memoreei.

#### Memoreei Cloud

The easy way to give memoreei an address. `memoreei cloud connect` (or **Connect to
Memoreei Cloud** in the dashboard) opens a sign-in page, you approve this computer there,
and it gets an address like `https://k3x9q2mfa7td.memoreei.net/mcp` to add as a connector
in Claude or ChatGPT. Nothing to open on your router: memoreei holds a connection out to
the relay, and chatbots reach it through that. Your messages stay on your computer. The
relay carries TLS it can't open: the certificate's key is made on your computer and never
leaves it. Your Memoreei Cloud account is for the address; chatbots sign in to memoreei
itself, as above. `memoreei cloud status` says whether it's online; `memoreei cloud
disconnect` takes the computer off your account. While the computer sleeps, chatbots
can't reach it, and memoreei tells them to say so.

Nothing contacts Memoreei Cloud until you connect. To hide the offer, set
`MEMOREEI_CLOUD_OFFER=0`. Once connected, memoreei suggests a dashboard password, so you
can [manage it from anywhere](#from-anywhere); `MEMOREEI_PASSWORD_OFFER=0` hides that too.

#### Your own tunnel

Memoreei Cloud uses nothing but standards, so you can bring your own address instead.
Turn on the public listener and point any tunnel or proxy at it: Cloudflare Tunnel,
Tailscale Funnel, ngrok, or Caddy on a machine with a public address.

```bash
MEMOREEI_PUBLIC_PORT=3680                                  # on 127.0.0.1 unless MEMOREEI_PUBLIC_HOST says otherwise
MEMOREEI_PUBLIC_MCP_URL=https://memories.example.com/mcp   # the https address clients use
MEMOREEI_TRUSTED_PROXY=127.0.0.1                           # optional: read the caller's address from X-Forwarded-For
```

With `MEMOREEI_PUBLIC_MCP_URL` set, memoreei signs chatbots in at that address
(`https://memories.example.com/authorize` and the rest, announced in its RFC 8414
metadata): dynamic and metadata-document client registration, PKCE, and device codes
for agents, as Claude, ChatGPT and agent harnesses expect, approved as above. Without
it, the public listener takes `mem_` keys only. To serve TLS from memoreei itself, add
`MEMOREEI_PUBLIC_TLS_CERT` and `MEMOREEI_PUBLIC_TLS_KEY`. The public listener has one
identity at a time, so it's this or Memoreei Cloud.

**Or forward a port.** With no tunnel at all, forward a port on your router to this
computer and let memoreei serve TLS itself, with a certificate for a hostname that points
at your home address:

```bash
MEMOREEI_PUBLIC_HOST=0.0.0.0                               # listen beyond this computer
MEMOREEI_PUBLIC_PORT=3680                                  # the port your router forwards here
MEMOREEI_PUBLIC_MCP_URL=https://memories.example.com:3680/mcp
MEMOREEI_PUBLIC_TLS_CERT=/path/to/fullchain.pem
MEMOREEI_PUBLIC_TLS_KEY=/path/to/privkey.pem
```

Only the public listener is reachable this way: `/mcp`, the sign-in and its metadata,
and the dashboard only if you've [opened it from anywhere](#from-anywhere). memoreei
reads the certificate when it starts, so restart it after renewing one.

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
Unless you give it a password and open it [from anywhere](#from-anywhere), it answers only
on the server itself, after a one-time sign-in link:

```bash
memoreei admin-url     # http://localhost:3679/admin/login?token=…
```

The link works once, within five minutes, and signs that browser in for a month. With no
display, `admin-url` also prints the `ssh -L` that brings the dashboard to your laptop.
Dashboard sessions and API keys are separate: neither opens the other.

#### From anywhere

The dashboard can have a password. It's optional: without one, nothing above changes.
Set one on the dashboard's **Security** page, or:

```bash
memoreei admin password       # set or change it (--stdin for scripts)
memoreei admin remote on      # let the dashboard answer away from this computer
memoreei admin status         # password, 2FA, from anywhere, recent sign-ins
memoreei admin reset          # forgot it? removes the password and 2FA, here
```

The rule: **with no password, the dashboard answers only on the computer**, as above.
With a password and **Manage memoreei from anywhere** switched on, it also answers
elsewhere, to the password: at your [Memoreei Cloud](#memoreei-cloud) address
(`https://<name>.memoreei.net/admin/`), through [your own tunnel](#your-own-tunnel) at
`MEMOREEI_PUBLIC_MCP_URL`'s address, and on your network at this computer's address or
name (`http://192.168.1.5:3679/admin/`, plain HTTP, so prefer the https addresses on a
network you don't trust). Anyone who reaches it sees only a sign-in page. Through a
tunnel, the dashboard checks that `Host` and `Origin` name that public address, so the
tunnel must pass `Host` on unchanged (Cloudflare Tunnel, Tailscale Funnel and Caddy do).

- **2FA**, optional: a code from an authenticator app as well as the password. Set it up
  on the Security page (scan the QR code), and keep the ten recovery codes it shows
  once, for a lost phone.
- **Signing in** with the password lasts a week. On the computer, the one-time link
  still signs you in without it. A new password, or switching on 2FA, signs out every
  browser that signed in with the old way.
- **Failed sign-ins** away from the computer are limited per address and in all, and
  listed on the Security page. Nobody can lock you out by guessing: a browser you've
  signed in with before isn't held up by strangers' failures, and on the computer
  there's no limit at all.
- **Away from the computer, the password and 2FA can't be changed**, and the switch
  can only be turned off. So a stolen session can't lock you out, and a forgotten
  password is reset where memoreei runs.

### HTTPS

Plain HTTP is fine on a home network or a VPN. For anything public, put HTTPS in front,
and prefer the public listener (above), which answers only `/mcp`.
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
| Signal (Signal Desktop, macOS and Linux) | live, from Signal Desktop's database | 🧪 Beta |
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
| `search_memoreei` | hybrid search, narrowed by `participant`, `participant_scope`, `source`, `after`, `before` | ✅ |
| `get_context` | the messages before and after a search result, in its chat | ✅ |
| `list_sources` | the most-messaged people (with their chats) and group chats; `name` finds anyone | ✅ |
| `sync` | refresh everything configured on the server; no arguments | ✅ |
| `add_memoreei` | store a note | — |
| `sync_discord`, `_telegram`, `_matrix`, `_slack`, `_email`, `_mastodon`, `_imessage`, `_whatsapp`, `_signal` | sync one connector | — |
| `sync_all`, `refresh_memoreei` | sync every configured connector, without import files | — |
| `import_discord_package`, `import_messenger`, `import_instagram`, `import_sms_backup`, `import_json_file`, `import_csv_file` | import an export file | — |
| `import_contacts_vcf`, `sync_contacts` | names for phone numbers, from a vCard or macOS Contacts | — |

A local (stdio) client gets all of them. Each tool's parameters are in its MCP
description, which your client shows it, and the server sends a short set of
instructions on how to use them.

**Finding people.** `participant` takes a name, part of one (`"Jane"`, `"jan do"`) or a
phone number in any format, and finds that person on every app: iMessage stores a
number, WhatsApp and Signal a name, and the two are joined through your contacts and
the number each WhatsApp or Signal message came from. People who only share a name
are never merged; a name that fits several people searches all of them and says so in
the result's `note`. `participant_scope` decides how much: `conversation` (the default)
is what they wrote anywhere plus both sides of your one-to-one chats, `sent` only what
they wrote, `any_chat` every chat they're in. `me` is you, as is your own name.

Every filter narrows the search before anything is ranked, so a filtered search
returns the best matches among those messages. A search returns `results`, plus
`people` when you named one and a `note` whenever there's something to explain, such
as a name that fits several people or why nothing came back.

---

## CLI

```bash
memoreei setup [connector]          # configure connectors; offers the first API key
memoreei serve                      # stdio, for a local client
memoreei serve --http [--port 3679] [--tls-cert … --tls-key …]
memoreei open                       # the dashboard, starting Memoreei if needed
memoreei admin-url                  # a one-time dashboard sign-in link
memoreei admin password | remote on|off | status | reset   # the dashboard from anywhere
memoreei key create | list | revoke <name>
memoreei service install | status | logs | uninstall | grant-access
memoreei status                     # message counts, sources, last sync times
memoreei config                     # settings, tokens masked
memoreei sync [source]              # everything, or one of discord, telegram, matrix,
                                    #   slack, email, mastodon, imessage, whatsapp,
                                    #   signal
memoreei search "printer issue" --limit 5 --person "Jane" --after 2026-01-01
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

`memoreei setup` and the dashboard write settings to `config.env` and tokens and
passwords into the encrypted database; [`.env.example`](.env.example) lists every setting
for editing by hand. A secret typed into `config.env` by hand works too: at the next start
it moves into the database and out of the file. The environment wins over a `.env` in
the current directory, which wins over `config.env` and the database.

| Variable | Default | |
|---|---|---|
| `MEMOREEI_HOME` | per platform, [above](#install) | holds `config.env`, `memoreei.db` and `memoreei.key` |
| `MEMOREEI_DB_PATH` | `$MEMOREEI_HOME/memoreei.db` | |
| `MEMOREEI_DB_KEY` | — | the database's key, 64 hex characters, in place of the [usual places](#privacy): for containers |
| `EMBEDDING_PROVIDER` | `fastembed` | or `openai`, with `OPENAI_API_KEY` |
| `AUTO_SYNC` | `false` | sync in the background while the server runs |
| `AUTO_SYNC_INTERVAL` | `300` | seconds |
| `MEMOREEI_HOST` | `0.0.0.0` | network server address |
| `MEMOREEI_PORT` | `3679` | network server port |
| `MEMOREEI_PUBLIC_URL` | — | the URL `key create` prints, e.g. behind a proxy |
| `MEMOREEI_TLS_CERT`, `MEMOREEI_TLS_KEY` | — | serve HTTPS directly |
| `MEMOREEI_ME` | your account's full name | names, numbers or addresses that mean you, comma-separated |

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

The tokens, passwords and Signal's key in that table (and `OPENAI_API_KEY`) are kept in
the database, not in `config.env`. API keys aren't settings at all: they live in the
database, hashed.

---

## Privacy

- Everything is stored in one database on your machine, **always encrypted**
  (SQLCipher, AES-256): a copy of `memoreei.db` (a backup, a synced folder, a lost
  laptop) is unreadable without its key. The sources' tokens and passwords are inside
  it too. The home directory is readable only by you, and `config.env` is mode 600.
- The key is 256 random bits, made on first start and kept in GNOME Keyring or KWallet
  on a Linux desktop, and otherwise in `memoreei.key` beside the database (mode 600):
  on a Mac (until Memoreei.app has a Developer ID, the Keychain would ask for your
  password after every update), on a headless Linux box, and elsewhere.
  `memoreei config` says which; in the keyring it is "Memoreei database key". **Back it
  up with the database**: without the key, a backup of `memoreei.db` is noise.
- A database from an earlier memoreei is encrypted on its first start (about 7 seconds
  for 150,000 messages). The old file's blocks are freed, not overwritten.
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
