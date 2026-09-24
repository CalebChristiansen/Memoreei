"""API keys for the network server.

A key is ``mem_`` plus 32 random bytes. Only its sha256 is stored, in the ``api_keys``
table, so a copied database doesn't hand out working keys. sha256 without a salt is
fine here: the keys are long random strings, not passwords anyone could guess.

The HTTP server checks ``Authorization: Bearer <key>`` with BearerAuthMiddleware. The
middleware only needs a ``verify(key) -> name | None`` callable, which is the seam a
future OAuth mode would replace.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import socket
import subprocess
import time
from typing import Any, Awaitable, Callable

from memoreei.storage.database import Database

log = logging.getLogger("memoreei.auth")

KEY_PREFIX = "mem_"
KEY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TOUCH_INTERVAL = 60  # seconds between last_used_at writes per key

Verifier = Callable[[str], Awaitable["str | None"]]


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class KeyStore:
    """Checks presented keys against the hashes in the database."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._last_touch: dict[str, float] = {}

    async def verify(self, key: str) -> str | None:
        """Return the key's name if it is valid, else None."""
        presented = hash_key(key)
        match: str | None = None
        for name, stored in await self.db.api_key_hashes():
            if hmac.compare_digest(presented, stored):
                match = name
        if match is not None:
            now = time.monotonic()
            if now - self._last_touch.get(match, -TOUCH_INTERVAL) >= TOUCH_INTERVAL:
                self._last_touch[match] = now
                await self.db.touch_api_key(match)
        return match


def _bearer_token(headers: list[tuple[bytes, bytes]]) -> str | None:
    for raw_name, raw_value in headers:
        if raw_name.lower() == b"authorization":
            scheme, _, token = raw_value.decode("latin-1").strip().partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
            return None
    return None


class BearerAuthMiddleware:
    """ASGI middleware: every HTTP request needs a valid bearer key.

    Keys are accepted only in the Authorization header, never in the query string.
    Failures get 401 with ``WWW-Authenticate: Bearer realm="memoreei"`` and no hint of
    OAuth, so clients report a bad key instead of starting a login flow.
    """

    def __init__(self, app: Any, verify: Verifier) -> None:
        self.app = app
        self.verify = verify

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client") or ("?", 0)
        method, path = scope.get("method", "?"), scope.get("path", "?")
        token = _bearer_token(scope.get("headers", []))
        name = await self.verify(token) if token else None
        if name is None:
            reason = "invalid key" if token else "no key"
            log.warning("%s %s from %s rejected: %s", method, path, client[0], reason)
            await _send_401(send, reason)
            return

        status = 0

        async def _send(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        scope.setdefault("state", {})["memoreei_key"] = name
        await self.app(scope, receive, _send)
        log.info("%s %s %d key=%s", method, path, status, name)


async def _send_401(send: Callable, reason: str) -> None:
    body = json.dumps(
        {"error": "unauthorized", "error_description": f"{reason}: send Authorization: Bearer <key>"}
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"www-authenticate", b'Bearer realm="memoreei"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ── Client config ───────────────────────────────────────────────────────────


def local_ipv4_addresses() -> list[str]:
    """Every non-loopback, non-link-local IPv4 address on this machine, best effort."""
    found: list[str] = []
    for cmd, pattern in (
        (["ip", "-4", "-o", "addr", "show"], r"inet (\d+\.\d+\.\d+\.\d+)/"),
        (["ifconfig"], r"inet (?:addr:)?(\d+\.\d+\.\d+\.\d+)"),
    ):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        found = re.findall(pattern, out)
        if found:
            break
    if not found:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("192.0.2.1", 9))  # TEST-NET; nothing is sent
                found = [s.getsockname()[0]]
        except OSError:
            found = []
    result: list[str] = []
    for ip in found:
        if ip.startswith(("127.", "169.254.", "0.")) or ip in result:
            continue
        result.append(ip)
    return result


def server_urls(port: int, public_url: str | None, tls: bool) -> list[str]:
    """The URL(s) a client could use. MEMOREEI_PUBLIC_URL wins when set."""
    if public_url:
        return [public_url if public_url.endswith("/mcp") else public_url + "/mcp"]
    scheme = "https" if tls else "http"
    ips = local_ipv4_addresses() or ["<server-ip>"]
    return [f"{scheme}://{ip}:{port}/mcp" for ip in ips]


def client_config_text(key: str, urls: list[str], from_public_url: bool) -> str:
    """Ready-to-paste config for Claude Code and .mcp.json."""
    url = urls[0]
    lines: list[str] = []
    if from_public_url:
        lines += ["  Server URL:", f"    {url}", ""]
    else:
        lines += ["  Server URL (use whichever address the client can reach):"]
        lines += [f"    {u}" for u in urls]
        lines += [""]
    lines += [
        "  Claude Code:",
        f'    claude mcp add --transport http memoreei {url} \\',
        f'      --header "Authorization: Bearer {key}"',
        "",
        "  .mcp.json (reads the key from the MEMOREEI_KEY environment variable):",
    ]
    snippet = {
        "mcpServers": {
            "memoreei": {
                "type": "http",
                "url": url,
                "headers": {"Authorization": "Bearer ${MEMOREEI_KEY}"},
            }
        }
    }
    lines += ["    " + ln for ln in json.dumps(snippet, indent=2).splitlines()]
    return "\n".join(lines)
