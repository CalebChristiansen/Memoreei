from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ulid import ULID

from memoreei.storage.models import MemoryItem


def _contact_label(contact_name: str, address: str) -> str:
    """Return contact_name if meaningful, else the phone address."""
    name = (contact_name or "").strip()
    if name and name.lower() not in ("null", "(unknown)", "unknown", ""):
        return name
    return (address or "").strip()


def _parse_sms_element(elem: ET.Element) -> MemoryItem | None:
    """Parse a single <sms> element into a MemoryItem."""
    address = elem.get("address", "").strip()
    date_ms = elem.get("date", "0")
    msg_type = elem.get("type", "1")
    body = (elem.get("body") or "").strip()
    contact_name = elem.get("contact_name", "")

    if not body or body.lower() == "null":
        return None

    ts = int(date_ms) // 1000  # ms → seconds
    contact = _contact_label(contact_name, address)
    source = f"sms:{contact}"

    is_sent = msg_type == "2"
    sender = "me" if is_sent else contact
    source_id = f"sms:{address}:{date_ms}"

    return MemoryItem(
        id=str(ULID()),
        source=source,
        source_id=source_id,
        content=f"{sender}: {body}",
        summary=None,
        participants=[contact, "me"],
        ts=ts,
        ingested_at=int(time.time()),
        metadata={
            "address": address,
            "contact_name": contact,
            "type": "sent" if is_sent else "received",
            "message_type": "sms",
        },
        embedding=None,
    )


def _parse_mms_element(elem: ET.Element) -> MemoryItem | None:
    """Parse a single <mms> element, extracting text/plain parts."""
    address = elem.get("address", "").strip()
    date_ms = elem.get("date", "0")
    msg_box = elem.get("msg_box", "1")  # 1=received, 2=sent
    contact_name = elem.get("contact_name", "")

    # Extract text from <parts><part ct="text/plain" text="...">
    text_parts: list[str] = []
    parts_elem = elem.find("parts")
    if parts_elem is not None:
        for part in parts_elem.findall("part"):
            if part.get("ct", "") == "text/plain":
                text = (part.get("text") or "").strip()
                if text and text.lower() != "null":
                    text_parts.append(text)

    body = " ".join(text_parts).strip()
    if not body:
        return None

    ts = int(date_ms) // 1000
    contact = _contact_label(contact_name, address)
    source = f"sms:{contact}"

    is_sent = msg_box == "2"
    sender = "me" if is_sent else contact
    source_id = f"mms:{address}:{date_ms}"

    return MemoryItem(
        id=str(ULID()),
        source=source,
        source_id=source_id,
        content=f"{sender}: {body}",
        summary=None,
        participants=[contact, "me"],
        ts=ts,
        ingested_at=int(time.time()),
        metadata={
            "address": address,
            "contact_name": contact,
            "type": "sent" if is_sent else "received",
            "message_type": "mms",
        },
        embedding=None,
    )


def parse_sms_backup(file_path: str | Path) -> list[MemoryItem]:
    """Parse an Android SMS Backup & Restore XML file into MemoryItems.

    Uses iterparse (streaming) to handle large backup files without loading
    the entire XML into memory.
    """
    path = Path(file_path)
    messages: list[MemoryItem] = []

    for _event, elem in ET.iterparse(str(path), events=("end",)):
        if elem.tag == "sms":
            item = _parse_sms_element(elem)
            if item:
                messages.append(item)
            elem.clear()
        elif elem.tag == "mms":
            item = _parse_mms_element(elem)
            if item:
                messages.append(item)
            elem.clear()

    return messages
