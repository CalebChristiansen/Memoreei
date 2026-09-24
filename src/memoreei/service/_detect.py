from __future__ import annotations

import sys

import typer

from ._base import ServiceBackend


def get_backend() -> ServiceBackend:
    if sys.platform == "darwin":
        from ._launchd import LaunchdBackend
        return LaunchdBackend()
    if sys.platform.startswith("linux"):
        from ._systemd import SystemdBackend
        return SystemdBackend()
    typer.echo(f"Service management is not supported on {sys.platform}.")
    raise typer.Exit(1)
