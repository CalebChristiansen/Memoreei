"""
Memoreei Discord bot — search memories by mention or !remember command.

Usage:
    @MemoryBot what did we talk about last week?
    !remember printer issues

Setup:
    DISCORD_BOT_TOKEN=... in .env (project root)
    python usecases/memorybot/bot.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import discord
from dotenv import load_dotenv

# Allow importing from project src when run directly
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
load_dotenv(PROJECT_ROOT / ".env")

from memoreei.storage.database import Database  # noqa: E402
from memoreei.search.embeddings import EmbeddingProvider  # noqa: E402
from memoreei.search.hybrid import HybridSearch  # noqa: E402

DB_PATH = PROJECT_ROOT / "memoreei.db"
MAX_RESULTS = 5
MAX_CONTENT_LEN = 280  # truncate long messages


def _ts_to_str(ts: int) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _truncate(text: str, max_len: int = MAX_CONTENT_LEN) -> str:
    return text if len(text) <= max_len else text[:max_len].rstrip() + "…"


def _format_results(results: list[dict], query: str) -> str:
    if not results:
        return f"No memories found for: **{query}**"

    lines = [f"**Memories matching \"{query}\"** ({len(results)} result{'s' if len(results) != 1 else ''})\n"]
    for i, r in enumerate(results, 1):
        source = r.get("source", "unknown")
        ts = _ts_to_str(r.get("ts", 0))
        content = _truncate(r.get("content", ""))
        participants = r.get("participants") or []
        who = ", ".join(participants[:3]) if participants else ""
        who_str = f" · {who}" if who else ""
        lines.append(f"**{i}.** `[{source}{who_str}]` {ts}")
        lines.append(f"> {content}\n")

    return "\n".join(lines)


class MemoryBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.db: Database | None = None
        self.search: HybridSearch | None = None

    async def setup_hook(self) -> None:
        self.db = Database(str(DB_PATH))
        await self.db.connect()
        embedder = EmbeddingProvider()
        self.search = HybridSearch(db=self.db, embedder=embedder)
        print(f"[memorybot] DB connected: {DB_PATH}")

    async def close(self) -> None:
        if self.db:
            await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        print(f"[memorybot] Logged in as {self.user} (id={self.user.id})")
        print("[memorybot] Listening for mentions and !remember commands")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        content = message.content.strip()
        query: str | None = None

        # Triggered by mention: @MemoryBot <query>
        if self.user and self.user.mentioned_in(message):
            # Strip the mention tag(s) from content
            query = content
            for mention in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
                query = query.replace(mention, "").strip()

        # Triggered by !remember <query>
        elif content.lower().startswith("!remember "):
            query = content[len("!remember "):].strip()

        if not query:
            return

        async with message.channel.typing():
            try:
                assert self.search is not None
                results = await self.search.search(query, limit=MAX_RESULTS)
                reply = _format_results(results, query)
            except Exception as exc:
                reply = f"Error querying memories: {exc}"

        # Discord message limit is 2000 chars
        if len(reply) > 1990:
            reply = reply[:1990] + "\n…(truncated)"

        await message.reply(reply, mention_author=False)


def main() -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("DISCORD_BOT_TOKEN not set in environment / .env")

    bot = MemoryBot()
    asyncio.run(bot.start(token))


if __name__ == "__main__":
    main()
