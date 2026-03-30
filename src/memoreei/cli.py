from __future__ import annotations

import asyncio
import json
from typing import Optional

import typer

app = typer.Typer(help="Memoreei — personal memory MCP server CLI")


@app.command()
def serve(
    sse: bool = typer.Option(False, "--sse", help="Use SSE transport instead of stdio"),
    port: int = typer.Option(8080, "--port", help="Port for SSE transport"),
) -> None:
    """Start the MCP server."""
    from memoreei.server import mcp

    if sse:
        mcp.run(transport="sse", port=port)
    else:
        mcp.run(transport="stdio")


@app.command()
def status() -> None:
    """Show DB stats: message counts, sources, last sync times."""

    async def _run() -> None:
        from memoreei.config import get_config
        from memoreei.search.embeddings import get_provider
        from memoreei.storage.database import Database

        cfg = get_config()
        db = Database(db_path=cfg.db_path)
        await db.connect()

        sources = await db.list_sources()
        total = sum(s.get("count", 0) for s in sources)

        typer.echo(f"DB: {cfg.db_path}")
        typer.echo(f"Total messages: {total}")
        typer.echo(f"Embedding provider: {cfg.embedding_provider}")
        typer.echo("")
        typer.echo("Sources:")
        if sources:
            for s in sources:
                typer.echo(f"  {s['source']:40s}  {s.get('count', 0):>6} messages")
        else:
            typer.echo("  (none)")

        typer.echo("")
        typer.echo(f"Configured connectors: {cfg.configured_connectors() or ['(none)']}")
        await db.close()

    asyncio.run(_run())


@app.command()
def sync(
    source: Optional[str] = typer.Argument(None, help="Source to sync (discord, telegram, matrix, slack, email, mastodon). Omit for all."),
) -> None:
    """Sync a specific source or all configured sources."""

    async def _run() -> None:
        from memoreei.config import get_config
        from memoreei.search.embeddings import get_provider
        from memoreei.storage.database import Database
        from memoreei.sync_manager import SyncManager
        from memoreei.tools.memory_tools import MemoryTools

        cfg = get_config()
        db = Database(db_path=cfg.db_path)
        await db.connect()
        embedder = get_provider()
        tools = MemoryTools(db=db, embedder=embedder)
        manager = SyncManager()

        if source:
            count = await manager.sync_source(source, tools)
            typer.echo(f"Synced {count} new messages from {source}")
        else:
            configured = cfg.configured_connectors()
            if not configured:
                typer.echo("No connectors configured.")
                return
            total = 0
            for src in configured:
                count = await manager.sync_source(src, tools)
                typer.echo(f"  {src}: {count} new messages")
                total += count
            typer.echo(f"Total: {total} new messages")

        await db.close()

    asyncio.run(_run())


@app.command()
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of results"),
    source: Optional[str] = typer.Option(None, "--source", "-s", help="Filter by source"),
) -> None:
    """Search memories from the CLI."""

    async def _run() -> None:
        from memoreei.config import get_config
        from memoreei.search.embeddings import get_provider
        from memoreei.storage.database import Database
        from memoreei.tools.memory_tools import MemoryTools

        cfg = get_config()
        db = Database(db_path=cfg.db_path)
        await db.connect()
        embedder = get_provider()
        tools = MemoryTools(db=db, embedder=embedder)

        results = await tools.search_memory(query=query, limit=limit, source=source)
        if not results:
            typer.echo("No results found.")
            return

        for i, r in enumerate(results, 1):
            typer.echo(f"\n[{i}] {r.get('source', '?')}  {r.get('timestamp', '')}")
            typer.echo(f"    {r.get('content', '')[:200]}")
            if r.get("participant"):
                typer.echo(f"    — {r['participant']}")

        await db.close()

    asyncio.run(_run())


@app.command(name="import")
def import_whatsapp(
    file: str = typer.Argument(..., help="Path to WhatsApp .txt export file"),
) -> None:
    """Import a WhatsApp chat export .txt file."""

    async def _run() -> None:
        from memoreei.config import get_config
        from memoreei.search.embeddings import get_provider
        from memoreei.storage.database import Database
        from memoreei.tools.memory_tools import MemoryTools

        cfg = get_config()
        db = Database(db_path=cfg.db_path)
        await db.connect()
        embedder = get_provider()
        tools = MemoryTools(db=db, embedder=embedder)

        result = await tools.ingest_whatsapp(file_path=file)
        typer.echo(json.dumps(result, indent=2))
        await db.close()

    asyncio.run(_run())


@app.command()
def config() -> None:
    """Show current configuration and connector readiness."""
    from memoreei.config import get_config

    cfg = get_config()

    def _show(label: str, value: object, *, mask: bool = False) -> None:
        if value:
            display = "***" if mask else str(value)
            typer.echo(f"  {label:30s} {display}")
        else:
            typer.echo(typer.style(f"  {label:30s} (not set)", fg=typer.colors.YELLOW))

    typer.echo("=== Core ===")
    _show("MEMOREEI_DB_PATH", cfg.db_path)
    _show("EMBEDDING_PROVIDER", cfg.embedding_provider)
    _show("AUTO_SYNC", cfg.auto_sync)
    _show("SYNC_INTERVAL", cfg.sync_interval)

    typer.echo("\n=== Discord ===")
    _show("DISCORD_BOT_TOKEN", cfg.discord_token, mask=True)
    _show("DISCORD_CHANNEL_ID", cfg.discord_channel_id)

    typer.echo("\n=== Telegram ===")
    _show("TELEGRAM_BOT_TOKEN", cfg.telegram_token, mask=True)
    _show("TELEGRAM_CHAT_ID", cfg.telegram_chat_id)

    typer.echo("\n=== Matrix ===")
    _show("MATRIX_HOMESERVER", cfg.matrix_homeserver)
    _show("MATRIX_ACCESS_TOKEN", cfg.matrix_access_token, mask=True)
    _show("MATRIX_ROOM_ID", cfg.matrix_room_id)

    typer.echo("\n=== Slack ===")
    _show("SLACK_BOT_TOKEN", cfg.slack_bot_token, mask=True)
    _show("SLACK_CHANNEL_ID", cfg.slack_channel_id)

    typer.echo("\n=== Gmail ===")
    _show("GMAIL_EMAIL", cfg.gmail_email)
    _show("GMAIL_APP_PASSWORD", cfg.gmail_app_password, mask=True)

    typer.echo("\n=== Mastodon ===")
    _show("MASTODON_INSTANCE", cfg.mastodon_instance)
    _show("MASTODON_HASHTAG", cfg.mastodon_hashtag)
    _show("MASTODON_ACCESS_TOKEN", cfg.mastodon_access_token, mask=True)

    typer.echo("\n=== Ready connectors ===")
    connectors = cfg.configured_connectors()
    if connectors:
        for c in connectors:
            typer.echo(typer.style(f"  ✓ {c}", fg=typer.colors.GREEN))
    else:
        typer.echo("  (none configured)")
