"""API keys and the Streamable HTTP network server."""
from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

import memoreei.server as server_module
from memoreei.auth import KeyStore, client_config_text, generate_key, hash_key, server_urls
from memoreei.cli import app
from memoreei.config import get_config
from memoreei.server import NETWORK_TOOLS, build_http_app, build_network_server
from memoreei.storage.database import Database

runner = CliRunner()

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}
TOOLS_LIST = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}


# ── Keys in the database ─────────────────────────────────────────────────────


def test_generated_keys_are_prefixed_and_unique():
    a, b = generate_key(), generate_key()
    assert a.startswith("mem_") and b.startswith("mem_")
    assert a != b
    assert len(a) > 40


async def test_key_store_accepts_good_rejects_bad(temp_db):
    key = generate_key()
    assert await temp_db.add_api_key("laptop", hash_key(key))
    store = KeyStore(temp_db)
    assert await store.verify(key) == "laptop"
    assert await store.verify(generate_key()) is None
    assert await store.verify("") is None


async def test_duplicate_key_name_refused(temp_db):
    assert await temp_db.add_api_key("laptop", hash_key(generate_key()))
    assert not await temp_db.add_api_key("laptop", hash_key(generate_key()))


async def test_revoked_key_stops_working(temp_db):
    key = generate_key()
    await temp_db.add_api_key("laptop", hash_key(key))
    store = KeyStore(temp_db)
    assert await temp_db.revoke_api_key("laptop")
    assert await store.verify(key) is None
    assert not await temp_db.revoke_api_key("laptop")


async def test_last_used_is_recorded_and_throttled(temp_db):
    key = generate_key()
    await temp_db.add_api_key("laptop", hash_key(key))
    store = KeyStore(temp_db)
    with patch.object(temp_db, "touch_api_key", wraps=temp_db.touch_api_key) as touch:
        await store.verify(key)
        await store.verify(key)
        await store.verify(key)
    assert touch.call_count == 1
    [row] = await temp_db.list_api_keys()
    assert row["last_used_at"] is not None


# ── key CLI ──────────────────────────────────────────────────────────────────


def _db_rows(sql: str) -> list[tuple]:
    conn = sqlite3.connect(get_config().db_path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_key_create_prints_key_once_and_stores_only_hash(monkeypatch):
    monkeypatch.setattr("memoreei.auth.local_ipv4_addresses", lambda: ["192.0.2.10"])
    result = runner.invoke(app, ["key", "create", "laptop"])
    assert result.exit_code == 0, result.output
    key = next(w for w in result.output.split() if w.startswith("mem_"))

    [(name, stored)] = _db_rows("SELECT name, key_hash FROM api_keys")
    assert name == "laptop"
    assert stored == hash_key(key)
    assert key not in stored

    assert "http://192.0.2.10:3679/mcp" in result.output
    assert "claude mcp add --transport http memoreei http://192.0.2.10:3679/mcp" in result.output
    assert f"Authorization: Bearer {key}" in result.output
    assert "${MEMOREEI_KEY}" in result.output


def test_key_create_duplicate_name_refused():
    assert runner.invoke(app, ["key", "create", "laptop"]).exit_code == 0
    again = runner.invoke(app, ["key", "create", "laptop"])
    assert again.exit_code == 1
    assert "already exists" in again.output
    assert len(_db_rows("SELECT name FROM api_keys")) == 1


def test_key_create_rejects_odd_names():
    assert runner.invoke(app, ["key", "create", "my laptop; rm"]).exit_code == 2


def test_key_list_shows_names_never_hashes():
    runner.invoke(app, ["key", "create", "laptop"])
    [(stored,)] = _db_rows("SELECT key_hash FROM api_keys")
    result = runner.invoke(app, ["key", "list"])
    assert result.exit_code == 0
    assert "laptop" in result.output
    assert "never" in result.output
    assert stored not in result.output
    assert "mem_" not in result.output


def test_key_revoke():
    runner.invoke(app, ["key", "create", "laptop"])
    assert runner.invoke(app, ["key", "revoke", "laptop"]).exit_code == 0
    assert _db_rows("SELECT name FROM api_keys") == []
    assert runner.invoke(app, ["key", "revoke", "laptop"]).exit_code == 1


def test_key_create_uses_public_url(monkeypatch):
    monkeypatch.setenv("MEMOREEI_PUBLIC_URL", "https://memories.example.com/")
    result = runner.invoke(app, ["key", "create", "phone"])
    assert "https://memories.example.com/mcp" in result.output
    assert "whichever address" not in result.output


def test_server_urls_one_per_address(monkeypatch):
    monkeypatch.setattr("memoreei.auth.local_ipv4_addresses", lambda: ["192.0.2.10", "198.51.100.7"])
    assert server_urls(3679, None, tls=False) == [
        "http://192.0.2.10:3679/mcp",
        "http://198.51.100.7:3679/mcp",
    ]
    assert server_urls(3679, None, tls=True)[0].startswith("https://")
    text = client_config_text("mem_x", server_urls(3679, None, tls=False), from_public_url=False)
    assert "use whichever address the client can reach" in text


# ── serve --http ─────────────────────────────────────────────────────────────


def test_serve_http_without_keys_starts_but_says_every_client_is_refused():
    # It starts so the dashboard can make the first key; /mcp still refuses everything
    # (test_no_keys_means_mcp_refuses_everything).
    with patch("uvicorn.run") as run:
        result = runner.invoke(app, ["serve", "--http"])
    assert result.exit_code == 0, result.output
    assert "no API keys yet, so every client is refused" in result.output
    run.assert_called_once()


def test_serve_http_starts_with_a_key():
    runner.invoke(app, ["key", "create", "laptop"])
    with patch("uvicorn.run") as run:
        result = runner.invoke(app, ["serve", "--http", "--port", "4123"])
    assert result.exit_code == 0, result.output
    kwargs = run.call_args.kwargs
    assert kwargs["host"] == "0.0.0.0"
    assert kwargs["port"] == 4123
    assert kwargs["ssl_certfile"] is None


def test_serve_http_defaults_to_3679():
    runner.invoke(app, ["key", "create", "laptop"])
    with patch("uvicorn.run") as run:
        runner.invoke(app, ["serve", "--http"])
    assert run.call_args.kwargs["port"] == 3679


def test_serve_http_passes_tls_through(tmp_path):
    runner.invoke(app, ["key", "create", "laptop"])
    with patch("uvicorn.run") as run:
        result = runner.invoke(
            app, ["serve", "--http", "--tls-cert", "/c.pem", "--tls-key", "/k.pem"]
        )
    assert result.exit_code == 0, result.output
    assert run.call_args.kwargs["ssl_certfile"] == "/c.pem"
    assert run.call_args.kwargs["ssl_keyfile"] == "/k.pem"


def test_serve_http_tls_needs_both_halves():
    runner.invoke(app, ["key", "create", "laptop"])
    with patch("uvicorn.run") as run:
        result = runner.invoke(app, ["serve", "--http", "--tls-cert", "/c.pem"])
    assert result.exit_code == 2
    run.assert_not_called()


def test_serve_has_no_sse_option():
    result = runner.invoke(app, ["serve", "--sse"])
    assert result.exit_code != 0


# ── The HTTP app itself ──────────────────────────────────────────────────────


@pytest.fixture
def http_client(tmp_path, mock_embedder, monkeypatch):
    """A running network app on a fresh DB with one good key and one revoked one."""
    import asyncio

    db_path = str(tmp_path / "http.db")
    monkeypatch.setenv("MEMOREEI_DB_PATH", db_path)
    monkeypatch.setattr(server_module, "get_provider", lambda: mock_embedder)
    monkeypatch.setattr(server_module, "_db", None)
    monkeypatch.setattr(server_module, "_tools", None)

    good, revoked = generate_key(), generate_key()

    async def _seed() -> None:
        db = Database(db_path=db_path)
        await db.connect()
        await db.add_api_key("laptop", hash_key(good))
        await db.add_api_key("old-phone", hash_key(revoked))
        await db.revoke_api_key("old-phone")
        await db.close()

    asyncio.run(_seed())

    store: KeyStore | None = None

    async def verify(key: str) -> str | None:
        nonlocal store
        if store is None:
            store = KeyStore(await server_module._get_db())
        return await store.verify(key)

    with TestClient(build_http_app(verify), base_url="http://192.0.2.10:3679") as client:
        client.good_key = good  # type: ignore[attr-defined]
        client.revoked_key = revoked  # type: ignore[attr-defined]
        yield client
        # The server's DB connection belongs to the client's event loop; close it there.
        if server_module._db is not None:
            client.portal.call(server_module._db.close)


def _auth(key: str) -> dict:
    return {**MCP_HEADERS, "Authorization": f"Bearer {key}"}


def test_no_header_is_401_with_bearer_challenge(http_client):
    r = http_client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == 'Bearer realm="memoreei"'
    assert r.json()["error"] == "unauthorized"
    assert "resource_metadata" not in r.headers["www-authenticate"]


def test_get_without_key_is_401(http_client):
    assert http_client.get("/mcp").status_code == 401


def test_bad_key_is_401(http_client):
    r = http_client.post("/mcp", json=INITIALIZE, headers=_auth(generate_key()))
    assert r.status_code == 401


def test_revoked_key_is_401(http_client):
    r = http_client.post("/mcp", json=INITIALIZE, headers=_auth(http_client.revoked_key))
    assert r.status_code == 401


def test_non_bearer_scheme_is_401(http_client):
    headers = {**MCP_HEADERS, "Authorization": f"Basic {http_client.good_key}"}
    assert http_client.post("/mcp", json=INITIALIZE, headers=headers).status_code == 401


def test_key_in_query_string_is_401(http_client):
    key = http_client.good_key
    for param in ("access_token", "key", "token"):
        r = http_client.post(f"/mcp?{param}={key}", json=INITIALIZE, headers=MCP_HEADERS)
        assert r.status_code == 401, param


def test_good_key_initializes_and_lists_exactly_four_tools(http_client):
    headers = _auth(http_client.good_key)
    r = http_client.post("/mcp", json=INITIALIZE, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["serverInfo"]["name"] == "memoreei"

    r = http_client.post("/mcp", json=TOOLS_LIST, headers=headers)
    assert r.status_code == 200, r.text
    names = sorted(t["name"] for t in r.json()["result"]["tools"])
    assert names == ["get_context", "list_sources", "search_memoreei", "sync"]


def test_lan_ip_host_header_is_accepted(http_client):
    """The SDK's DNS-rebinding check would reject non-localhost Hosts; it must be off."""
    for host in ("192.0.2.10:3679", "memories.example.com", "memoreei.lan:3679"):
        headers = {**_auth(http_client.good_key), "Host": host, "Origin": f"http://{host}"}
        r = http_client.post("/mcp", json=INITIALIZE, headers=headers)
        assert r.status_code == 200, (host, r.status_code, r.text)


def test_tool_call_over_http(http_client):
    headers = _auth(http_client.good_key)
    call = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "list_sources", "arguments": {}},
    }
    r = http_client.post("/mcp", json=call, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["result"]["isError"] is False


def test_local_only_tool_is_not_callable_over_http(http_client):
    headers = _auth(http_client.good_key)
    call = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "import_csv_file", "arguments": {"file_path": "/etc/passwd", "content_column": "x"}},
    }
    r = http_client.post("/mcp", json=call, headers=headers)
    body = r.json()
    assert "error" in body or body["result"]["isError"] is True


def test_last_used_updates_after_request(http_client):
    http_client.post("/mcp", json=INITIALIZE, headers=_auth(http_client.good_key))
    rows = sqlite3.connect(get_config().db_path).execute(
        "SELECT name, last_used_at FROM api_keys"
    ).fetchall()
    assert rows == [(rows[0][0], rows[0][1])]
    assert rows[0][0] == "laptop" and rows[0][1] is not None


# ── Tool surfaces ────────────────────────────────────────────────────────────


async def test_network_server_lists_exactly_four_tools():
    tools = await build_network_server().list_tools()
    assert sorted(t.name for t in tools) == ["get_context", "list_sources", "search_memoreei", "sync"]


async def test_sync_tool_takes_no_arguments():
    [sync] = [t for t in await build_network_server().list_tools() if t.name == "sync"]
    assert sync.inputSchema.get("properties", {}) == {}


async def test_network_tools_take_no_paths():
    for tool in await build_network_server().list_tools():
        props = tool.inputSchema.get("properties", {})
        assert not any("path" in p or "file" in p for p in props), tool.name


async def test_local_server_keeps_every_tool():
    names = {t.name for t in await server_module.mcp.list_tools()}
    assert {"add_memory", "import_csv_file", "sync_discord", "sync"} <= names
    assert {fn.__name__ for fn in NETWORK_TOOLS} <= names


def test_container_bridges_are_not_offered_as_server_addresses(monkeypatch):
    from memoreei import auth as auth_module

    ip_output = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: eth0    inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0\n"
        "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
        "4: br-3f2a    inet 172.18.0.1/16 scope global br-3f2a\n"
        "5: wg0    inet 203.0.113.5/32 scope global wg0\n"
    )
    monkeypatch.setattr(
        auth_module.subprocess, "run",
        lambda cmd, **kw: type("R", (), {"stdout": ip_output})(),
    )
    assert auth_module.local_ipv4_addresses() == ["192.0.2.10", "203.0.113.5"]
