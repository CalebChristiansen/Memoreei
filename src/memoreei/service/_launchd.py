from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from memoreei.config import memoreei_home

from ._base import ServiceBackend, print_installed

_LABEL = "com.memoreei.server"


def _launchd_paths() -> tuple[Path, Path, Path]:
    memoreei_dir = memoreei_home()
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
    log_path = memoreei_dir / "memoreei.log"
    return memoreei_dir, plist_path, log_path


class LaunchdBackend(ServiceBackend):
    def install(self, memoreei_bin: str, env_path: Path, port: int) -> None:
        memoreei_dir, plist_path, log_path = _launchd_paths()
        start_sh = memoreei_dir / "start.sh"

        memoreei_dir.mkdir(parents=True, exist_ok=True)
        plist_path.parent.mkdir(parents=True, exist_ok=True)

        start_sh.write_text(
            f"#!/bin/bash\n"
            f"set -a\n"
            f'source "{env_path}"\n'
            f"set +a\n"
            f'export MEMOREEI_HOME="{env_path.parent}"\n'
            f'exec "{memoreei_bin}" serve --http --port {port}\n'
        )
        start_sh.chmod(0o755)

        plist_path.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{start_sh}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{memoreei_dir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_path}</string>
    <key>StandardErrorPath</key>
    <string>{log_path}</string>
</dict>
</plist>
"""
        )

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
