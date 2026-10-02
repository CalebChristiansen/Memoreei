"""What every WhatsApp reader hands the shared sync: messages, already named.

A reader knows one place WhatsApp keeps its chats (the Mac app's database today) and
turns it into these. Everything after that, how a message becomes a memory, where the
sync got to and what a chat is called, is the same whichever reader it came from.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Literal, Protocol

Kind = Literal["text", "photo", "video", "gif", "link", "document"]


@dataclass(frozen=True)
class Chat:
    jid: str           # 12025550142@s.whatsapp.net, 120363…@g.us, or an anonymous …@lid
    name: str          # what WhatsApp calls it: the contact, or the group's subject
    is_group: bool


@dataclass(frozen=True)
class WhatsAppMessage:
    position: int        # where the reader is up to; only ever grows, in its own units
    message_id: str      # WhatsApp's own ID for the message
    chat: Chat
    ts: int              # unix seconds
    from_me: bool
    sender_name: str     # "me" when from_me
    sender_jid: str | None
    sender_phone: str | None  # +12025550142, when the reader can tell
    kind: Kind
    text: str            # the words: message text, caption, or a document's file name
    caption: str | None = None     # a document's caption; text holds its file name
    link_title: str | None = None  # a shared link's page title


class Reader(Protocol):
    """One place WhatsApp messages can be read from."""

    #: Names this reader's checkpoint. Two readers never share one.
    name: str

    def chats(self) -> list[Chat]:
        """Every chat the reader can see, for naming sources."""
        ...

    def end(self) -> int:
        """The position of the newest message, read or skipped; 0 when there are none."""
        ...

    def messages(self, after: int, upto: int) -> Iterator[WhatsAppMessage]:
        """Messages worth keeping with after < position <= upto, oldest first."""
        ...
