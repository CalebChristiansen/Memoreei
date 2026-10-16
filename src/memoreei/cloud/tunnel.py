"""The tunnel to Memoreei Cloud's relay.

memoreei dials out on 443 and holds a control WebSocket open, so nobody has to
open a port. When a chatbot connects to this computer's hostname, the relay
reads only the hostname from the TLS hello and sends {"type": "open", "id",
"peer"} down the control socket. We answer with a fresh data WebSocket for that
id, and splice its bytes to the public listener on loopback, which holds the
certificate and terminates TLS itself. The relay never sees inside.

The relay knows the caller's address and we don't (here everything comes from
127.0.0.1), so each loopback connection's local port is remembered with the
peer the relay named, for the gate's rate limits and the access log.

The control socket reconnects with jittered backoff, up to a minute. A close
with 4401 or 4403 means this computer was removed from the account, and the
tunnel stops for good; 4409 means another copy of this enrollment came online.
Failures to connect are sorted into what a person can act on: no internet, a
network blocking the relay, or a network intercepting encrypted connections.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import socket
import time
from typing import Any, Callable

import aiohttp

log = logging.getLogger("memoreei.cloud")

BACKOFF_MAX = 60
STABLE_AFTER = 60


class Revoked(Exception):
    pass


def address_of(peer: Any) -> str:
    """The caller's address from the relay's "ip:port" or "[v6]:port". Limits count
    addresses: with the port left on, every connection would be a stranger."""
    if not isinstance(peer, str) or not peer:
        return "relay"
    if peer.startswith("["):
        return peer[1:].partition("]")[0] or "relay"
    host, sep, port = peer.rpartition(":")
    return host if sep and port.isdigit() and ":" not in host else peer


def explain(e: BaseException) -> tuple[str, str]:
    """(state, what to tell the person) for a failure to reach the relay."""
    dns = getattr(aiohttp, "ClientConnectorDNSError", None)
    if (dns is not None and isinstance(e, dns)) or isinstance(getattr(e, "os_error", None), socket.gaierror):
        return "offline", "Can't look up the relay's address. Is this computer online?"
    if isinstance(e, aiohttp.ClientConnectorCertificateError):
        return "blocked", ("Something on this network is intercepting encrypted connections "
                           "(TLS inspection), so the relay can't be trusted from here. "
                           "Try another network.")
    if isinstance(e, aiohttp.ClientSSLError):
        return "blocked", "This network is interfering with encrypted connections to the relay."
    if isinstance(e, aiohttp.WSServerHandshakeError):
        if e.status in (401, 403):
            return "revoked", "Memoreei Cloud doesn't know this computer any more."
        return "relay", f"The relay refused the tunnel (HTTP {e.status}); retrying."
    if isinstance(e, (aiohttp.ClientConnectorError, asyncio.TimeoutError, TimeoutError)):
        return "blocked", ("Can't connect to the relay. This network may be blocking it "
                           "(it needs port 443), or this computer is offline.")
    return "offline", f"Lost the connection to the relay ({type(e).__name__}); reconnecting."


class Tunnel:
    def __init__(self, tunnel_url: str, token: str, local_port: int,
                 on_status: Callable[[str, str], None] | None = None) -> None:
        self.url = tunnel_url.rstrip("/")
        self.token = token
        self.local_port = local_port
        self.on_status = on_status or (lambda state, message: None)
        self.peers: dict[int, str] = {}
        self.state = "connecting"
        self._message = "Connecting to the relay"
        self.hostname: str | None = None
        self._session: aiohttp.ClientSession | None = None
        self._data: set[asyncio.Task] = set()

    def _set(self, state: str, message: str) -> None:
        if (state, message) != (self.state, self._message):
            log.info("tunnel: %s: %s", state, message)
        self.state, self._message = state, message
        self.on_status(state, message)

    def peer_for(self, local_port: int) -> str | None:
        return self.peers.get(local_port)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def run(self) -> None:
        """Hold the tunnel until cancelled, or until the device is revoked."""
        backoff = 1.0
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_connect=15))
        try:
            while True:
                started = time.monotonic()
                try:
                    await self._control()
                except Revoked as e:
                    self._set("revoked", str(e))
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    state, message = explain(e)
                    if state == "revoked":
                        self._set(state, message)
                        return
                    self._set(state, message)
                if time.monotonic() - started > STABLE_AFTER:
                    backoff = 1.0
                await asyncio.sleep(backoff + random.random())
                backoff = min(backoff * 2, BACKOFF_MAX)
        finally:
            for t in list(self._data):
                t.cancel()
            await self._session.close()
            self._session = None

    async def _control(self) -> None:
        assert self._session is not None
        async with self._session.ws_connect(f"{self.url}/v1/control", headers=self.headers,
                                            heartbeat=20, autoping=True) as ws:
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    m = json.loads(msg.data)
                except ValueError:
                    continue
                if m.get("type") == "hello":
                    self.hostname = m.get("hostname")
                    self._set("online", f"Online as {self.hostname}")
                elif m.get("type") == "open" and isinstance(m.get("id"), str):
                    task = asyncio.create_task(self._serve(m["id"], address_of(m.get("peer"))))
                    self._data.add(task)
                    task.add_done_callback(self._data.discard)
            code = ws.close_code
        if code in (4401, 4403):
            raise Revoked("This computer was removed from your Memoreei Cloud account.")
        if code == 4409:
            self._set("replaced", "Another copy of this computer's enrollment came online, "
                                  "so this one stepped aside.")
            await asyncio.sleep(BACKOFF_MAX)
            return
        self._set("offline", "The relay closed the tunnel; reconnecting.")

    async def _serve(self, conn_id: str, peer: str) -> None:
        assert self._session is not None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self.local_port), 5)
        except (OSError, asyncio.TimeoutError) as e:
            log.warning("tunnel: the public listener refused: %r", e)
            return
        local = writer.get_extra_info("sockname")[1]
        self.peers[local] = peer
        try:
            async with self._session.ws_connect(f"{self.url}/v1/data/{conn_id}", headers=self.headers,
                                                max_msg_size=1 << 22) as ws:
                async def down() -> None:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            writer.write(msg.data)
                            await writer.drain()
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            break

                async def up() -> None:
                    while data := await reader.read(65536):
                        await ws.send_bytes(data)

                tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    await asyncio.sleep(0.1)  # let the other side's last bytes through
                finally:
                    for t in tasks:
                        t.cancel()
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
            log.warning("tunnel: data connection failed: %r", e)
        finally:
            self.peers.pop(local, None)
            writer.close()

    def summary(self) -> dict[str, Any]:
        return {"state": self.state, "message": self._message, "hostname": self.hostname,
                "open_connections": len(self._data)}
