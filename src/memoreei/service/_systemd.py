from __future__ import annotations

import os
import subprocess
from pathlib import Path

import typer

from ._base import ServiceBackend

_SERVICE_NAME = "memoreei"


def _systemd_paths() -> tuple[Path, Path]:
    memoreei_dir = Path.home() / ".memoreei"
    unit_path = Path.home() / ".config" / "systemd" / "user" / f"{_SERVICE_NAME}.service"
    return memoreei_dir, unit_path


def _handle_systemd_error(result: "subprocess.CompletedProcess[str]") -> None:
    err = result.stderr.strip()
    typer.echo(f"systemctl error: {err}")
    if "Failed to connect to bus" in err or "No such file or directory" in err:
        user = os.environ.get("USER", os.environ.get("LOGNAME", "$USER"))
        typer.echo(f"\n  Your systemd user session is not running.")
        typer.echo(f"  Fix: loginctl enable-linger {user}")
        typer.echo(f"  Then log out and back in, and retry.")


class SystemdBackend(ServiceBackend):
    def install(self, memoreei_bin: str, env_path: Path, port: int) -> None:
        memoreei_dir, unit_path = _systemd_paths()
        memoreei_dir.mkdir(parents=True, exist_ok=True)
        unit_path.parent.mkdir(parents=True, exist_ok=True)

        unit_path.write_text(
            f"[Unit]\n"
            f"Description=Memoreei MCP server\n\n"
            f"[Service]\n"
            f"EnvironmentFile={env_path}\n"
            f"ExecStart={memoreei_bin} serve --sse --port {port}\n"
            f"Restart=always\n"
            f"RestartSec=5\n\n"
            f"[Install]\n"
            f"WantedBy=default.target\n"
        )

        result = subprocess.run(
            ["systemctl", "--user", "daemon-reload"], capture_output=True, text=True
        )
        if result.returncode != 0:
            _handle_systemd_error(result)
            raise typer.Exit(1)

        result = subprocess.run(
            ["systemctl", "--user", "enable", "--now", _SERVICE_NAME],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            _handle_systemd_error(result)
            raise typer.Exit(1)

        typer.echo(f"\n  ✓ Memoreei service installed and started\n")
        typer.echo(f"  Endpoint:  http://localhost:{port}/sse")
        typer.echo(f"  Logs:      memoreei service logs")
        typer.echo(f"  Status:    memoreei service status")
        typer.echo(f"  Stop:      memoreei service uninstall\n")

    def uninstall(self) -> None:
        _, unit_path = _systemd_paths()
        if not unit_path.exists():
            typer.echo("No service installed.")
            raise typer.Exit(0)
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", _SERVICE_NAME], capture_output=True
        )
        unit_path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        typer.echo("  ✓ Service uninstalled.")

    def status(self) -> None:
        result = subprocess.run(
            ["systemctl", "--user", "status", _SERVICE_NAME],
            capture_output=True,
            text=True,
        )
        # exit code 3 = inactive but unit is known (not an error)
        if result.returncode not in (0, 3):
            typer.echo("  Service not running.")
        else:
            typer.echo(result.stdout or result.stderr)

    def logs(self, follow: bool, lines: int) -> None:
        cmd = ["journalctl", "--user", "-u", _SERVICE_NAME, f"-n{lines}"]
        if follow:
            cmd.append("-f")
        subprocess.run(cmd)
