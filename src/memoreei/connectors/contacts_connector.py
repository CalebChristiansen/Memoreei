"""Read contacts from macOS AddressBook or a vCard file and normalize identifiers."""
from __future__ import annotations

import glob
import re
import sqlite3
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Phone number normalization
# ---------------------------------------------------------------------------

_DIGITS_RE = re.compile(r"\D")


def normalize_phone(raw: str) -> str:
    """Normalize a phone number string to E.164-ish format (+<digits>).

    Handles US numbers (10 digits → +1XXXXXXXXXX), strips formatting,
    and passes through already-formatted international numbers.
    """
    digits = _DIGITS_RE.sub("", raw)
    if not digits:
        return raw.strip()
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}"


def normalize_identifier(raw: str) -> str:
    """Normalize a phone number or leave an email address as-is."""
    raw = raw.strip()
    if "@" in raw:
        return raw.lower()
    return normalize_phone(raw)


# ---------------------------------------------------------------------------
# macOS AddressBook (sqlite)
# ---------------------------------------------------------------------------

def _addressbook_db_paths() -> list[Path]:
    pattern = str(
        Path.home() / "Library" / "Application Support" / "AddressBook"
        / "Sources" / "*" / "AddressBook-v22.abcddb"
    )
    return [Path(p) for p in glob.glob(pattern)]


def _display_name(first: str | None, last: str | None, org: str | None) -> str | None:
    parts = [p for p in (first, last) if p]
    if parts:
        return " ".join(parts)
    return org or None


def read_addressbook() -> list[tuple[str, str]]:
    """Read (normalized_identifier, display_name) pairs from macOS AddressBook.

    Returns an empty list on non-macOS or if the DB is inaccessible.
    """
    if sys.platform != "darwin":
        return []

    results: list[tuple[str, str]] = []
    for db_path in _addressbook_db_paths():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Phone numbers
            cur.execute("""
                SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, p.ZFULLNUMBER
                FROM ZABCDRECORD r
                JOIN ZABCDPHONENUMBER p ON p.ZOWNER = r.Z_PK
                WHERE p.ZFULLNUMBER IS NOT NULL
            """)
            for row in cur.fetchall():
                name = _display_name(row["ZFIRSTNAME"], row["ZLASTNAME"], row["ZORGANIZATION"])
                if name:
                    norm = normalize_identifier(row["ZFULLNUMBER"])
                    if norm:
                        results.append((norm, name))

            # Email addresses (for email-based iMessage handles)
            cur.execute("""
                SELECT r.ZFIRSTNAME, r.ZLASTNAME, r.ZORGANIZATION, e.ZADDRESS
                FROM ZABCDRECORD r
                JOIN ZABCDEMAILADDRESS e ON e.ZOWNER = r.Z_PK
                WHERE e.ZADDRESS IS NOT NULL
            """)
            for row in cur.fetchall():
                name = _display_name(row["ZFIRSTNAME"], row["ZLASTNAME"], row["ZORGANIZATION"])
                if name:
                    norm = normalize_identifier(row["ZADDRESS"])
                    if norm:
                        results.append((norm, name))

            conn.close()
        except Exception:
            pass

    return results


# ---------------------------------------------------------------------------
# vCard parser
# ---------------------------------------------------------------------------

def _vcf_field(line: str) -> tuple[str, str]:
    """Split a vCard line into (property_name, value), stripping type params."""
    if ":" not in line:
        return "", line
    prop, _, value = line.partition(":")
    prop = prop.split(";")[0].upper().strip()
    return prop, value.strip()


def read_vcf(path: Path) -> list[tuple[str, str]]:
    """Parse a .vcf file and return (normalized_identifier, display_name) pairs."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    results: list[tuple[str, str]] = []
    current_name: str | None = None
    current_phones: list[str] = []
    current_emails: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        prop, value = _vcf_field(line)

        if prop == "BEGIN" and value.upper() == "VCARD":
            current_name = None
            current_phones = []
            current_emails = []

        elif prop == "FN":
            current_name = value or None

        elif prop in ("TEL", "PHONE"):
            if value:
                current_phones.append(value)

        elif prop == "EMAIL":
            if value:
                current_emails.append(value)

        elif prop == "END" and value.upper() == "VCARD":
            if current_name:
                for phone in current_phones:
                    norm = normalize_identifier(phone)
                    if norm:
                        results.append((norm, current_name))
                for email in current_emails:
                    norm = normalize_identifier(email)
                    if norm:
                        results.append((norm, current_name))

    return results


# ---------------------------------------------------------------------------
# Combined sync — called by the iMessage connector and the CLI
# ---------------------------------------------------------------------------

async def sync_contacts(db: "Database", source: str = "addressbook") -> dict:  # type: ignore[name-defined]
    """Read contacts from AddressBook and write to the contacts table."""
    pairs = read_addressbook()
    if not pairs:
        return {"synced": 0, "source": source}
    rows = [(ident, name, source) for ident, name in pairs]
    count = await db.upsert_contacts(rows)
    return {"synced": count, "source": source}


async def import_vcf(db: "Database", path: Path) -> dict:  # type: ignore[name-defined]
    """Parse a .vcf file and write contacts to the DB."""
    pairs = read_vcf(path)
    if not pairs:
        return {"synced": 0, "error": "No contacts parsed from vCard file"}
    rows = [(ident, name, "vcf") for ident, name in pairs]
    count = await db.upsert_contacts(rows)
    return {"synced": count, "file": str(path)}
