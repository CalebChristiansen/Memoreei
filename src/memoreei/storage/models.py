from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class MemoryItem:
    id: str
    source: str
    source_id: str | None
    content: str
    summary: str | None
    participants: list[str]
    ts: int  # unix epoch
    ingested_at: int
    metadata: dict
    embedding: list[float] | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "source_id": self.source_id,
            "content": self.content,
            "summary": self.summary,
            "participants": self.participants,
            "ts": self.ts,
            "ingested_at": self.ingested_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_row(cls, row: dict) -> "MemoryItem":
        participants = row.get("participants")
        if isinstance(participants, str):
            participants = json.loads(participants) if participants else []

        metadata = row.get("metadata")
        if isinstance(metadata, str):
            metadata = json.loads(metadata) if metadata else {}

        embedding = row.get("embedding")
        if isinstance(embedding, (bytes, bytearray)) and embedding:
            import numpy as np
            embedding = np.frombuffer(embedding, dtype=np.float32).tolist()
        elif isinstance(embedding, str) and embedding:
            embedding = json.loads(embedding)

        return cls(
            id=row["id"],
            source=row["source"],
            source_id=row.get("source_id"),
            content=row["content"],
            summary=row.get("summary"),
            participants=participants or [],
            ts=row["ts"],
            ingested_at=row["ingested_at"],
            metadata=metadata or {},
            embedding=embedding,
        )
