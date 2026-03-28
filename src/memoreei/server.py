from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from memoreei.search.embeddings import get_provider
from memoreei.storage.database import Database
from memoreei.tools.memory_tools import MemoryTools

# Load .env from the project root (2 levels up from this file when installed)
_here = Path(__file__).parent
for candidate in [_here.parent.parent.parent / ".env", Path(".env")]:
    if candidate.exists():
        load_dotenv(candidate)
        break

mcp = FastMCP("memoreei")

# Lazily initialized singletons
_db: Database | None = None
_tools: MemoryTools | None = None


async def _get_tools() -> MemoryTools:
    global _db, _tools
    if _tools is None:
        db_path = os.environ.get("MEMOREEI_DB_PATH", "./memoreei.db")
        _db = Database(db_path=db_path)
        await _db.connect()
        embedder = get_provider()
        _tools = MemoryTools(db=_db, embedder=embedder)
    return _tools


@mcp.tool()
async def search_memory(
    query: str,
    limit: int = 10,
    source: str | None = None,
    participant: str | None = None,
    after: str | None = None,
    before: str | None = None,
) -> list[dict]:
    """Search your personal memories using hybrid keyword + semantic search.

    Args:
        query: Natural language search query
        limit: Maximum number of results to return (default: 10)
        source: Filter by data source (e.g. 'whatsapp:printer_conspiracy', 'discord:1487...')
        participant: Filter by participant name
        after: Only return memories after this date (ISO format, e.g. '2026-01-01')
        before: Only return memories before this date (ISO format, e.g. '2026-12-31')
    """
    tools = await _get_tools()
    return await tools.search_memory(
        query=query,
        limit=limit,
        source=source,
        participant=participant,
        after=after,
        before=before,
    )


@mcp.tool()
async def get_context(memory_id: str, before: int = 5, after: int = 5) -> list[dict]:
    """Get surrounding messages/context for a specific memory.

    Args:
        memory_id: The ID of the memory to get context for
        before: Number of messages before this one to include (default: 5)
        after: Number of messages after this one to include (default: 5)
    """
    tools = await _get_tools()
    return await tools.get_context(memory_id=memory_id, before=before, after=after)


@mcp.tool()
async def add_memory(
    content: str,
    source: str = "manual",
    metadata: dict | None = None,
) -> dict:
    """Add a manual memory/note to your personal memory store.

    Args:
        content: The text content to remember
        source: Source label for this memory (default: 'manual')
        metadata: Optional key-value metadata to attach
    """
    tools = await _get_tools()
    return await tools.add_memory(content=content, source=source, metadata=metadata)


@mcp.tool()
async def list_sources() -> dict:
    """List all data sources and their message counts."""
    tools = await _get_tools()
    return await tools.list_sources()


@mcp.tool()
async def ingest_whatsapp(file_path: str) -> dict:
    """Import a WhatsApp chat export .txt file into memory.

    Args:
        file_path: Absolute or relative path to the WhatsApp .txt export file
    """
    tools = await _get_tools()
    return await tools.ingest_whatsapp(file_path=file_path)


@mcp.tool()
async def sync_discord(channel_id: str | None = None) -> dict:
    """Sync recent Discord messages from the configured channel.

    Args:
        channel_id: Discord channel ID to sync (uses DISCORD_CHANNEL_ID env var if not provided)
    """
    tools = await _get_tools()
    return await tools.sync_discord_tool(channel_id=channel_id)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
