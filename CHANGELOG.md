# Changelog

All notable changes to Memoreei will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Use memoreei from Claude.ai, ChatGPT and agents.** A second front door for the
  internet, the public listener: `/mcp`, its OAuth metadata and memoreei's own sign-in
  only, sign-in tokens or `mem_` keys, rate limits on failed attempts, and an access log
  of every call on the dashboard's new Cloud page. Off unless you turn it on; the LAN
  listener is unchanged.
- **memoreei signs chatbots in itself, and you approve each one on the computer.** A
  chatbot or agent asking to connect shows a short code and waits; you approve that code
  in the dashboard (every page shows a note while one waits), with `memoreei chatbots
  approve CODE`, or from a desktop notification. `memoreei chatbots list | deny |
  remove`, and **Remove** on the Cloud page, which cuts a chatbot off at once. Claude.ai
  and ChatGPT register by client metadata document, other apps by dynamic registration,
  and agents without a browser by device code (RFC 8628). Access tokens last ten minutes;
  refresh tokens are signed by memoreei, replaced on every use, and an old one coming
  back cancels that chatbot's sign-in. Nothing outside your computer can mint a token for
  your memoreei.
- **Memoreei Cloud**, optional: `memoreei cloud connect | status | disconnect`, or the
  Cloud page. Enrolls the computer by device code, gets a certificate for its hostname
  with a key made here, renews it, and holds a tunnel to the relay, which carries TLS it
  can't open. Nothing contacts Memoreei Cloud until you connect; the offer on the status
  page and at the end of `memoreei setup` hides with `MEMOREEI_CLOUD_OFFER=0`.
- **Or your own:** `MEMOREEI_PUBLIC_PORT` and `MEMOREEI_PUBLIC_MCP_URL` put the public
  listener, and memoreei's sign-in, behind any tunnel or proxy.
- Chatbots reaching memoreei through the public listener are told that silence probably
  means the computer is asleep or offline.

### Changed
- **The database is always encrypted** (SQLCipher, AES-256, with a random 256-bit key).
  The key is kept in GNOME Keyring or KWallet on a Linux desktop, and otherwise in
  `memoreei.key` beside the database, mode 600 (the Mac too, until Memoreei.app has a
  Developer ID); `MEMOREEI_DB_KEY` in the environment overrides both, for containers.
  An existing database is encrypted on its first start, about 7 seconds for 150,000
  messages, and opening one afterwards costs milliseconds. A wrong or missing key stops
  memoreei with a message saying where it looked, rather than "file is not a database".
  `memoreei config` shows where the key is. Back up the key with the database.
- **Sources' tokens and passwords live in the encrypted database**, not in
  `config.env`: Gmail's app password, the Discord, Telegram and Slack bot tokens, the
  Matrix and Mastodon access tokens, Signal's key and `OPENAI_API_KEY`. The dashboard
  and `memoreei setup` save them there. Ones already in `config.env` (or added there by
  hand) move in at the next start and are removed from the file; the environment still
  wins over both.

## [0.4.7] - 2026-10-12

People by name or number, filters before ranking, and tools a small local model can use,
tried on the Mac as rc1 and rc2. Nothing changed since 0.4.7rc2. On the 4 GB MacBook
Air the server itself went from 576 MB under rc1 to 461 MB under rc2: macOS gave back
part of the copy that Linux kept, so the saving there is smaller than rc2's figures,
which were measured on Linux.

## [0.4.7rc2] - 2026-10-12

### Fixed
- **The search index takes about 250 MB less memory.** Building it held every embedding
  twice, once as read from the database and once in the index, and the second copy was
  never given back to the system. With 155,710 messages the server held 571 MB for the
  index (514 MB in 0.4.6); now it holds 311 MB, and building it peaks at 344 MB, not
  606 MB. A 4 GB Mac felt the difference.

## [0.4.7rc1] - 2026-10-12

### Changed
- **Search finds people by name or number.** `participant` takes a name, part of one or
  a phone number in any format, and finds that person on every app, joined through your
  contacts and the number each WhatsApp and Signal message came from. Before, it had to
  match the stored sender exactly: a name missed every iMessage, a number everything
  else. A name that fits several people searches all of them and says so.
- **Filters narrow the search before it ranks.** They used to be applied to the top 30
  matches from everyone, so a filtered search on a busy topic could come back empty.
- **Keyword search no longer needs every word.** Messages with all of them still come
  first; after those, ones with only the rarer words fill in. "What did he say he
  ordered" now finds the message that names what he ordered.
- New `participant_scope`: `conversation` (default; what they wrote, plus both sides of
  your one-to-one chats), `sent`, or `any_chat`. `source` takes a platform (`signal`) as
  well as a source id. Dates are in the server's time zone, and `before` includes its
  whole day.
- **The network tools return objects.** `search_memoreei` returns `{results, people,
  note}`; `note` says why results are empty or broader than asked. Each result gives
  `date`, `platform`, `chat`, `group` and `sender` in plain words, names in place of
  numbers where your contacts know them; `participants`, `contact_name`, `score` and the
  raw metadata are gone. `get_context` returns `{messages}`, counts messages rather than
  minutes, and marks the one asked about. `list_sources` groups chats by person and
  lists the 30 most-messaged people and groups, with counts of the rest, instead of
  every source; `name` finds anyone else. The full list ran to ~27k tokens on a large
  archive, more than a small model can read.
- The server describes every parameter with an example, sends instructions, declares
  output schemas and marks its read tools read-only. Mistakes come back as errors that
  say what to pass instead.
- **Unknown parameter names are no longer ignored.** A model that passed `person` for
  `participant` got an unfiltered search that looked like an answer. Plain stand-ins
  (`person`, `id`, `since`, `window`, …) are now mapped to the real parameter, and
  anything else is refused with the list of parameters the tool takes.
- `memoreei search` takes `--person`, `--scope`, `--after` and `--before`.

### Fixed
- `get_context` returns the messages around the one asked about. It looked within a
  few minutes either side and took the earliest, so in a chat whose messages are
  seconds apart every anchor came back as the chat's first messages.
- Punctuation in a search no longer fails it: a path, a URL, a hyphenated word or a file
  name gave "fts5: syntax error" instead of results.

### Added
- Signal messages keep their sender's phone number, and messages stored earlier get it
  on the next sync, once.
- `MEMOREEI_ME`: who "me" is, when your account's full name isn't enough.

## [0.4.6] - 2026-10-10

The Mac app on macOS 11 Big Sur, tried on a Big Sur MacBook and on macOS 12. Nothing
changed since 0.4.6rc1.

## [0.4.6rc1] - 2026-10-07

### Changed
- The Mac app runs on macOS 11 Big Sur, not only 12 and later. Nothing it bundles
  needed 12: the same pinned dependencies resolve for 11 unchanged.

## [0.4.5] - 2026-10-07

### Fixed
- Search stops failing with "could not broadcast input array" once the server has
  added new messages to an index of more than one. Every search failed from then on,
  until the server was restarted.

## [0.4.4] - 2026-10-02

### Fixed
- The Sources page no longer shows the old "Signal Desktop: not yet" placeholder beside
  the Signal source that replaced it.

## [0.4.3] - 2026-10-02

### Added
- **Signal**, read from Signal Desktop's own database on macOS and Linux. Choose
  *Connect* under Sources (offered once Signal Desktop is set up) or `memoreei setup
  signal`: that reads Signal's database key from the Keychain, GNOME Keyring or KWallet,
  once, and saves it, so nothing asks again unless Signal is reinstalled or relinked. On
  a Mac, macOS asks whether *Memoreei Server* may have it. Texts, captions, links,
  stories, group changes, calls and timer changes are kept; photos and files become
  labels like "[Photo] caption". Reactions are added to the message they're on, an edit
  keeps the original and adds the new wording as "[edited] …", and a message deleted
  (for everyone, or gone from Signal Desktop any other way) is marked `deleted` rather
  than removed. Disappearing messages are never stored; the status page counts them,
  beside the newest Signal message's age.

### Changed
- The Signal connector is new: the old one read a plain key that Signal stopped writing
  in 2024, and columns Signal has since renamed. `SIGNAL_DB_PATH` and
  `SIGNAL_CONFIG_PATH` are gone; `SIGNAL_DIR` replaces both. `sync_signal` takes no
  arguments.
- `sqlcipher3` is a dependency (and `secretstorage` on Linux), replacing the optional
  `pysqlcipher3`; `memoreei[signal]` still installs.

## [0.4.2] - 2026-10-02

### Added
- **WhatsApp**, read from WhatsApp for Mac's own database: texts, captions, shared links
  and document names, with senders named from WhatsApp's address book, including group
  members and people WhatsApp knows only by an anonymous ID. Set it up under Sources in
  the dashboard (offered when WhatsApp for Mac is installed), or `memoreei setup
  whatsapp`; `WHATSAPP_DB_PATH` can name an iPhone backup's `ChatStorage.sqlite`
  instead. Each chat is a source, `whatsapp:<WhatsApp ID>`, shown by its name.
- The status page shows how old the newest WhatsApp message is, since a linked Mac
  whose phone has been offline for weeks stops syncing without saying so.

### Changed
- **`search_memory` is now `search_memoreei`.** Next to every other server's tools it
  read as a generic "memory" search, which is easy to mistake for the client's own
  memory. Clients that call it by name need the new name; nothing else changed.
- The local tools follow: `add_memory` is `add_memoreei` and `refresh_memory` is
  `refresh_memoreei`. The dashboard counts memoreeis.

### Removed
- The WhatsApp `.txt` export importer: `memoreei import whatsapp`, the `ingest_whatsapp`
  tool and the dashboard's upload for it. It read only US-format dates, so an export
  from most of the world parsed as nothing. A registered `.txt` import from before now
  says it's unsupported on each sync, and `memoreei import forget <id>` removes it.
  Messages it already imported stay where they are.
- The sample chats are JSON Lines now (`data/samples/*.jsonl`), and the smoke tests
  import them with `memoreei import json`.

## [0.4.1] - 2026-09-27

### Added
- The log says `Full Disk Access: ok` when an iMessage sync first reads the Messages
  database, and again after one that couldn't. Access was checked only at startup, so a
  grant made after it left `missing` as the log's last word.
- The dashboard's Server card on a Mac: Start at login, the log, and Stop, which quits
  Memoreei.app. Until now those were only in the menu bar.

### Changed
- **A new look**, everywhere: a teal tile with an amber speech bubble for an icon, and
  the dashboard, the Mac app's menu and windows, the disk image and the Linux dialogs
  redrawn to match. The dashboard gains a sidebar, a status page that leads with what's
  stored, and a welcome page for a new install; it follows the system into dark mode.
- The Mac menu's items are *Open Dashboard*, *Allow Access to Messages…* and *Update to
  X…*. Full Disk Access takes three steps now: current macOS has no padlock to click.
- Setup's list of sources has no emoji.

## [0.4.0] - 2026-09-27

Memoreei for Linux: a `.deb`, an `.rpm` and a tarball with the server, its Python and
the search model inside, running as a user service with the same dashboard as the Mac
app. The release candidate below is the detail; since 0.4.0rc1 only the README changed,
now shorter and corrected.

## [0.4.0rc1] - 2026-09-27

### Added
- **Linux packages**: a `.deb`, an `.rpm` and a tarball for x86_64 and aarch64, each with
  its own Python and the search model inside, for Ubuntu 20.04, Debian 11, RHEL 8,
  current Fedora and newer. The server runs as a systemd user unit for the person whose
  messages it reads, shipped disabled; the tarball installs into `~/.local` without root.
  See the README's *On Linux: a package*.
- `memoreei open`, which the Linux launcher runs: starts the service if it isn't
  running (the first time, also at login), waits for it, and opens the dashboard with a
  one-time link. When the port belongs to another user's Memoreei, it says so and how to
  pick another.
- On Linux the dashboard switches *Start at login* and *Start at boot* (linger), shows
  the service's log, stops the server, and says when a new release is out (packaged
  installs only).
- `memoreei admin-url` on a machine with no display also prints the `ssh -L` that brings
  the dashboard to the computer you're at.

### Changed
- **The Linux home directory is `~/.local/share/memoreei`** (`$XDG_DATA_HOME/memoreei`),
  not `~/.memoreei`. Nothing is moved: to keep using `~/.memoreei`, set
  `MEMOREEI_HOME=~/.memoreei` (in `~/.config/memoreei/env` for the packages). A pip
  service installed before this version keeps running on `~/.memoreei`, but the
  `memoreei` command now looks in the new place, and so would a `memoreei service
  install`: move the data, or export `MEMOREEI_HOME=~/.memoreei`, before running it. The
  dashboard says where the old data is.
- `memoreei serve --http` won't start when its port is taken, and says by whom. It exits
  75, which the packaged unit doesn't restart on.
- The Signal connector says that Signal Desktop's `encryptedKey` can't be read yet,
  instead of failing on a missing `key`.

### Removed
- The Docker image and `docker-compose.yml`, which had never been run, and with them
  `MEMOREEI_ADMIN_REMOTE`. The dashboard only answers on the machine it runs on; reach a
  headless one through `ssh -L`.

## [0.3.1] - 2026-09-26

### Fixed
- `search_memory` took most of a minute on a database of 100k-odd messages, long enough
  for MCP clients to time out: every search read every embedding out of SQLite and
  scored them one at a time in Python. The embeddings are now held in memory as one
  matrix and scored in a single product, and whole rows are read for the winners alone.
  A search takes about a tenth of a second on an Intel MacBook Air.
- The server loads the embedding model and the vector index in the background as it
  starts, so the first client after a restart doesn't wait for either. When another
  process writes to the database (an import beside the server), the index is rebuilt in
  the background and searches carry on meanwhile.
- The model loads from its cache without asking Hugging Face first, and only downloads
  when it isn't there.
- The iMessage sync wrote chat names into the log through its progress bars. There are
  no progress bars now when stderr isn't a terminal, and no chat names in them when it is.

## [0.3.0] - 2026-09-26

memoreei is a network server that asks for a key, and on a Mac an app: Memoreei.app,
with the server, its Python and the search model inside, set up from a dashboard in
the browser. The release candidates below are the detail; nothing changed since
0.3.0rc6.

## [0.3.0rc6] - 2026-09-26

### Fixed
- The embedding model was downloaded to `/tmp/fastembed_cache`, fastembed's default:
  after one user's download no other user on the machine could write there (the first
  import failed with "Could not load model … from any source"), and a reboot emptied it.
  It now goes to a per-user cache, `~/.cache/memoreei/models` (`~/Library/Caches/Memoreei/models`
  on macOS); `FASTEMBED_CACHE_PATH` still wins. The Docker image keeps it in `/data`.
- Container and VM bridges (`docker0`, `br-…`, `veth…`, `virbr…`) are no longer offered
  as server addresses in client setup.

### Added
- `memoreei --version`.
- The README says Debian and Ubuntu need `python3-venv` before the first step.

## [0.3.0rc5] - 2026-09-26

### Added
- The dashboard's home page walks a new install through setup: add a source, then add
  a client, each a button, gone once both are done.
- The DMG opens on the familiar window: Memoreei, an arrow, the Applications folder,
  and a line saying to drag one onto the other. The layout is `dmgbuild`'s, on
  create-dmg's background.

### Changed
- Saving a source in the dashboard starts its first sync at once and returns to the
  status page, instead of waiting for the next automatic round.

## [0.3.0rc4] - 2026-09-26

### Fixed
- The dashboard refused its own plain forms ("Cross-origin request refused"), so
  iMessage couldn't be set up and *Sign out* failed. Its `no-referrer` policy made
  browsers send `Origin: null`; it's now `same-origin`, and a `null` origin falls back to
  `Sec-Fetch-Site`. Refused dashboard requests are logged.
- Memoreei.app's setup window comes back to the front after the firewall's "accept
  incoming connections?" prompt, instead of staying behind the browser.
- The README's first-launch step for the unsigned app: open it from Finder, since
  Launchpad and Spotlight offer no way past Gatekeeper.

## [0.3.0rc3] - 2026-09-26

### Added
- **Memoreei.app for macOS**: a menu-bar app with the server and its own Python inside,
  for macOS 12 or newer, as `Memoreei-arm64.dmg` and `Memoreei-x86_64.dmg` on each
  GitHub release. It walks you through Full Disk Access (granted to Memoreei, not to a
  Python interpreter), starts at login, restarts the server if it crashes, and says when
  an update is out. Unsigned for now: open it the first time with *Open Anyway*. Run it
  from Applications (it asks to be moved there if opened from the disk image); if you
  move it, the copy you open is the one that starts at login.
- **A web dashboard at `/admin`**: status, sources, and client keys (create, show once
  with client setup, revoke), with *Sync now*. It answers only on the machine the server
  runs on, unless `MEMOREEI_ADMIN_REMOTE=true` (the Docker image sets it), and only
  after signing in with a one-time link from `memoreei admin-url`. The sources page offers
  iMessage for now; the rest stay in `memoreei setup` until each is tested there.
- `memoreei admin-url`, and a `python -m memoreei` entry point.

### Changed
- **On macOS the home directory is `~/Library/Application Support/Memoreei`**, shared
  with Memoreei.app. Linux keeps `~/.memoreei`, Docker keeps `/data`, and `--home` /
  `MEMOREEI_HOME` still override. There's no automatic move: to keep an existing Mac
  install, move `~/.memoreei` there by hand, or set `MEMOREEI_HOME=~/.memoreei`.
- `serve --http` starts with no API keys, and refuses every client until one exists, so
  the first key can be made in the dashboard.
- The macOS service's launchd label is now `cafe.caleb.Memoreei.service` (was
  `com.memoreei.server`, a domain the project doesn't own). Run `memoreei service
  uninstall` with the old version before upgrading, then `service install` again.
- Background sync picks up `AUTO_SYNC` and the interval from `config.env` each round, so
  changing them (as the dashboard does) needs no restart.

### Fixed
- `memoreei status` crashed on any database with messages in it.
- `memoreei search` left its database open when nothing matched, and aiosqlite 0.22
  complained on exit.

## [0.3.0rc2] - 2026-09-26

memoreei becomes a network server that asks for a key. (`v0.3.0rc1` was tagged but never
published: its release tests failed on a fresh install, against mcp 2.x.)

### Breaking
- **SSE is gone.** `memoreei serve --sse` is replaced by `memoreei serve --http`, which
  serves MCP's Streamable HTTP transport at `/mcp`. Clients connect with
  `--transport http` to `http://<server-ip>:3679/mcp`.
- **Default port is 3679**, not 8080.
- **The network server needs an API key.** Every request needs
  `Authorization: Bearer <key>`, and `serve --http` refuses to start until one exists.
  Create them with `memoreei key create <name>`.
- **Config moved to `~/.memoreei/config.env`**, next to `memoreei.db`. memoreei no longer
  looks for `.env` in its source tree, and the database defaults to
  `~/.memoreei/memoreei.db` rather than `./memoreei.db`. A `.env` in the current
  directory still overrides, for development. There is no automatic migration: move
  your old `.env` to `~/.memoreei/config.env` by hand.
- **Smaller tool surface over the network.** Only `search_memory`, `get_context`,
  `list_sources` and `sync` are offered over HTTP. `add_memory` and every `sync_*`,
  `import_*` and `ingest_*` tool are local (stdio) only.
- `memoreei service install` now runs `serve --http` on port 3679 and reads
  `~/.memoreei/config.env`; reinstall the service after upgrading.

### Added
- `memoreei key create|list|revoke`: named API keys, one per client. Shown once at
  creation, stored only as sha256 hashes. `create` prints a ready-to-paste
  `claude mcp add` command and `.mcp.json` snippet with this machine's addresses (or
  `MEMOREEI_PUBLIC_URL`) filled in.
- `memoreei serve --http [--host] [--port] [--tls-cert --tls-key]`, and the matching
  `MEMOREEI_HOST`, `MEMOREEI_PORT`, `MEMOREEI_TLS_CERT`, `MEMOREEI_TLS_KEY` settings.
- `MEMOREEI_PUBLIC_URL`, for servers behind a reverse proxy.
- `--home` and `MEMOREEI_HOME` to use a home directory other than `~/.memoreei`.
- An argument-free `sync` tool, and `memoreei sync` with no argument now does the same:
  runs every configured connector, then re-reads registered import files that changed.
- Import files are remembered: every `memoreei import …` registers its file.
  `memoreei import list` and `memoreei import forget <id>` manage them.
- `memoreei import messenger|instagram|json|csv`, joining the existing importers.
- `memoreei setup` offers to create the first API key.
- Requests to the network server are logged with the name of the key that made them.

### Fixed
- `mcp` is pinned below 2.0, whose FastMCP rename broke fresh installs.
- JSON and CSV imports identify rows by content rather than position, so re-importing a
  re-exported or reordered file adds only the new rows instead of duplicating them.
- The Docker image builds (it was missing `README.md`) and keeps config and database in
  the one `/data` volume.
- Release candidates are marked as pre-releases on GitHub.

## [0.2.1] - 2026-03-31

### Added
- `memoreei setup` — interactive CLI for configuring connectors (checkbox multi-select)
- First-run DB path prompt with sensible default (`~/.memoreei/memoreei.db`)
- GitHub Actions CI (tests on push/PR, Python 3.11–3.13)
- GitHub Actions release workflow (auto-publish to PyPI on tag)
- Tests for FTS5 query sanitizer edge cases (apostrophes, quotes, parens, wildcards)
- Tests for `memoreei setup` CLI command (single connector, interactive, fresh env)
- `--reset` flag for `memoreei setup` to clear and reconfigure connectors
- Configured connector markers (✓) in interactive setup mode
- Progress output for `memoreei sync` command
- pytest-cov for test coverage reporting
- Coverage reporting in CI workflow
- GitHub Actions CI badge in README
- Codecov badge in README
- This changelog

### Changed
- `memoreei` with no subcommand now shows help instead of erroring
- Version bump to 0.2.1

### Fixed
- FTS5 syntax error on queries containing apostrophes or special characters

## [0.2.0] - 2026-03-30

### Added
- **7 new connectors**: iMessage, Discord Data Package, SMS Backup & Restore, Signal Desktop, Instagram, Facebook Messenger, Generic JSON/CSV
- `BaseConnector` abstract class and connector registry
- Centralized `Config` dataclass (`config.py`)
- CLI via typer: `memoreei serve|sync|search|status|config`
- `memoreei import` subcommands for WhatsApp, SMS, Discord packages
- CONTRIBUTING.md, architecture docs, connector docs, deployment guide
- Dockerfile and docker-compose.yml
- pyproject.toml packaging (PyPI-ready)
- Comprehensive test suite (190+ tests across 18 test files)

### Changed
- Sync is now on-demand only (opt-in background sync via `MEMOREEI_AUTO_SYNC=true`)
- README completely rewritten for open source
- Removed `usecases/` directory, added `examples/`

### Security
- Scrubbed all hardcoded personal data from source and git history
- Added `.internal/` gitignored directory for local-only context

## [0.1.0] - 2026-03-28

### Added
- Initial hackathon build
- WhatsApp, Discord, Telegram, Slack, Matrix, Mastodon, Gmail connectors
- Hybrid search (BM25 + vector with Reciprocal Rank Fusion)
- MCP server with 21 tools
- FastEmbed offline embeddings (ONNX)
- Movie Ring and Contact Dossier example apps

[Unreleased]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.7...HEAD
[0.4.7]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.6...v0.4.7
[0.4.7rc2]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.7rc1...v0.4.7rc2
[0.4.7rc1]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.6...v0.4.7rc1
[0.4.6]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.5...v0.4.6
[0.4.6rc1]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.5...v0.4.6rc1
[0.4.5]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.4...v0.4.5
[0.4.4]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.3...v0.4.4
[0.4.3]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/CalebChristiansen/Memoreei/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/CalebChristiansen/Memoreei/compare/v0.3.1...v0.4.0
[0.4.0rc1]: https://github.com/CalebChristiansen/Memoreei/compare/v0.3.1...v0.4.0rc1
[0.3.1]: https://github.com/CalebChristiansen/Memoreei/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/CalebChristiansen/Memoreei/compare/v0.2.2...v0.3.0
[0.3.0rc6]: https://github.com/CalebChristiansen/Memoreei/compare/v0.3.0rc5...v0.3.0rc6
[0.3.0rc5]: https://github.com/CalebChristiansen/Memoreei/compare/v0.3.0rc4...v0.3.0rc5
[0.3.0rc4]: https://github.com/CalebChristiansen/Memoreei/compare/v0.3.0rc3...v0.3.0rc4
[0.3.0rc3]: https://github.com/CalebChristiansen/Memoreei/compare/v0.3.0rc2...v0.3.0rc3
[0.3.0rc2]: https://github.com/CalebChristiansen/Memoreei/compare/v0.2.2...v0.3.0rc2
[0.2.1]: https://github.com/CalebChristiansen/Memoreei/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/CalebChristiansen/Memoreei/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/CalebChristiansen/Memoreei/releases/tag/v0.1.0
