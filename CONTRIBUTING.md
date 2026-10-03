# Contributing to Memoreei

Welcome! Memoreei is a personal memory search server that ingests messages from iMessage, WhatsApp, Signal, Discord, Telegram, Matrix, Slack, Gmail and more into a hybrid search database. Contributions are appreciated.

## Dev Environment Setup

```bash
git clone https://github.com/CalebChristiansen/Memoreei.git
cd Memoreei
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Configure connectors interactively:

```bash
memoreei setup
```

That writes `config.env` in memoreei's home (`~/.local/share/memoreei` on Linux). For
development you can instead keep a `.env` in the checkout, which overrides it while you
work from that directory:

```bash
cp .env.example .env
```

## Running Tests

```bash
pytest
```

With coverage:

```bash
pytest --cov=src/memoreei --cov-report=term-missing
```

Tests use `pytest-asyncio` (auto mode). All tests run against a fresh in-memory SQLite database per test — no external services required.

## Code Style

- **Type hints** on all function signatures
- **Docstrings** on public classes and non-trivial methods
- **Async** throughout — all I/O is async
- Keep functions focused; prefer small composable pieces over large methods

No formatter is enforced yet, but aim for PEP 8 style. A simple `ruff check src/` before submitting is appreciated.

## Adding a New Connector

See [docs/connectors.md](docs/connectors.md) for a step-by-step guide. The short version:

1. Create `src/memoreei/connectors/yourplatform_connector.py`: a sync that stores what's
   new since a checkpoint (`connectors/imessage_connector.py`, `connectors/whatsapp/` and `connectors/signal/`
   are the models for reading a local database)
2. Add config fields to `src/memoreei/config.py`, and the connector to `configured_connectors()`
3. Wire it in: a `MemoryTools` method, a branch in `SyncManager.sync_source`, a tool in
   `server.py`'s `LOCAL_TOOLS`, and a `catalog.py` entry for `memoreei setup` (plus
   `DASHBOARD_CONNECTORS` to offer it in the dashboard)
4. Write tests in `tests/test_yourplatform.py`, against fake data only

## PR Process

1. Fork the repo and create a branch: `git checkout -b feat/my-feature`
2. Make your changes and add tests
3. Ensure `pytest` passes
4. Open a pull request with a clear description of what it does and why

Keep PRs focused — one feature or fix per PR makes review easier. If you're unsure about scope or approach, open an issue first.
