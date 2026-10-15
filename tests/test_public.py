"""The public listener: token checks, the gate, and the runtime that runs them."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from starlette.testclient import TestClient

from memoreei.public.gate import FailureLimiter, PublicGate, rpc_summary
from memoreei.public.tokens import IssuerKeys, TokenRejected, TokenVerifier, metadata_urls

ISSUER = "https://auth.example.test"
RESOURCE = "https://abcdefghjk23.devices.example.test/mcp"
KEY = "mem_" + "k" * 43


class Issuer:
    """A fake OAuth issuer: metadata, a key set, and tokens signed with its keys."""

    def __init__(self) -> None:
        self.keys: dict[str, object] = {"one": ec.generate_private_key(ec.SECP256R1())}
        self.fetches = 0

    def jwks(self) -> dict:
        out = []
        for kid, key in self.keys.items():
            jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key())) if isinstance(
                key, ec.EllipticCurvePrivateKey) else json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
            out.append({**jwk, "kid": kid, "use": "sig"})
        return {"keys": out}

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks.json"})
        if request.url.path == "/jwks.json":
            self.fetches += 1
            return httpx.Response(200, json=self.jwks())
        return httpx.Response(404)

    def http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def token(self, kid: str = "one", alg: str | None = None, **claims) -> str:
        key = self.keys[kid]
        alg = alg or ("ES256" if isinstance(key, ec.EllipticCurvePrivateKey) else "RS256")
        now = int(time.time())
        body = {"iss": ISSUER, "aud": RESOURCE, "sub": "user_01", "client_id": "https://claude.ai/oauth/x",
                "iat": now, "exp": now + 600, **claims}
        return jwt.encode({k: v for k, v in body.items() if v is not None}, key, algorithm=alg,
                          headers={"kid": kid})


@pytest.fixture
def issuer() -> Issuer:
    return Issuer()


def verifier(issuer: Issuer, clock=time.monotonic) -> TokenVerifier:
    return TokenVerifier(ISSUER, RESOURCE, IssuerKeys(ISSUER, issuer.http(), clock=clock))


# ── tokens ───────────────────────────────────────────────────────────────────


def test_metadata_urls_follow_rfc8414_then_openid():
    assert metadata_urls("https://a.test") == [
        "https://a.test/.well-known/oauth-authorization-server", "https://a.test/.well-known/openid-configuration"]
    assert metadata_urls("https://a.test/realms/me") == [
        "https://a.test/.well-known/oauth-authorization-server/realms/me",
        "https://a.test/realms/me/.well-known/openid-configuration"]


async def test_a_good_token_passes(issuer):
    claims = await verifier(issuer).verify(issuer.token())
    assert claims.subject == "user_01" and claims.client_id == "https://claude.ai/oauth/x"


@pytest.mark.parametrize("claims, why", [
    ({"aud": "https://another0device.devices.example.test/mcp"}, "another audience"),
    ({"iss": "https://evil.test"}, "another issuer"),
    ({"exp": int(time.time()) - 120}, "expired"),
    ({"nbf": int(time.time()) + 600}, "not valid yet"),
    ({"sub": None}, "sub"),
    ({"exp": None}, "exp"),
])
async def test_bad_claims_are_refused(issuer, claims, why):
    with pytest.raises(TokenRejected, match=why):
        await verifier(issuer).verify(issuer.token(**claims))


async def test_a_minute_of_clock_skew_is_fine(issuer):
    await verifier(issuer).verify(issuer.token(exp=int(time.time()) - 30))


async def test_symmetric_and_none_algorithms_are_refused(issuer):
    hs = jwt.encode({"iss": ISSUER, "aud": RESOURCE, "sub": "x", "exp": int(time.time()) + 60}, "s" * 32,
                    algorithm="HS256", headers={"kid": "one"})
    with pytest.raises(TokenRejected, match="not accepted"):
        await verifier(issuer).verify(hs)
    none = jwt.encode({"iss": ISSUER, "aud": RESOURCE, "sub": "x"}, None, algorithm="none")
    with pytest.raises(TokenRejected):
        await verifier(issuer).verify(none)
    with pytest.raises(TokenRejected, match="malformed"):
        await verifier(issuer).verify("not.a.jwt")


async def test_a_forged_signature_is_refused(issuer):
    forger = Issuer()  # same kid, different key
    with pytest.raises(TokenRejected):
        await verifier(issuer).verify(forger.token())


async def test_a_rotated_key_is_fetched_at_once_but_not_too_often(issuer):
    now = [1000.0]
    v = verifier(issuer, clock=lambda: now[0])
    await v.verify(issuer.token())
    issuer.keys["two"] = ec.generate_private_key(ec.SECP256R1())
    now[0] += 11
    await v.verify(issuer.token("two"))           # unknown kid: fetched again
    assert issuer.fetches == 2
    with pytest.raises(TokenRejected, match="unknown signing key"):
        unknown = issuer.token("two").split(".")
        unknown[0] = jwt.utils.base64url_encode(json.dumps({"alg": "ES256", "kid": "three"}).encode()).decode()
        await v.verify(".".join(unknown))  # within ten seconds: no fetch
    assert issuer.fetches == 2


async def test_an_rsa_issuer_works_too(issuer):
    issuer.keys["rsa"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert (await verifier(issuer).verify(issuer.token("rsa"))).subject == "user_01"


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
def gate_client(issuer):
    records: list[dict] = []

    async def record(entry: dict) -> None:
        records.append(entry)

    peer = {"value": "203.0.113.7"}
    gate = PublicGate(echo_mcp, resource=RESOURCE, keys=keys, tokens=verifier(issuer),
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


def test_a_bad_token_says_invalid_token(gate_client, issuer):
    r = gate_client.post("/mcp", json=TOOL_CALL,
                         headers={"Authorization": f"Bearer {issuer.token(aud='https://other/mcp')}"})
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"]
    assert gate_client.records == []


def test_a_token_gets_in_and_is_logged(gate_client, issuer):
    r = gate_client.post("/mcp", json=TOOL_CALL, headers={"Authorization": f"Bearer {issuer.token()}"})
    assert r.status_code == 200
    assert r.json()["caller"] == {"kind": "oauth", "client": "https://claude.ai/oauth/x", "subject": "user_01"}
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


def test_without_an_issuer_there_is_no_metadata_and_no_hint():
    gate = PublicGate(echo_mcp, resource="", keys=keys)
    client = TestClient(gate)
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404
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
    monkeypatch.setenv("MEMOREEI_OAUTH_ISSUER", ISSUER)
    monkeypatch.setattr(config_module, "_config", None)
    assert "MEMOREEI_PUBLIC_MCP_URL" in plan(CloudState()).problem
    monkeypatch.setenv("MEMOREEI_PUBLIC_MCP_URL", RESOURCE + "/")
    monkeypatch.setattr(config_module, "_config", None)
    p = plan(CloudState())
    assert p.problem is None and p.resource == RESOURCE and p.issuer == ISSUER


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
