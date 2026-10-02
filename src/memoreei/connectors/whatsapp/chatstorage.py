"""Reads WhatsApp's ChatStorage.sqlite: the Mac app's database, and the iPhone's.

WhatsApp for Mac keeps every chat it has synced from the phone in plain SQLite, in the
same Core Data schema WhatsApp uses on iOS, so this also reads the copy inside an
iPhone backup. Two neighbours fill in names when they're beside it: ContactsV2.sqlite
(the address book as WhatsApp sees it) and LID.sqlite (which phone number is behind an
anonymous ``…@lid`` ID). Without them, people are named by what the messages say.

Opened read-only; WhatsApp can keep writing while this reads.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterator

from memoreei.connectors.whatsapp.messages import Chat, Kind, WhatsAppMessage

APPLE_EPOCH_OFFSET = 978307200  # seconds from 1970-01-01 to 2001-01-01

# ZMESSAGETYPE values worth keeping, and which column holds their words. Everything
# else (stickers, voice notes, reactions, calls, group events…) has none to search.
_KINDS: dict[int, Kind] = {0: "text", 1: "photo", 2: "video", 7: "link", 8: "document", 11: "gif"}

# ZSESSIONTYPE: 0 a person, 1 a group. Status updates (2), channels and broadcast lists
# aren't conversations.
_CHATS_QUERY = """
    SELECT ZCONTACTJID AS jid, ZPARTNERNAME AS name, ZSESSIONTYPE AS type
    FROM ZWACHATSESSION
    WHERE ZSESSIONTYPE IN (0, 1) AND ZCONTACTJID IS NOT NULL
      AND ZCONTACTJID NOT LIKE '%@newsletter' AND ZCONTACTJID NOT LIKE '%@broadcast'
"""

_MESSAGES_QUERY = """
    SELECT m.Z_PK AS pk, m.ZSTANZAID AS stanza, m.ZMESSAGEDATE AS date,
           m.ZISFROMME AS from_me, m.ZMESSAGETYPE AS type, m.ZTEXT AS text,
           m.ZPUSHNAME AS push_name, s.ZCONTACTJID AS chat_jid,
           g.ZMEMBERJID AS member_jid, g.ZCONTACTNAME AS member_name,
           g.ZFIRSTNAME AS member_first, i.ZTITLE AS title
    FROM ZWAMESSAGE m
    JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
    LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER
    LEFT JOIN ZWAMEDIAITEM i ON i.Z_PK = m.ZMEDIAITEM
    WHERE m.Z_PK > ? AND m.Z_PK <= ?
      AND m.ZMESSAGEDATE IS NOT NULL AND m.ZSTANZAID IS NOT NULL
      AND s.ZSESSIONTYPE IN (0, 1)
      AND s.ZCONTACTJID NOT LIKE '%@newsletter' AND s.ZCONTACTJID NOT LIKE '%@broadcast'
      AND (
        (m.ZMESSAGETYPE IN (0, 7, 8) AND TRIM(COALESCE(m.ZTEXT, '')) <> '')
        OR (m.ZMESSAGETYPE IN (1, 2, 11) AND TRIM(COALESCE(i.ZTITLE, '')) <> '')
      )
    ORDER BY m.Z_PK
"""


def _clean(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _phone_from_jid(jid: str) -> str | None:
    """+12025550142 from 12025550142@s.whatsapp.net (or …:7@s.whatsapp.net, a device)."""
    user, _, server = jid.partition("@")
    user = user.split(":", 1)[0]
    if server == "s.whatsapp.net" and user.isdigit():
        return f"+{user}"
    return None


def _plus(number: object) -> str | None:
    text = _clean(number)
    if text is None:
        return None
    return text if text.startswith("+") else f"+{text}"


class ChatStorageReader:
    """A reader over one ChatStorage.sqlite."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        # Per file: the Mac app and an iPhone backup number their rows differently.
        self.name = f"chatstorage:{self.db_path.resolve()}"
        self._conn: sqlite3.Connection | None = None
        self._book_names: dict[str, str] = {}
        self._push_names: dict[str, str] = {}
        self._phones: dict[str, str] = {}

    # ── Opening ──────────────────────────────────────────────────────────────

    def open(self) -> None:
        """Open the database and load the names. Raises sqlite3.Error if it can't."""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("SELECT 1 FROM ZWAMESSAGE LIMIT 1").fetchall()
            self._conn = conn
            self._load_names()
        except Exception:
            conn.close()
            self._conn = None
            raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "ChatStorageReader":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _neighbour(self, filename: str) -> sqlite3.Connection | None:
        path = self.db_path.with_name(filename)
        if not path.exists():
            return None
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error:
            return None

    def _rows(self, conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
        """A neighbour's rows, or none if its schema isn't what this expects."""
        try:
            return conn.execute(query).fetchall()
        except sqlite3.Error:
            return []

    def _load_names(self) -> None:
        assert self._conn is not None
        for row in self._rows(self._conn, "SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME"):
            if row["ZJID"] and _clean(row["ZPUSHNAME"]):
                self._push_names[row["ZJID"]] = _clean(row["ZPUSHNAME"])  # type: ignore[assignment]

        book = self._neighbour("ContactsV2.sqlite")
        if book is not None:
            try:
                for row in self._rows(
                    book,
                    "SELECT ZLID, ZWHATSAPPID, ZFULLNAME, ZPHONENUMBER FROM ZWAADDRESSBOOKCONTACT",
                ):
                    name, phone = _clean(row["ZFULLNAME"]), _plus(row["ZPHONENUMBER"])
                    for jid in (row["ZLID"], row["ZWHATSAPPID"]):
                        if not jid:
                            continue
                        if name:
                            self._book_names[jid] = name
                        if phone:
                            self._phones[jid] = phone
            finally:
                book.close()

        lids = self._neighbour("LID.sqlite")
        if lids is not None:
            try:
                for row in self._rows(lids, "SELECT ZIDENTIFIER, ZPHONENUMBER FROM ZWAZACCOUNT"):
                    phone = _plus(row["ZPHONENUMBER"])
                    if row["ZIDENTIFIER"] and phone:
                        self._phones.setdefault(row["ZIDENTIFIER"], phone)
                for row in self._rows(lids, "SELECT ZLID, ZPHONENUMBER FROM ZWAPHONENUMBERLIDPAIR"):
                    phone = _plus(row["ZPHONENUMBER"])
                    if row["ZLID"] and phone:
                        self._phones.setdefault(row["ZLID"], phone)
            finally:
                lids.close()

    # ── Names ────────────────────────────────────────────────────────────────

    def phone_for(self, jid: str | None) -> str | None:
        if not jid:
            return None
        return _phone_from_jid(jid) or self._phones.get(jid)

    def name_for(self, jid: str | None, *hints: object) -> str:
        """Address-book name, then what WhatsApp shows, then their own name, then a number."""
        candidates = [self._book_names.get(jid) if jid else None, *map(_clean, hints)]
        if jid:
            candidates += [self._push_names.get(jid), self.phone_for(jid)]
        for candidate in candidates:
            if candidate:
                return candidate
        return jid or "unknown"

    # ── Reading ──────────────────────────────────────────────────────────────

    def _chat(self, jid: str, name: object, session_type: int) -> Chat:
        if session_type == 1:
            return Chat(jid=jid, name=_clean(name) or jid, is_group=True)
        return Chat(jid=jid, name=self.name_for(jid, name), is_group=False)

    def chats(self) -> list[Chat]:
        assert self._conn is not None
        return [self._chat(r["jid"], r["name"], r["type"]) for r in self._conn.execute(_CHATS_QUERY)]

    def end(self) -> int:
        assert self._conn is not None
        row = self._conn.execute("SELECT MAX(Z_PK) FROM ZWAMESSAGE").fetchone()
        return int(row[0] or 0)

    def messages(self, after: int, upto: int) -> Iterator[WhatsAppMessage]:
        assert self._conn is not None
        chats = {chat.jid: chat for chat in self.chats()}
        for row in self._conn.execute(_MESSAGES_QUERY, (after, upto)):
            chat = chats.get(row["chat_jid"])
            if chat is None:
                continue
            yield self._message(row, chat)

    def _message(self, row: sqlite3.Row, chat: Chat) -> WhatsAppMessage:
        from_me = bool(row["from_me"])
        if from_me:
            sender_jid, sender = None, "me"
        elif chat.is_group:
            sender_jid = row["member_jid"]
            sender = self.name_for(
                sender_jid, row["member_name"], row["member_first"], row["push_name"]
            )
        else:
            sender_jid, sender = chat.jid, chat.name

        kind = _KINDS[int(row["type"])]
        text, title = _clean(row["text"]), _clean(row["title"])
        if kind in ("photo", "video", "gif"):
            text, title = title, None
        return WhatsAppMessage(
            position=int(row["pk"]),
            message_id=str(row["stanza"]),
            chat=chat,
            ts=int(row["date"]) + APPLE_EPOCH_OFFSET,
            from_me=from_me,
            sender_name=sender,
            sender_jid=sender_jid,
            sender_phone=self.phone_for(sender_jid),
            kind=kind,
            text=text or "",
            caption=title if kind == "document" else None,
            link_title=title if kind == "link" else None,
        )
