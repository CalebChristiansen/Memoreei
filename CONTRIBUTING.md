# Contributing to Memoreei

Welcome! Memoreei is a personal memory search server that ingests messages from Discord, WhatsApp, Telegram, Matrix, Slack, and Gmail into a hybrid search database. Contributions are appreciated.

## Dev Environment Setup

```bash
git clone https://github.com/your-fork/memoreei.git
cd memoreei
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Copy `.env.example` to `.env` and fill in any credentials you want to test with:

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

1. Create `src/memoreei/connectors/yourplatform_connector.py` implementing `BaseConnector`
2. Register it in `src/memoreei/connectors/__init__.py`
3. Add config fields to `src/memoreei/config.py`
4. Add an MCP tool in `src/memoreei/server.py`
5. Write tests in `tests/test_yourplatform.py`

## PR Process

1. Fork the repo and create a branch: `git checkout -b feat/my-feature`
2. Make your changes and add tests
3. Ensure `pytest` passes
4. Open a pull request with a clear description of what it does and why

Keep PRs focused — one feature or fix per PR makes review easier. If you're unsure about scope or approach, open an issue first.
