"""Memoreei.app's switches, for the dashboard its server serves.

The app (macos/Memoreei) runs this server as its child and owns "Start at login": a
LaunchAgent whose program is the app's own executable. The dashboard flips the same
switch by writing or removing the same file, as LaunchAgent.swift does, and the app's
menu reads that file each time it opens, so the two can't disagree. Stopping works by
asking: a flag file the app checks when its server exits, telling it the exit was
wanted and the whole app should quit, rather than a crash to restart after.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

BUNDLE_ID = "cafe.caleb.Memoreei"


def executable() -> str | None:
    """The app's own executable, which Memoreei.app passes its server. None outside it."""
    return os.environ.get("MEMOREEI_APP_EXECUTABLE") or None


def available() -> bool:
    return executable() is not None


def _library() -> Path:
    return Path.home() / "Library"


def launch_agent_path() -> Path:
    return _library() / "LaunchAgents" / f"{BUNDLE_ID}.plist"


def quit_flag() -> Path:
    """Signals.quitRequested in the app: in its state folder, whatever the home."""
    return _library() / "Caches" / "Memoreei" / "quit-requested"


def start_at_login() -> bool:
    return launch_agent_path().exists()


def set_start_at_login(on: bool) -> None:
    """As LaunchAgent.write() and remove() do. Turning it off leaves the loaded job alone
    until the next login: unloading it now would stop the app if launchd started it."""
    path = launch_agent_path()
    if not on:
        path.unlink(missing_ok=True)
    else:
        env = {"MEMOREEI_LAUNCHD": "1"}
        # The app always tells its server where home is; the agent only needs it when
        # it isn't the default (a development home).
        home = os.environ.get("MEMOREEI_HOME")
        if home and Path(home) != _library() / "Application Support" / "Memoreei":
            env["MEMOREEI_HOME"] = home
        log = str(_library() / "Logs" / "Memoreei" / "app.log")
        plist = {
            "Label": BUNDLE_ID,
            "ProgramArguments": [executable()],
            "EnvironmentVariables": env,
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},
            "LimitLoadToSessionType": "Aqua",
            "ProcessType": "Interactive",
            "StandardOutPath": log,
            "StandardErrorPath": log,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("wb") as f:
            plistlib.dump(plist, f)
        tmp.replace(path)
    _mark_decided()


def _mark_decided() -> None:
    """The user has chosen, so the app won't turn it on for them after the first grant."""
    subprocess.run(["defaults", "write", BUNDLE_ID, "startAtLoginDecided", "-bool", "true"],
                   capture_output=True)


def request_quit() -> None:
    """Tell the app that its server's coming exit is a request to quit, not a crash."""
    flag = quit_flag()
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()


def log_path() -> Path:
    return _library() / "Logs" / "Memoreei" / "memoreei.log"


def log_tail(lines: int = 300) -> str:
    try:
        with log_path().open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - lines * 400))
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])
