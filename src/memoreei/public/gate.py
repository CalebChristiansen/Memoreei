"""The public listener's front door.

Everything that reaches memoreei from the internet comes through here, whatever
carried it: Memoreei Cloud's relay, or an expert's own tunnel or proxy. These
paths answer and nothing else does:

  /mcp                                         the network tools, with a bearer
  /.well-known/oauth-protected-resource        RFC 9728 metadata naming this
  /.well-known/oauth-protected-resource/mcp    memoreei as its own issuer
  /authorize, /token, ... (memoreei.oauth)     the sign-in itself

  /admin/...                                   the dashboard, only once the person has
                                               set a password and switched on "from
                                               anywhere" (memoreei.admin.account)

Everything else is 404, /admin too until it's opened, and so is any file a
scanner fishes for (they ask for /.env within seconds of a hostname appearing in
certificate logs). The dashboard that answers here is its own copy, which knows it
came through this door: nothing here is ever "on the computer", though every
request the relay's tunnel carries arrives from 127.0.0.1 (memoreei.admin.auth).

A bearer is either one of memoreei's own ``mem_`` keys or an access token from
memoreei's own sign-in (memoreei.oauth). Without one, /mcp answers 401 with
``WWW-Authenticate: Bearer resource_metadata=...``, which is how Claude.ai and
ChatGPT find where to sign in. That hint is what the LAN listener leaves out on
purpose, and why this is a separate front door rather than a setting on it.
Sign-in exists only when memoreei knows its public address (Memoreei Cloud's
hostname, or MEMOREEI_PUBLIC_MCP_URL); without it, ``mem_`` keys are the only way in.

Failed requests (no bearer, a bad one, or a path that doesn't exist) count
against the caller's address and against everyone; past either limit the answer
is 429. A caller over its own limit is refused before its bearer is looked at,
so a limit can't be used to test keys. Successful calls are never counted:
Claude's and OpenAI's servers share addresses, and a busy chatbot isn't an
attacker.
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable

from memoreei.auth import KEY_PREFIX, Verifier, _bearer_token
from memoreei.oauth.server import PATHS as SIGNIN_PATHS
from memoreei.oauth.server import Rejected, SignIn

log = logging.getLogger("memoreei.public")

PRM_PATHS = ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp")
MAX_BODY = 1 << 20
MAX_SIGNIN_BODY = 64 << 10  # sign-in's forms and registrations are a few hundred bytes
MAX_ADMIN_BODY = 1 << 20
PER_ADDRESS = (20, 60)  # failures, seconds
OVERALL = (120, 60)


class FailureLimiter:
    """Counts failures in a sliding window, per address and in all."""

    def __init__(self, per_address: tuple[int, int] = PER_ADDRESS, overall: tuple[int, int] = OVERALL,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.per_address, self.overall, self.clock = per_address, overall, clock
        self._by_address: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    @staticmethod
    def _trim(q: deque[float], now: float, window: int) -> None:
        while q and q[0] <= now - window:
            q.popleft()

    def address_blocked(self, address: str) -> bool:
        q = self._by_address.get(address)
        if q is None:
            return False
        self._trim(q, self.clock(), self.per_address[1])
        if not q:
            del self._by_address[address]
            return False
        return len(q) >= self.per_address[0]

    def overall_blocked(self) -> bool:
        self._trim(self._all, self.clock(), self.overall[1])
        return len(self._all) >= self.overall[0]

    def fail(self, address: str) -> None:
        now = self.clock()
        self._by_address.setdefault(address, deque()).append(now)
        self._all.append(now)
        if len(self._by_address) > 10_000:  # a sweep of addresses: drop the quiet ones
            for addr in [a for a, q in self._by_address.items() if not q or q[-1] <= now - self.per_address[1]]:
                del self._by_address[addr]


Caller = dict[str, Any]
Recorder = Callable[[Caller], Awaitable[None]]


def rpc_summary(body: bytes) -> tuple[str | None, str | None]:
    """The JSON-RPC method(s) in a request body, and the tool if it calls one. Best effort."""
    try:
        msg = json.loads(body) if body else None
    except ValueError:
        return None, None
    msgs = msg if isinstance(msg, list) else [msg]
    methods, tools = [], []
    for m in msgs:
        if not isinstance(m, dict) or not isinstance(m.get("method"), str):
            continue
        methods.append(m["method"][:64])
        params = m.get("params")
        if m["method"] == "tools/call" and isinstance(params, dict) and isinstance(params.get("name"), str):
            tools.append(params["name"][:64])
    return (",".join(methods) or None), (",".join(tools) or None)


class PublicGate:
    def __init__(
        self,
        mcp_app: Any,
        *,
        resource: str,
        keys: Verifier,
        signin: SignIn | None = None,
        limiter: FailureLimiter | None = None,
        record: Recorder | None = None,
        peer_of: Callable[[dict], str] | None = None,
        resource_name: str = "memoreei",
        admin: Any = None,
        admin_open: Callable[[], Awaitable[bool]] | None = None,
    ) -> None:
        self.mcp_app = mcp_app
        self.resource = resource
        origin = resource[: -len("/mcp")] if resource.endswith("/mcp") else resource.rstrip("/")
        self.metadata_url = origin + PRM_PATHS[0]
        self.keys, self.signin = keys, signin
        self.limiter = limiter or FailureLimiter()
        self.record = record
        self.peer_of = peer_of or (lambda scope: (scope.get("client") or ("?", 0))[0])
        self.resource_name = resource_name
        self.admin, self.admin_open = admin, admin_open  # the dashboard's public copy, and whether it's open

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "lifespan":  # the runtime runs the MCP app's lifespan itself
            while (await receive())["type"] != "lifespan.shutdown":
                await send({"type": "lifespan.startup.complete"})
            await send({"type": "lifespan.shutdown.complete"})
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        peer = self.peer_of(scope)
        method, path = scope.get("method", "?"), scope.get("path", "?")

        if self.limiter.address_blocked(peer):
            log.warning("%s %s from %s refused: too many failures", method, path, peer)
            return await _reply(send, 429, {"error": "slow_down"}, [(b"retry-after", b"60")])

        if path in PRM_PATHS and method in ("GET", "HEAD") and self.signin is not None:
            return await _reply(send, 200, {
                "resource": self.resource,
                "authorization_servers": [self.signin.issuer],
                "bearer_methods_supported": ["header"],
                "resource_name": self.resource_name,
            }, [(b"cache-control", b"public, max-age=300")])
        if path in SIGNIN_PATHS and self.signin is not None:
            body, too_big = await _read_body(receive, MAX_SIGNIN_BODY)
            if too_big:
                self.limiter.fail(peer)
                return await _reply(send, 413, {"error": "request too large"})
            scope.setdefault("state", {})["memoreei_peer"] = peer  # sign-in logs what matters itself
            return await self.signin(scope, _replay(body, receive), send)
        if (path == "/admin" or path.startswith("/admin/")) and self.admin is not None \
                and self.admin_open is not None and await self.admin_open():
            return await self._dashboard(scope, receive, send, peer)
        if path != "/mcp":
            self.limiter.fail(peer)
            log.info("%s %s from %s: 404", method, path[:120], peer)
            return await _reply(send, 404, {"error": "not_found"})

        body, too_big = await _read_body(receive)
        if too_big:
            return await _reply(send, 413, {"error": "request too large"})

        token = _bearer_token(scope.get("headers", []))
        caller: Caller | None = None
        why = "no bearer"
        if token and token.startswith(KEY_PREFIX):
            name = await self.keys(token)
            caller = {"kind": "key", "client": name, "subject": None} if name else None
            why = "invalid key"
        elif token and self.signin is not None:
            try:
                caller = await self.signin.verify(token)
            except Rejected as e:
                why = f"token {e}"
        elif token:
            why = "a token, but sign-in isn't set up (no public address)"

        if caller is None:
            self.limiter.fail(peer)
            log.warning("%s %s from %s rejected: %s", method, path, peer, why)
            if self.limiter.overall_blocked():
                return await _reply(send, 429, {"error": "slow_down"}, [(b"retry-after", b"60")])
            challenge = "Bearer"
            if self.signin is not None:
                challenge += f' resource_metadata="{self.metadata_url}"'
            if token:
                challenge += ', error="invalid_token"'
            return await _reply(send, 401, {"error": "invalid_token" if token else "unauthorized",
                                            "error_description": why},
                                [(b"www-authenticate", challenge.encode())])

        rpc, tool = rpc_summary(body)
        status = 0
        replay = _replay(body, receive)

        async def watch(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        scope.setdefault("state", {})["memoreei_caller"] = caller
        try:
            await self.mcp_app(scope, replay, watch)
        finally:
            log.info("%s %s %d from %s %s=%s%s", method, path, status, peer, caller["kind"],
                     caller["client"], f" tool={tool}" if tool else "")
            if self.record is not None:
                try:
                    await self.record({**caller, "peer": peer, "method": rpc, "tool": tool, "status": status})
                except Exception as e:  # the access log never takes a call down with it
                    log.warning("access log: %r", e)


    async def _dashboard(self, scope: dict, receive: Callable, send: Callable, peer: str) -> None:
        """The dashboard, mounted at /admin as on the LAN listener. Its guard asks for
        the password; its sign-in counts failures itself (memoreei.admin.account)."""
        if scope["path"] == "/admin":
            location = b"/admin/"
            await send({"type": "http.response.start", "status": 307,
                        "headers": [(b"location", location), (b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        body, too_big = await _read_body(receive, MAX_ADMIN_BODY)
        if too_big:
            self.limiter.fail(peer)
            return await _reply(send, 413, {"error": "request too large"})
        scope.setdefault("state", {})["memoreei_peer"] = peer
        status = 0

        async def watch(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.admin({**scope, "root_path": scope.get("root_path", "") + "/admin"},
                             _replay(body, receive), watch)
        finally:
            log.info("%s %s %d from %s (dashboard)", scope.get("method", "?"), scope["path"][:120], status, peer)


def _replay(body: bytes, receive: Callable) -> Callable[[], Awaitable[dict]]:
    """A receive() that hands over a body already read, then carries on as the real one."""
    sent = False

    async def replay() -> dict:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


async def _read_body(receive: Callable, limit: int = MAX_BODY) -> tuple[bytes, bool]:
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return b"", True
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    return b"".join(chunks), False


async def _reply(send: Callable, status: int, obj: dict, headers: list[tuple[bytes, bytes]] | None = None) -> None:
    body = json.dumps(obj).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                    *(headers or [])],
    })
    await send({"type": "http.response.body", "body": body})
