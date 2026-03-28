from __future__ import annotations

import os
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


class FastEmbedProvider(EmbeddingProvider):
    """Local ONNX embeddings via fastembed. No API key required."""

    MODEL_NAME = "BAAI/bge-small-en-v1.5"
    _DIMENSION = 384

    def __init__(self) -> None:
        self._model: "fastembed.TextEmbedding | None" = None  # type: ignore[name-defined]

    def _get_model(self) -> "fastembed.TextEmbedding":  # type: ignore[name-defined]
        if self._model is None:
            from fastembed import TextEmbedding  # type: ignore[import]
            self._model = TextEmbedding(model_name=self.MODEL_NAME)
        return self._model

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        return [emb.tolist() for emb in model.embed(texts)]

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
