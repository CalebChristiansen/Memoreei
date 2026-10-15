"""Memoreei Cloud from the computer's side: state, certificates, connect, the tunnel,
and the public listener run end to end inside the server."""
from __future__ import annotations

import asyncio
import datetime
import json
import os
import socket
import ssl
import stat
import sys
import time

import aiohttp
import httpx
import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from starlette.testclient import TestClient

from memoreei.cloud import certs
from memoreei.cloud.connect import connect, disconnect
from memoreei.cloud.service import CloudError, Service
from memoreei.cloud.state import CloudState, Device, tunnel_url_for
from memoreei.cloud.tunnel import Tunnel, explain
from memoreei.public.listener import Listener

AUTH = "https://auth.example.test"
HOST = "abcdefghjk23.devices.example.test"
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="file modes")


# ── a fake CA and token server ───────────────────────────────────────────────


def self_signed(hostname: str, days: int = 90, key=None) -> tuple[bytes, bytes]:
    key = key or ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()))


class FakeService:
    """The token server's device API, enough for connect and disconnect."""

    def __init__(self) -> None:
        self.approve_after = 2
        self.polls = 0
        self.decision = "allow"
        self.revoked: list[str] = []
        self.csrs: list[x509.CertificateSigningRequest] = []
        self.ca = ec.generate_private_key(ec.SECP256R1())
        self.names: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        auth = request.headers.get("authorization", "")
        if path == "/v1/device/code":
            self.names.append(dict(httpx.QueryParams(request.content.decode()))["name"])
            return httpx.Response(200, json={
                "device_code": "dc", "user_code": "ABCD-EFGH", "verification_uri": f"{AUTH}/device",
                "verification_uri_complete": f"{AUTH}/device?code=ABCD-EFGH", "expires_in": 600, "interval": 5})
        if path == "/v1/device/token":
            self.polls += 1
            if self.polls < self.approve_after:
                return httpx.Response(400, json={"error": "authorization_pending"})
            if self.decision != "allow":
                return httpx.Response(400, json={"error": "access_denied"})
            return httpx.Response(200, json={"device_token": "mdt_secret", "token_type": "Bearer",
                                             "device_id": "d1", "hostname": HOST})
        if auth != "Bearer mdt_secret" or "mdt_secret" in self.revoked:
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == "/v1/device":
            return httpx.Response(200, json={"device_id": "d1", "hostname": HOST})
        if path == "/v1/device/certificate":
            csr = x509.load_pem_x509_csr(json.loads(request.content)["csr"].encode())
            self.csrs.append(csr)
            now = datetime.datetime.now(datetime.timezone.utc)
            cert = (x509.CertificateBuilder().subject_name(csr.subject)
                    .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fake CA")]))
                    .public_key(csr.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=90))
                    .sign(self.ca, hashes.SHA256()))
            return httpx.Response(200, json={"hostname": HOST, "not_after": int(cert.not_valid_after_utc.timestamp()),
                                             "certificate_chain": cert.public_bytes(serialization.Encoding.PEM).decode()})
        if path == "/v1/device/revoke":
            self.revoked.append("mdt_secret")
            return httpx.Response(200, json={"revoked": HOST})
        return httpx.Response(404)

    def factory(self, auth_url: str) -> Service:
        return Service(auth_url, httpx.AsyncClient(transport=httpx.MockTransport(self.handler)))


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("MEMOREEI_CLOUD_URL", AUTH)
    fake = FakeService()

    async def no_wait(seconds):
        pass

    monkeypatch.setattr("memoreei.cloud.service._sleep", no_wait)
    return fake


# ── state ────────────────────────────────────────────────────────────────────


def test_the_tunnel_sits_beside_the_token_server():
    assert tunnel_url_for("https://auth.dev.memoreei.com") == "wss://tunnel.dev.memoreei.com"
    assert tunnel_url_for("https://auth.memoreei.com/") == "wss://tunnel.memoreei.com"


@posix_only
def test_secrets_are_mode_600_in_a_700_folder(tmp_path):
    state = CloudState(tmp_path / "cloud")
    state.save_token("mdt_x")
    state.save_key(b"key")
    assert stat.S_IMODE(os.stat(state.root).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(state.token_path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(state.key_path).st_mode) == 0o600
    assert state.token() == "mdt_x"
    state.forget()
    assert not state.root.exists()


def test_ready_needs_device_token_and_certificate(tmp_path):
    state = CloudState(tmp_path / "cloud")
    assert not state.ready()
    state.save_device(Device(AUTH, "wss://tunnel.example.test", HOST, "d1", "laptop", 1))
    state.save_token("mdt_x")
    assert not state.ready()
    cert, key = self_signed(HOST)
    state.save_certificate(cert.decode())
    state.save_key(key)
    assert state.ready() and state.device().mcp_url == f"https://{HOST}/mcp"


# ── certificates ─────────────────────────────────────────────────────────────


def test_the_csr_names_only_the_hostname_and_the_key_stays(tmp_path):
    state = CloudState(tmp_path / "cloud")
    key = certs.load_or_make_key(state)
    csr = x509.load_pem_x509_csr(certs.make_csr(key, HOST).encode())
    assert csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == HOST
    assert csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(
        x509.DNSName) == [HOST]
    assert certs.load_or_make_key(state).private_numbers() == key.private_numbers()  # made once


def test_renewal_is_due_two_thirds_of_the_way_through(tmp_path):
    state = CloudState(tmp_path / "cloud")
    cert, _ = self_signed(HOST, days=90)
    state.save_certificate(cert.decode())
    not_before, not_after = certs.validity(cert)
    assert certs.renew_at(state) == not_before + (not_after - not_before) * 2 // 3
    assert 59 <= (certs.renew_at(state) - time.time()) / 86400 <= 61


# ── connect and disconnect ───────────────────────────────────────────────────


async def test_connect_enrolls_then_gets_a_certificate(fake):
    seen = []
    device = await connect("Studio Mac", lambda what, detail: seen.append(what), service_for=fake.factory)
    assert seen == ["code", "approved", "certificate"]
    assert fake.names == ["Studio Mac"] and fake.polls == 2
    state = CloudState()
    assert state.ready() and state.token() == "mdt_secret"
    assert device.hostname == HOST and device.auth_url == AUTH and device.tunnel_url == "wss://tunnel.example.test"
    # The certificate is for the key made here, which never went anywhere.
    cert = x509.load_pem_x509_certificate(state.cert_path.read_bytes())
    assert cert.public_key().public_numbers() == certs.load_or_make_key(state).public_key().public_numbers()


async def test_connect_again_is_a_no_op(fake):
    await connect(None, service_for=fake.factory)
    await connect(None, service_for=fake.factory)
    assert len(fake.names) == 1 and len(fake.csrs) == 1


async def test_a_failed_certificate_is_retried_without_enrolling_again(fake):
    real = fake.handler

    def no_cert_once(request):
        if request.url.path == "/v1/device/certificate" and not getattr(fake, "failed", False):
            fake.failed = True
            return httpx.Response(429, json={"error": "slow_down", "error_description": "5 a week"},
                                  headers={"retry-after": "3600"})
        return real(request)

    fake.handler = no_cert_once
    with pytest.raises(CloudError, match="5 a week") as e:
        await connect(None, service_for=fake.factory)
    assert e.value.retry_after == 3600
    assert CloudState().device() is not None and not CloudState().ready()
    await connect(None, service_for=fake.factory)
    assert CloudState().ready() and len(fake.names) == 1


async def test_saying_no_adds_nothing(fake):
    fake.decision = "deny"
    with pytest.raises(CloudError, match="cancelled"):
        await connect(None, service_for=fake.factory)
    assert CloudState().device() is None and CloudState().token() is None


async def test_connect_refuses_beside_a_custom_issuer(fake, monkeypatch):
    monkeypatch.setenv("MEMOREEI_OAUTH_ISSUER", "https://keycloak.example.test/realms/me")
    with pytest.raises(CloudError, match="MEMOREEI_OAUTH_ISSUER"):
        await connect(None, service_for=fake.factory)
    assert fake.names == []


async def test_disconnect_revokes_then_forgets(fake):
    await connect(None, service_for=fake.factory)
    message = await disconnect(service_for=fake.factory)
    assert fake.revoked == ["mdt_secret"] and "off your Memoreei Cloud account" in message
    assert not CloudState().root.exists()


async def test_disconnect_offline_still_forgets_and_says_where_to_finish(fake):
    await connect(None, service_for=fake.factory)

    def offline(request):
        raise httpx.ConnectError("no route")

    fake.handler = offline
    message = await disconnect(service_for=fake.factory)
    assert f"{AUTH}/device" in message and not CloudState().root.exists()


# ── the tunnel, against a fake relay ─────────────────────────────────────────


class FakeRelay:
    def __init__(self) -> None:
        self.control: web.WebSocketResponse | None = None
        self.data: dict[str, asyncio.Future] = {}
        self.close_code: int | None = None
        self.online = asyncio.Event()

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if request.headers.get("Authorization") != "Bearer mdt_secret":
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        if request.path == "/v1/control":
            self.control = ws
            if self.close_code:
                await ws.close(code=self.close_code)
                return ws
            await ws.send_str(json.dumps({"type": "hello", "hostname": HOST}))
            self.online.set()
            async for _ in ws:
                pass
        else:
            conn_id = request.path.rsplit("/", 1)[1]
            done = asyncio.Event()
            self.data[conn_id].set_result((ws, done))
            await done.wait()  # call() owns the socket until it's finished with it
        return ws

    async def call(self, peer: str, payload: bytes) -> bytes:
        conn_id = f"{len(self.data):032x}"
        self.data[conn_id] = asyncio.get_running_loop().create_future()
        await self.control.send_str(json.dumps({"type": "open", "id": conn_id, "peer": peer}))
        ws, done = await asyncio.wait_for(self.data[conn_id], 5)
        await ws.send_bytes(payload)
        msg = await asyncio.wait_for(ws.receive(), 5)
        await ws.close()
        done.set()
        return msg.data


@pytest.fixture
async def relay():
    fake = FakeRelay()
    app = web.Application()
    app.router.add_route("GET", "/v1/{rest:.*}", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fake.url = f"ws://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    yield fake
    await runner.cleanup()


async def test_the_tunnel_splices_callers_and_names_them(relay):
    seen_peers = []

    async def echo(reader, writer):
        data = await reader.read(100)  # by now the tunnel has noted who this is
        seen_peers.append(tunnel.peer_for(writer.get_extra_info("peername")[1]))
        writer.write(b"pong:" + data)
        await writer.drain()
        writer.close()

    local = await asyncio.start_server(echo, "127.0.0.1", 0)
    tunnel = Tunnel(relay.url, "mdt_secret", local.sockets[0].getsockname()[1])
    task = asyncio.create_task(tunnel.run())
    try:
        await asyncio.wait_for(relay.online.wait(), 5)
        await asyncio.sleep(0.05)
        assert tunnel.state == "online" and tunnel.hostname == HOST
        assert await relay.call("203.0.113.7:51234", b"ping") == b"pong:ping"
        assert seen_peers == ["203.0.113.7:51234"]
    finally:
        task.cancel()
        local.close()


async def test_a_revoked_device_stops_for_good(relay):
    relay.close_code = 4403
    tunnel = Tunnel(relay.url, "mdt_secret", 9)
    await asyncio.wait_for(tunnel.run(), 5)
    assert tunnel.state == "revoked"


async def test_an_unknown_token_is_revoked_too(relay):
    tunnel = Tunnel(relay.url, "mdt_nope", 9)
    await asyncio.wait_for(tunnel.run(), 5)
    assert tunnel.state == "revoked"


def test_failures_are_explained_in_plain_words():
    from types import SimpleNamespace

    key = SimpleNamespace(host="tunnel.example.test", port=443, ssl=True)
    state, message = explain(aiohttp.ClientConnectorError(key, ConnectionRefusedError(111, "refused")))
    assert state == "blocked" and "port 443" in message
    state, message = explain(aiohttp.ClientConnectorError(key, socket.gaierror(-2, "Name or service not known")))
    assert state == "offline" and "online" in message
    cert_error = aiohttp.ClientConnectorCertificateError(key, ssl.SSLCertVerificationError("self-signed"))
    assert explain(cert_error)[0] == "blocked" and "TLS inspection" in explain(cert_error)[1]
    assert explain(asyncio.TimeoutError())[0] == "blocked"


# ── the listener's certificate ───────────────────────────────────────────────


async def _served_cert(port: int) -> bytes:
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx, server_hostname=HOST)
    der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    writer.close()
    return der


async def test_a_renewed_certificate_is_served_without_a_restart(tmp_path):
    async def app(scope, receive, send):
        pass

    cert, key = self_signed(HOST)
    (tmp_path / "c.pem").write_bytes(cert)
    (tmp_path / "k.pem").write_bytes(key)
    listener = Listener(app, "127.0.0.1", 0, str(tmp_path / "c.pem"), str(tmp_path / "k.pem"))
    await listener.start()
    try:
        first = await _served_cert(listener.port)
        cert2, key2 = self_signed(HOST)
        (tmp_path / "c.pem").write_bytes(cert2)
        (tmp_path / "k.pem").write_bytes(key2)
        listener.reload_certificate()
        second = await _served_cert(listener.port)
        assert first != second
        assert second == x509.load_pem_x509_certificate(cert2).public_bytes(serialization.Encoding.DER)
    finally:
        await listener.stop()


async def test_a_taken_port_fails_without_taking_the_server_down():
    async def app(scope, receive, send):
        pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        with pytest.raises(OSError):
            await Listener(app, "127.0.0.1", s.getsockname()[1]).start()
