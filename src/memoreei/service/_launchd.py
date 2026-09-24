from __future__ import annotations

import subprocess
from pathlib import Path

import typer

from ._base import ServiceBackend

_LABEL = "com.memoreei.server"


def _launchd_paths() -> tuple[Path, Path, Path]:
    memoreei_dir = Path.home() / ".memoreei"
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
            f'#!/bin/bash\nset -a\nsource "{env_path}"\nset +a\nexec "{memoreei_bin}" serve --sse --port {port}\n'
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

        typer.echo(f"\n  ✓ Memoreei service installed and started\n")
        typer.echo(f"  Endpoint:  http://localhost:{port}/sse")
        typer.echo(f"  Logs:      memoreei service logs")
        typer.echo(f"  Status:    memoreei service status")
        typer.echo(f"  Stop:      memoreei service uninstall\n")

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
