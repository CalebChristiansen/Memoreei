"""What the chat app deletes, kept and marked (memoreei.marks), across the connectors that
can see it, and down a chain of paired memoreeis. Signal's own cases are in test_signal."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from memoreei import marks
from memoreei.connectors.imessage_connector import IMessageConnector
from memoreei.connectors.matrix_connector import MatrixConnector
from memoreei.connectors.whatsapp import sync_whatsapp
from memoreei.tools.memory_tools import MemoryTools

from tests.test_peers import contents, memoreeis, msg, net, pair, push_all  # noqa: F401  (fixtures)


def _mirror(monkeypatch, value: str) -> None:
    monkeypatch.setenv(marks.MIRROR, value)
    monkeypatch.setattr("memoreei.config._process_env_keys", frozenset({marks.MIRROR}))


async def _meta(db, source_id: str) -> dict:
    async with db._db.execute("SELECT metadata FROM memories WHERE source_id = ?", (source_id,)) as cur:
        row = await cur.fetchone()
    return json.loads(row[0]) if row else None


# ── The mark itself ───────────────────────────────────────────────────────────


def test_the_mark_in_words():
    at = 1_786_000_000  # 2026-08
    assert marks.describe("signal:abc", {"gone": {"how": "deleted", "at": at}}).startswith("deleted in Signal on 2026-")
    assert marks.describe("whatsapp:x", {"gone": {"how": "expired", "at": None}}) == "expired in WhatsApp"
    assert marks.describe("imessage:+12025550142", {"gone": {"how": "unsent", "at": None}}) == "unsent in iMessage"
    assert marks.describe("signal:abc", {"deleted": True}) == "deleted in Signal"  # before 0.5
    assert marks.describe("signal:abc", {}) is None


def test_a_mark_keeps_its_first_date():
    first = marks.marked({}, "deleted", at=100)
    assert marks.marked(first, "deleted", at=200)["gone"] == {"how": "deleted", "at": 100}
    assert marks.marked({"x": 1}, "expired", at=5, before=first)["gone"]["at"] == 100


def test_mirroring_is_per_app(monkeypatch):
    assert not marks.mirrors("signal")
    _mirror(monkeypatch, "signal, whatsapp")
    assert marks.mirrors("signal") and marks.mirrors("WhatsApp") and not marks.mirrors("imessage")
    _mirror(monkeypatch, "all")
    assert marks.mirrors("imessage")


def test_mirroring_is_read_from_config_env_as_it_stands(isolated_home):
    (isolated_home / "config.env").write_text(f"{marks.MIRROR}=imessage\n")
    assert marks.mirrors("imessage") and not marks.mirrors("signal")


# ── iMessage: unsends and deletions ───────────────────────────────────────────

_IMSG = """
CREATE TABLE handle (rowid INTEGER PRIMARY KEY, id TEXT);
CREATE TABLE chat (rowid INTEGER PRIMARY KEY, chat_identifier TEXT, display_name TEXT, room_name TEXT);
CREATE TABLE message (rowid INTEGER PRIMARY KEY, guid TEXT, text TEXT, handle_id INTEGER, service TEXT,
    date INTEGER, is_from_me INTEGER, date_edited INTEGER DEFAULT 0, date_retracted INTEGER DEFAULT 0);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
"""
LINK = "+12025550142"


@pytest.fixture
def chat_db(tmp_path) -> Path:
    path = tmp_path / "chat.db"
    conn = sqlite3.connect(path)
    conn.executescript(_IMSG)
    conn.execute("INSERT INTO handle VALUES (1, ?)", (LINK,))
    conn.execute("INSERT INTO chat VALUES (1, ?, 'Link', NULL)", (LINK,))
    for n, text in enumerate(["the master sword is in the woods", "wrong chat", "see you at Hyrule castle"], 1):
        conn.execute("INSERT INTO message (rowid, guid, text, handle_id, service, date, is_from_me) "
                     "VALUES (?, ?, ?, 1, 'iMessage', ?, 0)", (n, f"G{n}", text, 730000000 + n))
        conn.execute("INSERT INTO chat_message_join VALUES (1, ?)", (n,))
    conn.commit()
    conn.close()
    return path


async def _imessage(db, embedder, path: Path) -> IMessageConnector:
    connector = IMessageConnector(db=db, embedder=embedder, db_path=str(path))
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        await connector.sync()
    return connector


async def test_imessage_unsent_and_deleted_are_kept_and_marked(temp_db, mock_embedder, chat_db):
    await _imessage(temp_db, mock_embedder, chat_db)
    conn = sqlite3.connect(chat_db)
    conn.execute("UPDATE message SET text = NULL, date_edited = 1, date_retracted = 1 WHERE guid = 'G2'")
    conn.execute("DELETE FROM chat_message_join WHERE message_id = 3")  # Recently Deleted
    conn.commit()
    conn.close()
    connector = await _imessage(temp_db, mock_embedder, chat_db)
    assert connector.marked == 2
    assert (await _meta(temp_db, f"imessage:{LINK}:G2"))["gone"]["how"] == "unsent"
    assert (await _meta(temp_db, f"imessage:{LINK}:G3"))["gone"]["how"] == "deleted"
    assert "gone" not in await _meta(temp_db, f"imessage:{LINK}:G1")
    assert (await _imessage(temp_db, mock_embedder, chat_db)).marked == 0

    hits = (await MemoryTools(temp_db, mock_embedder).search_memoreei("wrong chat"))["results"]
    assert hits[0]["mark"].startswith("unsent in iMessage on ") and hits[0]["deleted"] is True


async def test_imessage_mirrored_deletes_instead(temp_db, mock_embedder, chat_db, monkeypatch):
    await _imessage(temp_db, mock_embedder, chat_db)
    _mirror(monkeypatch, "imessage")
    conn = sqlite3.connect(chat_db)
    conn.execute("UPDATE message SET text = NULL, date_edited = 1 WHERE guid = 'G2'")  # no date_retracted
    conn.commit()
    conn.close()
    assert (await _imessage(temp_db, mock_embedder, chat_db)).removed == 1
    assert await _meta(temp_db, f"imessage:{LINK}:G2") is None


async def test_imessage_a_narrowed_sync_marks_nothing(temp_db, mock_embedder, chat_db):
    await _imessage(temp_db, mock_embedder, chat_db)
    conn = sqlite3.connect(chat_db)
    conn.execute("DELETE FROM chat_message_join WHERE message_id = 3")
    conn.commit()
    conn.close()
    connector = IMessageConnector(db=temp_db, embedder=mock_embedder, db_path=str(chat_db))
    with patch("memoreei.connectors.imessage_connector.is_macos", return_value=True):
        await connector.sync(chat_name=LINK)
    assert connector.marked == 0


# ── WhatsApp: deleted for everyone, deleted for me, expired ───────────────────

ZELDA = "12025550143@s.whatsapp.net"


@pytest.fixture
def chatstorage(tmp_path) -> Path:
    path = tmp_path / "ChatStorage.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMEMBERJID TEXT,
            ZCONTACTNAME TEXT, ZFIRSTNAME TEXT);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZTITLE TEXT);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZGROUPMEMBER INTEGER,
            ZMEDIAITEM INTEGER, ZISFROMME INTEGER, ZMESSAGETYPE INTEGER, ZMESSAGEDATE REAL, ZSTANZAID TEXT,
            ZTEXT TEXT, ZPUSHNAME TEXT, ZFROMJID TEXT, ZEPHEMERALDURATION INTEGER);
    """)
    conn.execute("INSERT INTO ZWACHATSESSION VALUES (1, ?, 'Zelda', 0)", (ZELDA,))
    rows = [(1, "W1", "the triforce is in the attic", None), (2, "W2", "ignore that", None),
            (3, "W3", "this one vanishes in a day", 86400), (4, "W4", "deleted for me", None)]
    for pk, stanza, text, timer in rows:
        conn.execute("INSERT INTO ZWAMESSAGE (Z_PK, ZCHATSESSION, ZISFROMME, ZMESSAGETYPE, ZMESSAGEDATE, "
                     "ZSTANZAID, ZTEXT, ZEPHEMERALDURATION) VALUES (?, 1, 0, 0, ?, ?, ?, ?)",
                     (pk, 800000000 + pk, stanza, text, timer))
    conn.commit()
    conn.close()
    return path


async def test_whatsapp_deletions_and_expiry_are_kept_and_marked(temp_db, mock_embedder, chatstorage):
    assert (await sync_whatsapp(temp_db, mock_embedder, str(chatstorage)))["synced"] == 4
    assert (await _meta(temp_db, f"whatsapp:{ZELDA}:W3"))["expires_in"] == 86400
    conn = sqlite3.connect(chatstorage)
    conn.execute("UPDATE ZWAMESSAGE SET ZMESSAGETYPE = 14, ZTEXT = NULL WHERE ZSTANZAID = 'W2'")
    conn.execute("DELETE FROM ZWAMESSAGE WHERE ZSTANZAID IN ('W3', 'W4')")
    conn.commit()
    conn.close()
    result = await sync_whatsapp(temp_db, mock_embedder, str(chatstorage))
    assert result["marked"] == 3 and result["synced"] == 0
    hows = {s: (await _meta(temp_db, f"whatsapp:{ZELDA}:{s}")).get("gone", {}).get("how") for s in ("W1", "W2", "W3", "W4")}
    assert hows == {"W1": None, "W2": "deleted", "W3": "expired", "W4": "deleted"}


async def test_whatsapp_mirrored(temp_db, mock_embedder, chatstorage, monkeypatch):
    await sync_whatsapp(temp_db, mock_embedder, str(chatstorage))
    conn = sqlite3.connect(chatstorage)
    conn.execute("DELETE FROM ZWAMESSAGE WHERE ZSTANZAID = 'W4'")
    conn.commit()
    conn.close()
    await sync_whatsapp(temp_db, mock_embedder, str(chatstorage))  # marked
    _mirror(monkeypatch, "whatsapp")
    assert (await sync_whatsapp(temp_db, mock_embedder, str(chatstorage)))["removed"] == 1
    assert await _meta(temp_db, f"whatsapp:{ZELDA}:W4") is None


# ── Matrix: redactions ───────────────────────────────────────────────────────


async def test_matrix_redactions_mark_what_was_kept(temp_db, mock_embedder, monkeypatch):
    room = "!lostwoods:example.com"
    pages = [
        [{"type": "m.room.message", "event_id": "$e1", "sender": "@saria:example.com",
          "origin_server_ts": 1_786_000_000_000, "content": {"msgtype": "m.text", "body": "follow the song"}}],
        [{"type": "m.room.redaction", "event_id": "$e2", "sender": "@saria:example.com", "redacts": "$e1",
          "origin_server_ts": 1_786_000_100_000, "content": {}}],
    ]

    async def fetch(self, room_id, from_token=None):
        return pages.pop(0), "next"

    monkeypatch.setattr(MatrixConnector, "_fetch_messages", fetch)
    connector = MatrixConnector("https://example.com", "token", temp_db, mock_embedder)
    assert (await connector.sync_room(room))["synced"] == 1
    assert (await connector.sync_room(room))["marked"] == 1
    assert (await _meta(temp_db, f"matrix:{room}:$e1"))["gone"]["how"] == "deleted"


# ── Down a chain ─────────────────────────────────────────────────────────────


async def test_a_mark_travels_down_a_chain_and_only_the_reader_marks(net, memoreeis, monkeypatch):
    a, b, c = [await memoreeis(n) for n in "abc"]
    net.add("b", b)
    net.add("c", c)
    await a.insert_memory(msg("signal:zelda", 1, "the triforce is in the attic"))
    await a.insert_memory(msg("signal:zelda", 2, "ordinary"))
    await pair(net, a, b, monkeypatch)
    await pair(net, b, c, monkeypatch)
    await push_all(a, b, c)
    assert await contents(c) == ["ordinary", "the triforce is in the attic"]

    # B and C don't read Signal themselves: they never mark or mirror a copy.
    assert await b.mark_gone("signal", ["1"]) == 0
    assert await b.delete_marked("signal") == 0

    assert await a.mark_gone("signal", ["1"], at=1_786_000_000) == 1
    await push_all(a, b, c)
    assert (await _meta(c, "signal:zelda:1"))["gone"] == {"how": "deleted", "at": 1_786_000_000}
    hit = (await MemoryTools(c, mock_embedder_for_c()).search_memoreei("triforce attic"))["results"][0]
    assert hit["mark"].startswith("deleted in Signal on ") and hit["via"]
    assert (await c.count_gone()) == {"signal": 1}

    # Mirroring on A: its deletion goes down as a delete.
    assert await a.delete_own("signal", ["1"]) == 1
    await push_all(a, b, c)
    assert await contents(c) == ["ordinary"]


def mock_embedder_for_c():
    from tests.test_peers import Embedder

    return Embedder()
