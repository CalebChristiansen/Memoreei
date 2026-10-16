"""Who may use the dashboard, and from where.

The dashboard runs in the same process as the MCP server, which on macOS is the process
holding the Full Disk Access grant. Anything that can drive it can read what that grant
reads, so it has three locks:

1. **Where from.** On this computer: requests that come from this machine and name it
   in ``Host`` (``localhost``, ``127.0.0.1`` or ``[::1]``), which also stops DNS
   rebinding: a web page on ``evil.example`` that resolves to 127.0.0.1 still sends
   ``Host: evil.example``. A headless server's dashboard is reached through ``ssh -L``,
   which arrives on loopback (``memoreei admin-url`` prints the command). Anywhere
   else only with a password set *and* "from anywhere" switched on
   (memoreei.admin.account): then other machines on the LAN listener, when ``Host``
   names this computer (its address, its name, or ``MEMOREEI_PUBLIC_URL``'s, so not a
   rebinding page's), and the public listener (Memoreei Cloud's relay, or your own
   tunnel). Which listener a request came
   in on decides; never its client address, because everything the relay's tunnel
   carries arrives from 127.0.0.1.
2. **A session.** On the computer, a one-time login link (``memoreei admin-url``, or
   the menu in Memoreei.app) starts one; its token works once and for a few minutes,
   and the session lasts a month. A session from a link only ever works on the
   computer. The password (and the authenticator code, with 2FA) starts one anywhere
   it's allowed, for a week. Only hashes are stored, as for API keys, and every
   sign-in gets a new session token, so none can be planted beforehand.
3. **Same-origin writes.** Every POST must carry an ``Origin`` matching the address it
   was sent to, so another site can't submit the dashboard's forms in the background.
   A browser that leaves ``Origin`` off a same-origin form post, or sends ``null``
   (which a ``no-referrer`` policy makes it do), is still let through if it says
   ``Sec-Fetch-Site: same-origin``, a header pages can't set themselves. Through the
   public listener the address is the public one memoreei knows (its Cloud hostname or
   ``MEMOREEI_PUBLIC_MCP_URL``), and ``Host`` must name it too.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from memoreei.auth import hash_key

if TYPE_CHECKING:
    from memoreei.storage.database import Database

LOGIN_TTL = 5 * 60
SESSION_TTL = 30 * 24 * 3600
COOKIE = "memoreei_admin"
BROWSER_COOKIE = "memoreei_browser"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Where a sign-in may send the browser next: one of the dashboard's own pages.
NEXT_PATH = re.compile(r"^/admin/[A-Za-z0-9_\-/]*$")


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


def names_this_computer(host_header: str, public_url: str | None = None) -> bool:
    """Whether a request from elsewhere on the LAN listener names this computer in
    ``Host``: by an IP address, by its own name (or ``name.local``), or by
    ``MEMOREEI_PUBLIC_URL``'s. Any other name is a page somewhere doing DNS rebinding,
    whose Origin would otherwise match its own Host."""
    name = host_name(host_header).rstrip(".")
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    own = socket.gethostname().lower().rstrip(".")
    short = own.split(".")[0]
    names = {own, short, f"{short}.local"}
    if public_url:
        names.add((urlsplit(public_url).hostname or "").lower())
    return bool(name) and name in names


def same_origin(
    origin: str | None, host_header: str, scheme: str, fetch_site: str | None = None
) -> bool:
    """Whether a browser's write request comes from the dashboard's own pages."""
    if origin is None or origin == "null":
        return fetch_site == "same-origin"
    return origin.rstrip("/").lower() == f"{scheme}://{host_header}".lower()


def origin_of(url: str) -> str:
    u = urlsplit(url)
    return f"{u.scheme}://{u.netloc}".lower()


def safe_next(path: str | None) -> str:
    """A sign-in's next page, if it's one of ours; else the dashboard's front page."""
    if path and NEXT_PATH.match(path) and "//" not in path:
        return path
    return "/admin/"


async def create_login_token(db: "Database") -> str:
    token = secrets.token_urlsafe(32)
    await db.add_admin_token(hash_key(token), "login", LOGIN_TTL)
    return token


async def redeem_login_token(db: "Database", token: str, peer: str | None = None,
                             user_agent: str | None = None) -> str | None:
    """Swap a one-time login token for a new session token, or None if it's no good."""
    from memoreei.admin.account import AccountStore

    if not token or not await db.take_admin_token(hash_key(token), "login"):
        return None
    return await AccountStore(db).add_session("link", peer, user_agent)


async def session_for(db: "Database", session: str | None) -> dict[str, Any] | None:
    from memoreei.admin.account import AccountStore

    return await AccountStore(db).session(session)


async def session_valid(db: "Database", session: str | None) -> bool:
    return await session_for(db, session) is not None


async def end_session(db: "Database", session: str | None) -> None:
    from memoreei.admin.account import AccountStore

    await AccountStore(db).end_session(session)


def login_url(token: str, port: int, tls: bool = False) -> str:
    scheme = "https" if tls else "http"
    return f"{scheme}://localhost:{port}/admin/login?token={token}"
