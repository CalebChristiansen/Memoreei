# Changelog

All notable changes to Memoreei will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `memoreei setup` — interactive CLI for configuring connectors (checkbox multi-select)
- First-run DB path prompt with sensible default (`~/.memoreei/memoreei.db`)
- GitHub Actions CI (tests on push/PR, Python 3.11–3.13)
- GitHub Actions release workflow (auto-publish to PyPI on tag)
- This changelog

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

[Unreleased]: https://github.com/CalebChristiansen/Memoreei/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/CalebChristiansen/Memoreei/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/CalebChristiansen/Memoreei/releases/tag/v0.1.0
