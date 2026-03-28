from __future__ import annotations

import asyncio
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memoreei.tools.memory_tools import MemoryTools

_STARTUP_DELAY: float = 3.0    # seconds after startup before first sync
_PERIODIC_INTERVAL: int = 60   # seconds between background syncs
_MIN_SYNC_INTERVAL: int = 30   # skip if last sync was < this many seconds ago
_FRESHNESS_THRESHOLD: int = 60 # trigger pre-search sync if data is older than this


class SyncManager:
    """Background sync manager: startup sync + periodic incremental syncs."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self.last_sync_time: float = 0.0

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def is_stale(self, threshold: float = _FRESHNESS_THRESHOLD) -> bool:
        return time.monotonic() - self.last_sync_time > threshold

    async def _run_discord_sync(self, tools: MemoryTools) -> int:
        """Run incremental Discord sync. Returns count of new messages."""
        import os
        if not os.environ.get("DISCORD_BOT_TOKEN"):
            return 0
        try:
            result = await tools.sync_discord_tool()
            count = result.get("synced", 0)
            if count > 0:
                print(f"[sync_manager] Synced {count} new Discord messages", file=sys.stderr)
            return count
        except Exception as e:
            print(f"[sync_manager] Discord sync error: {e}", file=sys.stderr)
            return 0

    async def refresh_all(self, tools: MemoryTools) -> int:
        """Force sync all configured sources. Returns new message count, -1 if skipped."""
        now = time.monotonic()
        if now - self.last_sync_time < _MIN_SYNC_INTERVAL:
            elapsed = now - self.last_sync_time
            print(
                f"[sync_manager] Skipping refresh — last sync {elapsed:.0f}s ago "
                f"(min {_MIN_SYNC_INTERVAL}s)",
                file=sys.stderr,
            )
            return -1

        lock = self._get_lock()
        async with lock:
            # Re-check after acquiring lock
            if time.monotonic() - self.last_sync_time < _MIN_SYNC_INTERVAL:
                return -1
            total = await self._run_discord_sync(tools)
            self.last_sync_time = time.monotonic()
            return total

    async def maybe_refresh(self, tools: MemoryTools, timeout: float = 5.0) -> None:
        """Trigger a quick incremental sync if data is stale. Used before searches."""
        if not self.is_stale():
            return
        try:
            await asyncio.wait_for(self.refresh_all(tools), timeout=timeout)
        except asyncio.TimeoutError:
            print(
                f"[sync_manager] Pre-search sync timed out after {timeout:.0f}s",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"[sync_manager] Pre-search sync error: {e}", file=sys.stderr)

    async def background_loop(self, tools: MemoryTools) -> None:
        """Background coroutine: startup sync followed by periodic incremental syncs."""
        await asyncio.sleep(_STARTUP_DELAY)
        print("[sync_manager] Starting background sync loop", file=sys.stderr)

        # Startup sync
        try:
            count = await self._run_discord_sync(tools)
            self.last_sync_time = time.monotonic()
            print(f"[sync_manager] Startup sync complete: {count} new messages", file=sys.stderr)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[sync_manager] Startup sync failed: {e}", file=sys.stderr)

        # Periodic sync loop
        while True:
            try:
                await asyncio.sleep(_PERIODIC_INTERVAL)
            except asyncio.CancelledError:
                print("[sync_manager] Background loop cancelled", file=sys.stderr)
                raise

            try:
                lock = self._get_lock()
                async with lock:
                    count = await self._run_discord_sync(tools)
                    self.last_sync_time = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[sync_manager] Periodic sync error: {e}", file=sys.stderr)
