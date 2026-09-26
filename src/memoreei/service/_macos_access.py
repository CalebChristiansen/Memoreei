"""Walk a user through granting Full Disk Access on macOS.

There is no API that asks for Full Disk Access: an app can only open the right pane of
System Settings and point at the file to add. This does that, with native dialogs
explaining each step, then restarts the service and checks it can really read Messages.

The file to add isn't obvious. A framework build of Python (Homebrew's, python.org's)
re-executes itself as ``Python.framework/Versions/X.Y/Resources/Python.app``, and that
bundle, not the venv's ``python`` symlink, is what macOS asks permission for.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

FULL_DISK_ACCESS_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

# Written to the service log at startup, so grant-access can tell whether it worked.
ACCESS_OK = "memoreei: Full Disk Access: ok"
ACCESS_MISSING = "memoreei: Full Disk Access: missing"


def binary_needing_access(executable: str | None = None) -> Path:
    """The file to add to Full Disk Access for this Python."""
    real = Path(executable or sys.executable).resolve()
    for parent in real.parents:
        if parent.parent.name == "Versions" and parent.parent.parent.name == "Python.framework":
            app = parent / "Resources" / "Python.app"
            if app.exists():
                return app
    return real


def display_name(target: Path) -> str:
    """What the file is called in Finder and in the Full Disk Access list."""
    return target.stem if target.suffix == ".app" else target.name


def can_read_messages(chat_db: str) -> bool:
    """Whether this process can read the Messages database."""
    path = Path(chat_db).expanduser()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


def open_full_disk_access(target: Path) -> None:
    """Open the Full Disk Access pane, and a Finder window with *target* selected."""
    subprocess.run(["open", FULL_DISK_ACCESS_PANE], capture_output=True)
    subprocess.run(["open", "-R", str(target)], capture_output=True)


def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def dialog(text: str, buttons: list[str], default: str, icon: str = "note") -> str | None:
    """Show a native dialog. Returns the button clicked, or None if it couldn't be shown
    (no one logged in at the screen, an SSH session without GUI access).
    """
    script = (
        f"display dialog {_applescript_string(text)}"
        f" with title \"Memoreei\""
        f" buttons {{{', '.join(_applescript_string(b) for b in buttons)}}}"
        f" default button {_applescript_string(default)}"
        f" with icon {icon}"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=900
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        # A button named "Cancel" ends the script with "User canceled" (-128).
        return "Cancel" if "-128" in result.stderr else None
    # "button returned:Done"
    return result.stdout.strip().partition("button returned:")[2] or None


def instructions(name: str) -> str:
    return (
        "Memoreei needs Full Disk Access to read your iMessages. macOS can't ask for it "
        "automatically, so here's how:\n\n"
        "1. Click Open Settings. Two windows open: System Settings at Full Disk Access, "
        f"and a Finder window with “{name}” selected.\n\n"
        "2. If there's a lock at the bottom of System Settings, click it and enter your "
        "password.\n\n"
        f"3. Drag “{name}” from the Finder window into the list in System "
        "Settings.\n\n"
        f"4. Make sure the switch (or checkbox) next to “{name}” is on.\n\n"
        "5. Come back to this window and click Done."
    )


def wait_for_startup_report(log_path: Path, offset: int, timeout: float = 45) -> bool | None:
    """Read the service log from *offset* until it reports on Full Disk Access."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with log_path.open("r", errors="replace") as f:
                f.seek(offset)
                text = f.read()
        except OSError:
            text = ""
        if ACCESS_OK in text:
            return True
        if ACCESS_MISSING in text:
            return False
        time.sleep(1)
    return None
