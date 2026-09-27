"""Shared pytest fixtures for the memoreei test suite."""
from __future__ import annotations

import os

import pytest

import memoreei.config as config_module
from memoreei.storage.database import Database


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """Every test gets its own MEMOREEI_HOME and never reads a real config.

    No ~/.memoreei, no developer's .env in the checkout: env loading is marked done,
    and tests that exercise loading reset it themselves.
    """
    home = tmp_path_factory.mktemp("memoreei-home")
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    monkeypatch.setenv("MEMOREEI_HOME", str(home))
    monkeypatch.delenv("MEMOREEI_DB_PATH", raising=False)
    monkeypatch.setattr(config_module, "_home_override", None)
    monkeypatch.setattr(config_module, "_env_loaded", True)
    monkeypatch.setattr(config_module, "_config", None)
    return home


@pytest.fixture(autouse=True)
def restore_environ():
    """config.reload_config() writes to os.environ, as it must in a running server."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
async def temp_db(tmp_path):
    """Fresh SQLite database per test, torn down after."""
    db = Database(db_path=str(tmp_path / "test.db"))
    await db.connect()
    yield db
    await db.close()


class MockEmbedder:
    """Returns fixed zero vectors — no ML dependencies required."""

    dim = 10

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dim for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return [0.0] * self.dim

    async def warm(self) -> None:
        pass


@pytest.fixture
def mock_embedder() -> MockEmbedder:
    """Mock embedder returning zero vectors of dim=10."""
    return MockEmbedder()
