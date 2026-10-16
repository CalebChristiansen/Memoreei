"""The public listener: the gate, sign-in's place in it, and the runtime that runs them."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from starlette.testclient import TestClient

from memoreei.oauth.server import Rejected
from memoreei.public.gate import FailureLimiter, PublicGate, rpc_summary

ISSUER = "https://abcdefghjk23.devices.example.test"
RESOURCE = f"{ISSUER}/mcp"
KEY = "mem_" + "k" * 43
TOKEN = "mat_" + "t" * 43


class FakeSignIn:
    """Stands in for memoreei.oauth.server.SignIn: one good token, and its own pages."""

    issuer = ISSUER

    async def verify(self, token: str) -> dict:
        if token != TOKEN:
            raise Rejected("unknown")
        return {"kind": "oauth", "client": "Claude", "subject": "grant-1"}

    async def __call__(self, scope, receive, send):
        out = json.dumps({"signin": scope["path"], "peer": scope["state"]["memoreei_peer"]}).encode()
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": out})




# ── the gate ─────────────────────────────────────────────────────────────────


async def echo_mcp(scope, receive, send):
    """Stands in for the MCP app: says who the gate let in, and what body arrived."""
    body = b""
    while True:
        m = await receive()
        body += m.get("body", b"")
        if not m.get("more_body"):
            break
    out = json.dumps({"caller": scope["state"]["memoreei_caller"], "body": body.decode()}).encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": out})


async def keys(token: str) -> str | None:
    return "laptop" if token == KEY else None


@pytest.fixture
def gate_client():
    records: list[dict] = []

    async def record(entry: dict) -> None:
        records.append(entry)

    peer = {"value": "203.0.113.7"}
    gate = PublicGate(echo_mcp, resource=RESOURCE, keys=keys, signin=FakeSignIn(),
                      limiter=FailureLimiter((5, 60), (50, 60)), record=record,
                      peer_of=lambda scope: peer["value"])
    client = TestClient(gate, base_url="https://abcdefghjk23.devices.example.test")
    client.records, client.peer = records, peer  # type: ignore[attr-defined]
    return client


TOOL_CALL = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_memoreei", "arguments": {}}}


@pytest.mark.parametrize("path", ["/", "/admin/", "/admin", "/.env", "/AGENTS.md", "/mcp/../admin",
                                  "/.well-known/openid-configuration", "/MCP"])
def test_only_mcp_and_metadata_answer(gate_client, path):
    r = gate_client.get(path, headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 404


def test_protected_resource_metadata_names_the_issuer(gate_client):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        r = gate_client.get(path)
        assert r.status_code == 200
        assert r.json()["resource"] == RESOURCE and r.json()["authorization_servers"] == [ISSUER]


def test_no_bearer_gets_the_oauth_hint(gate_client):
    r = gate_client.post("/mcp", json=TOOL_CALL)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == (
        'Bearer resource_metadata="https://abcdefghjk23.devices.example.test/.well-known/oauth-protected-resource"')


def test_a_bad_token_says_invalid_token(gate_client):
    r = gate_client.post("/mcp", json=TOOL_CALL, headers={"Authorization": "Bearer mat_someone_elses"})
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    assert gate_client.records == []


def test_a_token_gets_in_and_is_logged(gate_client):
    r = gate_client.post("/mcp", json=TOOL_CALL, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert r.json()["caller"] == {"kind": "oauth", "client": "Claude", "subject": "grant-1"}
    assert json.loads(r.json()["body"]) == TOOL_CALL  # the body the gate read is passed on whole
    [entry] = gate_client.records
    assert entry["peer"] == "203.0.113.7" and entry["tool"] == "search_memoreei"
    assert entry["method"] == "tools/call" and entry["status"] == 200


def test_mem_keys_work_through_the_gate_too(gate_client):
    r = gate_client.post("/mcp", json=TOOL_CALL, headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200 and r.json()["caller"]["kind"] == "key"
    assert gate_client.records[0]["client"] == "laptop"
    r = gate_client.post("/mcp", json=TOOL_CALL, headers={"Authorization": "Bearer mem_wrong"})
    assert r.status_code == 401


def test_a_huge_body_is_refused(gate_client):
    r = gate_client.post("/mcp", content=b"x" * ((1 << 20) + 1), headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 413


def test_too_many_failures_from_one_address_locks_it_out_even_with_a_good_key(gate_client):
    for _ in range(5):
        assert gate_client.get("/.env").status_code == 404
    r = gate_client.post("/mcp", json=TOOL_CALL, headers={"Authorization": f"Bearer {KEY}"})
    assert r.status_code == 429 and r.headers["retry-after"] == "60"
    gate_client.peer["value"] = "198.51.100.9"   # someone else is fine
    assert gate_client.post("/mcp", json=TOOL_CALL, headers={"Authorization": f"Bearer {KEY}"}).status_code == 200


def test_successes_are_never_counted(gate_client):
    for _ in range(20):
        assert gate_client.post("/mcp", json=TOOL_CALL, headers={"Authorization": f"Bearer {KEY}"}).status_code == 200


def test_the_overall_limit_counts_everyone():
    now = [0.0]
    limiter = FailureLimiter((100, 60), (3, 60), clock=lambda: now[0])
    for i in range(3):
        limiter.fail(f"192.0.2.{i}")
    assert limiter.overall_blocked() and not limiter.address_blocked("192.0.2.0")
    now[0] += 61
    assert not limiter.overall_blocked()


@pytest.mark.parametrize("path", ["/authorize", "/token", "/register", "/.well-known/oauth-authorization-server",
                                  "/device", "/authorize/wait"])
def test_sign_in_paths_go_to_sign_in_with_the_real_address(gate_client, path):
    r = gate_client.get(path)
    assert r.status_code == 200 and r.json() == {"signin": path, "peer": "203.0.113.7"}


def test_sign_in_bodies_are_small(gate_client):
    assert gate_client.post("/register", content=b"x" * (64 << 10)).status_code == 200
    assert gate_client.post("/token", content=b"x" * ((64 << 10) + 1)).status_code == 413


def test_sign_in_paths_are_still_behind_the_address_limit(gate_client):
    for _ in range(5):
        gate_client.get("/.env")
    assert gate_client.get("/authorize").status_code == 429


def test_without_sign_in_there_is_no_metadata_and_no_hint():
    gate = PublicGate(echo_mcp, resource="", keys=keys)
    client = TestClient(gate)
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404
    assert client.get("/authorize").status_code == 404
    assert client.post("/mcp", json=TOOL_CALL, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 401
    r = client.post("/mcp", json=TOOL_CALL)
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert client.post("/mcp", json=TOOL_CALL, headers={"Authorization": f"Bearer {KEY}"}).status_code == 200


def test_rpc_summary():
    assert rpc_summary(json.dumps(TOOL_CALL).encode()) == ("tools/call", "search_memoreei")
    assert rpc_summary(json.dumps([{"method": "initialize", "id": 1}, {"method": "tools/list", "id": 2}]).encode()) \
        == ("initialize,tools/list", None)
    assert rpc_summary(b"") == (None, None) and rpc_summary(b"{oops") == (None, None)


# ── the access log ───────────────────────────────────────────────────────────


async def test_access_log_records_calls_and_first_sight(temp_db):
    entry = {"kind": "oauth", "client": "https://chatgpt.com/oauth/a/client.json", "subject": "u", "peer": "1.2.3.4",
             "method": "tools/call", "tool": "search_memoreei", "status": 200}
    await temp_db.record_access(entry, when=1000)
    await temp_db.record_access(entry, when=2000)
    [first, second] = await temp_db.list_access()
    assert first["at"] == 2000 and second["tool"] == "search_memoreei"
    [client] = await temp_db.list_access_clients()
    assert client["first_seen_at"] == 1000 and client["last_seen_at"] == 2000 and client["calls"] == 2


async def test_access_log_forgets_after_its_window(temp_db):
    entry = {"kind": "key", "client": "laptop", "status": 200}
    await temp_db.record_access(entry, when=1000)
    await temp_db.record_access(entry, keep_seconds=500, when=1000 + 3600)
    assert len(await temp_db.list_access()) == 1


# ── the runtime, end to end inside the real server ───────────────────────────


def test_plan_is_off_until_asked(monkeypatch):
    from memoreei.cloud.state import CloudState
    from memoreei.public.runtime import plan

    assert plan(CloudState()) is None
    monkeypatch.setenv("MEMOREEI_PUBLIC_PORT", "3680")
    import memoreei.config as config_module

    monkeypatch.setattr(config_module, "_config", None)
    p = plan(CloudState())
    assert p.mode == "custom" and p.host == "127.0.0.1" and p.port == 3680 and p.issuer is None
    # Knowing its public address, memoreei signs chatbots in there.
    monkeypatch.setenv("MEMOREEI_PUBLIC_MCP_URL", RESOURCE + "/")
    monkeypatch.setattr(config_module, "_config", None)
    p = plan(CloudState())
    assert p.problem is None and p.resource == RESOURCE and p.issuer == ISSUER
    for bad in ("http://memoreei.example.test/mcp", "https://memoreei.example.test/other"):
        monkeypatch.setenv("MEMOREEI_PUBLIC_MCP_URL", bad)
        monkeypatch.setattr(config_module, "_config", None)
        p = plan(CloudState())
        assert "MEMOREEI_PUBLIC_MCP_URL" in p.problem and p.issuer is None


def test_a_cloud_device_is_its_own_issuer(tmp_path):
    from memoreei.cloud.state import CloudState, Device
    from memoreei.public.runtime import plan

    state = CloudState(tmp_path / "cloud")
    state.save_device(Device("https://auth.example.test", "wss://tunnel.example.test", "abcdefghjk23.devices.example.test",
                             "d1", "laptop", 0))
    state.save_token("mdt_x")
    state.save_key(b"x")
    state.save_certificate("x")
    p = plan(state)
    assert p.mode == "cloud" and p.issuer == ISSUER and p.resource == RESOURCE


@pytest.fixture
def public_server(tmp_path, mock_embedder, monkeypatch):
    """The whole HTTP app, with MEMOREEI_PUBLIC_PORT set, as `memoreei serve --http` runs it."""
    import memoreei.server as server_module
    from memoreei.auth import KeyStore, hash_key
    from memoreei.public import runtime as runtime_module
    from memoreei.server import build_http_app
    from memoreei.storage.database import Database

    db_path = str(tmp_path / "http.db")
    monkeypatch.setenv("MEMOREEI_DB_PATH", db_path)
    monkeypatch.setenv("MEMOREEI_PUBLIC_PORT", "0")
    monkeypatch.setattr(server_module, "get_provider", lambda: mock_embedder)
    monkeypatch.setattr(server_module, "_db", None)
    monkeypatch.setattr(server_module, "_tools", None)

    async def seed() -> None:
        db = Database(db_path=db_path)
        await db.connect()
        await db.add_api_key("agent", hash_key(KEY))
        await db.close()

    asyncio.run(seed())
    store = None

    async def verify(key: str):
        nonlocal store
        store = store or KeyStore(await server_module._get_db())
        return await store.verify(key)

    with TestClient(build_http_app(verify), base_url="http://192.0.2.10:3679") as lan:
        for _ in range(200):
            rt = runtime_module.RUNTIME
            if rt is not None and rt.listener is not None:
                break
            time.sleep(0.02)
        lan.public_url = f"http://127.0.0.1:{runtime_module.RUNTIME.listener.port}"  # type: ignore[attr-defined]
        yield lan
        if server_module._db is not None:
            lan.portal.call(server_module._db.close)


MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def test_end_to_end_with_a_chunked_body_and_the_access_log(public_server):
    import memoreei.server as server_module

    def chunks():  # Node's fetch (OpenClaw) sends no Content-Length
        body = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).encode()
        yield body[:10]
        yield body[10:]

    url = public_server.public_url + "/mcp"
    r = httpx.post(url, content=chunks(), headers={**MCP_HEADERS, "Authorization": f"Bearer {KEY}"})
    assert r.status_code == 200, r.text
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {"search_memoreei", "get_context", "list_sources", "sync"}

    assert httpx.get(public_server.public_url + "/admin/").status_code == 404
    assert httpx.post(url, json={}, headers=MCP_HEADERS).status_code == 401
    # The LAN listener is as it was: no OAuth hint.
    r = public_server.post("/mcp", json={}, headers=MCP_HEADERS)
    assert r.status_code == 401 and r.headers["www-authenticate"] == 'Bearer realm="memoreei"'

    rows = public_server.portal.call(lambda: _access(server_module))
    assert rows[0]["kind"] == "key" and rows[0]["client"] == "agent" and rows[0]["method"] == "tools/list"
    assert rows[0]["peer"] == "127.0.0.1" and rows[0]["status"] == 200


async def _access(server_module):
    return await (await server_module._get_db()).list_access()


def test_the_public_server_tells_chatbots_about_sleep(public_server):
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    r = httpx.post(public_server.public_url + "/mcp", json=init,
                   headers={**MCP_HEADERS, "Authorization": f"Bearer {KEY}"})
    assert "asleep or offline" in r.json()["result"]["instructions"]
    r = public_server.post("/mcp", json=init, headers={**MCP_HEADERS, "Authorization": f"Bearer {KEY}"})
    assert "asleep or offline" not in r.json()["result"]["instructions"]


# ── memoreei's own sign-in, end to end ───────────────────────────────────────


@pytest.fixture
def public_address(monkeypatch):
    """Set before public_server starts: the address a tunnel gives this memoreei."""
    monkeypatch.setenv("MEMOREEI_PUBLIC_MCP_URL", RESOURCE)


def test_a_chatbot_signs_in_is_approved_from_the_cli_and_can_be_removed(public_address, public_server):
    import base64
    import hashlib
    import re
    import secrets
    from urllib.parse import parse_qs, urlsplit

    from typer.testing import CliRunner

    from memoreei.cli import app

    base = public_server.public_url
    meta = httpx.get(base + "/.well-known/oauth-protected-resource").json()
    assert meta["authorization_servers"] == [ISSUER]
    assert httpx.get(base + "/.well-known/oauth-authorization-server").json()["token_endpoint"] == f"{ISSUER}/token"

    reg = httpx.post(base + "/register", json={"client_name": "Test Bot",
                                               "redirect_uris": ["http://127.0.0.1:9/cb"]}).json()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = httpx.get(base + "/authorize", params={
        "response_type": "code", "client_id": reg["client_id"], "redirect_uri": "http://127.0.0.1:9/cb",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s", "resource": RESOURCE})
    assert r.status_code == 303
    wait = base + r.headers["location"]
    cookie = r.headers["set-cookie"].split(";")[0]  # a __Host- cookie: sent by hand over plain http
    page = httpx.get(wait, headers={"Cookie": cookie}).text
    code = re.search(r"<p class=code>(\w+)</p>", page).group(1)

    # The person, at the computer, in another process altogether.
    out = CliRunner().invoke(app, ["chatbots", "list"])
    assert code in out.output and "Test Bot" in out.output and "it named itself" in out.output
    out = CliRunner().invoke(app, ["chatbots", "approve", code.lower()])
    assert out.exit_code == 0 and "Approved Test Bot" in out.output

    r = httpx.get(wait, headers={"Cookie": cookie})
    assert r.status_code == 303
    auth_code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    tok = httpx.post(base + "/token", data={"grant_type": "authorization_code", "code": auth_code,
                                            "client_id": reg["client_id"], "code_verifier": verifier}).json()
    headers = {**MCP_HEADERS, "Authorization": f"Bearer {tok['access_token']}"}
    r = httpx.post(base + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                   headers=headers)
    assert r.status_code == 200, r.text

    out = CliRunner().invoke(app, ["chatbots", "list"])
    assert "Test Bot" in out.output
    grant_id = re.search(r"^  ([0-9a-f]{8})  Test Bot", out.output, re.M).group(1)
    out = CliRunner().invoke(app, ["chatbots", "remove", grant_id])
    assert out.exit_code == 0 and "Removed Test Bot" in out.output
    r = httpx.post(base + "/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                   headers=headers)
    assert r.status_code == 401  # at once, not in ten minutes


def test_the_cli_says_when_nothing_is_waiting():
    from typer.testing import CliRunner

    from memoreei.cli import app

    out = CliRunner().invoke(app, ["chatbots", "approve", "BCDF"])
    assert out.exit_code == 1 and "Nothing is waiting" in out.output
    out = CliRunner().invoke(app, ["chatbots", "list"])
    assert "No chatbots connected" in out.output
