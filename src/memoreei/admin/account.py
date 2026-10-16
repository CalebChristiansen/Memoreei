"""The dashboard's one account: its password, 2FA, sessions, and access from anywhere.

One memoreei holds one person's messages, so there's one account and no user name.
The password is optional. Without one, the dashboard answers only on this computer,
signed in by one-time links, as it always has. With one, and with "from anywhere"
switched on, it also answers away from the computer (on the LAN, and through the
public listener: Memoreei Cloud's relay or your own tunnel), to a browser that signs
in with the password, and the authenticator code if 2FA is on.

Everything is in the encrypted database: the scrypt hash, the authenticator's secret,
hashes of recovery codes, sessions and known browsers. A forgotten password or a lost
phone is reset on the computer (`memoreei admin reset`, or the dashboard there), never
from away.

Failed sign-ins away from the computer are limited per address and for everyone at
once, in memory. No limit locks the account: a browser that has signed in before skips
the limit on everyone, so a stranger's guessing from many addresses can't keep its
owner out, and on the computer there are no limits, and the one-time link always works.
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Callable

from memoreei.admin import credentials
from memoreei.auth import hash_key as sha256  # as admin_tokens and api_keys are hashed
from memoreei.oauth.limits import Limiter
from memoreei.storage.rows import Rows

LINK_SESSION_TTL = 30 * 24 * 3600
PASSWORD_SESSION_TTL = 7 * 24 * 3600
BROWSER_TTL = 365 * 24 * 3600
SIGNINS_KEPT = 90 * 24 * 3600
PER_ADDRESS = (10, 15 * 60)    # failed sign-ins, seconds
OVERALL = (100, 60 * 60)


def now() -> int:
    return int(time.time())


class SignInLimits:
    """Failed sign-ins, per address and from everyone. Only failures count, but every
    attempt is counted as one while its password is checked, and forgiven if it was
    right: checking takes a tenth of a second, and attempts sent all at once must not
    each find the count as it was before any of them failed."""

    def __init__(self, per_address: tuple[int, int] = PER_ADDRESS, overall: tuple[int, int] = OVERALL,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.address = Limiter(*per_address, clock=clock)
        self.everyone = Limiter(*overall, clock=clock)

    def blocked(self, peer: str, known_browser: bool = False) -> str | None:
        """Why this attempt waits, or None."""
        if self.address.full(peer):
            return "too many failed sign-ins from this address"
        if not known_browser and self.everyone.full("*"):
            return "too many failed sign-ins from everyone"
        return None

    def take(self, peer: str, known_browser: bool = False) -> str | None:
        """Count an attempt as failed before checking it (forgive() it if it wasn't).
        Why it may not be tried at all, or None."""
        why = self.blocked(peer, known_browser)
        if why is None:
            self.fail(peer)
        return why

    def fail(self, peer: str) -> None:
        self.address.note(peer)
        self.everyone.note("*")

    def forgive(self, peer: str) -> None:
        """Take back the count for an attempt that turned out right."""
        for limiter, key in ((self.address, peer), (self.everyone, "*")):
            q = limiter.seen.get(key)
            if q:
                q.pop()


async def account_store() -> "AccountStore":
    """The running server's account."""
    from memoreei.server import _get_db

    return AccountStore(await _get_db())


class AccountStore(Rows):
    # ── the account ─────────────────────────────────────────────────────────

    async def account(self) -> dict[str, Any]:
        row = await self._one("SELECT * FROM admin_account WHERE id = 1")
        return row or {"password_hash": None, "password_set_at": None, "totp_secret": None,
                       "totp_pending": None, "totp_last_step": None, "totp_set_at": None, "remote": 0}

    async def _update(self, assignments: str, args: tuple = ()) -> None:
        await self.conn.execute("INSERT OR IGNORE INTO admin_account (id, updated_at) VALUES (1, ?)", (now(),))
        await self._write(f"UPDATE admin_account SET {assignments}, updated_at = ? WHERE id = 1", (*args, now()))

    async def has_password(self) -> bool:
        return bool((await self.account())["password_hash"])

    async def remote_open(self) -> bool:
        """Whether the dashboard answers away from this computer: a password, and the switch."""
        a = await self.account()
        return bool(a["password_hash"]) and bool(a["remote"])

    async def set_password(self, password_hash: str, keep_session: str | None = None) -> None:
        """A new password. Every other password session ends, and every known browser
        is forgotten: whoever knew the old one is out."""
        await self._update("password_hash = ?, password_set_at = ?", (password_hash, now()))
        await self.end_sessions("password", keep=keep_session)
        await self.forget_browsers()

    async def reset(self) -> None:
        """No password, no 2FA, not from anywhere, and nobody signed in by password."""
        await self._update("password_hash = NULL, password_set_at = NULL, totp_secret = NULL, "
                           "totp_pending = NULL, totp_last_step = NULL, totp_set_at = NULL, remote = 0")
        await self._write("DELETE FROM admin_recovery_codes")
        await self.end_sessions("password")
        await self.forget_browsers()

    async def set_remote(self, on: bool) -> bool:
        """Turn access from anywhere on (only with a password) or off."""
        if on and not await self.has_password():
            return False
        await self._update("remote = ?", (1 if on else 0,))
        return True

    # ── 2FA ─────────────────────────────────────────────────────────────────

    async def start_totp(self) -> str:
        """A new secret, waiting for a code from the app that scanned it."""
        secret = credentials.new_totp_secret()
        await self._update("totp_pending = ?", (secret,))
        return secret

    async def confirm_totp(self, code: str, keep_session: str | None = None) -> list[str] | None:
        """Switch 2FA on, if the code fits the waiting secret. Returns new recovery codes.
        Other password sessions end: they signed in without a code."""
        a = await self.account()
        if not a["totp_pending"]:
            return None
        step = credentials.check_totp(a["totp_pending"], code, None)
        if step is None:
            return None
        await self._update("totp_secret = totp_pending, totp_pending = NULL, totp_last_step = ?, totp_set_at = ?",
                           (step, now()))
        await self.end_sessions("password", keep=keep_session)
        return await self.new_recovery_codes()

    async def disable_totp(self) -> None:
        await self._update("totp_secret = NULL, totp_pending = NULL, totp_last_step = NULL, totp_set_at = NULL")
        await self._write("DELETE FROM admin_recovery_codes")

    async def use_totp_step(self, step: int) -> bool:
        """Spend a code's time step, once: the same code can't sign in twice."""
        return await self._write(
            "UPDATE admin_account SET totp_last_step = ? WHERE id = 1 "
            "AND (totp_last_step IS NULL OR totp_last_step < ?)", (step, step)) == 1

    async def new_recovery_codes(self) -> list[str]:
        codes = credentials.new_recovery_codes()
        await self.conn.execute("DELETE FROM admin_recovery_codes")
        await self.conn.executemany("INSERT INTO admin_recovery_codes (code_hash, created_at) VALUES (?, ?)",
                                    [(credentials.recovery_hash(c), now()) for c in codes])
        await self.conn.commit()
        return codes

    async def use_recovery_code(self, code: str) -> bool:
        if len(credentials.normalize_recovery_code(code)) != 10:
            return False
        return await self._write("UPDATE admin_recovery_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL",
                                 (now(), credentials.recovery_hash(code))) == 1

    async def recovery_codes_left(self) -> int:
        row = await self._one("SELECT COUNT(*) AS n FROM admin_recovery_codes WHERE used_at IS NULL")
        return row["n"] if row else 0

    # ── signing in ──────────────────────────────────────────────────────────

    async def check(self, password: str, code: str = "") -> tuple[bool, str]:
        """(right?, what happened, for the log). The code is looked at only after the
        password is right, so a recovery code is never spent on a wrong password."""
        a = await self.account()
        if not a["password_hash"]:
            return False, "no password is set"
        if not await credentials.check_password_async(password, a["password_hash"]):
            return False, "wrong password"
        if not a["totp_secret"]:
            return True, "password"
        step = credentials.check_totp(a["totp_secret"], code, a["totp_last_step"])
        if step is not None:
            return (True, "password and code") if await self.use_totp_step(step) else (False, "code already used")
        if code and await self.use_recovery_code(code):
            return True, "password and a recovery code"
        return False, "wrong code" if code.strip() else "no code"

    # ── sessions ────────────────────────────────────────────────────────────

    async def add_session(self, how: str, peer: str | None = None, user_agent: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        t = now()
        ttl = PASSWORD_SESSION_TTL if how == "password" else LINK_SESSION_TTL
        await self.conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ?", (t,))
        await self._write(
            "INSERT INTO admin_sessions (token_hash, how, peer, user_agent, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (sha256(token), how, peer, (user_agent or "")[:300] or None, t, t + ttl))
        return token

    async def session(self, token: str | None) -> dict[str, Any] | None:
        """The live session behind a cookie, or None. Sessions from before admin_sessions
        existed are one-time-link sessions."""
        if not token:
            return None
        h = sha256(token)
        row = await self._one("SELECT * FROM admin_sessions WHERE token_hash = ? AND expires_at > ?", (h, now()))
        if row is None and await self.db.admin_token_valid(h, "session"):
            row = {"token_hash": h, "how": "link", "peer": None, "user_agent": None, "created_at": None}
        return row

    async def end_session(self, token: str | None) -> None:
        if token:
            await self._write("DELETE FROM admin_sessions WHERE token_hash = ?", (sha256(token),))
            await self.db.delete_admin_token(sha256(token))

    async def end_sessions(self, how: str | None = None, keep: str | None = None) -> int:
        """End every session (or every one of a kind), except the one kept."""
        keep_hash = sha256(keep) if keep else ""
        if how is None:
            n = await self._write("DELETE FROM admin_sessions WHERE token_hash != ?", (keep_hash,))
            await self._write("DELETE FROM admin_tokens WHERE kind = 'session' AND token_hash != ?", (keep_hash,))
            return n
        return await self._write("DELETE FROM admin_sessions WHERE how = ? AND token_hash != ?", (how, keep_hash))

    async def sessions(self) -> list[dict[str, Any]]:
        return await self._all("SELECT * FROM admin_sessions WHERE expires_at > ? ORDER BY created_at DESC", (now(),))

    # ── known browsers ──────────────────────────────────────────────────────

    async def add_browser(self) -> str:
        token = secrets.token_urlsafe(32)
        t = now()
        await self.conn.execute("DELETE FROM admin_browsers WHERE expires_at <= ?", (t,))
        await self._write("INSERT INTO admin_browsers (token_hash, created_at, expires_at) VALUES (?, ?, ?)",
                          (sha256(token), t, t + BROWSER_TTL))
        return token

    async def browser_known(self, token: str | None) -> bool:
        return bool(token) and await self._one(
            "SELECT 1 FROM admin_browsers WHERE token_hash = ? AND expires_at > ?", (sha256(token or ""), now())
        ) is not None

    async def forget_browsers(self) -> None:
        await self._write("DELETE FROM admin_browsers")

    # ── the sign-in log ─────────────────────────────────────────────────────

    async def record_signin(self, peer: str | None, how: str, ok: bool, why: str | None = None) -> None:
        t = now()
        await self.conn.execute("DELETE FROM admin_signins WHERE at < ?", (t - SIGNINS_KEPT,))
        await self._write("INSERT INTO admin_signins (at, peer, how, ok, why) VALUES (?, ?, ?, ?, ?)",
                          (t, peer, how, 1 if ok else 0, why))

    async def signins(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self._all("SELECT * FROM admin_signins ORDER BY id DESC LIMIT ?", (limit,))


def describe_browser(user_agent: str | None) -> str:
    """'Safari on iPhone', from a User-Agent, roughly. For the sessions list."""
    ua = user_agent or ""
    if not ua:
        return "a browser"
    system = next((name for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                          ("Mac OS X", "Mac"), ("Windows", "Windows"), ("CrOS", "ChromeOS"),
                                          ("Linux", "Linux")) if key in ua), None)
    browser = next((name for key, name in (("Edg/", "Edge"), ("Firefox/", "Firefox"), ("FxiOS", "Firefox"),
                                           ("CriOS", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari"))
                    if key in ua), "A browser")
    return f"{browser} on {system}" if system else browser
