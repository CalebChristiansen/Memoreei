"""Who a search is about: a name or a number in, every way that person is stored out.

Messages name their sender in whatever form their app had to hand. iMessage keeps the
handle (``+12025550142``, an email address), WhatsApp and Signal a display name, with
the phone number beside it in metadata when there is one. A person typed into a search
is matched against all of those, joined only by an identifier both sides share: the
address book (number → name), and the number each WhatsApp or Signal message was sent
from. Two people who merely share a name are never merged; a name typed in a search
matches every sender with that name, and says so when there is more than one.

The same match is made two ways. ``Filter.sql`` narrows the keyword search inside
SQLite, and ``Filter.mask`` narrows the in-memory vector index, from the per-row columns
the index keeps for the purpose. They are built from one resolved set of labels,
numbers and chats, so they agree.
"""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

import numpy as np

from memoreei.connectors.contacts_connector import normalize_identifier

if TYPE_CHECKING:
    from memoreei.storage.database import _VectorIndex

Scope = Literal["sent", "conversation", "any_chat"]
SCOPES: tuple[str, ...] = ("sent", "conversation", "any_chat")

# More people than this from one name is a typo or a first initial, not a question.
MAX_PEOPLE = 10

# A stored row's sender, whether it came from me, its sender's number, and whether its
# chat is a group, as SQL over the memories table. Old rows can hold '' for JSON.
_META = "(CASE WHEN json_valid(metadata) THEN metadata END)"
_PARTS = "(CASE WHEN json_valid(participants) THEN participants END)"
SENDER_SQL = (
    f"CASE WHEN json_extract({_META}, '$.is_from_me') = 1 "
    f"OR json_extract({_META}, '$.type') = 'sent' THEN 'me' "
    f"ELSE coalesce(json_extract({_PARTS}, '$[0]'), '') END"
)
PHONE_SQL = f"coalesce(json_extract({_META}, '$.sender_phone'), '')"
GROUP_SQL = (
    f"coalesce(json_extract({_META}, '$.is_group'), "
    f"CASE WHEN source LIKE 'imessage:chat%' THEN 1 ELSE 0 END)"
)

_ME_WORDS = {"me", "myself"}
_PHONE_CHARS = re.compile(r"^[\d\s+().\-]+$")
_WORD = re.compile(r"[\w']+")


def fold(text: str) -> str:
    """Lowercase without accents, so 'Zoë' and 'zoe' are the same name."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def looks_like_identifier(text: str) -> bool:
    """A phone number or an email address, as opposed to a name."""
    text = text.strip()
    if "@" in text and " " not in text:
        return True
    return bool(_PHONE_CHARS.match(text)) and sum(c.isdigit() for c in text) >= 7


def _is_identifier_key(identifier: str) -> bool:
    """A contacts row keyed by a number or address, not by a chat (``signal:…``)."""
    return ":" not in identifier


def my_names() -> list[str]:
    """The names that mean the owner of these messages.

    MEMOREEI_ME (comma-separated names, numbers or addresses) wins; otherwise the
    account's full name, which is what macOS and most Linux installs fill in.
    """
    configured = os.environ.get("MEMOREEI_ME", "").strip()
    if configured:
        return [part.strip() for part in configured.split(",") if part.strip()]
    try:
        import pwd

        full = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except (ImportError, KeyError):
        full = ""
    return [full] if full else []


def matches_name(query_words: list[str], name: str) -> bool:
    """Every word typed starts some word of *name*: 'dav all' finds 'David Allen'."""
    words = _WORD.findall(fold(name))
    return bool(words) and all(any(w.startswith(q) for w in words) for q in query_words)


@dataclass
class Person:
    name: str
    identifiers: set[str] = field(default_factory=set)  # numbers and addresses
    labels: set[str] = field(default_factory=set)       # sender values as stored
    me: bool = False

    def public(self) -> dict:
        person: dict = {"name": self.name, "identifiers": sorted(self.identifiers)}
        if self.me:
            person["me"] = True
        return person


class Directory:
    """Everyone the messages and the address book know of, built once per index."""

    def __init__(self, contacts: dict[str, str], index: "_VectorIndex") -> None:
        self.contacts = contacts
        self.names_by_id = {fold(i) if "@" in i else i: n
                            for i, n in contacts.items() if _is_identifier_key(i)}
        self.ids_by_name: dict[str, set[str]] = {}
        for ident, name in self.names_by_id.items():
            self.ids_by_name.setdefault(fold(name), set()).add(ident)

        labels, counts = index.sender_counts()
        self.label_counts = dict(zip(labels, counts))
        # Every way a name is stored, by its folded form; numbers aren't names.
        self.labels_by_name: dict[str, set[str]] = {}
        for label in labels:
            if label and label != "me" and not looks_like_identifier(label):
                self.labels_by_name.setdefault(fold(label), set()).add(label)
        self.display: dict[str, str] = {}
        for label in sorted(labels, key=lambda lbl: -self.label_counts[lbl]):
            self.display.setdefault(fold(label), label)
        for name in contacts.values():
            self.display[fold(name)] = name  # the address book spells it best

        me = [fold(n) for n in my_names()]
        self.my_ids = {normalize_identifier(n) for n in me if looks_like_identifier(n)}
        self.my_names = {n for n in me if not looks_like_identifier(n)}
        for name in list(self.my_names):
            self.my_ids |= self.ids_by_name.get(name, set())

    def resolve(self, text: str) -> list[Person]:
        """Everyone *text* could mean, most-messaged first."""
        text = text.strip()
        if not text:
            return []
        if fold(text) in _ME_WORDS:
            return [self._me()]
        if looks_like_identifier(text):
            ident = normalize_identifier(text)
            if ident in self.my_ids:
                return [self._me()]
            name = self.names_by_id.get(ident) or ident
            return [Person(name=name, identifiers={ident}, labels={ident})]

        words = _WORD.findall(fold(text))
        keys = {k for k in set(self.labels_by_name) | set(self.ids_by_name)
                if matches_name(words, self.display.get(k, k))}
        people = []
        for key in keys:
            if key in self.my_names:
                people.append(self._me())
                continue
            ids = self.ids_by_name.get(key, set())
            people.append(Person(name=self.display.get(key, key), identifiers=set(ids),
                                 labels=self.labels_by_name.get(key, set()) | ids))
        people.sort(key=lambda p: (-sum(self.label_counts.get(lbl, 0) for lbl in p.labels), p.name))
        return people

    def _me(self) -> Person:
        name = next(iter(sorted(self.display.get(n, n) for n in self.my_names)), "me")
        return Person(name=name, identifiers=set(self.my_ids),
                      labels={"me"} | self.my_ids, me=True)

    def chat_sources_named(self, people: list[Person]) -> set[str]:
        """Chats the address book or a messaging app itself names after one of *people*."""
        names = {fold(p.name) for p in people}
        return {ident for ident, name in self.contacts.items()
                if not _is_identifier_key(ident) and fold(name) in names}


@dataclass
class Filter:
    """What a search is narrowed to. Empty, it narrows nothing."""

    labels: set[str] | None = None        # sender values as stored
    phones: set[str] | None = None        # metadata sender_phone values
    chats: set[str] | None = None         # whole chats (sources) the person is in
    platforms: set[str] | None = None     # 'imessage' matches 'imessage:…'
    sources: set[str] | None = None       # exact sources
    after_ts: int | None = None
    before_ts: int | None = None          # exclusive

    @property
    def empty(self) -> bool:
        return all(v is None for v in (self.labels, self.phones, self.chats, self.platforms,
                                       self.sources, self.after_ts, self.before_ts))

    @property
    def has_person(self) -> bool:
        return self.labels is not None

    def sql(self) -> tuple[str, list]:
        """A WHERE fragment over ``memories`` (no leading AND), and its parameters."""
        clauses: list[str] = []
        params: list = []
        if self.has_person:
            either = []
            for column, values in ((SENDER_SQL, self.labels), (PHONE_SQL, self.phones),
                                   ("source", self.chats)):
                if values:
                    either.append(f"{column} IN ({','.join('?' * len(values))})")
                    params.extend(sorted(values))
            clauses.append("(" + (" OR ".join(either) or "0") + ")")
        if self.platforms is not None or self.sources is not None:
            either = []
            for platform in sorted(self.platforms or ()):
                either.append("(source = ? OR substr(source, 1, ?) = ?)")
                params.extend([platform, len(platform) + 1, platform + ":"])
            if self.sources:
                either.append(f"source IN ({','.join('?' * len(self.sources))})")
                params.extend(sorted(self.sources))
            clauses.append("(" + (" OR ".join(either) or "0") + ")")
        if self.after_ts is not None:
            clauses.append("ts >= ?")
            params.append(self.after_ts)
        if self.before_ts is not None:
            clauses.append("ts < ?")
            params.append(self.before_ts)
        return " AND ".join(clauses) or "1", params

    def mask(self, index: "_VectorIndex") -> np.ndarray | None:
        """The index rows this filter keeps, or None for all of them."""
        if self.empty:
            return None
        n = len(index.ids)
        keep = np.ones(n, bool)
        if self.has_person:
            person = np.zeros(n, bool)
            if self.labels:
                person |= _isin(index.senders, self.labels)
            if self.phones:
                person |= _isin(index.phones, self.phones)
            if self.chats:
                person |= _isin(index.sources, self.chats)
            keep &= person
        if self.platforms is not None or self.sources is not None:
            wanted = set(self.sources or ())
            prefixes = tuple(p + ":" for p in self.platforms or ())
            keep &= np.fromiter(
                (s in wanted or s in (self.platforms or ()) or (bool(prefixes) and s.startswith(prefixes))
                 for s in index.sources), bool, n)
        if self.after_ts is not None:
            keep &= index.ts >= self.after_ts
        if self.before_ts is not None:
            keep &= index.ts < self.before_ts
        return keep


def _isin(column: np.ndarray, values: set[str]) -> np.ndarray:
    return np.fromiter((v in values for v in column), bool, len(column))


def parse_date(text: str, end: bool = False) -> int:
    """An ISO date or date-time as a unix time, in the server's own time zone.

    With *end*, a bare date means the end of that day, so ``before='2026-02-28'``
    still includes the 28th.
    """
    try:
        dt = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(
            f"Can't read the date {text!r}. Use YYYY-MM-DD, like '2026-03-15', "
            "or a date and time, like '2026-03-15T09:00'."
        ) from None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive means local, as dates in results are
    ts = int(dt.timestamp())
    bare_date = len(text.strip()) <= 10
    return ts + 86400 if end and bare_date else ts
