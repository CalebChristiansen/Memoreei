from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Optional

import typer

from memoreei.catalog import CONNECTORS as _CONNECTORS
from memoreei.catalog import is_connector_configured as _is_connector_configured
from memoreei.catalog import parse_env_vars as _parse_env_vars
from memoreei.catalog import read_env_lines as _read_env_lines
from memoreei.catalog import write_env_updates as _write_env_updates

if TYPE_CHECKING:
    from memoreei.storage.database import Database
    from memoreei.tools.memory_tools import MemoryTools

app = typer.Typer(help="Memoreei — personal memory MCP server CLI", invoke_without_command=True)
import_app = typer.Typer(help="Import data from various sources")
service_app = typer.Typer(help="Manage the Memoreei background service (macOS launchd, Linux systemd)")
key_app = typer.Typer(help="Manage API keys for the network server")
app.add_typer(import_app, name="import")
app.add_typer(service_app, name="service")
app.add_typer(key_app, name="key")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    home: Optional[str] = typer.Option(
        None,
        "--home",
        help="Memoreei's home directory, holding config.env and memoreei.db "
        "(default: $MEMOREEI_HOME, else ~/Library/Application Support/Memoreei on macOS, "
        "~/.memoreei elsewhere)",
    ),
    version: bool = typer.Option(False, "--version", help="Print the version and exit."),
) -> None:
    """Memoreei — personal memory MCP server CLI."""
    from memoreei.config import set_home

    if version:
        from memoreei import __version__

        typer.echo(f"memoreei {__version__}")
        raise typer.Exit()
    if home:
        set_home(home)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@asynccontextmanager
async def _open_tools() -> AsyncIterator["MemoryTools"]:
    """Open the configured database with the configured embedder, and close it after."""
    from memoreei.config import get_config
    from memoreei.search.embeddings import get_provider
    from memoreei.storage.database import Database
    from memoreei.tools.memory_tools import MemoryTools

    db = Database(db_path=get_config().db_path)
    await db.connect()
    try:
        yield MemoryTools(db=db, embedder=get_provider())
    finally:
        await db.close()


@asynccontextmanager
async def _open_db() -> AsyncIterator["Database"]:
    """Open the configured database without loading an embedding model."""
    from memoreei.config import get_config
    from memoreei.storage.database import Database

    db = Database(db_path=get_config().db_path)
    await db.connect()
    try:
        yield db
    finally:
        await db.close()


@app.command()
def serve(
    http: bool = typer.Option(
        False, "--http", help="Serve over the network (Streamable HTTP at /mcp) instead of stdio"
    ),
    host: Optional[str] = typer.Option(None, "--host", help="Address to bind (default: 0.0.0.0)"),
    port: Optional[int] = typer.Option(None, "--port", help="Port to listen on (default: 3679)"),
    tls_cert: Optional[str] = typer.Option(None, "--tls-cert", help="TLS certificate (PEM) for HTTPS"),
    tls_key: Optional[str] = typer.Option(None, "--tls-key", help="TLS private key (PEM) for HTTPS"),
) -> None:
    """Start the MCP server: stdio for a local client, or --http for the network."""
    import sys

    if not http:
        from memoreei.server import mcp

        mcp.run(transport="stdio")
        return

    import logging

    import uvicorn

    from memoreei.auth import KeyStore
    from memoreei.config import get_config
    from memoreei.server import _get_db, build_http_app

    cfg = get_config()
    host = host or cfg.host
    port = port or cfg.port
    tls_cert = tls_cert or cfg.tls_cert
    tls_key = tls_key or cfg.tls_key
    if bool(tls_cert) != bool(tls_key):
        typer.echo("--tls-cert and --tls-key go together; give both or neither.", err=True)
        raise typer.Exit(2)

    async def _key_names() -> list[str]:
        async with _open_db() as db:
            return [k["name"] for k in await db.list_api_keys()]

    names = asyncio.run(_key_names())
    if not names:
        # Still no way in without a key: /mcp refuses everything until one exists. The
        # server starts anyway so the dashboard can be where the first key is made.
        typer.echo(
            "memoreei: no API keys yet, so every client is refused. Create one in the "
            "dashboard, or: memoreei key create <name>",
            err=True,
        )

    if sys.platform == "darwin" and cfg.imessage_db_path:
        from memoreei.service import _macos_access as fda

        if fda.can_read_messages(cfg.imessage_db_path):
            typer.echo(fda.ACCESS_OK, err=True)
        else:
            typer.echo(
                f"{fda.ACCESS_MISSING}. iMessage sync will fail until it's granted: "
                f"{fda.how_to_grant()}",
                err=True,
            )

    store: KeyStore | None = None

    async def verify(key: str) -> str | None:
        nonlocal store
        if store is None:
            store = KeyStore(await _get_db())
        return await store.verify(key)

    # One plain line per event, for log files and grep. force=True replaces the Rich
    # handler the SDK installs, which wraps lines to the terminal width.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )
    logging.getLogger("mcp").setLevel(logging.WARNING)  # per-request chatter
    scheme = "https" if tls_cert else "http"
    typer.echo(
        f"memoreei: serving {scheme}://{host}:{port}/mcp for {len(names)} key(s): {', '.join(names)}",
        err=True,
    )
    typer.echo(
        f"memoreei: dashboard at {scheme}://localhost:{port}/admin/ "
        "(sign in with the link from: memoreei admin-url)",
        err=True,
    )
    _exit_with_parent()
    uvicorn.run(
        build_http_app(verify),
        host=host,
        port=port,
        ssl_certfile=tls_cert,
        ssl_keyfile=tls_key,
        access_log=False,  # the auth middleware logs each request with its key's name
        log_level="info",
    )


def _exit_with_parent() -> None:
    """Under Memoreei.app, stop when the app does, even if it crashed.

    The app sets MEMOREEI_PARENT_PID. Were the server to outlive it, it would hold the
    port and the app's next start would find it taken.
    """
    import os
    import signal
    import threading
    import time

    parent = os.environ.get("MEMOREEI_PARENT_PID")
    if not parent or not parent.isdigit():
        return

    def watch() -> None:
        while os.getppid() == int(parent):
            time.sleep(2)
        typer.echo("memoreei: Memoreei.app has gone; stopping", err=True)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


@app.command()
def status() -> None:
    """Show DB stats: message counts, sources, last sync times."""

    async def _run() -> None:
        from memoreei.config import get_config

        cfg = get_config()
        async with _open_db() as db:
            sources = await db.list_sources()
        total = sum(sources.values())

        typer.echo(f"DB: {cfg.db_path}")
        typer.echo(f"Total messages: {total}")
        typer.echo(f"Embedding provider: {cfg.embedding_provider}")
        typer.echo("")
        typer.echo("Sources:")
        if sources:
            for name, count in sources.items():
                typer.echo(f"  {name:40s}  {count:>6} messages")
        else:
            typer.echo("  (none)")

        typer.echo("")
        typer.echo(f"Configured connectors: {cfg.configured_connectors() or ['(none)']}")

    asyncio.run(_run())


@app.command()
def sync(
    source: Optional[str] = typer.Argument(
        None,
        help="Source to sync (discord, telegram, matrix, slack, email, mastodon, imessage). "
        "Omit to sync every configured source and re-read changed import files.",
    ),
) -> None:
    """Sync one source, or everything configured here plus registered import files."""
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
            from memoreei.imports import resync_imports

            configured = cfg.configured_connectors()
            registered = await db.list_imports()
            if not configured and not registered:
                typer.echo("Nothing to sync. Run 'memoreei setup' or 'memoreei import …' first.")
                await db.close()
                return
            total = 0
            total_time = 0.0
            if configured:
                typer.echo(f"Syncing {len(configured)} connector(s): {', '.join(configured)}\n")
                for src in configured:
                    count, elapsed = await _sync_one(manager, src, tools)
                    total += count
                    total_time += elapsed
            if registered:
                typer.echo(f"\nChecking {len(registered)} registered import(s)\n")
                for item in await resync_imports(tools):
                    label = f"#{item['id']} {item['kind']} {item['file']}"
                    if item["status"] == "imported":
                        typer.echo(f"  ✓ {label}: {item['new']} new")
                        total += item["new"]
                    elif item["status"] == "unchanged":
                        typer.echo(f"  · {label}: unchanged")
                    elif item["status"] == "missing":
                        typer.echo(f"  ✗ {label}: file missing")
                    else:
                        typer.echo(f"  ✗ {label}: {item.get('error')}")
            typer.echo(f"\nDone: {total} new messages ({total_time:.1f}s)")

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
        async with _open_tools() as tools:
            results = await tools.search_memory(query=query, limit=limit, source=source)
        if not results:
            typer.echo("No results found.")
            return

        for i, r in enumerate(results, 1):
            typer.echo(f"\n[{i}] {r.get('source', '?')}  {r.get('timestamp', '')}")
            typer.echo(f"    {r.get('content', '')[:200]}")
            if r.get("participant"):
                typer.echo(f"    — {r['participant']}")

    asyncio.run(_run())


@service_app.command(name="install")
def service_install(
    port: Optional[int] = typer.Option(None, "--port", help="Port for the network server (default: 3679)"),
) -> None:
    """Install and start the network server as a background service (macOS launchd, Linux systemd)."""
    import sys
    from pathlib import Path
    from memoreei.config import config_env_path, get_config
    from memoreei.service._detect import get_backend

    backend = get_backend()  # platform guard — exits early on unsupported OS

    env_path = config_env_path().resolve()
    if not env_path.exists():
        typer.echo(f"No config at {env_path}. Run 'memoreei setup' first.")
        raise typer.Exit(1)

    memoreei_bin = str(Path(sys.executable).parent / "memoreei")
    backend.install(memoreei_bin, env_path, port or get_config().port)

    if sys.platform == "darwin" and get_config().imessage_db_path:
        typer.echo("  iMessage needs Full Disk Access, granted once in System Settings.")
        if sys.stdin.isatty() and typer.confirm("  Walk through it now?", default=True):
            _grant_full_disk_access()
        else:
            typer.echo("  Any time: memoreei service grant-access\n")


def _grant_full_disk_access() -> None:
    """Guide the user through Full Disk Access with dialogs, then check it worked."""
    import os
    import subprocess
    from memoreei.service import _macos_access as fda
    from memoreei.service._launchd import _LABEL, _launchd_paths

    target = fda.binary_needing_access()
    name = fda.display_name(target)
    typer.echo(f"\n  Full Disk Access is needed for:\n    {target}\n")
    typer.echo("  " + fda.instructions(name).replace("\n\n", "\n  ") + "\n")

    choice = fda.dialog(fda.instructions(name), ["Cancel", "Open Settings"], "Open Settings")
    if choice == "Cancel":
        raise typer.Exit(1)
    fda.open_full_disk_access(target)
    if choice is None:
        # No dialogs possible (e.g. over SSH): the windows may still open on the Mac's screen.
        typer.echo("  Opened System Settings and Finder on the Mac's screen.")
        typer.echo("  When it's done, restart the service: memoreei service install\n")
        return

    _, plist_path, log_path = _launchd_paths()
    while True:
        done = fda.dialog(
            f"When \u201c{name}\u201d is in the Full Disk Access list and switched on, click "
            "Done. Memoreei will restart and check that it can read your messages.",
            ["Cancel", "Done"],
            "Done",
        )
        if done != "Done":
            raise typer.Exit(1)
        if not plist_path.exists():
            typer.echo("  ✓ Granted. Now start the service: memoreei service install\n")
            return
        offset = log_path.stat().st_size if log_path.exists() else 0
        subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{_LABEL}"], capture_output=True
        )
        typer.echo("  Restarting the service and checking…")
        ok = fda.wait_for_startup_report(log_path, offset)
        if ok:
            typer.echo("  ✓ Memoreei can read your messages.\n")
            fda.dialog("All set: Memoreei can read your messages.", ["OK"], "OK")
            return
        if ok is None:
            typer.echo("  Couldn't tell whether it worked. Check: memoreei service logs\n")
            return
        typer.echo(f"  ✗ Still no access. Is \u201c{name}\u201d in the list and switched on?")
        again = fda.dialog(
            f"Memoreei still can't read your messages.\n\nCheck that \u201c{name}\u201d is "
            "in the Full Disk Access list and its switch is on. If it's there, try switching "
            "it off and on again.",
            ["Cancel", "Try Again"],
            "Try Again",
            icon="caution",
        )
        if again != "Try Again":
            raise typer.Exit(1)
        fda.open_full_disk_access(target)


@service_app.command(name="grant-access")
def service_grant_access() -> None:
    """macOS: walk through granting Full Disk Access, which iMessage needs, and check it."""
    import sys

    if sys.platform != "darwin":
        typer.echo("Full Disk Access is a macOS setting; nothing to do here.")
        raise typer.Exit(0)
    _grant_full_disk_access()


@service_app.command(name="uninstall")
def service_uninstall() -> None:
    """Stop and remove the Memoreei background service."""
    from memoreei.service._detect import get_backend

    get_backend().uninstall()


@service_app.command(name="status")
def service_status() -> None:
    """Show whether the Memoreei service is running."""
    from memoreei.service._detect import get_backend

    get_backend().status()


@service_app.command(name="logs")
def service_logs(
    follow: bool = typer.Option(True, "--follow/--no-follow", help="Follow the log"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
) -> None:
    """Tail the service log (macOS) or stream journald (Linux)."""
    from memoreei.service._detect import get_backend

    get_backend().logs(follow=follow, lines=lines)


def _run_import(kind: str, path: str, options: Optional[dict] = None) -> None:
    """Import a file, register it for `memoreei sync`, and print the result."""
    from memoreei.imports import import_and_register

    async def _run() -> dict:
        async with _open_tools() as tools:
            return await import_and_register(tools, kind, path, options)

    result = asyncio.run(_run())
    typer.echo(json.dumps(result, indent=2))
    if "error" in result:
        raise typer.Exit(1)


@import_app.command(name="whatsapp")
def import_whatsapp(
    file: str = typer.Argument(..., help="Path to WhatsApp .txt export file"),
) -> None:
    """Import a WhatsApp chat export .txt file."""
    _run_import("whatsapp", file)


@import_app.command(name="sms")
def import_sms(
    file: str = typer.Argument(..., help="Path to SMS Backup & Restore .xml file"),
) -> None:
    """Import SMS/MMS messages from an Android SMS Backup & Restore XML file."""
    _run_import("sms", file)


@import_app.command(name="discord-package")
def import_discord_package(
    path: str = typer.Argument(..., help="Path to extracted Discord data package folder or ZIP file"),
) -> None:
    """Import a Discord Data Package (GDPR export) — all channels, DMs, servers.

    Request your data at Discord Settings > Privacy & Safety > Request All of My Data.
    """
    _run_import("discord-package", path)


@import_app.command(name="messenger")
def import_messenger(
    path: str = typer.Argument(..., help="Path to the extracted Messenger data folder"),
) -> None:
    """Import Facebook Messenger messages from a data download (JSON format)."""
    _run_import("messenger", path)


@import_app.command(name="instagram")
def import_instagram(
    path: str = typer.Argument(..., help="Path to the extracted Instagram data folder"),
) -> None:
    """Import Instagram DMs from a data download (JSON format)."""
    _run_import("instagram", path)


@import_app.command(name="json")
def import_json(
    file: str = typer.Argument(..., help="Path to a JSON or JSON-lines file"),
    content_field: str = typer.Option(..., "--content-field", help="Field holding the message text"),
    sender_field: str = typer.Option("", "--sender-field", help="Field holding the sender name"),
    timestamp_field: str = typer.Option("", "--timestamp-field", help="Field holding the timestamp"),
    source_label: str = typer.Option("json-import", "--source-label", help="Label for the imported messages"),
) -> None:
    """Import messages from any JSON file, given its field names."""
    _run_import(
        "json",
        file,
        {
            "content_field": content_field,
            "sender_field": sender_field,
            "timestamp_field": timestamp_field,
            "source_label": source_label,
        },
    )


@import_app.command(name="csv")
def import_csv(
    file: str = typer.Argument(..., help="Path to a CSV, TSV or other delimited file"),
    content_column: str = typer.Option(..., "--content-column", help="Column holding the message text"),
    sender_column: str = typer.Option("", "--sender-column", help="Column holding the sender name"),
    timestamp_column: str = typer.Option("", "--timestamp-column", help="Column holding the timestamp"),
    source_label: str = typer.Option("csv-import", "--source-label", help="Label for the imported messages"),
) -> None:
    """Import messages from any CSV file, given its column names."""
    _run_import(
        "csv",
        file,
        {
            "content_column": content_column,
            "sender_column": sender_column,
            "timestamp_column": timestamp_column,
            "source_label": source_label,
        },
    )


@import_app.command(name="contacts")
def import_contacts(
    file: str = typer.Argument(..., help="Path to a .vcf vCard file"),
) -> None:
    """Import contacts from a vCard (.vcf) file to resolve phone numbers to names.

    Export from macOS Contacts: File → Export → Export vCard.
    """
    _run_import("contacts-vcf", file)


def _fmt_time(ts: Optional[float]) -> str:
    import datetime as _dt

    if not ts:
        return "never"
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


@import_app.command(name="list")
def import_list() -> None:
    """List the import files that `memoreei sync` re-reads when they change."""

    async def _run() -> list[dict]:
        async with _open_db() as db:
            return await db.list_imports()

    entries = asyncio.run(_run())
    if not entries:
        typer.echo("No registered imports. Each 'memoreei import …' adds one.")
        return
    typer.echo(f"  {'ID':>4}  {'KIND':16}  {'LAST SYNCED':16}  PATH")
    for e in entries:
        typer.echo(f"  {e['id']:>4}  {e['kind']:16}  {_fmt_time(e['last_synced_at']):16}  {e['path']}")


@import_app.command(name="forget")
def import_forget(
    import_id: int = typer.Argument(..., help="ID from 'memoreei import list'"),
) -> None:
    """Stop re-reading an import file on sync. Messages already imported stay."""

    async def _run() -> bool:
        async with _open_db() as db:
            return await db.forget_import(import_id)

    if not asyncio.run(_run()):
        typer.echo(f"No registered import with ID {import_id}.")
        raise typer.Exit(1)
    typer.echo(f"  ✓ Forgot import #{import_id}.")


# ── API keys ────────────────────────────────────────────────────────────────


def _create_key(name: str) -> None:
    """Create a key and print it, once, with ready-to-paste client config."""
    from memoreei.auth import KEY_NAME_RE, client_config_text, generate_key, hash_key, server_urls
    from memoreei.config import get_config

    if not KEY_NAME_RE.match(name):
        typer.echo("Key names are letters, digits, '.', '_' or '-', up to 64 characters.")
        raise typer.Exit(2)

    key = generate_key()

    async def _run() -> bool:
        async with _open_db() as db:
            return await db.add_api_key(name, hash_key(key))

    if not asyncio.run(_run()):
        typer.echo(f"A key named '{name}' already exists. Pick another name, or revoke it first.")
        raise typer.Exit(1)

    cfg = get_config()
    urls = server_urls(cfg.port, cfg.public_url, tls=bool(cfg.tls_cert))
    typer.echo(f"\n  ✓ Created key '{name}'. This is the only time it will be shown:\n")
    typer.echo(f"    {key}\n")
    typer.echo(client_config_text(key, urls, from_public_url=bool(cfg.public_url)))
    typer.echo("")


@key_app.command(name="create")
def key_create(
    name: str = typer.Argument(..., help="A name for the client, e.g. laptop or work-desktop"),
) -> None:
    """Create an API key for one client. Shown once; only its hash is stored."""
    _create_key(name)


@key_app.command(name="list")
def key_list() -> None:
    """List API keys: name, created, last used. Never the keys themselves."""

    async def _run() -> list[dict]:
        async with _open_db() as db:
            return await db.list_api_keys()

    keys = asyncio.run(_run())
    if not keys:
        typer.echo("No API keys yet. Create one: memoreei key create <name>")
        return
    typer.echo(f"  {'NAME':24}  {'CREATED':16}  LAST USED")
    for k in keys:
        typer.echo(f"  {k['name']:24}  {_fmt_time(k['created_at']):16}  {_fmt_time(k['last_used_at'])}")


@key_app.command(name="revoke")
def key_revoke(
    name: str = typer.Argument(..., help="Name of the key to revoke"),
) -> None:
    """Revoke an API key. Clients using it are refused from their next request."""

    async def _run() -> bool:
        async with _open_db() as db:
            return await db.revoke_api_key(name)

    if not asyncio.run(_run()):
        typer.echo(f"No key named '{name}'.")
        raise typer.Exit(1)
    typer.echo(f"  ✓ Revoked '{name}'.")


def _prompt_connector_vars(key: str) -> list[tuple[str, str]]:
    """Prompt the user for a single connector's variables. Returns list of (var, value)."""
    import questionary

    info = _CONNECTORS[key]
    typer.echo(f"\n  {info['icon']}  {info['name']}\n")
    updates: list[tuple[str, str]] = []

    for var_info in info["vars"]:
        var_name, label, is_secret, hint = var_info[:4]
        var_default: str = var_info[4] if len(var_info) > 4 else ""
        typer.echo(f"  {typer.style(hint, dim=True)}")
        if is_secret:
            value = questionary.password(f"  {label}:").ask()
        else:
            value = questionary.text(f"  {label}:", default=var_default).ask()
        if value is None:
            # User pressed Ctrl-C
            raise typer.Exit(1)
        if value.strip():
            updates.append((var_name, value.strip()))
        typer.echo("")

    return updates


@app.command(name="admin-url")
def admin_url() -> None:
    """Print a one-time link that signs this browser in to the dashboard.

    The link works once, within five minutes, on the computer the server runs on (or
    from anywhere, if MEMOREEI_ADMIN_REMOTE=true, as the Docker image sets).
    """
    from memoreei.admin.auth import LOGIN_TTL, create_login_token, login_url
    from memoreei.config import get_config

    async def _run() -> str:
        async with _open_db() as db:
            return await create_login_token(db)

    cfg = get_config()
    typer.echo(login_url(asyncio.run(_run()), cfg.port, tls=bool(cfg.tls_cert)))
    typer.echo(f"Opens the dashboard once, within {LOGIN_TTL // 60} minutes.", err=True)


@app.command()
def setup(
    connector: Optional[str] = typer.Argument(
        None,
        help="Connector to configure (gmail, discord, telegram, slack, matrix, mastodon, signal, imessage). Omit to choose interactively.",
    ),
    reset: bool = typer.Option(False, "--reset", help="Clear existing values before reconfiguring"),
) -> None:
    """Interactive setup — configure connectors and write them to config.env in the home directory."""
    import questionary
    from pathlib import Path
    from memoreei.config import config_env_path, ensure_home, memoreei_home

    ensure_home()
    env_path = config_env_path()
    env_lines = _read_env_lines(env_path)

    # First-time setup: no core settings written yet
    first_time = "EMBEDDING_PROVIDER" not in _parse_env_vars(env_lines)

    if first_time:
        typer.echo("\n  🗄️  Database setup\n")
        default_path = str(memoreei_home() / "memoreei.db")
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

        # The default follows the home directory, so only a custom path is written down.
        core_updates: list[tuple[str, str]] = []
        if Path(db_path).expanduser() != Path(default_path):
            core_updates.append(("MEMOREEI_DB_PATH", db_path))

        # Embedding provider
        typer.echo("\n  🔍  Embedding provider\n")
        typer.echo("  fastembed runs fully offline (recommended). openai requires an API key.")
        provider = questionary.select(
            "  Embedding provider:",
            choices=["fastembed", "openai"],
            default="fastembed",
        ).ask()
        if provider is None:
            raise typer.Exit(1)
        core_updates.append(("EMBEDDING_PROVIDER", provider))
        if provider == "openai":
            api_key = questionary.password("  OpenAI API key:").ask()
            if api_key is None:
                raise typer.Exit(1)
            if api_key.strip():
                core_updates.append(("OPENAI_API_KEY", api_key.strip()))

        # Background auto-sync
        typer.echo("\n  🔄  Background sync\n")
        typer.echo("  Keeps your sources up to date automatically while the server runs.")
        auto_sync = questionary.confirm(
            "  Enable background auto-sync?",
            default=True,
        ).ask()
        if auto_sync is None:
            raise typer.Exit(1)
        core_updates.append(("AUTO_SYNC", "true" if auto_sync else "false"))
        if auto_sync:
            interval_str = questionary.text(
                "  Sync interval (seconds):",
                default="300",
            ).ask()
            if interval_str is None:
                raise typer.Exit(1)
            core_updates.append(("AUTO_SYNC_INTERVAL", interval_str.strip() or "300"))

        _write_env_updates(env_path, env_lines, core_updates)
        env_lines = _read_env_lines(env_path)  # reload after write
        typer.echo(f"\n  ✓ Database: {db_path}\n")

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
            _offer_first_key()
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
        _offer_first_key()
        raise typer.Exit(0)

    _write_env_updates(env_path, env_lines, all_updates)

    typer.echo(f"\n  ✓ Saved to {env_path}\n")
    for key in configured:
        sync_name = _CONNECTORS[key].get("sync_name", key)
        icon = _CONNECTORS[key]["icon"]
        typer.echo(f"  {icon}  Test: memoreei sync {sync_name}")
    typer.echo(f"\n  Check all: memoreei config\n")
    _offer_first_key()


def _offer_first_key() -> None:
    """At the end of setup, offer to create the network server's first API key."""
    import questionary

    async def _count() -> int:
        async with _open_db() as db:
            return len(await db.list_api_keys())

    if asyncio.run(_count()):
        return
    typer.echo("  🔑  Network access\n")
    typer.echo("  Other machines reach this server with an API key, one per client.")
    create = questionary.confirm("  Create the first key now?", default=True).ask()
    if not create:
        typer.echo("\n  Later: memoreei key create <name>\n")
        return
    name = questionary.text("  Name it after the client that will use it:", default="laptop").ask()
    if not name or not name.strip():
        typer.echo("\n  Later: memoreei key create <name>\n")
        return
    _create_key(name.strip())


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

    from memoreei.config import config_env_path, memoreei_home

    typer.echo("=== Core ===")
    _show("MEMOREEI_HOME", str(memoreei_home()))
    _show("Config file", str(config_env_path()) + ("" if config_env_path().exists() else " (missing)"))
    _show("MEMOREEI_DB_PATH", cfg.db_path)
    _show("EMBEDDING_PROVIDER", cfg.embedding_provider)
    _show("AUTO_SYNC", cfg.auto_sync)
    _show("SYNC_INTERVAL", cfg.sync_interval)

    typer.echo("\n=== Network server ===")
    _show("MEMOREEI_HOST", cfg.host)
    _show("MEMOREEI_PORT", cfg.port)
    _show("MEMOREEI_PUBLIC_URL", cfg.public_url)
    _show("MEMOREEI_TLS_CERT", cfg.tls_cert)
    _show("MEMOREEI_TLS_KEY", cfg.tls_key)

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
