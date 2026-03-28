from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

from ulid import ULID

from memoreei.storage.models import MemoryItem

# WhatsApp export format: [MM/DD/YY, HH:MM:SS] Sender: message
_MSG_PATTERN = re.compile(
    r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s*(\d{1,2}:\d{2}:\d{2})\]\s*([^:]+):\s*(.*)"
)

# System messages to skip (joined, left, changed group icon, etc.)
_SYSTEM_PATTERNS = [
    re.compile(r"Messages and calls are end-to-end encrypted", re.IGNORECASE),
    re.compile(r"added\s+\+?\d+", re.IGNORECASE),
    re.compile(r"left$", re.IGNORECASE),
    re.compile(r"changed the group", re.IGNORECASE),
    re.compile(r"changed their phone number", re.IGNORECASE),
    re.compile(r"was added", re.IGNORECASE),
    re.compile(r"removed\s+", re.IGNORECASE),
    re.compile(r"You were added", re.IGNORECASE),
]

_MEDIA_PATTERN = re.compile(r"<Media omitted>", re.IGNORECASE)


def _is_system_message(sender: str, content: str) -> bool:
    for pattern in _SYSTEM_PATTERNS:
        if pattern.search(content):
            return True
    return False


def _parse_timestamp(date_str: str, time_str: str) -> int:
    """Parse WhatsApp date/time strings to unix epoch."""
    # Try MM/DD/YY and MM/DD/YYYY
    for fmt in ("%m/%d/%y, %H:%M:%S", "%m/%d/%Y, %H:%M:%S"):
        try:
            dt = datetime.strptime(f"{date_str}, {time_str}", fmt)
            return int(dt.replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return int(time.time())


def parse_whatsapp_export(file_path: str | Path, source_name: str | None = None) -> list[MemoryItem]:
    """Parse a WhatsApp .txt export into a list of MemoryItems."""
    path = Path(file_path)
    if not source_name:
        source_name = f"whatsapp:{path.stem}"

    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    messages: list[MemoryItem] = []
    current_ts: int | None = None
    current_sender: str | None = None
    current_lines: list[str] = []
    current_date_str: str | None = None
    current_time_str: str | None = None

    def flush() -> None:
        if current_sender is None or not current_lines:
            return
        content = "\n".join(current_lines).strip()
        if not content:
            return
        if _is_system_message(current_sender, content):
            return

        is_media = bool(_MEDIA_PATTERN.search(content))
        metadata: dict = {}
        if is_media:
            metadata["media_omitted"] = True
            content = "[media]"

        ts = current_ts or int(time.time())
        source_id = f"{source_name}:{ts}:{current_sender}"

        item = MemoryItem(
            id=str(ULID()),
            source=source_name,
            source_id=source_id,
            content=f"{current_sender}: {content}",
            summary=None,
            participants=[current_sender],
            ts=ts,
            ingested_at=int(time.time()),
            metadata=metadata,
            embedding=None,
        )
        messages.append(item)

    for line in lines:
        match = _MSG_PATTERN.match(line)
        if match:
            flush()
            current_lines = []
            date_str, time_str, sender, msg = match.groups()
            current_date_str = date_str
            current_time_str = time_str
            current_ts = _parse_timestamp(date_str, time_str)
            current_sender = sender.strip()
            current_lines = [msg]
        else:
            # Continuation line
            if current_sender is not None:
                current_lines.append(line)

    flush()

    # Collect all unique participants from the chat
    all_participants = list({m.participants[0] for m in messages if m.participants})
    for item in messages:
        item.metadata["chat_participants"] = all_participants

    return messages
