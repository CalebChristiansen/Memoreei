"""Who holds the server's port, when it isn't free.

Two people on one computer each run their own Memoreei, and the first to start gets
3679. The second one's server can't bind it, and should say whose it is rather than
restart forever: then the fix (another MEMOREEI_PORT) is obvious. There's no automatic
port picking, because every client is configured with a fixed host and port.
"""
from __future__ import annotations

import os
import socket
import urllib.error
import urllib.request


def port_free(host: str, port: int) -> bool:
    """Whether *port* can be bound on *host*, as uvicorn is about to."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def port_owner(port: int) -> int | None:
    """The uid listening on TCP *port*, on any address. None if nobody, or not Linux.

    /proc/net/tcp lists every socket on the machine with its owner's uid: all a user can
    learn about someone else's process without root, and all that's needed here.
    """
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as f:
                next(f)
                for line in f:
                    fields = line.split()
                    local, state, uid = fields[1], fields[3], fields[7]
                    if state == "0A" and int(local.rpartition(":")[2], 16) == port:
                        return int(uid)
        except (OSError, StopIteration, ValueError, IndexError):
            continue
    return None


def user_name(uid: int) -> str:
    import pwd

    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid {uid}"


def is_memoreei(port: int) -> bool:
    """Whether what answers on *port* is a Memoreei: its dashboard says so when signed out."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/admin/", timeout=3)
    except urllib.error.HTTPError as exc:
        return exc.code == 401 and b"memoreei" in exc.read(4096).lower()
    except OSError:
        return False
    return False


def describe_holder(port: int) -> str:
    """"another Memoreei, belonging to zezima", "another of your programs", …"""
    uid = port_owner(port)
    what = "another Memoreei" if is_memoreei(port) else "another program"
    if uid is None:
        return what
    if uid == os.getuid():
        return "a Memoreei of yours that's already running" if what.endswith("Memoreei") \
            else "another of your programs"
    return f"{what}, belonging to {user_name(uid)}"
