from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP

from memoreei.config import get_config
from memoreei.search.embeddings import get_provider
from memoreei.storage.database import Database
from memoreei.sync_manager import SyncManager
from memoreei.tools.memory_tools import MemoryTools

# Lazily initialized singletons
_db: Database | None = None
_tools: MemoryTools | None = None
_sync_manager = SyncManager()


async def _get_tools() -> MemoryTools:
    global _db, _tools
    if _tools is None:
        cfg = get_config()
        _db = Database(db_path=cfg.db_path)
        await _db.connect()
        embedder = get_provider()
        _tools = MemoryTools(db=_db, embedder=embedder)
    return _tools


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[None]:  # type: ignore[type-arg]
    """Start optional background sync loop if auto_sync is enabled."""
    cfg = get_config()
    task: asyncio.Task | None = None
    if cfg.auto_sync:
        tools = await _get_tools()
        task = asyncio.create_task(_sync_manager.auto_sync_loop(tools, cfg))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


mcp = FastMCP("memoreei", lifespan=_lifespan)


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
async def refresh_memory() -> dict:
    """Trigger an immediate sync of all configured sources and return new message count."""
    tools = await _get_tools()
    count = await _sync_manager.refresh_all(tools)
    return {"status": "ok", "new_messages": count}


@mcp.tool()
async def sync_all() -> dict:
    """Sync every configured connector and return counts per source.

    Iterates over all connectors that have sufficient configuration (Discord,
    Telegram, Matrix, Slack, email, Mastodon) and syncs each one.
    """
    from memoreei.config import get_config
    tools = await _get_tools()
    cfg = get_config()
    results: dict[str, int] = {}
    for source in cfg.configured_connectors():
        results[source] = await _sync_manager.sync_source(source, tools)
    return {"status": "ok", "synced": results, "total": sum(results.values())}


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


@mcp.tool()
async def sync_imessage(chat_name: str | None = None) -> dict:
    """Sync iMessage/SMS conversations from the local macOS Messages database.

    Reads ~/Library/Messages/chat.db in read-only mode. macOS only — returns
    an error dict on other platforms without raising.

    Requires Full Disk Access granted to Terminal (or the app running this server)
    in System Settings → Privacy & Security → Full Disk Access.

    The path to chat.db can be overridden with the IMESSAGE_DB_PATH env var.

    Args:
        chat_name: Optional filter — only sync messages from this chat/contact.
                   Matches against chat_identifier (e.g. '+1234567890') or display name.
    """
    tools = await _get_tools()
    return await tools.sync_imessage_tool(chat_name=chat_name)


@mcp.tool()
async def sync_signal(conversation_id: str | None = None) -> dict:
    """Sync Signal Desktop messages from the local encrypted database.
    Requires Signal Desktop to be installed and pysqlcipher3 package.

    Reads the Signal SQLCipher database at the default OS location:
        Linux:   ~/.config/Signal/sql/db.sqlite
        macOS:   ~/Library/Application Support/Signal/sql/db.sqlite
        Windows: %APPDATA%\\Signal\\sql\\db.sqlite

    The encryption key is read from config.json in the same Signal directory.
    Override paths with SIGNAL_DB_PATH and SIGNAL_CONFIG_PATH env vars.

    Args:
        conversation_id: Optional filter for a specific conversation (ID, name,
                         phone number, or profile name).
    """
    tools = await _get_tools()
    return await tools.sync_signal_tool(conversation_id=conversation_id)


@mcp.tool()
async def import_sms_backup(file_path: str) -> dict:
    """Import SMS/MMS messages from an Android SMS Backup & Restore XML file.
    Works with the 'SMS Backup & Restore' app (most popular on Google Play).

    Args:
        file_path: Path to the XML backup file
    """
    tools = await _get_tools()
    return await tools.import_sms_backup(file_path=file_path)


@mcp.tool()
async def import_discord_package(package_path: str) -> dict:
    """Import a Discord Data Package (GDPR export). Imports all messages from all channels and DMs.

    Request your data at Discord Settings > Privacy & Safety > Request All of My Data.
    Once downloaded, extract the ZIP or pass it directly — both are supported.

    Args:
        package_path: Path to extracted data package folder or ZIP file
    """
    tools = await _get_tools()
    return await tools.import_discord_package_tool(package_path=package_path)


@mcp.tool()
async def import_messenger(data_path: str) -> dict:
    """Import Facebook Messenger messages from a data download (GDPR export, JSON format).
    Download from: Facebook Settings > Your Information > Download Your Information.
    Args:
        data_path: Path to the extracted Messenger data folder (containing messages/inbox/)
    """
    tools = await _get_tools()
    return await tools.import_messenger(data_path=data_path)


@mcp.tool()
async def import_json_file(
    file_path: str,
    content_field: str,
    sender_field: str = "",
    timestamp_field: str = "",
    source_label: str = "json-import",
) -> dict:
    """Import messages from any JSON file. Provide field names for your data format.

    Supports JSON arrays, JSON-lines format, and wrapped objects. Covers Google Chat
    takeout, Google Hangouts exports, LinkedIn data, and any custom JSON format.

    Args:
        file_path: Path to the JSON or JSON-lines file
        content_field: Field name containing the message text (required)
        sender_field: Field name containing the sender name (optional)
        timestamp_field: Field name containing the timestamp (optional, auto-detects format)
        source_label: Label to tag imported messages with (default: 'json-import')
    """
    tools = await _get_tools()
    return await tools.import_json_file(
        file_path=file_path,
        content_field=content_field,
        sender_field=sender_field,
        timestamp_field=timestamp_field,
        source_label=source_label,
    )


@mcp.tool()
async def import_csv_file(
    file_path: str,
    content_column: str,
    sender_column: str = "",
    timestamp_column: str = "",
    source_label: str = "csv-import",
) -> dict:
    """Import messages from any CSV file. Provide column names for your data format.

    Auto-detects delimiter (comma, tab, semicolon). Supports header rows.
    Covers LinkedIn exports, any spreadsheet or custom CSV format.

    Args:
        file_path: Path to the CSV, TSV, or delimited file
        content_column: Column name containing the message text (required)
        sender_column: Column name containing the sender name (optional)
        timestamp_column: Column name containing the timestamp (optional, auto-detects format)
        source_label: Label to tag imported messages with (default: 'csv-import')
    """
    tools = await _get_tools()
    return await tools.import_csv_file(
        file_path=file_path,
        content_column=content_column,
        sender_column=sender_column,
        timestamp_column=timestamp_column,
        source_label=source_label,
    )


@mcp.tool()
async def import_instagram(data_path: str) -> dict:
    """Import Instagram DMs from a data download (GDPR export, JSON format).
    Download at: Instagram Settings > Accounts Center > Your Information > Download Your Information.
    Args:
        data_path: Path to the extracted Instagram data folder (containing your_instagram_activity/)
    """
    tools = await _get_tools()
    return await tools.import_instagram(data_path=data_path)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
