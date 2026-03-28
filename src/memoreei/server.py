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


@mcp.tool()
async def sync_telegram(chat_id: str | None = None) -> dict:
    """Sync new Telegram messages received by the bot into memory.

    Uses the Telegram Bot API (getUpdates) to fetch messages sent to the bot
    since the last sync. Requires TELEGRAM_BOT_TOKEN in environment.

    Args:
        chat_id: Telegram chat ID to filter (positive = user DM, negative = group).
                 Syncs all chats if not provided. Uses TELEGRAM_CHAT_ID env var as default.
    """
    tools = await _get_tools()
    return await tools.sync_telegram_tool(chat_id=chat_id)


@mcp.tool()
async def sync_matrix(room_id: str | None = None) -> dict:
    """Sync Matrix room messages into memory using the Matrix Client-Server API.

    Fetches messages from a Matrix room and stores them for search. Uses
    a per-room pagination token checkpoint to avoid re-ingesting messages.

    Requires environment variables:
        MATRIX_HOMESERVER    - e.g. https://matrix.org
        MATRIX_ACCESS_TOKEN  - user access token
        MATRIX_ROOM_ID       - default room to sync (optional if room_id provided)

    Args:
        room_id: Matrix room ID to sync (e.g. !abc123:matrix.org).
                 Uses MATRIX_ROOM_ID env var if not provided.
    """
    tools = await _get_tools()
    return await tools.sync_matrix_tool(room_id=room_id)


@mcp.tool()
async def sync_slack(channel_id: str | None = None) -> dict:
    """Sync recent Slack messages from the configured channel into memory.

    Uses the Slack Web API (conversations.history) to fetch messages since the
    last sync. Requires a bot token with channels:history and users:read scopes.

    Requires environment variables:
        SLACK_BOT_TOKEN    - Slack bot token (xoxb-...)
        SLACK_CHANNEL_ID   - default channel to sync (optional if channel_id provided)

    Args:
        channel_id: Slack channel ID to sync (e.g. C1234567890).
                    Uses SLACK_CHANNEL_ID env var if not provided.
    """
    tools = await _get_tools()
    return await tools.sync_slack_tool(channel_id=channel_id)


@mcp.tool()
async def sync_email(folder: str = "INBOX", max_emails: int = 200) -> dict:
    """Sync Gmail messages into memory via IMAP.

    Fetches emails from a Gmail folder and stores them for search. Uses a
    per-folder UID checkpoint to avoid re-ingesting messages on subsequent syncs.

    Requires environment variables:
        GMAIL_EMAIL        - Gmail address (e.g. you@gmail.com)
        GMAIL_APP_PASSWORD - Gmail App Password (required if 2FA is enabled).
                             Create one at https://myaccount.google.com/apppasswords
                             GMAIL_PASSWORD may be used instead for non-2FA accounts,
                             but Google has deprecated plain-password IMAP access.

    Args:
        folder:     IMAP folder to sync (default: 'INBOX'). Other options: '[Gmail]/Sent Mail',
                    '[Gmail]/All Mail', etc.
        max_emails: Maximum number of emails to ingest per sync (default: 200).
    """
    tools = await _get_tools()
    return await tools.sync_email_tool(folder=folder, max_emails=max_emails)


@mcp.tool()
async def sync_mastodon(
    instance: str | None = None,
    hashtag: str | None = None,
    access_token: str | None = None,
) -> dict:
    """Sync recent Mastodon posts from a public or hashtag timeline into memory.

    Uses the Mastodon REST API. Public and hashtag timelines require no authentication.
    An access token is only needed for home timeline or private accounts.

    Optional environment variables:
        MASTODON_INSTANCE     - Mastodon instance URL (default: https://mastodon.social)
        MASTODON_HASHTAG      - default hashtag to sync (without #)
        MASTODON_ACCESS_TOKEN - access token for authenticated requests (optional)

    Args:
        instance:     Mastodon instance base URL (e.g. https://fosstodon.org).
                      Uses MASTODON_INSTANCE env var if not provided.
        hashtag:      Hashtag to sync (without #, e.g. 'python').
                      Uses MASTODON_HASHTAG env var or public timeline if not provided.
        access_token: OAuth access token. Uses MASTODON_ACCESS_TOKEN env var if not provided.
    """
    tools = await _get_tools()
    return await tools.sync_mastodon_tool(
        instance=instance, hashtag=hashtag, access_token=access_token
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
