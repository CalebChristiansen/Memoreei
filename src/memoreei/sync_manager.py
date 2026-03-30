from __future__ import annotations

import asyncio
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memoreei.config import Config
    from memoreei.tools.memory_tools import MemoryTools


class SyncManager:
    """On-demand sync manager with optional background polling."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._last_sync: dict[str, float] = {}

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def last_sync_time(self, source: str = "_all") -> float:
        return self._last_sync.get(source, 0.0)

    async def _run_discord_sync(self, tools: MemoryTools) -> int:
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

    async def sync_source(self, source_name: str, tools: MemoryTools) -> int:
        """Sync a specific connector by name. Returns new message count."""
        lock = self._get_lock()
        async with lock:
            count = 0
            if source_name == "discord":
                count = await self._run_discord_sync(tools)
            elif source_name == "telegram":
                try:
                    result = await tools.sync_telegram_tool()
                    count = result.get("synced", 0)
                except Exception as e:
                    print(f"[sync_manager] Telegram sync error: {e}", file=sys.stderr)
            elif source_name == "matrix":
                try:
                    result = await tools.sync_matrix_tool()
                    count = result.get("synced", 0)
                except Exception as e:
                    print(f"[sync_manager] Matrix sync error: {e}", file=sys.stderr)
            elif source_name == "slack":
                try:
                    result = await tools.sync_slack_tool()
                    count = result.get("synced", 0)
                except Exception as e:
                    print(f"[sync_manager] Slack sync error: {e}", file=sys.stderr)
            elif source_name == "email":
                try:
                    result = await tools.sync_email_tool()
                    count = result.get("synced", 0)
                except Exception as e:
                    print(f"[sync_manager] Email sync error: {e}", file=sys.stderr)
            elif source_name == "mastodon":
                try:
                    result = await tools.sync_mastodon_tool()
                    count = result.get("synced", 0)
                except Exception as e:
                    print(f"[sync_manager] Mastodon sync error: {e}", file=sys.stderr)
            else:
                print(f"[sync_manager] Unknown source: {source_name}", file=sys.stderr)
                return 0
            self._last_sync[source_name] = time.monotonic()
            return count

    async def refresh_all(self, tools: MemoryTools) -> int:
        """Sync all configured sources. Returns total new message count."""
        from memoreei.config import get_config
        cfg = get_config()
        total = 0
        for source in cfg.configured_connectors():
            total += await self.sync_source(source, tools)
        self._last_sync["_all"] = time.monotonic()
        return total

    async def auto_sync_loop(self, tools: MemoryTools, cfg: Config) -> None:
        """Optional background coroutine. Only called if config.auto_sync is True."""
        print(
            f"[sync_manager] Starting background sync loop (interval={cfg.sync_interval}s)",
            file=sys.stderr,
        )
        while True:
            try:
                await asyncio.sleep(cfg.sync_interval)
            except asyncio.CancelledError:
                print("[sync_manager] Background loop cancelled", file=sys.stderr)
                raise
            try:
                await self.refresh_all(tools)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[sync_manager] Background sync error: {e}", file=sys.stderr)
