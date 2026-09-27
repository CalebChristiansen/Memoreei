from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import typer

from memoreei.config import memoreei_home

from ._base import ServiceBackend, print_installed

# Named under Memoreei.app's bundle identifier (cafe.caleb.Memoreei), the maintainer's
# domain. The app and this service run the same server on the same port: use one or
# the other.
_LABEL = "cafe.caleb.Memoreei.service"


def _launchd_paths() -> tuple[Path, Path, Path]:
    memoreei_dir = memoreei_home()
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
    log_path = memoreei_dir / "memoreei.log"
    return memoreei_dir, plist_path, log_path


class LaunchdBackend(ServiceBackend):
    def install(self, memoreei_bin: str, env_path: Path, port: int) -> None:
        memoreei_dir, plist_path, log_path = _launchd_paths()

        memoreei_dir.mkdir(parents=True, exist_ok=True)
        plist_path.parent.mkdir(parents=True, exist_ok=True)

        # launchd runs memoreei itself, with no shell script in between: macOS holds the
        # first program a job runs responsible for its file access, so a wrapper script
        # would make Full Disk Access a question about /bin/bash rather than Python.
        # memoreei reads config.env from MEMOREEI_HOME by itself.
        stale_wrapper = memoreei_dir / "start.sh"
        if stale_wrapper.exists():
            stale_wrapper.unlink()

        plist = {
            "Label": _LABEL,
            "ProgramArguments": [memoreei_bin, "serve", "--http", "--port", str(port)],
            "EnvironmentVariables": {"MEMOREEI_HOME": str(env_path.parent)},
            "WorkingDirectory": str(memoreei_dir),
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
        }
        with plist_path.open("wb") as f:
            plistlib.dump(plist, f)

        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True, text=True)
        if result.returncode != 0:
            typer.echo(f"Failed to load service: {result.stderr.strip()}")
            raise typer.Exit(1)
        subprocess.run(["launchctl", "start", _LABEL], capture_output=True)

        print_installed(port)

    def uninstall(self) -> None:
        _, plist_path, _ = _launchd_paths()
        if not plist_path.exists():
            typer.echo("No service installed.")
            raise typer.Exit(0)
        subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
        plist_path.unlink()
        typer.echo("  ✓ Service uninstalled.")

    def status(self) -> None:
        result = subprocess.run(["launchctl", "list", _LABEL], capture_output=True, text=True)
        if result.returncode != 0:
            typer.echo("  Service not running.")
        else:
            typer.echo(result.stdout)

    def logs(self, follow: bool, lines: int) -> None:
        _, _, log_path = _launchd_paths()
        if not log_path.exists():
            typer.echo(f"No log file at {log_path}. Has the service been started?")
            raise typer.Exit(1)
        cmd = ["tail", f"-n{lines}"]
        if follow:
            cmd.append("-f")
        cmd.append(str(log_path))
        subprocess.run(cmd)
