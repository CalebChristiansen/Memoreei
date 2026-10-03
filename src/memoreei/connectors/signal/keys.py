"""Signal Desktop's database key, unsealed from wherever Signal sealed it.

Signal Desktop is an Electron app, and keeps the SQLCipher key for its database in
config.json as ``encryptedKey``: hex, sealed by Electron's safeStorage. The password that
unseals it lives in the operating system's secret store, and reading it is what asks
for consent. On a Mac that is the Keychain, which shows a prompt naming the process that
asks, so this file calls the Keychain from inside the server instead of through
``security``. On Linux it is GNOME Keyring (libsecret) or KWallet, which hand secrets to any
program in the unlocked desktop session. Choosing to connect Signal is the consent there.

The sealed form, as Chromium's os_crypt writes it:

    prefix   b"v10" or b"v11", then AES-128-CBC with an IV of 16 spaces, PKCS#7 padded
    AES key  PBKDF2-HMAC-SHA1(password, b"saltysalt", iterations, 16 bytes)
    macOS    v10, 1003 iterations, password from the Keychain (Signal Safe Storage)
    Linux    1 iteration; v11 takes its password from the keyring, v10 is b"peanuts"

What comes out is 64 hex characters, used as a raw SQLCipher key.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

KEYCHAIN_SERVICE = "Signal Safe Storage"
KEYCHAIN_ACCOUNT = "Signal Key"
LIBSECRET_ATTRIBUTES = {"application": "Signal", "xdg:schema": "chrome_libsecret_os_crypt_password_v2"}
KWALLET_FOLDER = "Signal Keys"
KWALLET_ENTRY = "Signal Safe Storage"

_SALT = b"saltysalt"
_IV = b" " * 16


class KeyUnavailable(Exception):
    """Why the key can't be had, in words for the person who clicked Connect."""


def read_db_key(signal_dir: Path) -> str:
    """The SQLCipher key for Signal Desktop's database, as 64 hex characters.

    On a Mac this may put a Keychain prompt in front of whoever is at the screen, and
    waits for their answer. Raises KeyUnavailable with a reason fit to show them.
    """
    config_path = signal_dir / "config.json"
    try:
        config = json.loads(config_path.read_text())
    except FileNotFoundError:
        raise KeyUnavailable(
            "Signal Desktop hasn't been set up here yet. Open it and link it to your phone first."
        ) from None
    except (OSError, ValueError) as exc:
        raise KeyUnavailable(f"Can't read Signal Desktop's settings ({exc}).") from None

    if isinstance(config.get("key"), str):  # Signal before 2024 kept it in the clear
        return _checked(config["key"].encode())
    sealed_hex = config.get("encryptedKey")
    if not isinstance(sealed_hex, str):
        raise KeyUnavailable("Signal Desktop's settings have no database key in them.")
    try:
        sealed = bytes.fromhex(sealed_hex)
    except ValueError:
        raise KeyUnavailable("Signal Desktop's database key isn't in a form Memoreei knows.") from None

    if sys.platform == "darwin":
        return unseal(sealed, [_keychain_password()], iterations=1003)
    backend = str(config.get("safeStorageBackend") or "")
    return unseal(sealed, _linux_passwords(sealed[:3], backend), iterations=1)


def unseal(sealed: bytes, passwords: list[bytes], iterations: int) -> str:
    """Electron safeStorage's AES-CBC, tried with each password until one gives a key."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if sealed[:3] not in (b"v10", b"v11") or len(sealed) < 19 or (len(sealed) - 3) % 16:
        raise KeyUnavailable("Signal Desktop's database key isn't in a form Memoreei knows.")
    for password in passwords:
        aes_key = hashlib.pbkdf2_hmac("sha1", password, _SALT, iterations, 16)
        decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(_IV)).decryptor()
        plain = decryptor.update(sealed[3:]) + decryptor.finalize()
        pad = plain[-1]
        if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad):
            try:
                return _checked(plain[:-pad])
            except KeyUnavailable:
                continue
    raise KeyUnavailable(
        "The password in the keyring doesn't open Signal Desktop's database key. "
        "Was Signal set up under a different keyring?"
    )


def _checked(key: bytes) -> str:
    text = key.decode("ascii", "replace")
    if len(text) != 64 or any(c not in "0123456789abcdefABCDEF" for c in text):
        raise KeyUnavailable("Signal Desktop's database key isn't in a form Memoreei knows.")
    return text.lower()


# ---------------------------------------------------------------------------
# macOS: the Keychain
# ---------------------------------------------------------------------------

_KEYCHAIN_ERRORS = {
    -128: "The Keychain prompt was cancelled. Click Connect again and choose Allow.",
    -25293: "The Keychain prompt was declined. Click Connect again and choose Allow.",
    -25300: "Signal Desktop's key isn't in the Keychain. Open Signal Desktop once, then try again.",
    -25308: (
        "macOS wouldn't show the Keychain prompt here. Connect from the dashboard "
        "on this Mac, while logged in at its screen."
    ),
}


def _keychain_password() -> bytes:
    """Signal's safeStorage password, from the login Keychain, asked for by this process.

    SecKeychainFindGenericPassword is deprecated but present in every macOS, and takes
    two C strings where SecItemCopyMatching takes a CFDictionary. Through ctypes the
    prompt names this process ("Memoreei Server"), where `security` would name itself.
    """
    import ctypes

    security = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Security.framework/Security")
    find = security.SecKeychainFindGenericPassword
    find.restype = ctypes.c_int32
    find.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
    ]
    free = security.SecKeychainItemFreeContent
    free.restype = ctypes.c_int32
    free.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

    service, account = KEYCHAIN_SERVICE.encode(), KEYCHAIN_ACCOUNT.encode()
    length, data = ctypes.c_uint32(0), ctypes.c_void_p()
    status = find(None, len(service), service, len(account), account,
                  ctypes.byref(length), ctypes.byref(data), None)
    if status != 0:
        raise KeyUnavailable(_KEYCHAIN_ERRORS.get(status, f"The Keychain said no (error {status})."))
    try:
        return ctypes.string_at(data, length.value)
    finally:
        free(None, data)


# ---------------------------------------------------------------------------
# Linux: libsecret, KWallet, or none
# ---------------------------------------------------------------------------


def _linux_passwords(prefix: bytes, backend: str) -> list[bytes]:
    """The passwords to try, by what Signal recorded as its store (safeStorageBackend)."""
    if prefix == b"v10":
        return [b"peanuts"]  # Chromium's fixed password, for when there was no keyring
    if backend.startswith("kwallet"):
        return [_kwallet_password(backend)]
    if backend in ("", "gnome_libsecret", "gnome_any", "gnome_keyring"):
        return [_libsecret_password()]
    # "basic_text" and anything newer: no keyring held it. Chromium's basic store seals
    # v11 with an empty password; peanuts is tried too, in case that ever changes.
    return [b"", b"peanuts"]


def _libsecret_password() -> bytes:
    try:
        import secretstorage
    except ImportError:
        raise KeyUnavailable("This copy of Memoreei can't reach the GNOME keyring (no secretstorage).") from None
    try:
        bus = secretstorage.dbus_init()
    except Exception:
        raise KeyUnavailable(
            "Can't reach the desktop's keyring. Connect Signal from the dashboard while "
            "logged in to this computer's desktop."
        ) from None
    try:
        items = list(secretstorage.search_items(bus, LIBSECRET_ATTRIBUTES))
        if not items:
            raise KeyUnavailable(
                "Signal Desktop's key isn't in the keyring. Open Signal Desktop once, then try again."
            )
        item = items[0]
        if item.is_locked():
            item.unlock()  # the desktop's own unlock prompt, if it has one
            if item.is_locked():
                raise KeyUnavailable("The keyring is locked. Unlock it, then click Connect again.")
        return item.get_secret()
    except KeyUnavailable:
        raise
    except Exception as exc:
        raise KeyUnavailable(f"The keyring wouldn't give up Signal's key ({type(exc).__name__}).") from None
    finally:
        bus.close()


def _kwallet_password(backend: str) -> bytes:
    """Signal's password from KWallet, over D-Bus. ``backend`` is kwallet, kwallet5 or kwallet6."""
    try:
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
    except ImportError:
        raise KeyUnavailable("This copy of Memoreei can't reach KWallet (no jeepney).") from None

    version = {"kwallet6": "6", "kwallet5": "5"}.get(backend, "")
    service = f"org.kde.kwalletd{version or ''}"
    path = f"/modules/kwalletd{version or ''}"
    wallet = DBusAddress(path, bus_name=service, interface="org.kde.KWallet")
    app_id = "Memoreei"
    try:
        conn = open_dbus_connection(bus="SESSION")
    except Exception:
        raise KeyUnavailable(
            "Can't reach the desktop's session bus. Connect Signal from the dashboard while "
            "logged in to this computer's desktop."
        ) from None
    try:
        def call(method: str, signature: str | None = None, *args: object) -> object:
            reply = conn.send_and_get_reply(new_method_call(wallet, method, signature, args), timeout=120)
            if reply.header.message_type.name == "error":
                raise KeyUnavailable(f"KWallet said no ({reply.body[0] if reply.body else method}).")
            return reply.body[0] if reply.body else None

        name = call("networkWallet")
        handle = call("open", "sxs", name, 0, app_id)  # KWallet's own unlock prompt, if locked
        if not isinstance(handle, int) or handle < 0:
            raise KeyUnavailable("KWallet stayed closed. Open it, then click Connect again.")
        try:
            secret = call("readPassword", "isss", handle, KWALLET_FOLDER, KWALLET_ENTRY, app_id)
        finally:
            call("close", "ibs", handle, False, app_id)
        if not secret:
            raise KeyUnavailable(
                "Signal Desktop's key isn't in KWallet. Open Signal Desktop once, then try again."
            )
        return str(secret).encode()
    except KeyUnavailable:
        raise
    except Exception as exc:
        raise KeyUnavailable(f"KWallet wouldn't give up Signal's key ({type(exc).__name__}).") from None
    finally:
        conn.close()
