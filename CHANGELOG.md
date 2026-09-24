# Changelog

All notable changes to Memoreei will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0rc1] - 2026-09-24

memoreei becomes a network server that asks for a key.

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

[Unreleased]: https://github.com/CalebChristiansen/Memoreei/compare/v0.2.1...HEAD
[0.3.0rc1]: https://github.com/CalebChristiansen/Memoreei/compare/v0.2.2...v0.3.0rc1
[0.2.1]: https://github.com/CalebChristiansen/Memoreei/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/CalebChristiansen/Memoreei/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/CalebChristiansen/Memoreei/releases/tag/v0.1.0
