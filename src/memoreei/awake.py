"""Stay awake while plugged in, so chatbots can reach this computer.

A sleeping computer is a silent memoreei: Claude.ai and ChatGPT can't reach it through
Memoreei Cloud or the person's own address, and say so. With MEMOREEI_STAY_AWAKE on,
the server holds off sleep while it's on mains power and something outside the
computer can reach it (Memoreei Cloud, or MEMOREEI_PUBLIC_PORT). Off by default.

  macOS  ``caffeinate -s -w <pid>``. macOS honours ``-s`` only on AC power, so on
         battery the Mac sleeps as usual, and caffeinate ends when this process does.
  Linux  ``systemd-inhibit --what=sleep --mode=block`` around ``tail --pid=<pid>``, so
         the lock ends with this process too. Held only on mains power (a computer
         with no battery always is), checked every half minute.

Closing a laptop's lid still sleeps it on both: macOS does that regardless, and logind
ignores inhibitors for the lid by default. The display still turns off.

The setting is read from config.env on each look, so ``memoreei stay-awake on``
reaches a running server without a restart.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Callable

log = logging.getLogger("memoreei.awake")

SETTING = "MEMOREEI_STAY_AWAKE"
TICK = 30
POWER_SUPPLIES = Path("/sys/class/power_supply")
WHY = "Memoreei: chatbots can reach this computer while it's awake"


def setting_on() -> bool:
    """MEMOREEI_STAY_AWAKE, from the real environment if set there, else config.env as it is now."""
    from memoreei import config

    if SETTING in config._process_env_keys:
        value = os.environ.get(SETTING, "")
    else:
        from dotenv import dotenv_values

        path = config.config_env_path()
        value = (dotenv_values(path).get(SETTING) or "") if path.is_file() else ""
    return value.strip().lower() in ("1", "true", "yes", "on")


def on_mains(root: Path = POWER_SUPPLIES) -> bool:
    """Whether this computer is plugged in. With no system battery (a desktop, a server,
    or no /sys/class/power_supply at all), it always is."""
    def read(p: Path) -> str:
        try:
            return p.read_text().strip()
        except OSError:
            return ""

    try:
        supplies = list(root.iterdir())
    except OSError:
        return True
    battery = False
    for s in supplies:
        kind = read(s / "type")
        if kind in ("Mains", "USB", "USB_C", "USB_PD") and read(s / "online") == "1":
            return True
        if kind == "Battery" and read(s / "scope") != "Device":  # a mouse's battery is "Device"
            battery = True
    return not battery


def mac_on_battery() -> bool | None:
    """macOS: whether it's on battery now, from pmset; None if pmset doesn't say."""
    import subprocess

    try:
        out = subprocess.run(["pmset", "-g", "ps"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "'Battery Power'" in out:
        return True
    if "'AC Power'" in out:
        return False
    return None


def command(platform: str, pid: int, which: Callable[[str], str | None] = shutil.which) -> list[str] | None:
    """What holds off sleep here, for as long as *pid* lives; None where nothing can."""
    if platform == "darwin":
        tool = which("caffeinate") or ("/usr/bin/caffeinate" if Path("/usr/bin/caffeinate").exists() else None)
        return [tool, "-s", "-w", str(pid)] if tool else None
    if platform.startswith("linux"):
        inhibit, tail = which("systemd-inhibit"), which("tail")
        if not (inhibit and tail):
            return None
        return [inhibit, "--what=sleep", "--who=Memoreei", f"--why={WHY}", "--mode=block",
                tail, f"--pid={pid}", "-f", "/dev/null"]
    return None


class StayAwake:
    """Holds off sleep while it's wanted, plugged in and possible, and says which."""

    def __init__(self, reachable: Callable[[], bool], *, platform: str = sys.platform,
                 setting: Callable[[], bool] = setting_on,
                 plugged_in: Callable[[], bool | None] | None = None,
                 make_command: Callable[[], list[str] | None] | None = None) -> None:
        self.reachable = reachable
        self.platform = platform
        self.setting = setting
        self.plugged_in = plugged_in or self._plugged_in
        self.make_command = make_command or (lambda: command(platform, os.getpid()))
        self.proc: asyncio.subprocess.Process | None = None
        self.state = "off"
        self.error: str | None = None
        self._poke = asyncio.Event()
        self._lock = asyncio.Lock()  # the loop and the dashboard's switch both reconcile

    def _plugged_in(self) -> bool | None:
        if self.platform == "darwin":
            battery = mac_on_battery()
            return None if battery is None else not battery
        return on_mains()

    def status(self) -> dict[str, Any]:
        """What the dashboard and `memoreei cloud status` show."""
        messages = {
            "off": "Off: this computer sleeps as usual.",
            "idle": "On, but nothing outside this computer reaches it yet, so it sleeps as usual.",
            "holding": "On: this computer stays awake while it's plugged in.",
            "battery": "On, but this computer is on battery, so it sleeps as usual until it's plugged in.",
            "unsupported": "This computer has no way for memoreei to hold off sleep.",
            "failed": f"This computer wouldn't stay awake: {self.error}",
        }
        return {"on": self.state != "off", "state": self.state, "message": messages[self.state]}

    def poke(self) -> None:
        self._poke.set()

    async def run(self) -> None:
        try:
            while True:
                await self.reconcile()
                self._poke.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._poke.wait(), TICK)
        finally:
            async with self._lock:
                await self._release()

    async def reconcile(self) -> None:
        async with self._lock:
            await self._reconcile()

    async def _reconcile(self) -> None:
        if not await asyncio.to_thread(self.setting):
            await self._release()
            self.state, self.error = "off", None
            return
        if not self.reachable():
            await self._release()
            self.state = "idle"
            return
        # macOS's caffeinate -s minds the power source itself; asking is only for what we say.
        plugged = await asyncio.to_thread(self.plugged_in)
        if plugged is False and self.platform != "darwin":
            await self._release()
            self.state = "battery"
            return
        await self._hold()
        if self.state == "holding" and plugged is False:
            self.state = "battery"

    async def _hold(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            self.state = "holding"
            return
        self.proc = None  # it ended by itself (someone killed it): hold again
        if self.state == "failed":
            return  # said why; try again once the setting, the power or the reach changes
        argv = self.make_command()
        if argv is None:
            self.state = "unsupported"
            return
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE)
        except OSError as e:
            self.state, self.error = "failed", str(e)
            log.warning("couldn't hold off sleep: %s", e)
            return
        # Refusals (no logind, polkit saying no) come back at once; give them a moment.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.proc.wait(), 1.0)
        if self.proc.returncode is None:
            self.state, self.error = "holding", None
            log.info("holding off sleep while plugged in (%s)", os.path.basename(argv[0]))
            return
        proc, self.proc = self.proc, None
        err = (await proc.stderr.read()).decode(errors="replace").strip() if proc.stderr else ""
        self.state, self.error = "failed", err or f"{os.path.basename(argv[0])} exited with {proc.returncode}"
        log.warning("couldn't hold off sleep: %s", self.error)

    async def _release(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        if proc.stderr is not None:
            await proc.stderr.read()  # to its end, so the pipe is closed with the loop still running
        log.info("no longer holding off sleep")


AWAKE: StayAwake | None = None


def status() -> dict[str, Any]:
    """The running server's keep-awake, or, outside it, what the setting says."""
    if AWAKE is not None:
        return AWAKE.status()
    return {"on": setting_on(), "state": "unknown", "message": ""}


def poke() -> None:
    if AWAKE is not None:
        AWAKE.poke()


def _reachable() -> bool:
    from memoreei.public import runtime

    rt = runtime.RUNTIME
    return rt is not None and rt.current is not None


@contextlib.asynccontextmanager
async def stay_awake() -> AsyncIterator[StayAwake]:
    """Run it for as long as the server runs."""
    global AWAKE
    awake = AWAKE = StayAwake(_reachable)
    task = asyncio.create_task(awake.run(), name="stay-awake")
    try:
        yield awake
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        AWAKE = None
