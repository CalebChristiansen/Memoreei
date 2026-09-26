"""Who may use the dashboard, and from where.

The dashboard runs in the same process as the MCP server, which on macOS is the process
holding the Full Disk Access grant. Anything that can drive it can read what that grant
reads, so it has three locks:

1. **Loopback only.** Requests must come from this machine and name it in ``Host``
   (``localhost``, ``127.0.0.1`` or ``[::1]``), which also stops DNS rebinding: a web page
   on ``evil.example`` that resolves to 127.0.0.1 still sends ``Host: evil.example``.
   ``MEMOREEI_ADMIN_REMOTE=true`` lifts this, for Docker, where the host's browser is
   never on the container's loopback.
2. **A session**, started by a one-time login link (``memoreei admin-url``, or the menu
   in Memoreei.app). The link's token works once and for a few minutes; the session
   cookie it's swapped for lasts a month. Only hashes are stored, as for API keys.
3. **Same-origin writes.** Every POST must carry an ``Origin`` matching the ``Host`` it
   was sent to, so another site can't submit the dashboard's forms in the background.
   A browser that leaves ``Origin`` off a same-origin form post is still let through
   if it says ``Sec-Fetch-Site: same-origin``, a header pages can't set themselves.
"""
from __future__ import annotations

import ipaddress
import os
import secrets
from typing import TYPE_CHECKING

from memoreei.auth import hash_key

if TYPE_CHECKING:
    from memoreei.storage.database import Database

LOGIN_TTL = 5 * 60
SESSION_TTL = 30 * 24 * 3600
COOKIE = "memoreei_admin"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def remote_allowed() -> bool:
    return os.environ.get("MEMOREEI_ADMIN_REMOTE", "").lower() in ("1", "true", "yes")


def host_name(host_header: str) -> str:
    """``Host`` without its port: ``[::1]:3679`` → ``::1``, ``localhost:3679`` → ``localhost``."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.rpartition(":")[0] if host.count(":") == 1 else host


def is_loopback_client(client_host: str | None) -> bool:
    if not client_host:
        return False
    try:
        return ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        return False


def local_request(client_host: str | None, host_header: str) -> bool:
    """Whether a request both comes from and is addressed to this machine."""
    return is_loopback_client(client_host) and host_name(host_header) in LOOPBACK_HOSTS


def same_origin(
    origin: str | None, host_header: str, scheme: str, fetch_site: str | None = None
) -> bool:
    """Whether a browser's write request comes from the dashboard's own pages."""
    if origin is None:
        return fetch_site == "same-origin"
    if origin == "null":
        return False
    return origin.rstrip("/").lower() == f"{scheme}://{host_header}".lower()


async def create_login_token(db: "Database") -> str:
    token = secrets.token_urlsafe(32)
    await db.add_admin_token(hash_key(token), "login", LOGIN_TTL)
    return token


async def redeem_login_token(db: "Database", token: str) -> str | None:
    """Swap a one-time login token for a new session token, or None if it's no good."""
    if not token or not await db.take_admin_token(hash_key(token), "login"):
        return None
    session = secrets.token_urlsafe(32)
    await db.add_admin_token(hash_key(session), "session", SESSION_TTL)
    return session


async def session_valid(db: "Database", session: str | None) -> bool:
    return bool(session) and await db.admin_token_valid(hash_key(session), "session")


async def end_session(db: "Database", session: str | None) -> None:
    if session:
        await db.delete_admin_token(hash_key(session))


def login_url(token: str, port: int, tls: bool = False) -> str:
    scheme = "https" if tls else "http"
    return f"{scheme}://localhost:{port}/admin/login?token={token}"
