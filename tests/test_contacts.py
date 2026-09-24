"""Tests for contacts connector — phone normalization, vCard parsing, DB round-trip."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from memoreei.connectors.contacts_connector import (
    normalize_phone,
    normalize_identifier,
    read_vcf,
    read_addressbook,
    _display_name,
)


# ---------------------------------------------------------------------------
# normalize_phone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("+12025550142", "+12025550142"),       # already E.164
    ("(202) 555-0142", "+12025550142"),     # US formatted
    ("202-555-0142", "+12025550142"),       # dashes
    ("2025550142", "+12025550142"),         # 10 digits, no country code
    ("12025550142", "+12025550142"),        # 11 digits starting with 1
    ("+44 20 7946 0958", "+442079460958"),  # UK international
    ("", ""),                               # empty
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


# ---------------------------------------------------------------------------
# normalize_identifier
# ---------------------------------------------------------------------------


def test_normalize_identifier_email_lowercased():
    assert normalize_identifier("Zezima@Gmail.COM") == "zezima@gmail.com"


def test_normalize_identifier_phone_normalized():
    assert normalize_identifier("(408) 555-1234") == "+14085551234"


# ---------------------------------------------------------------------------
# _display_name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("first,last,org,expected", [
    ("Saria", "Kokiri", None, "Saria Kokiri"),
    ("Saria", None, None, "Saria"),
    (None, "Kokiri", None, "Kokiri"),
    (None, None, "Grand Exchange", "Grand Exchange"),
    (None, None, None, None),
    ("", "", "", None),
])
def test_display_name(first, last, org, expected):
    result = _display_name(first or None, last or None, org or None)
    assert result == expected


# ---------------------------------------------------------------------------
# read_vcf
# ---------------------------------------------------------------------------


VCF_CONTENT = """\
BEGIN:VCARD
VERSION:3.0
FN:Saria Kokiri
TEL;TYPE=CELL:+1 (202) 555-0142
EMAIL:saria@example.com
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Zezima Lumbridge
TEL;TYPE=HOME:(650) 555-9876
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Wise Old Man
EMAIL:nophone@example.com
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:
TEL:+14085551111
END:VCARD
"""


def test_read_vcf_parses_contacts(tmp_path):
    vcf = tmp_path / "contacts.vcf"
    vcf.write_text(VCF_CONTENT)
    pairs = read_vcf(vcf)
    d = dict(pairs)
    assert d.get("+12025550142") == "Saria Kokiri"
    assert d.get("saria@example.com") == "Saria Kokiri"
    assert d.get("+16505559876") == "Zezima Lumbridge"
    assert d.get("nophone@example.com") == "Wise Old Man"


def test_read_vcf_skips_blank_name(tmp_path):
    vcf = tmp_path / "contacts.vcf"
    vcf.write_text(VCF_CONTENT)
    pairs = read_vcf(vcf)
    d = dict(pairs)
    # The contact with empty FN: should not appear
    assert "+14085551111" not in d


def test_read_vcf_missing_file(tmp_path):
    result = read_vcf(tmp_path / "nonexistent.vcf")
    assert result == []


def test_read_vcf_empty_file(tmp_path):
    vcf = tmp_path / "empty.vcf"
    vcf.write_text("")
    assert read_vcf(vcf) == []


# ---------------------------------------------------------------------------
# read_addressbook — non-macOS
# ---------------------------------------------------------------------------


def test_read_addressbook_non_macos_returns_empty():
    with patch.object(sys, "platform", "linux"):
        result = read_addressbook()
    assert result == []


# ---------------------------------------------------------------------------
# Database round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_get_contacts(tmp_path):
    from memoreei.storage.database import Database

    db = Database(db_path=str(tmp_path / "test.db"))
    await db.connect()

    contacts = [
        ("+12025550142", "Saria Kokiri", "vcf"),
        ("saria@example.com", "Saria Kokiri", "vcf"),
        ("+16505559876", "Zezima Lumbridge", "addressbook"),
    ]
    count = await db.upsert_contacts(contacts)
    assert count == 3

    result = await db.get_contacts()
    assert result["+12025550142"] == "Saria Kokiri"
    assert result["saria@example.com"] == "Saria Kokiri"
    assert result["+16505559876"] == "Zezima Lumbridge"

    await db.close()


@pytest.mark.asyncio
async def test_upsert_contacts_overwrites_existing(tmp_path):
    from memoreei.storage.database import Database

    db = Database(db_path=str(tmp_path / "test.db"))
    await db.connect()

    await db.upsert_contacts([("+12025550142", "Old Name", "vcf")])
    await db.upsert_contacts([("+12025550142", "New Name", "addressbook")])

    result = await db.get_contacts()
    assert result["+12025550142"] == "New Name"

    await db.close()


@pytest.mark.asyncio
async def test_resolve_identifier(tmp_path):
    from memoreei.storage.database import Database

    db = Database(db_path=str(tmp_path / "test.db"))
    await db.connect()

    await db.upsert_contacts([("+12025550142", "Saria Kokiri", "vcf")])

    assert await db.resolve_identifier("+12025550142") == "Saria Kokiri"
    assert await db.resolve_identifier("+19999999999") is None

    await db.close()


# ---------------------------------------------------------------------------
# _resolve_source_name (memory_tools helper)
# ---------------------------------------------------------------------------


def test_resolve_source_name_imessage():
    from memoreei.tools.memory_tools import _resolve_source_name

    contacts = {"+12025550142": "Saria Kokiri"}
    assert _resolve_source_name("imessage:+12025550142", contacts) == "Saria Kokiri"


def test_resolve_source_name_no_match():
    from memoreei.tools.memory_tools import _resolve_source_name

    contacts = {}
    assert _resolve_source_name("imessage:+12025550142", contacts) is None


def test_resolve_source_name_no_prefix():
    from memoreei.tools.memory_tools import _resolve_source_name

    contacts = {"manual": "something"}
    assert _resolve_source_name("manual", contacts) is None


def test_resolve_source_name_email_handle():
    from memoreei.tools.memory_tools import _resolve_source_name

    contacts = {"saria@example.com": "Saria Kokiri"}
    assert _resolve_source_name("imessage:saria@example.com", contacts) == "Saria Kokiri"
