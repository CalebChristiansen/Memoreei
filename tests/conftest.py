"""Shared pytest fixtures for the memoreei test suite."""
from __future__ import annotations

import pytest

from memoreei.storage.database import Database


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


@pytest.fixture
def mock_embedder() -> MockEmbedder:
    """Mock embedder returning zero vectors of dim=10."""
    return MockEmbedder()
