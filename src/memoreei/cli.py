from __future__ import annotations

import asyncio
import json
from typing import Optional

import typer

app = typer.Typer(help="Memoreei — personal memory MCP server CLI", invoke_without_command=True)
import_app = typer.Typer(help="Import data from various sources")
app.add_typer(import_app, name="import")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Memoreei — personal memory MCP server CLI."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


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
    import time as _time

    async def _sync_one(manager: "SyncManager", src: str, tools: "MemoryTools") -> tuple[int, float]:
        typer.echo(f"  Syncing {src}...", nl=False)
        t0 = _time.monotonic()
        try:
            count = await manager.sync_source(src, tools)
        except Exception as exc:
            elapsed = _time.monotonic() - t0
            typer.echo(f"\r  ✗ {src}: error ({elapsed:.1f}s) — {exc}")
            return 0, elapsed
        elapsed = _time.monotonic() - t0
        typer.echo(f"\r  ✓ {src}: {count} messages ({elapsed:.1f}s)")
        return count, elapsed

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
            typer.echo(f"Syncing {source}...")
            t0 = _time.monotonic()
            count = await manager.sync_source(source, tools)
            elapsed = _time.monotonic() - t0
            typer.echo(f"✓ {source}: {count} messages ({elapsed:.1f}s)")
        else:
            configured = cfg.configured_connectors()
            if not configured:
                typer.echo("No connectors configured. Run: memoreei setup")
                return
            typer.echo(f"Syncing {len(configured)} connector(s): {', '.join(configured)}\n")
            total = 0
            total_time = 0.0
            for src in configured:
                count, elapsed = await _sync_one(manager, src, tools)
                total += count
                total_time += elapsed
            typer.echo(f"\nDone: {total} messages from {len(configured)} source(s) ({total_time:.1f}s)")

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


@import_app.command(name="whatsapp")
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


@import_app.command(name="sms")
def import_sms(
    file: str = typer.Argument(..., help="Path to SMS Backup & Restore .xml file"),
) -> None:
    """Import SMS/MMS messages from an Android SMS Backup & Restore XML file."""

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

        result = await tools.import_sms_backup(file_path=file)
        typer.echo(json.dumps(result, indent=2))
        await db.close()

    asyncio.run(_run())


@import_app.command(name="discord-package")
def import_discord_package(
    path: str = typer.Argument(..., help="Path to extracted Discord data package folder or ZIP file"),
) -> None:
    """Import a Discord Data Package (GDPR export) — all channels, DMs, servers.

    Request your data at Discord Settings > Privacy & Safety > Request All of My Data.
    """

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

        result = await tools.import_discord_package_tool(package_path=path)
        typer.echo(json.dumps(result, indent=2))
        await db.close()

    asyncio.run(_run())


_CONNECTORS = {
    "gmail": {
        "name": "Gmail (IMAP)",
        "icon": "📧",
        "vars": [
            ("GMAIL_EMAIL", "Gmail address", False, "e.g. you@gmail.com"),
            ("GMAIL_APP_PASSWORD", "App Password", True,
             "Generate at https://myaccount.google.com/apppasswords (requires 2FA)"),
        ],
        "sync_name": "email",
    },
    "discord": {
        "name": "Discord (Bot API)",
        "icon": "🎮",
        "vars": [
            ("DISCORD_BOT_TOKEN", "Bot token", True, "From https://discord.com/developers/applications"),
            ("DISCORD_CHANNEL_ID", "Channel ID", False, "Right-click channel → Copy ID (enable Developer Mode)"),
        ],
    },
    "telegram": {
        "name": "Telegram",
        "icon": "✈️",
        "vars": [
            ("TELEGRAM_BOT_TOKEN", "Bot token", True, "From @BotFather on Telegram"),
            ("TELEGRAM_CHAT_ID", "Chat ID", False, "Use @userinfobot or check API updates"),
        ],
    },
    "slack": {
        "name": "Slack",
        "icon": "💬",
        "vars": [
            ("SLACK_BOT_TOKEN", "Bot token", True, "From https://api.slack.com/apps → OAuth & Permissions"),
            ("SLACK_CHANNEL_ID", "Channel ID", False, "Right-click channel → View channel details → copy ID"),
        ],
    },
    "matrix": {
        "name": "Matrix",
        "icon": "🟩",
        "vars": [
            ("MATRIX_HOMESERVER", "Homeserver URL", False, "e.g. https://matrix.org"),
            ("MATRIX_ACCESS_TOKEN", "Access token", True, "Settings → Help & About → Access Token in Element"),
            ("MATRIX_ROOM_ID", "Room ID", False, "e.g. !abc123:matrix.org"),
        ],
    },
    "mastodon": {
        "name": "Mastodon",
        "icon": "🐘",
        "vars": [
            ("MASTODON_INSTANCE", "Instance URL", False, "e.g. https://mastodon.social"),
            ("MASTODON_HASHTAG", "Hashtag to track (optional)", False, "Without the # sign"),
            ("MASTODON_ACCESS_TOKEN", "Access token", True,
             "Preferences → Development → New Application → copy token"),
        ],
    },
    "signal": {
        "name": "Signal Desktop",
        "icon": "🔒",
        "vars": [
            ("SIGNAL_DB_PATH", "Signal DB path (optional)", False,
             "Leave blank for auto-detect (~/.config/Signal/sql/db.sqlite)"),
            ("SIGNAL_CONFIG_PATH", "Signal config path (optional)", False,
             "Leave blank for auto-detect (~/.config/Signal/config.json)"),
        ],
    },
    "imessage": {
        "name": "iMessage (macOS only)",
        "icon": "🍎",
        "vars": [
            ("IMESSAGE_DB_PATH", "Messages DB path", False,
             "Default: ~/Library/Messages/chat.db"),
        ],
    },
}


def _find_env_path() -> "Path":
    from pathlib import Path

    env_path = Path(".env")
    if not env_path.exists():
        candidate = Path(__file__).parent.parent.parent.parent / ".env"
        if candidate.exists():
            env_path = candidate
    return env_path


def _read_env_lines(env_path: "Path") -> list[str]:
    if env_path.exists():
        return env_path.read_text().splitlines()
    return []


def _parse_env_vars(env_lines: list[str]) -> dict[str, str]:
    """Parse .env lines into a dict of KEY -> VALUE (non-commented, non-empty)."""
    result: dict[str, str] = {}
    for line in env_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if key and value:
                result[key] = value
    return result


def _is_connector_configured(key: str, env_vars: dict[str, str]) -> bool:
    """Check if all vars for a connector have non-empty values in env_vars."""
    info = _CONNECTORS[key]
    return all(var_name in env_vars for var_name, *_ in info["vars"])


def _write_env_updates(
    env_path: "Path", env_lines: list[str], updates: list[tuple[str, str]]
) -> None:
    for var_name, value in updates:
        found = False
        for i, line in enumerate(env_lines):
            stripped = line.lstrip("# ").strip()
            if stripped.startswith(f"{var_name}=") or stripped.startswith(f"{var_name} ="):
                env_lines[i] = f"{var_name}={value}"
                found = True
                break
        if not found:
            env_lines.append(f"{var_name}={value}")
    env_path.write_text("\n".join(env_lines) + "\n")


def _prompt_connector_vars(key: str) -> list[tuple[str, str]]:
    """Prompt the user for a single connector's variables. Returns list of (var, value)."""
    import questionary

    info = _CONNECTORS[key]
    typer.echo(f"\n  {info['icon']}  {info['name']}\n")
    updates: list[tuple[str, str]] = []

    for var_name, label, is_secret, hint in info["vars"]:
        typer.echo(f"  {typer.style(hint, dim=True)}")
        if is_secret:
            value = questionary.password(f"  {label}:").ask()
        else:
            value = questionary.text(f"  {label}:").ask()
        if value is None:
            # User pressed Ctrl-C
            raise typer.Exit(1)
        if value.strip():
            updates.append((var_name, value.strip()))
        typer.echo("")

    return updates


@app.command()
def setup(
    connector: Optional[str] = typer.Argument(
        None,
        help="Connector to configure (gmail, discord, telegram, slack, matrix, mastodon, signal, imessage). Omit to choose interactively.",
    ),
    reset: bool = typer.Option(False, "--reset", help="Clear existing values before reconfiguring"),
) -> None:
    """Interactive setup — configure connectors and write credentials to .env."""
    import questionary
    from pathlib import Path

    env_path = _find_env_path()
    env_lines = _read_env_lines(env_path)

    # Check if this is first-time setup (no .env or no DB path configured)
    existing_db_path = None
    for line in env_lines:
        stripped = line.strip()
        if stripped.startswith("MEMOREEI_DB_PATH=") and not stripped.startswith("#"):
            existing_db_path = stripped.split("=", 1)[1].strip()
            break

    if not existing_db_path:
        typer.echo("\n  🗄️  Database setup\n")
        default_path = str(Path.home() / ".memoreei" / "memoreei.db")
        db_path = questionary.text(
            "  Where should Memoreei store its database?",
            default=default_path,
        ).ask()
        if db_path is None:
            raise typer.Exit(1)
        db_path = db_path.strip() or default_path
        # Ensure parent directory exists
        db_dir = Path(db_path).expanduser().parent
        db_dir.mkdir(parents=True, exist_ok=True)
        # Prepend to env updates
        _write_env_updates(env_path, env_lines, [("MEMOREEI_DB_PATH", db_path)])
        env_lines = _read_env_lines(env_path)  # reload after write
        typer.echo(f"  ✓ Database: {db_path}\n")

    if connector:
        # Single connector mode
        key = connector.lower().replace("-", "").replace("_", "")
        if key not in _CONNECTORS:
            typer.echo(f"Unknown connector: {connector}")
            typer.echo(f"Available: {', '.join(_CONNECTORS.keys())}")
            raise typer.Exit(1)
        selected_keys = [key]
    else:
        # Interactive multi-select with spacebar
        env_vars = _parse_env_vars(env_lines)
        choices = [
            questionary.Choice(
                title=f"{info['icon']}  {info['name']}"
                + (" ✓" if _is_connector_configured(key, env_vars) else ""),
                value=key,
            )
            for key, info in _CONNECTORS.items()
        ]
        selected_keys = questionary.checkbox(
            "Select connectors to configure (space to toggle, enter to confirm):",
            choices=choices,
            instruction="",
        ).ask()
        if not selected_keys:
            typer.echo("\nNothing selected.")
            raise typer.Exit(0)

    # If --reset, remove existing vars for selected connectors from env_lines
    if reset:
        vars_to_clear = set()
        for key in selected_keys:
            for var_name, *_ in _CONNECTORS[key]["vars"]:
                vars_to_clear.add(var_name)
        env_lines = [
            line for line in env_lines
            if not any(
                line.strip().startswith(f"{v}=") or line.strip().startswith(f"{v} =")
                for v in vars_to_clear
            )
        ]

    # Prompt for each selected connector
    all_updates: list[tuple[str, str]] = []
    configured: list[str] = []

    for key in selected_keys:
        updates = _prompt_connector_vars(key)
        if updates:
            all_updates.extend(updates)
            configured.append(key)

    if not all_updates:
        typer.echo("\n  Nothing to save.")
        raise typer.Exit(0)

    _write_env_updates(env_path, env_lines, all_updates)

    typer.echo(f"\n  ✓ Saved to {env_path.resolve()}\n")
    for key in configured:
        sync_name = _CONNECTORS[key].get("sync_name", key)
        icon = _CONNECTORS[key]["icon"]
        typer.echo(f"  {icon}  Test: memoreei sync {sync_name}")
    typer.echo(f"\n  Check all: memoreei config\n")


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
