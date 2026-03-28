from __future__ import annotations

import asyncio
import email
import email.header
import email.policy
import imaplib
import os
import re
import time
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from ulid import ULID

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

SOURCE_PREFIX = "email"
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
FETCH_BATCH = 50          # messages per IMAP FETCH call
MAX_BODY_CHARS = 4000     # truncate very long emails
INTER_BATCH_SLEEP = 0.5   # seconds between batches (rate limiting)


class _HTMLStripper(HTMLParser):
    """Minimal HTML → plain-text converter."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip = True
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._chunks.append(data)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self._chunks)).strip()


def _strip_html(html: str) -> str:
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text()


def _decode_header_value(raw: str | bytes | None) -> str:
    if raw is None:
        return ""
    parts = email.header.decode_header(raw if isinstance(raw, str) else raw.decode("latin-1", errors="replace"))
    decoded_parts: list[str] = []
    for chunk, charset in parts:
        if isinstance(chunk, bytes):
            decoded_parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            decoded_parts.append(chunk)
    return " ".join(decoded_parts).strip()


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain-text body from a (possibly multipart) email."""
    plain: list[str] = []
    html: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ct == "text/plain":
                plain.append(text)
            elif ct == "text/html":
                html.append(text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html.append(text)
            else:
                plain.append(text)

    if plain:
        return "\n".join(plain).strip()
    elif html:
        return _strip_html("\n".join(html))
    return ""


class EmailConnector:
    """Sync Gmail via IMAP. Requires GMAIL_EMAIL and GMAIL_APP_PASSWORD env vars."""

    def __init__(self, email_addr: str, password: str, db: Database, embedder: Any) -> None:
        self.email_addr = email_addr
        self.password = password
        self.db = db
        self.embedder = embedder

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        conn.login(self.email_addr, self.password)
        return conn

    async def sync_folder(self, folder: str = "INBOX", max_emails: int = 200) -> int:
        """Fetch new emails from a folder since last checkpoint. Returns count ingested."""
        last_uid = await self.db.get_email_checkpoint(self.email_addr, folder)

        loop = asyncio.get_event_loop()
        raw_emails = await loop.run_in_executor(
            None, self._fetch_emails_sync, folder, last_uid, max_emails
        )
        if not raw_emails:
            return 0

        items = [self._parse_email(raw, folder) for raw in raw_emails if raw]
        items = [i for i in items if i is not None]
        if not items:
            return 0

        # Embed in batches with rate limiting
        all_embeddings: list[list[float]] = []
        for i in range(0, len(items), FETCH_BATCH):
            batch = items[i : i + FETCH_BATCH]
            texts = [item.content for item in batch]
            embeddings = await self.embedder.embed(texts)
            all_embeddings.extend(embeddings)
            if i + FETCH_BATCH < len(items):
                await asyncio.sleep(INTER_BATCH_SLEEP)

        for item, emb in zip(items, all_embeddings):
            item.embedding = emb

        await self.db.bulk_insert(items)

        # Checkpoint = highest UID seen
        newest_uid = max(
            int(item.metadata.get("uid", 0)) for item in items if item.metadata.get("uid")
        )
        if newest_uid:
            await self.db.set_email_checkpoint(self.email_addr, folder, str(newest_uid))

        return len(items)

    def _fetch_emails_sync(
        self, folder: str, last_uid: str | None, max_emails: int
    ) -> list[tuple[str, bytes] | None]:
        """Blocking IMAP fetch — runs in executor."""
        conn = self._connect()
        try:
            conn.select(folder, readonly=True)

            if last_uid:
                # Fetch only messages with UID > last checkpoint
                status, data = conn.uid("SEARCH", None, f"UID {int(last_uid)+1}:*")
            else:
                # First sync: fetch recent messages only (avoid flooding on large inboxes)
                status, data = conn.uid("SEARCH", None, "ALL")

            if status != "OK" or not data or not data[0]:
                return []

            uids = data[0].decode().split()
            if not uids:
                return []

            # Limit to most recent max_emails
            uids = uids[-max_emails:]

            results: list[tuple[str, bytes] | None] = []
            for i in range(0, len(uids), FETCH_BATCH):
                batch_uids = uids[i : i + FETCH_BATCH]
                uid_set = ",".join(batch_uids)
                status, msg_data = conn.uid("FETCH", uid_set, "(RFC822)")
                if status != "OK":
                    continue
                for j, item in enumerate(msg_data):
                    if isinstance(item, tuple) and len(item) >= 2:
                        results.append((batch_uids[j // 2] if j < len(batch_uids) * 2 else "0", item[1]))
                time.sleep(INTER_BATCH_SLEEP)

            return results
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    def _parse_email(
        self, raw: tuple[str, bytes] | None, folder: str
    ) -> MemoryItem | None:
        if raw is None:
            return None
        uid_str, raw_bytes = raw
        try:
            msg = email.message_from_bytes(raw_bytes, policy=email.policy.compat32)
        except Exception:
            return None

        subject = _decode_header_value(msg.get("Subject", "(no subject)"))
        from_raw = _decode_header_value(msg.get("From", ""))
        to_raw = _decode_header_value(msg.get("To", ""))
        _, from_addr = parseaddr(from_raw)
        from_name = from_raw.split("<")[0].strip().strip('"') or from_addr

        # Parse timestamp
        ts = int(time.time())
        date_str = msg.get("Date", "")
        if date_str:
            try:
                ts = int(parsedate_to_datetime(date_str).timestamp())
            except Exception:
                pass

        body = _extract_body(msg)
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "… [truncated]"

        message_id = msg.get("Message-ID", "").strip() or f"{self.email_addr}:{uid_str}"
        content = f"Subject: {subject}\nFrom: {from_name} <{from_addr}>\nTo: {to_raw}\n\n{body}".strip()
        if not content:
            return None

        source = f"{SOURCE_PREFIX}:{self.email_addr}:{folder}"
        source_id = f"{source}:{message_id}"

        participants: list[str] = []
        if from_name:
            participants.append(from_name)
        elif from_addr:
            participants.append(from_addr)

        return MemoryItem(
            id=str(ULID()),
            source=source,
            source_id=source_id,
            content=content,
            summary=subject or None,
            participants=participants,
            ts=ts,
            ingested_at=int(time.time()),
            metadata={
                "uid": uid_str,
                "message_id": message_id,
                "subject": subject,
                "from": from_addr,
                "to": to_raw,
                "folder": folder,
                "has_attachments": _has_attachments(msg),
            },
            embedding=None,
        )


def _has_attachments(msg: email.message.Message) -> bool:
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if "attachment" in disp:
                return True
    return False


async def sync_email(
    db: Database,
    embedder: Any,
    folder: str = "INBOX",
    max_emails: int = 200,
) -> dict:
    """Top-level sync function used by the MCP server tool."""
    email_addr = os.environ.get("GMAIL_EMAIL", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "") or os.environ.get("GMAIL_PASSWORD", "")

    if not email_addr:
        return {"error": "GMAIL_EMAIL not set in environment", "synced": 0}
    if not password:
        return {
            "error": (
                "No Gmail password set. Set GMAIL_APP_PASSWORD (app password) "
                "or GMAIL_PASSWORD in environment. Gmail requires an App Password "
                "when 2FA is enabled (https://myaccount.google.com/apppasswords)."
            ),
            "synced": 0,
        }

    connector = EmailConnector(email_addr=email_addr, password=password, db=db, embedder=embedder)
    try:
        count = await connector.sync_folder(folder=folder, max_emails=max_emails)
        return {"synced": count, "email": email_addr, "folder": folder}
    except imaplib.IMAP4.error as e:
        err = str(e)
        if "AUTHENTICATIONFAILED" in err or "authentication failed" in err.lower():
            return {
                "error": (
                    f"Gmail authentication failed for {email_addr}. "
                    "If the account has 2FA enabled, create an App Password at "
                    "https://myaccount.google.com/apppasswords and set GMAIL_APP_PASSWORD. "
                    "Gmail no longer supports plain-password IMAP access."
                ),
                "synced": 0,
            }
        return {"error": f"IMAP error: {err}", "synced": 0}
    except Exception as e:
        return {"error": str(e), "synced": 0}
