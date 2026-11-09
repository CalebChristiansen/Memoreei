"""What a chat app did to a message after memoreei kept it: deleted, unsent or expired.

memoreei keeps its copy. The app's deletion, an unsend or a disappearing message's
timer marks the message instead, in its metadata:

    "gone": {"how": "deleted" | "unsent" | "expired", "at": <unix seconds or null>}

``at`` is when memoreei saw it go (apps rarely say when it happened), null when nobody
knows. Results say it in words ("deleted in Signal on 2026-11-09"), and because the mark
is metadata, a pairing (memoreei.peers) carries it to every copy as an edit.

MEMOREEI_MIRROR_DELETIONS in config.env turns this off for the apps it names
("signal,whatsapp", or "all"): their deletions then remove memoreei's copy too, and
that goes on to every copy as a delete. Off by default.

Messages marked before 0.5 carry ``"deleted": true`` instead (Signal's "deleted for
everyone"); they read as deleted, date unknown.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any

KEY = "gone"
HOWS = ("deleted", "unsent", "expired")
MIRROR = "MEMOREEI_MIRROR_DELETIONS"

# The apps whose connectors report deletions, by their source prefix.
APPS = {"signal": "Signal", "imessage": "iMessage", "whatsapp": "WhatsApp", "matrix": "Matrix"}


def gone(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """The mark on a message's metadata, or None if the app still has it."""
    meta = meta or {}
    mark = meta.get(KEY)
    if isinstance(mark, dict) and mark.get("how") in HOWS:
        return {"how": mark["how"], "at": mark.get("at")}
    if meta.get("deleted") is True:
        return {"how": "deleted", "at": None}
    return None


def marked(meta: dict[str, Any], how: str, at: int | None = None,
           before: dict[str, Any] | None = None) -> dict[str, Any]:
    """*meta* with the mark set. A message already marked keeps its first mark (and its
    date), so syncing again never moves "deleted on …" forward."""
    assert how in HOWS
    meta = {k: v for k, v in meta.items() if k != "deleted"}
    meta[KEY] = gone(before) or gone(meta) or {"how": how, "at": int(time.time()) if at is None else at}
    return meta


def describe(source: str, meta: dict[str, Any] | None) -> str | None:
    """"deleted in Signal on 2026-11-09", or None for a message the app still has."""
    mark = gone(meta)
    if mark is None:
        return None
    platform = source.partition(":")[0]
    words = f"{mark['how']} in {APPS.get(platform, platform.capitalize() or 'its app')}"
    if mark.get("at"):
        words += " on " + datetime.fromtimestamp(int(mark["at"])).astimezone().strftime("%Y-%m-%d")
    return words


def mirrors(platform: str) -> bool:
    """Whether this memoreei follows *platform*'s deletions instead of keeping and marking.

    Read from config.env on each look, like stay-awake, so a change reaches a running
    server at its next sync.
    """
    from memoreei.config import live_setting

    wanted = {w.strip().lower() for w in live_setting(MIRROR).split(",") if w.strip()}
    return bool(wanted & {platform.lower(), "all", "1", "true", "yes", "on"})


def too_many(gone_count: int, held: int) -> bool:
    """A sanity check before marking: more than half of what memoreei holds from an app
    vanishing at once means it is reading a different or emptied store (a new account, a
    restored Mac), not that the person deleted it all. Nothing is marked then."""
    return gone_count > 20 and gone_count * 2 > held
