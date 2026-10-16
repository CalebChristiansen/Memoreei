"""A desktop notification when a chatbot asks to connect, where there's a desktop.

macOS: ``osascript``'s ``display notification``. Linux: ``notify-send``, when there's
a session bus to send it on. A headless server has neither, and the request still
waits in the dashboard and in ``memoreei chatbots list``. Best effort throughout:
a notification that fails is logged and forgotten.

Client names come from strangers' documents, so they go to both programs as
arguments, never spliced into a script, and lose any markup first.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Any

log = logging.getLogger("memoreei.oauth")

MIN_GAP = 10  # seconds: a flood of requests is one notification, not fifty
_last = float("-inf")


def message(request: dict[str, Any]) -> tuple[str, str]:
    name = request["client_name"]
    if request.get("self_registered"):
        name += " (it named itself)"
    else:
        name += f" ({request['client_host']})"
    return ("Memoreei: approve a sign-in?",
            f"{name} wants to use memoreei, code {request['user_code']}. "
            f"Approve it in memoreei's dashboard if you just asked it to connect.")


def _command(title: str, body: str) -> list[str] | None:
    if sys.platform == "darwin" and shutil.which("osascript"):
        return ["osascript", "-e", "on run argv", "-e",
                "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run",
                title, body]
    if sys.platform.startswith("linux") and shutil.which("notify-send"):
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        if os.environ.get("DBUS_SESSION_BUS_ADDRESS") or (runtime and os.path.exists(os.path.join(runtime, "bus"))):
            clean = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return ["notify-send", "--app-name=Memoreei", "--urgency=critical", "--", title, clean]
    return None


def _run(cmd: list[str]) -> None:
    env = dict(os.environ)
    runtime = env.get("XDG_RUNTIME_DIR")
    if "DBUS_SESSION_BUS_ADDRESS" not in env and runtime:  # a user service isn't always told
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
    subprocess.run(cmd, env=env, timeout=10, capture_output=True, check=False)


async def notify(request: dict[str, Any]) -> None:
    global _last
    if time.monotonic() - _last < MIN_GAP:
        return
    cmd = _command(*message(request))
    if cmd is None:
        return
    _last = time.monotonic()
    try:
        await asyncio.to_thread(_run, cmd)
    except (OSError, subprocess.SubprocessError) as e:
        log.info("couldn't show a notification: %r", e)
