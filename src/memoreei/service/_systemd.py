"""The systemd user unit: written by `service install` for pip installs, shipped by the
Linux packages.

A package (.deb, .rpm, or the tarball's install.sh) puts ``memoreei.service`` where the
user's manager finds it, disabled. Then `service install` only enables it. A pip install
has no such file, so `service install` writes one pointing at its own interpreter.

The rest is what the launcher (`memoreei open`) and the dashboard need to know about the
service: whether it's enabled, running, lingering, and what it logged.
"""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

import typer

from memoreei.config import memoreei_home

from ._base import ServiceBackend, print_installed

_SERVICE_NAME = "memoreei"
UNIT = f"{_SERVICE_NAME}.service"
# 75 (EX_TEMPFAIL): `serve` found the port taken. The shipped unit doesn't restart on it.
EXIT_PORT_IN_USE = 75


def bundle_root() -> Path | None:
    """The packaged tree (/opt/memoreei, ~/.local/opt/memoreei), if this is running from one.

    linux/memoreei.sh sets MEMOREEI_BUNDLE; a pip install never does.
    """
    root = os.environ.get("MEMOREEI_BUNDLE")
    return Path(root) if root else None


def _systemd_paths() -> tuple[Path, Path]:
    memoreei_dir = memoreei_home()
    unit_path = Path.home() / ".config" / "systemd" / "user" / UNIT
    return memoreei_dir, unit_path


def _user_env() -> dict[str, str]:
    """This environment, plus the runtime directory `systemctl --user` needs to find the
    manager. su, sudo -u and some SSH setups leave XDG_RUNTIME_DIR unset."""
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR"):
        runtime = f"/run/user/{os.getuid()}"
        if os.path.isdir(runtime):
            env["XDG_RUNTIME_DIR"] = runtime
    return env


def systemctl(*args: str) -> "subprocess.CompletedProcess[str]":
    try:
        return subprocess.run(
            ["systemctl", "--user", *args], capture_output=True, text=True, env=_user_env()
        )
    except OSError as exc:  # no systemctl at all
        return subprocess.CompletedProcess(["systemctl"], 127, "", str(exc))


def _handle_systemd_error(result: "subprocess.CompletedProcess[str]") -> None:
    err = result.stderr.strip()
    typer.echo(f"systemctl error: {err}")
    if "Failed to connect to bus" in err or "No such file or directory" in err:
        user = os.environ.get("USER", os.environ.get("LOGNAME", "$USER"))
        typer.echo(f"\n  Your systemd user session is not running.")
        typer.echo(f"  Fix: loginctl enable-linger {user}")
        typer.echo(f"  Then log out and back in, and retry.")


# ── What the launcher and the dashboard ask ─────────────────────────────────


def manager_available() -> bool:
    """Whether there's a systemd user manager to talk to."""
    return sys.platform.startswith("linux") and systemctl("show-environment").returncode == 0


def unit_state() -> dict[str, bool]:
    """installed: the manager knows the unit. enabled: it starts at login. active: running."""
    result = systemctl("show", UNIT, "-p", "LoadState", "-p", "UnitFileState", "-p", "ActiveState")
    props = dict(line.partition("=")[::2] for line in result.stdout.splitlines())
    return {
        "installed": props.get("LoadState") == "loaded",
        "enabled": props.get("UnitFileState") in ("enabled", "enabled-runtime", "linked"),
        "active": props.get("ActiveState") in ("active", "activating", "reloading"),
    }


def set_enabled(on: bool) -> bool:
    """Start at login, or not. Doesn't start or stop anything now."""
    return systemctl("enable" if on else "disable", UNIT).returncode == 0


def start() -> "subprocess.CompletedProcess[str]":
    return systemctl("enable", "--now", UNIT)


def stop_soon() -> None:
    """Stop the unit without waiting, so the process being stopped (perhaps this one) can
    still finish answering the request that asked."""
    systemctl("stop", "--no-block", UNIT)


def wait_until_listening(port: int, timeout: float = 60) -> bool:
    """Wait for the unit's server to hold *port*. False as soon as the unit stops."""
    import time

    from ._port import port_owner

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_owner(port) == os.getuid():
            return True
        if not unit_state()["active"]:
            return False
        time.sleep(0.3)
    return False


def running_as_unit() -> bool:
    """Whether this process is the memoreei unit's own server."""
    try:
        return f"/{UNIT}" in Path("/proc/self/cgroup").read_text()
    except OSError:
        return False


def linger() -> bool | None:
    """Whether this user's services start at boot, before anyone logs in. None: unknown."""
    try:
        result = subprocess.run(
            ["loginctl", "show-user", getpass.getuser(), "-p", "Linger", "--value"],
            capture_output=True, text=True,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return value == "yes" if value in ("yes", "no") else None


def linger_command() -> str:
    return f"sudo loginctl enable-linger {getpass.getuser()}"


def enable_linger() -> bool:
    """Ask logind to start this user's manager at boot. Most desktops let a user do this
    for themselves; where polkit says no, linger_command() is what an admin runs."""
    try:
        result = subprocess.run(
            ["loginctl", "enable-linger", getpass.getuser()],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and linger() is True


def journal(lines: int = 200) -> str:
    """The unit's recent log, oldest first: a time and the message, without the host
    name and PID that journalctl's own formats spend half the width on."""
    import datetime
    import json

    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", UNIT, "-n", str(lines), "--no-pager", "-o", "json", "-q"],
            capture_output=True, text=True, env=_user_env(),
        )
    except OSError:
        return ""
    out = []
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
            when = datetime.datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1e6)
            message = entry.get("MESSAGE", "")
            if isinstance(message, list):  # not valid UTF-8: journald sends the bytes
                message = bytes(message).decode("utf-8", "replace")
        except (ValueError, KeyError, TypeError):
            continue
        out.append(f"{when:%Y-%m-%d %H:%M:%S}  {message}")
    return "\n".join(out) + ("\n" if out else "")


# ── `memoreei service …` ────────────────────────────────────────────────────


class SystemdBackend(ServiceBackend):
    def install(self, memoreei_bin: str, env_path: Path, port: int) -> None:
        if bundle_root():
            self._enable_packaged(port)
            return
        memoreei_dir, unit_path = _systemd_paths()
        memoreei_dir.mkdir(parents=True, exist_ok=True)
        unit_path.parent.mkdir(parents=True, exist_ok=True)

        unit_path.write_text(
            f"[Unit]\n"
            f"Description=Memoreei MCP server\n\n"
            f"[Service]\n"
            f"EnvironmentFile={env_path}\n"
            f"Environment=MEMOREEI_HOME={env_path.parent}\n"
            f"ExecStart={memoreei_bin} serve --http --port {port}\n"
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

        print_installed(port)

    def _enable_packaged(self, port: int) -> None:
        """Enable the unit the package shipped, rather than writing one."""
        _, unit_path = _systemd_paths()
        root = bundle_root()
        # A unit left in ~/.config by an earlier pip install would shadow the package's.
        if unit_path.exists() and f"{root}/bin/memoreei" not in unit_path.read_text():
            aside = unit_path.with_name(UNIT + ".pip-install")
            unit_path.rename(aside)
            typer.echo(f"  Moved an older unit from a pip install aside: {aside}")
            systemctl("daemon-reload")
        result = start()
        if result.returncode != 0:
            _handle_systemd_error(result)
            raise typer.Exit(1)
        if not wait_until_listening(port):
            typer.echo("  ✗ Memoreei didn't start. Its log ends:\n")
            typer.echo("\n".join("    " + line for line in journal(6).splitlines()))
            raise typer.Exit(1)
        print_installed(port)
        if linger() is False:
            typer.echo("  It starts when you log in. To start it at boot instead, before anyone")
            typer.echo("  logs in (a headless server, say), let your services linger:")
            if sys.stdin.isatty() and typer.confirm("  Turn that on now?", default=True):
                if enable_linger():
                    typer.echo("  ✓ Memoreei now starts at boot.\n")
                else:
                    typer.echo(f"  That needs an administrator: {linger_command()}\n")
            else:
                typer.echo(f"    loginctl enable-linger {getpass.getuser()}\n")

    def uninstall(self) -> None:
        if bundle_root():
            # The unit belongs to the package; switch it off rather than delete it.
            systemctl("disable", "--now", UNIT)
            typer.echo("  ✓ Service stopped, and won't start at login.")
            return
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
