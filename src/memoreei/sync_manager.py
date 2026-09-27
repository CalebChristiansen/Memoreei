from __future__ import annotations

import asyncio
import sys
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from memoreei.service._macos_access import ACCESS_OK

if TYPE_CHECKING:
    from memoreei.config import Config
    from memoreei.tools.memory_tools import MemoryTools


class SyncManager:
    """On-demand sync manager with optional background polling."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._everything_lock: asyncio.Lock | None = None
        self._last_sync: dict[str, float] = {}
        # The last sync_everything: {"finished_at": epoch seconds, "result": …}, for status.
        self.last_run: dict[str, Any] | None = None
        # Whether the last iMessage sync could read chat.db; None until one has run.
        self._imessage_ok: bool | None = None

    @property
    def running(self) -> bool:
        return self._everything_lock is not None and self._everything_lock.locked()

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
        """Sync a specific connector by name. Returns new message count. Raises on error."""
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
            elif source_name == "imessage":
                try:
                    result = await tools.sync_imessage_tool()
                    count = result.get("synced", 0)
                    if "error" in result:
                        self._imessage_ok = False
                        print(f"[sync_manager] iMessage sync error: {result['error']}", file=sys.stderr)
                    elif not self._imessage_ok:
                        # The startup check runs once, so a grant made after it would
                        # otherwise leave "missing" as the log's last word on access.
                        self._imessage_ok = True
                        print(f"{ACCESS_OK} (the iMessage sync read the Messages database)", file=sys.stderr)
                except Exception as e:
                    self._imessage_ok = False
                    print(f"[sync_manager] iMessage sync error: {e}", file=sys.stderr)
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

    async def sync_everything(self, tools: MemoryTools) -> dict[str, Any]:
        """Run every configured connector, then re-read changed registered imports.

        This is what the argument-free `sync` tool and `memoreei sync` do. It only
        refreshes sources configured on this machine; it never takes a path or a token.
        """
        from memoreei.config import get_config
        from memoreei.imports import resync_imports

        if self._everything_lock is None:
            self._everything_lock = asyncio.Lock()
        async with self._everything_lock:
            connectors: dict[str, Any] = {}
            for source in get_config().configured_connectors():
                try:
                    connectors[source] = await self.sync_source(source, tools)
                except Exception as e:
                    connectors[source] = {"error": str(e)}
            imports = await resync_imports(tools)
            self._last_sync["_all"] = time.monotonic()
            total = sum(v for v in connectors.values() if isinstance(v, int))
            total += sum(i.get("new", 0) for i in imports)
            result = {"connectors": connectors, "imports": imports, "new_messages": total}
            self.last_run = {"finished_at": time.time(), "result": result}
        return result

    async def auto_sync_loop(
        self, get_tools: Callable[[], Awaitable[MemoryTools]], get_cfg: Callable[[], Config]
    ) -> None:
        """Background sync, for as long as the server runs.

        Settings are read afresh each round, so turning AUTO_SYNC on or changing the
        interval in config.env (the dashboard does both) needs no restart.
        """
        print("[sync_manager] Background sync loop started", file=sys.stderr)
        while True:
            try:
                await asyncio.sleep(get_cfg().sync_interval)
            except asyncio.CancelledError:
                print("[sync_manager] Background loop cancelled", file=sys.stderr)
                raise
            if not get_cfg().auto_sync:
                continue
            try:
                await self.sync_everything(await get_tools())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[sync_manager] Background sync error: {e}", file=sys.stderr)
