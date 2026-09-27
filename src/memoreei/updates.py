"""Whether a newer Memoreei has been released: GitHub is asked once a day.

Only the Linux packages ask, and the dashboard shows the answer. Memoreei.app asks for
itself (macos/Memoreei/Updates.swift), and pip users have pip. It only ever tells:
updating means downloading the new package and installing it, which restarts the
server on it.
"""
from __future__ import annotations

import asyncio
import json
import re
import urllib.request
from contextlib import asynccontextmanager
from typing import AsyncIterator

from memoreei import __version__

RELEASES_API = "https://api.github.com/repos/CalebChristiansen/Memoreei/releases/latest"
CHECK_EVERY = 24 * 3600

_available: dict[str, str] | None = None


def version_key(v: str) -> tuple[int, ...]:
    """Just enough of PEP 440 for memoreei's own versions: 0.3.0, 0.3.0rc2, 0.3.1a1."""
    m = re.match(r"v?(\d+(?:\.\d+)*)(?:(a|b|rc)(\d+))?", v)
    if not m:
        return (0,)
    numbers = [int(n) for n in m.group(1).split(".")] + [0, 0]
    rank = {"a": 1, "b": 2, "rc": 3}.get(m.group(2) or "", 4)  # a final sorts after its rcs
    return (*numbers[:3], rank, int(m.group(3) or 0))


def is_newer(a: str, b: str) -> bool:
    return version_key(a) > version_key(b)


def available() -> dict[str, str] | None:
    """{"version": "0.4.1", "url": <release page>} if a newer release is out."""
    return _available


def _latest() -> dict[str, str] | None:
    request = urllib.request.Request(
        RELEASES_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": f"Memoreei/{__version__}"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        release = json.load(response)
    tag, page = release.get("tag_name"), release.get("html_url")
    if not tag or not page:
        return None
    return {"version": tag.removeprefix("v"), "url": page}


async def check() -> None:
    global _available
    try:
        latest = await asyncio.to_thread(_latest)
    except Exception:  # offline, rate-limited: try again tomorrow
        return
    _available = latest if latest and is_newer(latest["version"], __version__) else None


@asynccontextmanager
async def update_checks(enabled: bool) -> AsyncIterator[None]:
    """Check now and then daily, for as long as the server runs, if *enabled*."""

    async def loop() -> None:
        while True:
            await check()
            await asyncio.sleep(CHECK_EVERY)

    task = asyncio.create_task(loop()) if enabled else None
    try:
        yield
    finally:
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
