"""Help a user grant Full Disk Access on macOS.

There is no API that asks for Full Disk Access: an app can only open the right pane of
System Settings and point at the file to add. This does both, and works out which file
that is, because it isn't obvious. A framework build of Python (Homebrew's, python.org's)
re-executes itself as ``Python.framework/Versions/X.Y/Resources/Python.app``, and that
bundle, not the venv's ``python`` symlink, is what macOS asks permission for.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FULL_DISK_ACCESS_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


def binary_needing_access(executable: str | None = None) -> Path:
    """The file to add to Full Disk Access for this Python."""
    real = Path(executable or sys.executable).resolve()
    for parent in real.parents:
        if parent.parent.name == "Versions" and parent.parent.parent.name == "Python.framework":
            app = parent / "Resources" / "Python.app"
            if app.exists():
                return app
    return real


def open_full_disk_access(target: Path) -> None:
    """Open the Full Disk Access pane, and a Finder window with *target* selected."""
    subprocess.run(["open", FULL_DISK_ACCESS_PANE], capture_output=True)
    subprocess.run(["open", "-R", str(target)], capture_output=True)
