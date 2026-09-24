from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ServiceBackend(ABC):
    @abstractmethod
    def install(self, memoreei_bin: str, env_path: Path, port: int) -> None: ...

    @abstractmethod
    def uninstall(self) -> None: ...

    @abstractmethod
    def status(self) -> None: ...

    @abstractmethod
    def logs(self, follow: bool, lines: int) -> None: ...
