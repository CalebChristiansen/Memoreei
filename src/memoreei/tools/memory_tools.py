from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ulid import ULID

from memoreei.connectors.contacts_connector import import_vcf, normalize_identifier, sync_contacts
from memoreei.connectors.discord_connector import sync_discord
from memoreei.connectors.generic_connector import import_json, import_csv
from memoreei.connectors.discord_package_connector import import_discord_package as _import_discord_package
from memoreei.connectors.imessage_connector import sync_imessage
from memoreei.connectors.whatsapp import sync_whatsapp
from memoreei.connectors.signal import sync_signal
from memoreei.connectors.email_connector import sync_email
from memoreei.connectors.mastodon_connector import sync_mastodon
from memoreei.connectors.matrix_connector import sync_matrix
from memoreei.connectors.slack_connector import sync_slack
from memoreei.connectors.telegram_connector import sync_telegram
from memoreei.connectors.instagram_connector import parse_instagram_export
from memoreei.connectors.messenger_connector import parse_messenger_export
from memoreei.connectors.sms_connector import parse_sms_backup
from memoreei.search.embeddings import EmbeddingProvider
from memoreei.search.hybrid import HybridSearch
from memoreei.search.people import (
    MAX_PEOPLE, SCOPES, Directory, Filter, Person, fold, looks_like_identifier, parse_date,
)
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem


def _resolve_source_name(source: str, contacts: dict[str, str]) -> str | None:
    """Given a source like 'imessage:+12025550142', return the contact name if known."""
    if ":" not in source:
        return None
    if source in contacts:
        return contacts[source]  # WhatsApp and Signal name their chats by source
    _, _, identifier = source.partition(":")
    if looks_like_identifier(identifier):
        identifier = normalize_identifier(identifier)
    return contacts.get(identifier)


def sender_of(item: MemoryItem) -> str:
    """Who sent *item*, as stored: 'me', a handle, or a display name."""
    meta = item.metadata
    if meta.get("is_from_me") is True or meta.get("type") == "sent":
        return "me"
    return item.participants[0] if item.participants else ""


def _local_date(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M")


def present(item: MemoryItem, contacts: dict[str, str]) -> dict[str, Any]:
    """A memory as the tools return it: who, where and when, in plain words."""
    meta = item.metadata
    sender = sender_of(item)
    if sender != "me":
        ident = normalize_identifier(sender) if looks_like_identifier(sender) else None
        phone = meta.get("sender_phone")
        sender = (contacts.get(ident or "") or (contacts.get(phone) if phone else None)
                  or sender or "unknown")
    platform, _, chat_id = item.source.partition(":")
    group = bool(meta.get("is_group")) or item.source.startswith("imessage:chat")
    chat = (meta.get("chat_name") if group else None) or _resolve_source_name(item.source, contacts) \
        or meta.get("chat_name") or ("unnamed group" if group else chat_id or item.source)
    row: dict[str, Any] = {
        "id": item.id,
        "date": _local_date(item.ts),
        "platform": platform,
        "chat": chat,
        "group": group,
        "sender": sender,
        "content": item.content,
        "source": item.source,
    }
    if meta.get("deleted"):
        row["deleted"] = True
    return row


class MemoryTools:
    def __init__(self, db: Database, embedder: EmbeddingProvider) -> None:
        self.db = db
        self.embedder = embedder
        self.search = HybridSearch(db=db, embedder=embedder)

    async def search_memoreei(
        self,
        query: str,
        limit: int = 10,
        source: str | None = None,
        participant: str | None = None,
        participant_scope: str = "conversation",
        after: str | None = None,
        before: str | None = None,
        weak_match_note: bool = False,
    ) -> dict[str, Any]:
        """Search, narrowed first by any filters; see server.search_memoreei for the contract.

        With *weak_match_note*, say so when no message contains all the query's words:
        semantic search always returns its nearest neighbours, and a small model can't
        otherwise tell "the answer" from "the least unrelated".
        """
        if participant_scope not in SCOPES:
            raise ValueError(
                f"participant_scope must be one of {', '.join(SCOPES)}, not {participant_scope!r}."
            )
        limit = max(1, min(int(limit), 100))
        contacts = await self.db.get_contacts()
        out: dict[str, Any] = {"results": []}
        filt = Filter()
        described: list[str] = []

        if source:
            source = source.strip()
            platforms = await self.db.platforms()
            if source.lower() in platforms:
                filt.platforms = {source.lower()}
            elif source in await self.db.list_sources():
                filt.sources = {source}
            else:
                hint = "" if ":" in source else (
                    " To search one person's messages, pass their name or number as "
                    "participant instead.")
                raise ValueError(
                    f"There is no source {source!r}. Use a platform ({', '.join(platforms)}) "
                    f"or a source id exactly as list_sources gives it.{hint}"
                )
            described.append(f"source {source!r}")
        if after:
            filt.after_ts = parse_date(after)
            described.append(f"after {after}")
        if before:
            filt.before_ts = parse_date(before, end=True)
            described.append(f"before {before}")
        if filt.after_ts is not None and filt.before_ts is not None and filt.after_ts >= filt.before_ts:
            raise ValueError(f"after ({after}) must be earlier than before ({before}).")

        if participant and participant.strip():
            index = await self.db.search_index()
            directory = index.directory
            if directory is None or directory.contacts != contacts:
                directory = index.directory = Directory(contacts, index)
            people = directory.resolve(participant)
            if not people:
                out["people"] = []
                out["note"] = (
                    f"Nobody called {participant!r} is in the messages or the address book. "
                    "Check the spelling, try just part of the name, or give a phone number; "
                    "or search without participant."
                )
                return out
            if len(people) > MAX_PEOPLE:
                names = ", ".join(p.name for p in people[:MAX_PEOPLE])
                out["people"] = [p.public() for p in people[:MAX_PEOPLE]]
                out["note"] = (
                    f"{participant!r} could be {len(people)} people ({names}, …). Nothing was "
                    "searched: give more of the name, or a phone number."
                )
                return out
            out["people"] = [p.public() for p in people]
            notes = []
            if any(p.me for p in people) and participant_scope != "sent":
                notes.append(
                    f"{participant!r} is the owner of these messages, and with participant_scope "
                    f"{participant_scope!r} that covers every chat, so it narrowed nothing. "
                    "Use participant_scope 'sent' for only what they wrote."
                )
                people = [p for p in people if not p.me]
            if people:
                self._narrow_to(filt, people, participant_scope, index, directory,
                                by_name=not looks_like_identifier(participant))
                described.append(f"participant {participant!r}")
            if len(out["people"]) > 1:
                notes.append(
                    f"{participant!r} matched {len(out['people'])} people: "
                    f"{', '.join(p['name'] for p in out['people'])}. All of them were searched; "
                    "give a full name or a number to narrow it."
                )
            if notes:
                out["note"] = " ".join(notes)

        stats: dict[str, int] = {}
        scored = await self.search.search_items(query, limit, filt, stats)
        out["results"] = [present(item, contacts) for _, item in scored]
        if weak_match_note and out["results"] and not stats.get("keyword_hits"):
            weak = ("No message contains all of these words; these are only the nearest by "
                    "meaning. If none of them answers the question, try other words, and if "
                    "those fail too, it probably isn't in the messages.")
            out["note"] = f"{out['note']} {weak}" if out.get("note") else weak
        if not out["results"]:
            within = f" within {', '.join(described)}" if described else ""
            empty = (f"Nothing matched{within}. Try other words"
                     + (", or fewer filters." if described else "."))
            out["note"] = f"{out['note']} {empty}" if out.get("note") else empty
        return out

    @staticmethod
    def _narrow_to(filt: Filter, people: list[Person], scope: str, index: Any,
                   directory: Directory, by_name: bool) -> None:
        """Set *filt* to *people*'s messages, and their chats as far as *scope* says."""
        filt.labels = set().union(*(p.labels for p in people))
        filt.phones = set().union(*(p.identifiers for p in people))
        if scope == "sent":
            return
        sent = Filter(labels=filt.labels, phones=filt.phones).mask(index)
        theirs = set(index.sources[sent].tolist())
        group_sources = set(index.sources[index.groups].tolist())
        chats = {s for s in theirs if s not in group_sources}
        for p in people:
            for ident in p.identifiers:
                chats |= {f"imessage:{ident}", f"sms:{ident}"}
            if by_name:
                chats.add(f"sms:{p.name}")
        if by_name:
            chats |= directory.chat_sources_named(people) - group_sources
        if scope == "any_chat":
            chats |= theirs
        filt.chats = chats

    async def get_context(
        self, memory_id: str, before: int = 5, after: int = 5
    ) -> dict[str, Any]:
        before, after = max(0, min(int(before), 50)), max(0, min(int(after), 50))
        items = await self.db.get_context(memory_id, before=before, after=after)
        if not items:
            raise ValueError(
                f"No message has the id {memory_id!r}. Ids come from the results of "
                "search_memoreei; pass one exactly as given."
            )
        contacts = await self.db.get_contacts()
        messages = [present(item, contacts) for item in items]
        for message in messages:
            if message["id"] == memory_id:
                message["hit"] = True
        return {"messages": messages}

    async def add_memoreei(
        self,
        content: str,
        source: str = "manual",
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        embedding = await self.embedder.embed_query(content)
        item = MemoryItem(
            id=str(ULID()),
            source=source,
            source_id=None,
            content=content,
            summary=None,
            participants=[],
            ts=int(time.time()),
            ingested_at=int(time.time()),
            metadata=metadata or {},
            embedding=embedding,
        )
        memory_id = await self.db.insert_memory(item)
        return {"id": memory_id, "source": source, "content": content}

    async def list_sources(self) -> dict[str, Any]:
        """Every chat, grouped by the person it is with; group chats on their own."""
        summary = await self.db.source_summary()
        contacts = await self.db.get_contacts()
        people: dict[str, dict[str, Any]] = {}
        groups: list[dict[str, Any]] = []
        other: list[dict[str, Any]] = []
        for row in summary:
            source, count = row["source"], row["count"]
            name = _resolve_source_name(source, contacts) or row.get("chat_name")
            if row["group"]:
                groups.append({"name": name or "unnamed group", "source": source, "messages": count})
            elif name:
                person = people.setdefault(fold(name), {"name": name, "messages": 0, "sources": {}})
                person["messages"] += count
                person["sources"][source] = count
            else:
                other.append({"source": source, "messages": count})
        return {
            "total": sum(r["count"] for r in summary),
            "platforms": sorted({r["source"].partition(":")[0] for r in summary}),
            "people": sorted(people.values(), key=lambda p: -p["messages"]),
            "groups": groups,
            "other": other,
        }

    async def sync_contacts_tool(self) -> dict[str, Any]:
        """Sync contacts from macOS AddressBook into the contacts table."""
        return await sync_contacts(self.db)

    async def sync_discord_tool(self, channel_id: str | None = None) -> dict[str, Any]:
        return await sync_discord(db=self.db, embedder=self.embedder, channel_id=channel_id)

    async def sync_telegram_tool(self, chat_id: str | None = None) -> dict[str, Any]:
        return await sync_telegram(db=self.db, embedder=self.embedder, chat_id=chat_id)

    async def sync_matrix_tool(self, room_id: str | None = None) -> dict[str, Any]:
        return await sync_matrix(db=self.db, embedder=self.embedder, room_id=room_id)

    async def sync_slack_tool(self, channel_id: str | None = None) -> dict[str, Any]:
        return await sync_slack(db=self.db, embedder=self.embedder, channel_id=channel_id)

    async def sync_email_tool(
        self, folder: str = "INBOX", max_emails: int = 200
    ) -> dict[str, Any]:
        return await sync_email(db=self.db, embedder=self.embedder, folder=folder, max_emails=max_emails)

    async def sync_mastodon_tool(
        self,
        instance: str | None = None,
        hashtag: str | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        return await sync_mastodon(
            db=self.db,
            embedder=self.embedder,
            instance=instance,
            hashtag=hashtag,
            access_token=access_token,
        )

    async def sync_imessage_tool(self, chat_name: str | None = None) -> dict[str, Any]:
        await sync_contacts(self.db)  # refresh contacts before syncing messages
        return await sync_imessage(db=self.db, embedder=self.embedder, chat_name=chat_name)

    async def sync_whatsapp_tool(self) -> dict[str, Any]:
        """Sync WhatsApp from WhatsApp for Mac's database (or WHATSAPP_DB_PATH)."""
        return await sync_whatsapp(db=self.db, embedder=self.embedder)

    async def import_contacts_vcf(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}", "synced": 0}
        return await import_vcf(self.db, path)

    async def sync_signal_tool(self) -> dict[str, Any]:
        """Sync Signal Desktop with the key saved when Signal was connected."""
        return await sync_signal(db=self.db, embedder=self.embedder)

    async def import_sms_backup(self, file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        if not path.exists():
            return {"error": f"File not found: {file_path}", "ingested": 0}
        if path.suffix.lower() not in (".xml",):
            return {"error": f"Expected a .xml file, got: {path.suffix}", "ingested": 0}

        items = parse_sms_backup(path)
        if not items:
            return {"error": "No messages parsed from file", "ingested": 0}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        sources = list({item.source for item in items})
        return {
            "ingested": count,
            "file": str(path),
            "sources": sources,
        }

    async def import_discord_package_tool(self, package_path: str) -> dict[str, Any]:
        return await _import_discord_package(package_path=package_path, db=self.db, embedder=self.embedder)

    async def import_instagram(self, data_path: str) -> dict[str, Any]:
        path = Path(data_path)
        if not path.exists():
            return {"error": f"Path not found: {data_path}", "ingested": 0}

        items = parse_instagram_export(path)
        if not items:
            return {"error": "No messages parsed from export", "ingested": 0}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        sources = list({item.source for item in items})
        return {
            "ingested": count,
            "path": str(path),
            "sources": sources,
        }

    async def import_json_file(
        self,
        file_path: str,
        content_field: str,
        sender_field: str = "",
        timestamp_field: str = "",
        source_label: str = "json-import",
    ) -> dict[str, Any]:
        field_mapping: dict[str, str] = {"content": content_field}
        if sender_field:
            field_mapping["sender"] = sender_field
        if timestamp_field:
            field_mapping["timestamp"] = timestamp_field

        items, errors = import_json(file_path, field_mapping, source_label)
        if not items:
            return {"error": errors[0] if errors else "No messages parsed", "ingested": 0, "errors": errors}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        result: dict[str, Any] = {"ingested": count, "file": str(file_path), "source": source_label}
        if errors:
            result["parse_errors"] = errors
        return result

    async def import_csv_file(
        self,
        file_path: str,
        content_column: str,
        sender_column: str = "",
        timestamp_column: str = "",
        source_label: str = "csv-import",
    ) -> dict[str, Any]:
        field_mapping: dict[str, str] = {"content": content_column}
        if sender_column:
            field_mapping["sender"] = sender_column
        if timestamp_column:
            field_mapping["timestamp"] = timestamp_column

        items, errors = import_csv(file_path, field_mapping, source_label)
        if not items:
            return {"error": errors[0] if errors else "No messages parsed", "ingested": 0, "errors": errors}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        result: dict[str, Any] = {"ingested": count, "file": str(file_path), "source": source_label}
        if errors:
            result["parse_errors"] = errors
        return result

    async def import_messenger(self, data_path: str) -> dict[str, Any]:
        path = Path(data_path)
        if not path.exists():
            return {"error": f"Path not found: {data_path}", "ingested": 0}

        items = parse_messenger_export(path)
        if not items:
            return {"error": "No messages parsed from export", "ingested": 0}

        texts = [item.content for item in items]
        embeddings = await self.embedder.embed(texts)
        for item, emb in zip(items, embeddings):
            item.embedding = emb

        count = await self.db.bulk_insert(items)
        sources = list({item.source for item in items})
        return {
            "ingested": count,
            "path": str(path),
            "sources": sources,
        }
