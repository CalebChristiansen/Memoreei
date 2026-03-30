"""Generic JSON and CSV file importers for Memoreei.

Supports any structured data file with a user-provided field mapping.
Covers LinkedIn exports, Google Chat/Hangouts takeouts, and custom formats.
"""
from __future__ import annotations

import csv
import io
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ulid import ULID

from memoreei.storage.models import MemoryItem

# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%B %d, %Y %H:%M:%S",
    "%B %d, %Y",
]


def _parse_timestamp(value: Any) -> int:
    """Auto-detect and convert a timestamp value to unix epoch (seconds)."""
    if value is None:
        return int(time.time())

    # Already a number (unix epoch or millis)
    if isinstance(value, (int, float)):
        ts = int(value)
        # If it looks like milliseconds (> year 3000 in seconds), convert
        if ts > 32503680000:
            ts = ts // 1000
        return ts

    s = str(value).strip()
    if not s:
        return int(time.time())

    # Try numeric string
    try:
        ts = int(float(s))
        if ts > 32503680000:
            ts = ts // 1000
        return ts
    except ValueError:
        pass

    # Try ISO 8601 with timezone offset like +00:00
    s_clean = s
    if s_clean.endswith("Z"):
        s_clean = s_clean[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s_clean)
        return int(dt.timestamp())
    except ValueError:
        pass

    # Try common formats
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue

    # Fallback: current time
    return int(time.time())


# ---------------------------------------------------------------------------
# Core import helpers
# ---------------------------------------------------------------------------

def _build_memory_item(
    row: dict[str, Any],
    field_mapping: dict[str, str],
    source_label: str,
    index: int,
) -> MemoryItem | None:
    """Convert a row dict to a MemoryItem using the field mapping.

    field_mapping keys are canonical names: content, sender, timestamp, source.
    Values are the actual field names in the data row.
    """
    content_field = field_mapping.get("content", "")
    sender_field = field_mapping.get("sender", "")
    timestamp_field = field_mapping.get("timestamp", "")
    source_field = field_mapping.get("source", "")

    # Content is required
    content_raw = row.get(content_field, "") if content_field else ""
    content = str(content_raw).strip() if content_raw is not None else ""
    if not content:
        return None

    sender = ""
    if sender_field:
        sender_raw = row.get(sender_field)
        if sender_raw is not None:
            sender = str(sender_raw).strip()

    ts = int(time.time())
    if timestamp_field:
        ts = _parse_timestamp(row.get(timestamp_field))

    # Source can be overridden per-row
    if source_field and row.get(source_field):
        source = f"{source_label}:{str(row[source_field]).strip()}"
    else:
        source = source_label

    formatted_content = f"{sender}: {content}" if sender else content
    source_id = f"{source}:{index}"

    return MemoryItem(
        id=str(ULID()),
        source=source,
        source_id=source_id,
        content=formatted_content,
        summary=None,
        participants=[sender] if sender else [],
        ts=ts,
        ingested_at=int(time.time()),
        metadata={"row_index": index, "source_label": source_label},
        embedding=None,
    )


# ---------------------------------------------------------------------------
# JSON import
# ---------------------------------------------------------------------------

def import_json(
    file_path: str | Path,
    field_mapping: dict[str, str],
    source_label: str = "json-import",
) -> tuple[list[MemoryItem], list[str]]:
    """Parse a JSON file (array or JSON-lines) into MemoryItems.

    Args:
        file_path: Path to the JSON or JSON-lines file.
        field_mapping: Maps canonical field names to actual field names in the data.
            Canonical names: "content" (required), "sender", "timestamp", "source".
            Example: {"content": "message_body", "sender": "from", "timestamp": "date"}
        source_label: Source label prefix for all imported items.

    Returns:
        (items, errors) — list of MemoryItems and list of error strings.
    """
    path = Path(file_path)
    errors: list[str] = []
    items: list[MemoryItem] = []

    if not path.exists():
        return [], [f"File not found: {file_path}"]

    # Try multiple encodings
    text = None
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        return [], [f"Could not decode file with any supported encoding: {file_path}"]

    # Attempt 1: standard JSON array or object
    rows: list[dict[str, Any]] = []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            rows = [r for r in parsed if isinstance(r, dict)]
        elif isinstance(parsed, dict):
            # Single object or a wrapper — look for a list value
            for v in parsed.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    rows = v
                    break
            if not rows:
                rows = [parsed]
    except json.JSONDecodeError:
        # Attempt 2: JSON-lines format
        for lineno, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    rows.append(obj)
            except json.JSONDecodeError as e:
                errors.append(f"Line {lineno}: {e}")

    if not rows:
        return [], errors + ["No records found in JSON file"]

    content_field = field_mapping.get("content", "")
    if not content_field:
        return [], ["field_mapping must include a 'content' key"]

    # Validate content field exists in first row
    sample = rows[0]
    if content_field not in sample:
        available = list(sample.keys())
        return [], [
            f"content field '{content_field}' not found in data. "
            f"Available fields: {available}"
        ]

    for i, row in enumerate(rows):
        item = _build_memory_item(row, field_mapping, source_label, i)
        if item is not None:
            items.append(item)

    return items, errors


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------

def _detect_delimiter(sample: str) -> str:
    """Sniff the delimiter from the first ~4KB of a CSV file."""
    try:
        dialect = csv.Sniffer().sniff(sample[:4096], delimiters=",\t;|")
        return dialect.delimiter
    except csv.Error:
        return ","


def import_csv(
    file_path: str | Path,
    field_mapping: dict[str, str],
    source_label: str = "csv-import",
    has_header: bool = True,
) -> tuple[list[MemoryItem], list[str]]:
    """Parse a CSV/TSV file into MemoryItems.

    Args:
        file_path: Path to the CSV, TSV, or delimiter-separated file.
        field_mapping: Maps canonical field names to column names (if has_header=True)
            or column indices as strings (if has_header=False).
            Canonical names: "content" (required), "sender", "timestamp", "source".
            Example (header): {"content": "Message", "sender": "From", "timestamp": "Date"}
            Example (no header): {"content": "2", "sender": "0", "timestamp": "1"}
        source_label: Source label prefix for all imported items.
        has_header: Whether the first row is a header row (default True).

    Returns:
        (items, errors) — list of MemoryItems and list of error strings.
    """
    path = Path(file_path)
    errors: list[str] = []
    items: list[MemoryItem] = []

    if not path.exists():
        return [], [f"File not found: {file_path}"]

    content_field = field_mapping.get("content", "")
    if not content_field:
        return [], ["field_mapping must include a 'content' key"]

    # Try multiple encodings
    text = None
    for encoding in ("utf-8", "utf-8-sig", "latin-1", "cp1252"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        return [], [f"Could not decode file with any supported encoding: {file_path}"]

    delimiter = _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    rows_raw = list(reader)
    if not rows_raw:
        return [], ["CSV file is empty"]

    if has_header:
        headers = [h.strip() for h in rows_raw[0]]
        data_rows = rows_raw[1:]

        # Validate content field
        if content_field not in headers:
            return [], [
                f"content column '{content_field}' not found in CSV headers. "
                f"Available columns: {headers}"
            ]

        for i, row in enumerate(data_rows):
            row_dict = {headers[j]: row[j] if j < len(row) else "" for j in range(len(headers))}
            item = _build_memory_item(row_dict, field_mapping, source_label, i)
            if item is not None:
                items.append(item)
    else:
        # No header — field_mapping values are column indices as strings
        data_rows = rows_raw
        for i, row in enumerate(data_rows):
            row_dict = {str(j): row[j] for j in range(len(row))}
            item = _build_memory_item(row_dict, field_mapping, source_label, i)
            if item is not None:
                items.append(item)

    return items, errors
