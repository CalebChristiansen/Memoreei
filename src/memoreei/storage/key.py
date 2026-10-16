"""The key memoreei.db is encrypted with, and every connection to it.

memoreei.db is a SQLCipher database: every page is AES-256 on disk, so a copy of the file
(a backup, a synced folder, a laptop left on a train) is noise without the key. The key
is 32 random bytes, used raw rather than as a passphrase, so opening costs no key
derivation. It lives in exactly one of these, looked for in this order:

    MEMOREEI_DB_KEY   64 hex characters in the environment; wins (containers, tests)
    the key file      memoreei.key beside the database, mode 600
    the keyring       GNOME Keyring or KWallet (the Secret Service), on a Linux desktop

A new key goes into the keyring when there is one and it's unlocked, and into the key
file otherwise: headless Linux, Windows, and the Mac. The Mac's Keychain would be the
natural home, but its "only this app" lock follows the code signature, and Memoreei.app
is signed ad hoc, so every update would ask for the password again. It waits for a
Developer ID (see _keychain).

A database from before encryption is converted the first time it's opened: exported into
an encrypted copy beside it, which then replaces it.
"""
from __future__ import annotations

import contextlib
import functools
import os
import re
import secrets
import sys
import threading
from pathlib import Path
from typing import Iterator

import aiosqlite
import sqlcipher3

DB_KEY_ENV = "MEMOREEI_DB_KEY"
SQLITE_HEADER = b"SQLite format 3\x00"
KEYRING_ATTRIBUTES = {"application": "memoreei", "xdg:schema": "net.memoreei.DatabaseKey"}

_HEX_KEY = re.compile(r"[0-9a-fA-F]{64}")
_keys: dict[str, tuple[str, str]] = {}  # resolved path -> (key, where it came from)
_lock = threading.Lock()


class DatabaseKeyError(Exception):
    """The database can't be opened for want of the right key, in words for its owner."""


def key_file(db_path: str | Path) -> Path:
    """memoreei.key beside memoreei.db (any database: its name, with .key)."""
    return Path(db_path).with_suffix(".key")


def db_state(db_path: str | Path) -> str:
    """"missing" (or empty), "plain" (an ordinary SQLite file), or "encrypted"."""
    try:
        with open(db_path, "rb") as f:
            head = f.read(len(SQLITE_HEADER))
    except FileNotFoundError:
        return "missing"
    if not head:
        return "missing"
    return "plain" if head == SQLITE_HEADER else "encrypted"


def get_key(db_path: str | Path) -> str:
    """The database's key as 64 hex characters, made and stored if it has none yet.

    Raises DatabaseKeyError when the database is already encrypted and its key isn't in
    any of the places it could be: making a new one then would only lock it for good.
    """
    return _key_and_source(db_path)[0]


def key_source(db_path: str | Path) -> str | None:
    """Where the database's key is kept, for `memoreei config`; None if it has none yet.
    Never makes one."""
    if os.environ.get(DB_KEY_ENV, "").strip():
        return DB_KEY_ENV
    path = str(Path(db_path).resolve())
    if key_file(path).is_file():
        return str(key_file(path))
    with _keyring() as ring:
        if ring is not None and ring.get(path):
            return "the keyring"
    return None


def _key_and_source(db_path: str | Path) -> tuple[str, str]:
    if env := os.environ.get(DB_KEY_ENV, "").strip():
        if not _HEX_KEY.fullmatch(env):
            raise DatabaseKeyError(f"{DB_KEY_ENV} must be 64 hex characters (a 256-bit key).")
        return env.lower(), DB_KEY_ENV
    path = str(Path(db_path).resolve())
    with _lock:
        if path in _keys:
            return _keys[path]
        found = _stored_key(path)
        if found is None:
            found = _new_key(path)
        _keys[path] = found
        return found


def _stored_key(path: str) -> tuple[str, str] | None:
    file = key_file(path)
    try:
        text = file.read_text().strip()
    except FileNotFoundError:
        text = None
    if text is not None:
        if not _HEX_KEY.fullmatch(text):
            raise DatabaseKeyError(f"{file} doesn't hold a key (64 hex characters).")
        if os.name == "posix" and file.stat().st_mode & 0o077:
            os.chmod(file, 0o600)
        return text.lower(), str(file)
    with _keyring() as ring:
        if ring is not None and (key := ring.get(path)):
            return key, "the keyring"
        locked = ring is None and _keyring_locked()
    if db_state(path) == "encrypted":
        raise DatabaseKeyError(
            f"{path} is encrypted, and its key isn't here: not in {DB_KEY_ENV}, not in "
            f"{file}, {'and the keyring is locked (unlock it, then start again)' if locked else 'not in the keyring'}. "
            f"If you have the key, put it in {file} or {DB_KEY_ENV}."
        )
    return None


def _new_key(path: str) -> tuple[str, str]:
    key = secrets.token_hex(32)
    with _keyring() as ring:
        if ring is not None and ring.put(path, key):
            return key, "the keyring"
    file = key_file(path)
    file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # another process made it first; theirs is the key
        return _stored_key(path) or (key, str(file))
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
        f.flush()
        os.fsync(f.fileno())
    return key, str(file)


# --- the keyring ---------------------------------------------------------------------

class _SecretService:
    """The desktop keyring's default collection, unlocked. Items are named by database."""

    def __init__(self, collection: object) -> None:
        self.collection = collection

    def _attributes(self, path: str) -> dict[str, str]:
        return {**KEYRING_ATTRIBUTES, "database": path}

    def get(self, path: str) -> str | None:
        for item in self.collection.search_items(self._attributes(path)):  # type: ignore[attr-defined]
            key = item.get_secret().decode().strip()
            if _HEX_KEY.fullmatch(key):
                return key.lower()
        return None

    def put(self, path: str, key: str) -> bool:
        try:
            self.collection.create_item(  # type: ignore[attr-defined]
                f"Memoreei database key ({path})", self._attributes(path), key.encode(), replace=True)
        except Exception:
            return False
        return self.get(path) == key  # only trust it once it reads back


def _session_bus() -> object | None:
    """The desktop session's D-Bus connection, or None.

    A user service isn't always told the bus's address, so the usual socket in the
    runtime directory is tried too: a key the keyring was given from a terminal must be
    found by the service as well.
    """
    if not sys.platform.startswith("linux"):
        return None
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not address:
        runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        if not os.path.exists(f"{runtime}/bus"):
            return None
        address = f"unix:path={runtime}/bus"
    try:
        from jeepney.io.blocking import open_dbus_connection
        return open_dbus_connection(address)
    except Exception:
        return None


@contextlib.contextmanager
def _keyring() -> Iterator[_SecretService | None]:
    """The Secret Service's default collection when it's there and already unlocked.

    Never prompts: a server started at boot has nobody to answer, and a locked keyring
    just means the key file is used (or, for an existing key, a clear error).
    """
    bus = _session_bus()
    if bus is None:
        yield None
        return
    try:
        try:
            import secretstorage
            collection = None
            if secretstorage.check_service_availability(bus):
                collection = secretstorage.get_default_collection(bus)
                if collection.is_locked():
                    collection = None
        except Exception:
            collection = None
        yield _SecretService(collection) if collection is not None else None
    finally:
        bus.close()  # type: ignore[attr-defined]


def _keyring_locked() -> bool:
    """Whether a keyring is there but locked, for the error that says where to look."""
    bus = _session_bus()
    if bus is None:
        return False
    try:
        import secretstorage
        return bool(secretstorage.check_service_availability(bus)
                    and secretstorage.get_default_collection(bus).is_locked())
    except Exception:
        return False
    finally:
        bus.close()  # type: ignore[attr-defined]


def _keychain() -> None:
    """The Mac's Keychain: not used yet.

    A Keychain item can be locked to the app that made it, so nothing else reads it
    without asking. The lock is the app's code signature, and Memoreei.app is signed ad
    hoc, so each update is a stranger to it and would prompt. Once the app has a
    Developer ID, new keys belong here and an existing key file can move in.
    """
    return None


# --- connections ---------------------------------------------------------------------

def connect(db_path: str | Path, **kwargs: object) -> sqlcipher3.Connection:
    """A SQLCipher connection to the database, unlocked; a plain database is converted first.

    Every connection to memoreei.db comes from here (or connect_async, which wraps it).
    """
    path = str(db_path)
    key, source = _key_and_source(path)
    if db_state(path) == "plain":
        _encrypt(path, key)
    conn = sqlcipher3.connect(path, **kwargs)
    try:
        _unlock(conn, key)
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher3.DatabaseError:
        conn.close()
        raise DatabaseKeyError(
            f"{path} won't open with the key from {source}: it was encrypted with another "
            f"key, or isn't a memoreei database. If you have its key, put it in "
            f"{key_file(path)} or {DB_KEY_ENV}."
        ) from None
    return conn


def connect_async(db_path: str | Path) -> aiosqlite.Connection:
    """connect(), for aiosqlite: await the result, as with aiosqlite.connect()."""
    return aiosqlite.Connection(functools.partial(connect, db_path), iter_chunk_size=64)


def _unlock(conn: sqlcipher3.Connection, key: str) -> None:
    assert _HEX_KEY.fullmatch(key)  # it's spliced into the statement, so make sure
    conn.execute(f"PRAGMA key = \"x'{key}'\"")


def _encrypt(path: str, key: str) -> None:
    """Replace a plain SQLite database with an encrypted copy of itself.

    The copy is written beside it and checked before it's swapped in, so a crash at any
    point leaves one whole database: the plain one, or the encrypted one. Its WAL is
    folded in first, and removed before the swap, or SQLite would replay plain pages
    into an encrypted file. The old file's blocks are freed, not overwritten.
    """
    with _lock, _exclusive(path):
        if db_state(path) != "plain":  # someone else got here first
            return
        assert _HEX_KEY.fullmatch(key)
        tmp = path + ".encrypting"
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        plain = sqlcipher3.connect(path)
        try:
            plain.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            version = plain.execute("PRAGMA user_version").fetchone()[0]
            plain.execute(f"ATTACH DATABASE ? AS encrypted KEY \"x'{key}'\"", (tmp,))
            plain.execute("SELECT sqlcipher_export('encrypted')")
            plain.execute(f"PRAGMA encrypted.user_version = {int(version)}")
            plain.execute("DETACH DATABASE encrypted")
            want = plain.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        finally:
            plain.close()
        check = sqlcipher3.connect(tmp)
        try:
            _unlock(check, key)
            got = check.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
            if got != want or check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError(f"the encrypted copy of {path} didn't check out; the plain one is untouched")
        finally:
            check.close()
        for suffix in ("-wal", "-shm", "-journal"):
            with contextlib.suppress(FileNotFoundError):
                os.remove(path + suffix)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        print(f"[database] Encrypted {path}", file=sys.stderr)


@contextlib.contextmanager
def _exclusive(path: str) -> Iterator[None]:
    """One process at a time converts a database (where the platform can lock files)."""
    try:
        import fcntl
    except ImportError:
        yield
        return
    with open(path + ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
            with contextlib.suppress(FileNotFoundError):
                os.remove(path + ".lock")
