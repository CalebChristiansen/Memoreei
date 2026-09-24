from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import typer


class ServiceBackend(ABC):
    @abstractmethod
    def install(self, memoreei_bin: str, env_path: Path, port: int) -> None: ...

    @abstractmethod
    def uninstall(self) -> None: ...

    @abstractmethod
    def status(self) -> None: ...

    @abstractmethod
    def logs(self, follow: bool, lines: int) -> None: ...


def print_installed(port: int) -> None:
    from memoreei.auth import server_urls
    from memoreei.config import get_config

    cfg = get_config()
    typer.echo("\n  ✓ Memoreei service installed and started\n")
    for url in server_urls(port, cfg.public_url, tls=bool(cfg.tls_cert)):
        typer.echo(f"  Endpoint:  {url}")
    typer.echo("  Clients:   memoreei key create <name>  (one key per client)")
    typer.echo("  Logs:      memoreei service logs")
    typer.echo("  Status:    memoreei service status")
    typer.echo("  Stop:      memoreei service uninstall\n")
