"""Signal Desktop's database, read-only, turned into messages with names and words.

The database is SQLCipher, opened with the raw key from keys.py. A message is split
three ways: its plain fields (id, type, body, sent_at, sender, timer) are columns, the
rest of Signal's MessageAttributesType is in its ``json`` column (reactions, edit
history, group changes, "deleted for everyone", link previews), and since Signal 8 its
attachments are rows in message_attachments. The reader lays the columns over the json
and adds the attachments, so the rest of this file sees one message.

Only words are kept. Attachments become labels ("[Photo] caption"), never files.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# Message types that become memoreeis. Everything else (key changes, safety numbers,
# delivery problems, "joined Signal") is bookkeeping with nothing in it worth finding.
CHAT_TYPES = ("incoming", "outgoing")
EVENT_TYPES = ("group-v2-change", "call-history", "timer-notification", "profile-change", "story")
READ_TYPES = CHAT_TYPES + EVENT_TYPES

_REQUIRED = {
    "messages": {"id", "json", "type", "sent_at", "conversationId"},
    "conversations": {"id", "json", "type"},
}

VOICE_MESSAGE_FLAG = 1
GIF_FLAG = 8

# What a change to a message changes the length of. octet_length takes it from the record
# header without reading the value, so measuring every message each round stays cheap.
_SIZE = "coalesce(octet_length(json), 0) + coalesce(octet_length(body), 0)"  # SignalService.AttachmentPointer.Flags.VOICE_MESSAGE


class SchemaChanged(Exception):
    """Signal's database isn't laid out the way this reader expects."""


@dataclass(frozen=True)
class Chat:
    id: str
    name: str
    is_group: bool


@dataclass
class SignalMessage:
    rowid: int
    message_id: str
    chat: Chat
    ts: int                      # unix seconds, as sent
    kind: str                    # Signal's message type
    from_me: bool
    sender_name: str             # "me" when from_me
    text: str                    # the searchable words, without the sender
    edits: list[str] = field(default_factory=list)   # later versions, oldest first
    deleted: bool = False        # deleted for everyone
    reactions: list[dict[str, str]] = field(default_factory=list)
    disappearing: bool = False   # had a disappearing-message timer: never stored
    size: int = 0                # length of its json and body, which change when it does


def _duration(seconds: int | float | None) -> str:
    if not seconds:
        return "off"
    seconds = int(seconds)
    for unit, length in (("week", 604800), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= length and seconds % length == 0:
            n = seconds // length
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{seconds} seconds"


def _clock(seconds: int | float | None) -> str:
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def attachment_label(att: dict[str, Any]) -> str:
    """One attachment as words: what it is, and its file name where that says something."""
    content_type = str(att.get("contentType") or "")
    name = str(att.get("fileName") or "").strip()
    if int(att.get("flags") or 0) & VOICE_MESSAGE_FLAG:
        duration = att.get("duration")
        return f"[Voice note {_clock(duration)}]" if duration else "[Voice note]"
    if content_type == "image/gif" or int(att.get("flags") or 0) & GIF_FLAG:
        return "[GIF]"
    if content_type.startswith("image/"):
        return "[Photo]"
    if content_type.startswith("video/"):
        return "[Video]"
    if content_type.startswith("audio/"):
        return f"[Audio] {name}".rstrip()
    return f"[Document] {name}".rstrip()


class SignalReader:
    """Signal Desktop's db.sqlite, opened read-only with its raw SQLCipher key."""

    def __init__(self, db_path: str | Path, key: str) -> None:
        self.db_path = Path(db_path)
        self._key = key
        self._conn: Any = None
        self._chats: dict[str, Chat] = {}
        self._names: dict[str, str] = {}   # service id (ACI/PNI) → a person's name
        self._me: str | None = None        # our own ACI
        self._calls: dict[str, dict[str, Any]] = {}

    # -- opening ---------------------------------------------------------------

    def open(self) -> "SignalReader":
        import sqlcipher3

        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlcipher3.connect(uri, uri=True, check_same_thread=False)
        try:
            conn.execute(f"PRAGMA key = \"x'{self._key}'\"")
            conn.execute("PRAGMA query_only = 1")
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception:
            conn.close()
            raise
        self._conn = conn
        self._check_schema()
        self._load_names()
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SignalReader":
        return self.open()

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _check_schema(self) -> None:
        for table, needed in _REQUIRED.items():
            have = {row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")}
            missing = needed - have
            if missing:
                raise SchemaChanged(
                    f"Signal Desktop's {table} table has changed (no {', '.join(sorted(missing))}). "
                    "This version of Memoreei can't read it."
                )

    def _load_names(self) -> None:
        rows = self._conn.execute("SELECT id, type, json FROM conversations").fetchall()
        for conv_id, conv_type, raw in rows:
            data = _loads(raw)
            is_group = conv_type == "group"
            name = _conversation_name(data, is_group) or ("Unnamed group" if is_group else "Unknown")
            self._chats[conv_id] = Chat(conv_id, name, is_group)
            if not is_group:
                for sid in (data.get("serviceId"), data.get("pni"), data.get("uuid")):
                    if sid:
                        self._names[str(sid).lower()] = name
                if data.get("e164"):
                    self._names[str(data["e164"])] = name
        row = self._conn.execute("SELECT json FROM items WHERE id = 'uuid_id'").fetchone() if self._has_table("items") else None
        if row:
            value = str(_loads(row[0]).get("value") or "")
            self._me = value.split(".")[0].lower() or None
        if self._has_table("callsHistory"):
            cols = {r[1] for r in self._conn.execute("PRAGMA table_info(callsHistory)")}
            if {"callId", "type", "direction", "status"} <= cols:
                for call_id, call_type, direction, status in self._conn.execute(
                    "SELECT callId, type, direction, status FROM callsHistory"
                ):
                    self._calls[str(call_id)] = {"type": call_type, "direction": direction, "status": status}

    def _has_table(self, name: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone() is not None

    # -- what the sync asks for -------------------------------------------------

    def chats(self) -> list[Chat]:
        return list(self._chats.values())

    def end(self) -> int:
        """The newest message's rowid, kept or not; 0 when there are none."""
        return int(self._conn.execute("SELECT coalesce(max(rowid), 0) FROM messages").fetchone()[0])

    def messages(self, after: int, upto: int) -> Iterator[SignalMessage]:
        """Messages that could become memoreeis, with after < rowid <= upto, oldest first."""
        placeholders = ",".join("?" * len(READ_TYPES))
        yield from self._read(
            f"rowid > ? AND rowid <= ? AND type IN ({placeholders})", (after, upto, *READ_TYPES)
        )

    def sizes(self, upto: int) -> dict[str, int]:
        """Every readable message's id, and the length of what can change in it, up to *upto*.

        A reaction, an edit or a deletion rewrites the message's json (and an edit or a
        deletion its body), so a new length is how a change to an old message shows,
        without reading every message every round.
        """
        placeholders = ",".join("?" * len(READ_TYPES))
        return {
            str(message_id): int(size or 0)
            for message_id, size in self._conn.execute(
                f"SELECT id, {_SIZE} FROM messages WHERE rowid <= ? AND type IN ({placeholders})",
                (upto, *READ_TYPES),
            )
        }

    def by_ids(self, message_ids: list[str]) -> Iterator[SignalMessage]:
        for start in range(0, len(message_ids), 500):
            chunk = message_ids[start:start + 500]
            yield from self._read(f"id IN ({','.join('?' * len(chunk))})", tuple(chunk))

    # Columns hold what Signal moved out of the json; the json holds the rest.
    _COLUMNS = ("id", "type", "body", "sent_at", "timestamp", "conversationId", "sourceServiceId",
                "source", "expireTimer", "isViewOnce", "isErased")

    def _read(self, where: str, params: tuple) -> Iterator[SignalMessage]:
        have = self._message_columns()
        cols = [c for c in self._COLUMNS if c in have]
        cursor = self._conn.execute(
            f"SELECT rowid, json, {_SIZE}, {', '.join(cols)} FROM messages WHERE {where} ORDER BY rowid",
            params,
        )
        while rows := cursor.fetchmany(500):
            attachments = self._attachments([r[cols.index("id") + 3] for r in rows])
            for row in rows:
                data = _loads(row[1])
                size = int(row[2] or 0)
                for name, value in zip(cols, row[3:]):
                    if value is not None:
                        data[name] = value
                if data.get("id") in attachments:
                    data["attachments"] = attachments[data["id"]]
                msg = self.to_message(int(row[0]), data, size)
                if msg is not None:
                    yield msg

    def _message_columns(self) -> set[str]:
        if not hasattr(self, "_msg_cols"):
            self._msg_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(messages)")}
        return self._msg_cols

    def _attachments(self, message_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Each message's own attachments (not its link previews or quotes), in order.

        Signal 8 keeps them in message_attachments; older ones kept them in the json,
        which is used as it is when this table isn't there.
        """
        if not message_ids or not self._has_table("message_attachments"):
            return {}
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(message_attachments)")}
        wanted = [c for c in ("contentType", "fileName", "caption", "flags", "duration",
                              "storyTextAttachmentJson", "isViewOnce") if c in have]
        # -1 (or NULL in older rows) is the message as it is now; 0… are earlier versions.
        edit_filter = "AND coalesce(editHistoryIndex, -1) = -1" if "editHistoryIndex" in have else ""
        order = "orderInMessage" if "orderInMessage" in have else "rowid"
        found: dict[str, list[dict[str, Any]]] = {}
        placeholders = ",".join("?" * len(message_ids))
        for row in self._conn.execute(
            f"SELECT messageId, {', '.join(wanted)} FROM message_attachments "
            f"WHERE messageId IN ({placeholders}) AND attachmentType = 'attachment' {edit_filter} "
            f"ORDER BY messageId, {order}",
            message_ids,
        ):
            att = {k: v for k, v in zip(wanted, row[1:]) if v is not None}
            story = att.pop("storyTextAttachmentJson", None)
            if story:
                att["textAttachment"] = _loads(story)
            found.setdefault(str(row[0]), []).append(att)
        return found

    # -- one message -------------------------------------------------------------

    def to_message(self, rowid: int, data: dict[str, Any], size: int = 0) -> SignalMessage | None:
        """One message, from its json with its columns laid over it."""
        kind = str(data.get("type") or "")
        chat = self._chats.get(str(data.get("conversationId") or ""))
        if chat is None or kind not in READ_TYPES:
            return None
        from_me = kind == "outgoing" or (
            kind == "story" and not data.get("sourceServiceId")
        ) or (self._me is not None and str(data.get("sourceServiceId") or "").lower() == self._me)
        sender = "me" if from_me else self.name_of(data.get("sourceServiceId"), data.get("source"))

        if kind in CHAT_TYPES or kind == "story":
            text = self._chat_text(data)
        elif kind == "group-v2-change":
            text = self._group_change(data.get("groupV2Change") or {})
        elif kind == "call-history":
            text = self._call(data, chat)
        elif kind == "timer-notification":
            update = data.get("expirationTimerUpdate") or {}
            who = "me" if update.get("fromSync") or _is(update.get("sourceServiceId"), self._me) else \
                self.name_of(update.get("sourceServiceId"), update.get("source"))
            setting = _duration(update.get("expireTimer"))
            text = f"{who} turned off disappearing messages" if setting == "off" \
                else f"{who} set disappearing messages to {setting}"
        else:  # profile-change
            change = data.get("profileChange") or {}
            new = str(change.get("newName") or "").strip()
            old = str(change.get("oldName") or "").strip()
            text = f"{old or sender} changed their profile name to {new}" if new else ""

        edits: list[str] = []
        history = data.get("editHistory") or []
        if len(history) > 1:
            # Newest first, and the first entry is the message as it is now.
            versions = [self._chat_text(v) for v in reversed(history)]
            text, edits = versions[0], [v for v in versions[1:] if v]

        deleted = bool(data.get("deletedForEveryone"))
        if not text.strip() and not deleted:
            return None
        return SignalMessage(
            rowid=rowid,
            message_id=str(data.get("id") or ""),
            chat=chat,
            ts=int(data.get("sent_at") or data.get("timestamp") or 0) // 1000,
            kind=kind,
            from_me=from_me,
            sender_name=sender,
            text=text.strip(),
            edits=edits,
            deleted=deleted,
            reactions=[
                {"emoji": str(r.get("emoji") or ""), "from": self._reactor(r.get("fromId"))}
                for r in data.get("reactions") or []
                if r.get("emoji")
            ],
            disappearing=bool(data.get("expireTimer")) and kind in CHAT_TYPES,
            size=size,
        )

    def name_of(self, service_id: Any, e164: Any = None) -> str:
        if service_id and _is(service_id, self._me):
            return "me"
        for k in (str(service_id).lower() if service_id else None, str(e164) if e164 else None):
            if k and k in self._names:
                return self._names[k]
        return str(e164) if e164 else "Unknown"

    def _reactor(self, conversation_id: Any) -> str:
        chat = self._chats.get(str(conversation_id or ""))
        if chat is None:
            return "Unknown"
        # Our own conversation is Note to Self; a reaction from it is ours.
        return "me" if self._me and self._own_conversation(chat.id) else chat.name

    def _own_conversation(self, conversation_id: str) -> bool:
        row = self._conn.execute(
            "SELECT json FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        return bool(row) and _is(_loads(row[0]).get("serviceId"), self._me)

    def _chat_text(self, data: dict[str, Any]) -> str:
        parts: list[str] = []
        body = str(data.get("body") or "").strip()
        if data.get("isViewOnce"):
            parts.append("[View-once media]")
        for att in data.get("attachments") or []:
            text_story = (att.get("textAttachment") or {}).get("text")
            if text_story:
                parts.append(str(text_story).strip())
                continue
            caption = str(att.get("caption") or "").strip()
            parts.append(f"{attachment_label(att)} {caption}".strip())
        if data.get("type") == "story" and parts:
            parts[0] = f"[Story] {parts[0]}"
        if body and parts and parts[-1].endswith("]"):
            parts[-1] = f"{parts[-1]} {body}"  # a phone sends a photo's caption as the body
        elif body:
            parts.append(body)
        sticker = data.get("sticker")
        if sticker:
            parts.append(f"[Sticker {sticker.get('emoji') or ''}]".replace(" ]", "]"))
        for preview in data.get("preview") or []:
            title = str(preview.get("title") or "").strip()
            if title and title not in body:
                parts.append(f"[Link] {title}")
        poll = data.get("poll")
        if poll:
            question = str(poll.get("question") or "").strip()
            options = [str(o) for o in poll.get("options") or [] if o]
            parts.append(f"[Poll] {question}" + (f" ({', '.join(options)})" if options else ""))
        for contact in data.get("contact") or []:
            name = (contact.get("name") or {})
            shown = name.get("displayName") or " ".join(
                p for p in (name.get("givenName"), name.get("familyName")) if p
            )
            parts.append(f"[Contact] {shown}".strip())
        if data.get("payment"):
            parts.append("[Payment]")
        if data.get("giftBadge"):
            parts.append("[Gift badge]")
        return "\n".join(p for p in parts if p)

    def _group_change(self, change: dict[str, Any]) -> str:
        actor = self.name_of(change.get("from")) if change.get("from") else ""
        lines = []
        for d in change.get("details") or []:
            line = self._group_detail(actor, change.get("from"), d)
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _group_detail(self, actor: str, actor_id: Any, d: dict[str, Any]) -> str:
        """One change as a sentence. Without an actor (Signal doesn't always record one),
        a member change reads from the member's side and the rest from the group's."""
        t = d.get("type")
        if not actor:
            actor_id = d.get("aci")
            actor = "Someone"
        who = self.name_of(d.get("aci") or d.get("serviceId") or d.get("pni")) if (
            d.get("aci") or d.get("serviceId") or d.get("pni")) else "someone"
        if t == "create":
            return f"{actor} created the group"
        if t == "title":
            new = str(d.get("newTitle") or "").strip()
            return f"{actor} changed the group name to {new}" if new else f"{actor} removed the group name"
        if t == "description":
            if d.get("removed"):
                return f"{actor} removed the group description"
            return f"{actor} changed the group description to {str(d.get('description') or '').strip()}"
        if t == "avatar":
            return f"{actor} {'removed' if d.get('removed') else 'changed'} the group photo"
        if t in ("member-add", "member-add-from-admin-approval"):
            return f"{who} joined the group" if _is(d.get("aci"), actor_id) else f"{actor} added {who}"
        if t == "member-add-from-invite":
            return f"{who} accepted an invitation to the group"
        if t == "member-add-from-link":
            return f"{who} joined the group via the group link"
        if t == "member-remove":
            return f"{who} left the group" if _is(d.get("aci"), actor_id) else f"{actor} removed {who}"
        if t == "member-privilege":
            role = "an admin" if d.get("newPrivilege") == 2 else "a member"
            return f"{actor} made {who} {role}"
        if t in ("pending-add-one", "pending-add-many"):
            n = d.get("count")
            return f"{actor} invited {n} people to the group" if n else f"{actor} invited {who} to the group"
        if t in ("group-link-add", "access-invite-link"):
            return f"{actor} changed the group link settings"
        if t == "group-link-remove":
            return f"{actor} turned off the group link"
        if t == "group-link-reset":
            return f"{actor} reset the group link"
        if t == "announcements-only":
            on = d.get("announcementsOnly")
            return f"{actor} {'allowed only admins' if on else 'allowed all members'} to send messages"
        if t == "summary":
            return f"{actor} changed the group"
        return ""

    def _call(self, data: dict[str, Any], chat: Chat) -> str:
        call = self._calls.get(str(data.get("callId") or ""))
        if not call:
            return ""
        kind = {"Audio": "voice call", "Video": "video call", "Group": "group call",
                "Adhoc": "call link call"}.get(str(call["type"]), "call")
        status, incoming = str(call["status"]), call["direction"] == "Incoming"
        if status in ("Missed", "MissedNotificationProfile"):
            return f"Missed {kind} from {chat.name}" if incoming else f"{chat.name} missed a {kind} from me"
        if status == "Declined":
            return f"Declined {kind} from {chat.name}" if incoming else f"{chat.name} declined a {kind}"
        if status in ("Accepted", "Joined"):
            return f"{kind.capitalize()} with {chat.name}"
        if status in ("GenericGroupCall", "Ringing", "OutgoingRing"):
            return f"{kind.capitalize()} in {chat.name}"
        return ""


def _conversation_name(data: dict[str, Any], is_group: bool) -> str:
    for k in ("name", "profileFullName", "systemGivenName", "profileName", "e164", "username"):
        value = data.get(k)
        if k == "profileFullName" and not value and data.get("profileName"):
            value = " ".join(p for p in (data.get("profileName"), data.get("profileFamilyName")) if p)
        if value and str(value).strip():
            return str(value).strip()
    return ""


def _is(service_id: Any, other: Any) -> bool:
    return bool(service_id) and bool(other) and str(service_id).lower() == str(other).lower()


def _loads(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}
