from __future__ import annotations

import asyncio
import os
import threading
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts. Returns list of embedding vectors."""
        ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string."""
        ...

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding vector dimension."""
        ...

    async def warm(self) -> None:
        """Get ready to embed, so the first query doesn't pay for it. Optional."""


class FastEmbedProvider(EmbeddingProvider):
    """Local ONNX embeddings via fastembed. No API key required."""

    MODEL_NAME = "BAAI/bge-small-en-v1.5"
    _DIMENSION = 384

    def __init__(self) -> None:
        self._model: "fastembed.TextEmbedding | None" = None  # type: ignore[name-defined]
        self._loading = threading.Lock()

    def _get_model(self) -> "fastembed.TextEmbedding":  # type: ignore[name-defined]
        with self._loading:
            if self._model is None:
                from fastembed import TextEmbedding  # type: ignore[import]

                from memoreei.config import model_cache_dir

                cache_dir = str(model_cache_dir())
                try:
                    # Without this, every load asks Hugging Face about a model already on
                    # disk: seconds, or a hang when the network is down.
                    self._model = TextEmbedding(model_name=self.MODEL_NAME, cache_dir=cache_dir,
                                                local_files_only=True)
                except Exception:
                    self._model = TextEmbedding(model_name=self.MODEL_NAME, cache_dir=cache_dir)
            return self._model

    async def warm(self) -> None:
        await asyncio.to_thread(self._get_model)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        def run() -> list[list[float]]:
            return [emb.tolist() for emb in self._get_model().embed(texts)]

        return await asyncio.to_thread(run)

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]

    @property
    def dimension(self) -> int:
        return self._DIMENSION


class OpenAIProvider(EmbeddingProvider):
    """OpenAI text-embedding-3-small. Requires OPENAI_API_KEY."""

    MODEL_NAME = "text-embedding-3-small"
    _DIMENSION = 1536
    BATCH_SIZE = 50

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise ValueError("OPENAI_API_KEY is required for OpenAIProvider")
        self._client: "openai.AsyncOpenAI | None" = None  # type: ignore[name-defined]

    def _get_client(self) -> "openai.AsyncOpenAI":  # type: ignore[name-defined]
        if self._client is None:
            import openai  # type: ignore[import]
            self._client = openai.AsyncOpenAI(api_key=self._api_key)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        results: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]
            response = await client.embeddings.create(model=self.MODEL_NAME, input=batch)
            results.extend([item.embedding for item in response.data])
        return results

    async def embed_query(self, text: str) -> list[float]:
        results = await self.embed([text])
        return results[0]

    @property
    def dimension(self) -> int:
        return self._DIMENSION


def get_provider() -> EmbeddingProvider:
    """Return the configured embedding provider based on env vars."""
    provider_name = os.environ.get("EMBEDDING_PROVIDER", "fastembed").lower()
    if provider_name == "openai":
        return OpenAIProvider()
    return FastEmbedProvider()
