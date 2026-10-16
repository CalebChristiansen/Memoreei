"""OAuth's rows in memoreei.db: clients, requests waiting for the person, grants,
codes, access tokens, and the refresh-token signing key.

Everything secret is stored as a sha256 (codes, access tokens, device codes, the
browser cookie), as for API keys. Refresh tokens aren't stored at all: they're
signed (memoreei.oauth.tokens), and the grant row holds the one number that says
which of them is current.

The server and the CLI (`memoreei chatbots approve`) are separate processes on the
same file, so every change that must happen once is a single UPDATE with the
condition in its WHERE, checked by its rowcount.
"""
from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Any

import sqlcipher3

from memoreei.storage.rows import Rows

REQUEST_TTL = 10 * 60          # a request waits ten minutes for the person
CODE_TTL = 60
ACCESS_TTL = 10 * 60
REFRESH_GRACE = 30             # the refresh token before the newest still works this long
FINISH_GRACE = 60              # an approved device code may be collected this late
MAX_WAITING = 20               # requests waiting at once, from everyone
MAX_WAITING_PER_ADDRESS = 5

# Four characters to read off one screen and find on another. No vowels (no words),
# nothing that looks like something else (0/O, 1/I/L, 5/S).
USER_CODE_ALPHABET = "BCDFGHJKMNPQRTVWXZ2346789"
USER_CODE_LENGTH = 4


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def now() -> int:
    return int(time.time())


def normalize_user_code(text: str) -> str:
    """What a person typed, as it's stored: the code's own characters, upper case."""
    return "".join(c for c in str(text).upper() if c in USER_CODE_ALPHABET)


def short_id(grant_id: str) -> str:
    """A grant's name for the CLI: the first eight characters of its id."""
    return grant_id.replace("-", "")[:8]


class Busy(Exception):
    """Too many requests are waiting already."""


class OAuthStore(Rows):
    # ── clients ─────────────────────────────────────────────────────────────

    async def get_client(self, client_id: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,))

    async def save_client(self, client_id: str, kind: str, metadata_json: str) -> None:
        t = now()
        await self._write(
            "INSERT INTO oauth_clients (client_id, kind, metadata, fetched_at, created_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT (client_id) DO UPDATE SET metadata = excluded.metadata, "
            "fetched_at = excluded.fetched_at WHERE oauth_clients.kind = excluded.kind",
            (client_id, kind, metadata_json, t, t))

    async def first_jti(self, client_id: str, jti: str, expires_at: int) -> bool:
        """True the first time a client's assertion id is seen."""
        await self.conn.execute("DELETE FROM oauth_jtis WHERE expires_at < ?", (now(),))
        return await self._write(
            "INSERT OR IGNORE INTO oauth_jtis (client_id, jti, expires_at) VALUES (?,?,?)",
            (client_id, jti, expires_at)) == 1

    # ── requests waiting for the person ─────────────────────────────────────

    async def add_request(self, kind: str, *, client_id: str, client_name: str, client_host: str,
                          self_registered: bool, peer: str | None, resource: str,
                          redirect_uri: str | None = None, state: str | None = None,
                          code_challenge: str | None = None, browser_secret: str | None = None,
                          device_code: str | None = None, interval: int | None = None) -> dict[str, Any]:
        """A new request, with a user code no other waiting request has. Raises Busy."""
        t = now()
        await self.conn.execute("DELETE FROM oauth_requests WHERE expires_at < ?", (t - 86400,))
        waiting = await self._one(
            "SELECT COUNT(*) AS n, SUM(peer IS ?) AS here FROM oauth_requests "
            "WHERE decided_at IS NULL AND expires_at >= ?", (peer, t))
        assert waiting is not None
        if waiting["n"] >= MAX_WAITING or (waiting["here"] or 0) >= MAX_WAITING_PER_ADDRESS:
            raise Busy()
        request_id = secrets.token_urlsafe(18)
        for _ in range(10):
            code = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))
            if await self._one("SELECT 1 FROM oauth_requests WHERE user_code = ? AND expires_at >= ?",
                               (code, t)):
                continue
            try:
                await self._write(
                    "INSERT INTO oauth_requests (id, kind, user_code, client_id, client_name, client_host, "
                    "self_registered, peer, resource, redirect_uri, state, code_challenge, browser_hash, "
                    "device_code_hash, interval, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (request_id, kind, code, client_id, client_name, client_host, int(self_registered), peer,
                     resource, redirect_uri, state, code_challenge,
                     sha256(browser_secret) if browser_secret else None,
                     sha256(device_code) if device_code else None, interval, t, t + REQUEST_TTL))
            except sqlcipher3.IntegrityError:
                continue
            row = await self.request(request_id)
            assert row is not None
            return row
        raise Busy()

    async def request(self, request_id: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM oauth_requests WHERE id = ?", (request_id,))

    async def browser_request(self, request_id: str, browser_secret: str | None) -> dict[str, Any] | None:
        """An authorize request, if this is the browser that started it."""
        if not request_id or not browser_secret:
            return None
        return await self._one(
            "SELECT * FROM oauth_requests WHERE id = ? AND kind = 'authorize' AND browser_hash = ?",
            (request_id, sha256(browser_secret)))

    async def device_request(self, device_code: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM oauth_requests WHERE device_code_hash = ?", (sha256(device_code),))

    async def waiting(self) -> list[dict[str, Any]]:
        """Requests the person hasn't answered, oldest first."""
        return await self._all(
            "SELECT * FROM oauth_requests WHERE decided_at IS NULL AND expires_at >= ? ORDER BY created_at",
            (now(),))

    async def waiting_by_code(self, typed: str) -> dict[str, Any] | None:
        code = normalize_user_code(typed)
        if len(code) != USER_CODE_LENGTH:
            return None
        return await self._one(
            "SELECT * FROM oauth_requests WHERE user_code = ? AND decided_at IS NULL AND expires_at >= ?",
            (code, now()))

    async def decide(self, request_id: str, approved: bool) -> bool:
        """The person's answer. False if it was answered already, or has expired."""
        t = now()
        return await self._write(
            "UPDATE oauth_requests SET approved = ?, decided_at = ? "
            "WHERE id = ? AND decided_at IS NULL AND expires_at >= ?",
            (1 if approved else 0, t, request_id, t)) == 1

    async def cancel(self, request_id: str) -> None:
        """The browser gave up: as good as a no, and gone from the person's list."""
        await self.decide(request_id, False)

    async def finish(self, request_id: str, grant_id: str | None = None) -> bool:
        """True once, for whoever collects an approved request's answer."""
        return await self._write(
            "UPDATE oauth_requests SET finished_at = ?, grant_id = ? WHERE id = ? AND approved = 1 "
            "AND finished_at IS NULL AND expires_at >= ?",
            (now(), grant_id, request_id, now() - FINISH_GRACE)) == 1

    async def set_grant(self, request_id: str, grant_id: str) -> None:
        await self._write("UPDATE oauth_requests SET grant_id = ? WHERE id = ?", (grant_id, request_id))

    async def note_poll(self, request_id: str, at: float, interval: int) -> None:
        await self._write("UPDATE oauth_requests SET last_poll_at = ?, interval = ? WHERE id = ?",
                          (at, interval, request_id))

    # ── codes ───────────────────────────────────────────────────────────────

    async def add_code(self, request_id: str) -> str:
        code = secrets.token_urlsafe(32)
        await self.conn.execute("DELETE FROM oauth_codes WHERE created_at < ?", (now() - 86400,))
        await self._write("INSERT INTO oauth_codes (code_hash, request_id, created_at) VALUES (?,?,?)",
                          (sha256(code), request_id, now()))
        return code

    async def take_code(self, code: str) -> tuple[dict[str, Any] | None, bool]:
        """(the code's row joined to its request, used before?), or (None, False) if
        unknown or stale. Marks it used."""
        row = await self._one(
            "SELECT c.code_hash, c.created_at AS code_created_at, c.grant_id AS code_grant_id, r.* "
            "FROM oauth_codes c JOIN oauth_requests r ON r.id = c.request_id WHERE c.code_hash = ?",
            (sha256(code),))
        if row is None or row["code_created_at"] < now() - CODE_TTL:
            return None, False
        first = await self._write("UPDATE oauth_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
                                  (now(), row["code_hash"])) == 1
        return row, not first

    async def code_led_to(self, code_hash: str, grant_id: str) -> None:
        await self._write("UPDATE oauth_codes SET grant_id = ? WHERE code_hash = ?", (grant_id, code_hash))

    # ── grants ──────────────────────────────────────────────────────────────

    async def add_grant(self, request: dict[str, Any]) -> dict[str, Any]:
        grant_id = str(uuid.uuid4())
        t = now()
        await self._write(
            "INSERT INTO oauth_grants (id, client_id, client_name, client_host, self_registered, resource, "
            "created_at, refreshed_at) VALUES (?,?,?,?,?,?,?,?)",
            (grant_id, request["client_id"], request["client_name"], request["client_host"],
             request["self_registered"], request["resource"], t, t))
        grant = await self.grant(grant_id)
        assert grant is not None
        return grant

    async def grant(self, grant_id: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM oauth_grants WHERE id = ?", (grant_id,))

    async def live_grant(self, grant_id: str) -> dict[str, Any] | None:
        return await self._one("SELECT * FROM oauth_grants WHERE id = ? AND revoked_at IS NULL", (grant_id,))

    async def revoke_grant(self, grant_id: str, why: str) -> bool:
        t = now()
        done = await self._write(
            "UPDATE oauth_grants SET revoked_at = ?, revoked_why = ? WHERE id = ? AND revoked_at IS NULL",
            (t, why, grant_id)) == 1
        await self._write("DELETE FROM oauth_access_tokens WHERE grant_id = ?", (grant_id,))
        return done

    async def grants(self, include_revoked: bool = False) -> list[dict[str, Any]]:
        """Grants, newest use first, with their calls from the access log."""
        where = "" if include_revoked else "WHERE g.revoked_at IS NULL"
        return await self._all(
            "SELECT g.*, (SELECT COUNT(*) FROM access_log a WHERE a.kind = 'oauth' AND a.subject = g.id) AS calls "
            f"FROM oauth_grants g {where} ORDER BY COALESCE(g.last_used_at, g.created_at) DESC")

    async def grant_by_prefix(self, prefix: str) -> list[dict[str, Any]]:
        """Live grants whose id starts with this (dashes ignored), for the CLI."""
        p = prefix.replace("-", "").lower()
        if len(p) < 4:
            return []
        return [g for g in await self.grants() if g["id"].replace("-", "").startswith(p)]

    async def touch_grant(self, grant_id: str, when: int | None = None) -> None:
        await self._write("UPDATE oauth_grants SET last_used_at = ? WHERE id = ?", (when or now(), grant_id))

    async def rotate_refresh(self, grant_id: str, generation: int) -> bool:
        """Move the grant on from this generation of refresh token, once."""
        return await self._write(
            "UPDATE oauth_grants SET refresh_generation = refresh_generation + 1, refreshed_at = ? "
            "WHERE id = ? AND refresh_generation = ? AND revoked_at IS NULL",
            (now(), grant_id, generation)) == 1

    # ── access tokens ───────────────────────────────────────────────────────

    async def add_access_token(self, grant_id: str, ttl: int = ACCESS_TTL) -> str:
        token = "mat_" + secrets.token_urlsafe(32)
        t = now()
        await self.conn.execute("DELETE FROM oauth_access_tokens WHERE expires_at < ?", (t,))
        await self._write("INSERT INTO oauth_access_tokens (token_hash, grant_id, expires_at) VALUES (?,?,?)",
                          (sha256(token), grant_id, t + ttl))
        return token

    async def access_grant(self, token: str) -> dict[str, Any] | None:
        """The live grant behind an unexpired access token, or None."""
        return await self._one(
            "SELECT g.* FROM oauth_access_tokens t JOIN oauth_grants g ON g.id = t.grant_id "
            "WHERE t.token_hash = ? AND t.expires_at >= ? AND g.revoked_at IS NULL",
            (sha256(token), now()))

    async def access_token_grant_id(self, token: str) -> str | None:
        row = await self._one("SELECT grant_id FROM oauth_access_tokens WHERE token_hash = ?", (sha256(token),))
        return row["grant_id"] if row else None

    # ── the signing key ─────────────────────────────────────────────────────

    async def signing_key(self) -> tuple[str, bytes]:
        """(id, secret) of the key refresh tokens are signed with, made on first use."""
        row = await self._one("SELECT id, secret FROM oauth_keys ORDER BY created_at DESC LIMIT 1")
        if row is None:
            await self._write("INSERT OR IGNORE INTO oauth_keys (id, secret, created_at) VALUES (?,?,?)",
                              (secrets.token_hex(4), secrets.token_bytes(32), now()))
            row = await self._one("SELECT id, secret FROM oauth_keys ORDER BY created_at DESC LIMIT 1")
            assert row is not None
        return row["id"], bytes(row["secret"])

    async def key_by_id(self, key_id: str) -> bytes | None:
        row = await self._one("SELECT secret FROM oauth_keys WHERE id = ?", (key_id,))
        return bytes(row["secret"]) if row else None
