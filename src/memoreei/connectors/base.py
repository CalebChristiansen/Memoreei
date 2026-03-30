from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

@dataclass
class SyncResult:
    """Result of a connector sync operation."""
    synced: int = 0
    source: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        d = {"synced": self.synced, "source": self.source}
        if self.errors:
            d["errors"] = self.errors
        return d

class BaseConnector(ABC):
    """Abstract base for all Memoreei connectors."""

    name: str = "unknown"

    @abstractmethod
    async def sync(self, **kwargs: Any) -> SyncResult:
        """Run an incremental sync. Returns SyncResult."""
        ...

    @classmethod
    @abstractmethod
    def is_configured(cls) -> bool:
        """Check if this connector has the required config (env vars, files, etc)."""
        ...
