"""WhatsApp, read from a ChatStorage.sqlite like WhatsApp for Mac's.

The fixture builds the three files the Mac app keeps side by side, with only the columns
the reader uses, and people from the usual cast.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from memoreei.connectors.whatsapp import ChatStorageReader, sync_whatsapp
from memoreei.connectors.whatsapp.chatstorage import APPLE_EPOCH_OFFSET
from memoreei.connectors.whatsapp.messages import Chat, WhatsAppMessage
from memoreei.connectors.whatsapp.sync import body
from memoreei.storage.models import MemoryItem
from memoreei.tools.memory_tools import MemoryTools

ZEZIMA = "12025550142@s.whatsapp.net"
GROUP = "120363000000000001@g.us"
HANS_LID = "90000000000001@lid"         # in the address book, with a number
STRANGER_LID = "90000000000002@lid"     # only LID.sqlite knows the number
GERTRUDE_LID = "90000000000003@lid"     # a group member WhatsApp has a name for
AGGIE = "12025550143@s.whatsapp.net"    # a group member known only by her push name
T0 = 795_000_000  # 2026-03-12, in seconds since 2001


def _make_chatstorage(folder: Path) -> Path:
    db = folder / "ChatStorage.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT,
            ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
            ZMEMBERJID TEXT, ZCONTACTNAME TEXT, ZFIRSTNAME TEXT);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZTITLE TEXT);
        CREATE TABLE ZWAPROFILEPUSHNAME (Z_PK INTEGER PRIMARY KEY, ZJID TEXT, ZPUSHNAME TEXT);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER,
            ZGROUPMEMBER INTEGER, ZMEDIAITEM INTEGER, ZISFROMME INTEGER,
            ZMESSAGETYPE INTEGER, ZMESSAGEDATE REAL, ZSTANZAID TEXT, ZTEXT TEXT,
            ZPUSHNAME TEXT, ZFROMJID TEXT);
        """
    )
    conn.executemany(
        "INSERT INTO ZWACHATSESSION VALUES (?, ?, ?, ?)",
        [
            (1, ZEZIMA, "Zezima", 0),
            (2, GROUP, "Grand Exchange traders", 1),
            (3, HANS_LID, "", 0),
            (4, "status@broadcast", None, 2),
            (5, "120363000000000002@newsletter", "Varrock Herald", 0),
            (6, STRANGER_LID, None, 0),
        ],
    )
    conn.executemany(
        "INSERT INTO ZWAGROUPMEMBER VALUES (?, ?, ?, ?, ?)",
        [(1, 2, GERTRUDE_LID, "Gertrude", None), (2, 2, AGGIE, None, None)],
    )
    conn.execute("INSERT INTO ZWAPROFILEPUSHNAME VALUES (1, ?, 'Aggie')", (AGGIE,))
    conn.executemany(
        "INSERT INTO ZWAMEDIAITEM VALUES (?, ?, ?)",
        [
            (1, 3, "sunset over Lumbridge"),
            (2, 4, None),
            (3, 6, "Party hats for sale"),
            (4, 7, "the rota"),
        ],
    )
    # pk, chat, member, media, from_me, type, stanza, text
    rows = [
        (1, 1, None, None, 0, 0, "A1", "selling lobsters"),
        (2, 1, None, None, 1, 0, "A2", "2gp each?"),
        (3, 1, None, 1, 0, 1, "A3", None),                       # photo with a caption
        (4, 1, None, 2, 0, 1, "A4", None),                       # photo, no caption
        (5, 1, None, None, 0, 15, "A5", None),                   # sticker
        (6, 1, None, 3, 1, 7, "A6", "look https://example.com/party"),
        (7, 1, None, 4, 0, 8, "A7", "party-rota.pdf"),
        (8, 2, 1, None, 0, 0, "B1", "who took my party hat"),
        (9, 2, 2, None, 0, 0, "B2", "not me"),
        (10, 4, None, None, 0, 0, "S1", "my status"),            # a status update
        (11, 5, None, None, 0, 0, "N1", "channel news"),         # a channel
        (12, 3, None, None, 0, 0, "C1", "I have been walking around this castle for years"),
        (13, 6, None, None, 0, 0, "D1", "hello?"),
        (14, 1, None, None, 0, 0, "A8", "   "),                   # nothing to say
    ]
    conn.executemany(
        "INSERT INTO ZWAMESSAGE (Z_PK, ZCHATSESSION, ZGROUPMEMBER, ZMEDIAITEM, ZISFROMME,"
        " ZMESSAGETYPE, ZSTANZAID, ZTEXT, ZMESSAGEDATE) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(*r, T0 + r[0] * 60) for r in rows],
    )
    conn.commit()
    conn.close()

    book = sqlite3.connect(folder / "ContactsV2.sqlite")
    book.execute(
        "CREATE TABLE ZWAADDRESSBOOKCONTACT (Z_PK INTEGER PRIMARY KEY, ZLID TEXT,"
        " ZWHATSAPPID TEXT, ZFULLNAME TEXT, ZPHONENUMBER TEXT)"
    )
    book.execute(
        "INSERT INTO ZWAADDRESSBOOKCONTACT VALUES (1, ?, '12025550144@s.whatsapp.net', 'Hans', '+12025550144')",
        (HANS_LID,),
    )
    book.commit()
    book.close()

    lids = sqlite3.connect(folder / "LID.sqlite")
    lids.execute(
        "CREATE TABLE ZWAZACCOUNT (Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER TEXT, ZPHONENUMBER TEXT)"
    )
    lids.execute("INSERT INTO ZWAZACCOUNT VALUES (1, ?, '12025550199')", (STRANGER_LID,))
    lids.commit()
    lids.close()
    return db


@pytest.fixture
def chatstorage(tmp_path) -> Path:
    folder = tmp_path / "group.net.whatsapp.WhatsApp.shared"
    folder.mkdir()
    return _make_chatstorage(folder)


@pytest.fixture
def tools(temp_db, mock_embedder) -> MemoryTools:
    return MemoryTools(db=temp_db, embedder=mock_embedder)


async def _sync(tools: MemoryTools, path: Path) -> dict:
    return await sync_whatsapp(db=tools.db, embedder=tools.embedder, db_path=str(path))


async def _stored(tools: MemoryTools) -> dict[str, MemoryItem]:
    """source_id → item, for everything stored."""
    async with tools.db._db.execute("SELECT id FROM memories") as cursor:
        ids = [row["id"] for row in await cursor.fetchall()]
    items = [await tools.db.get_by_id(i) for i in ids]
    return {item.source_id: item for item in items if item is not None}


def _id(chat: str, stanza: str) -> str:
    return f"whatsapp:{chat}:{stanza}"


# ── What gets kept ───────────────────────────────────────────────────────────


async def test_keeps_words_and_skips_the_rest(tools, chatstorage):
    result = await _sync(tools, chatstorage)
    assert result == {"synced": 9, "db_path": str(chatstorage)}
    stored = await _stored(tools)
    stanzas = sorted(i.split(":")[-1] for i in stored)
    # No uncaptioned photo, sticker, status update, channel post or blank message.
    assert stanzas == ["A1", "A2", "A3", "A6", "A7", "B1", "B2", "C1", "D1"]


async def test_content_says_who_and_what(tools, chatstorage):
    await _sync(tools, chatstorage)
    stored = await _stored(tools)
    assert stored[_id(ZEZIMA, "A1")].content == "Zezima: selling lobsters"
    assert stored[_id(ZEZIMA, "A2")].content == "me: 2gp each?"
    assert stored[_id(ZEZIMA, "A3")].content == "Zezima: [Photo] sunset over Lumbridge"
    assert stored[_id(ZEZIMA, "A6")].content == (
        "me: look https://example.com/party\n[Link] Party hats for sale"
    )
    assert stored[_id(ZEZIMA, "A7")].content == "Zezima: [Document] party-rota.pdf\nthe rota"


async def test_sources_are_keyed_by_jid(tools, chatstorage):
    await _sync(tools, chatstorage)
    sources = await tools.db.list_sources()
    assert set(sources) == {f"whatsapp:{j}" for j in (ZEZIMA, GROUP, HANS_LID, STRANGER_LID)}
    item = (await _stored(tools))[_id(ZEZIMA, "A1")]
    assert item.metadata["chat_name"] == "Zezima"
    assert item.metadata["is_group"] is False
    assert item.metadata["sender_phone"] == "+12025550142"


async def test_timestamps_count_from_2001(tools, chatstorage):
    await _sync(tools, chatstorage)
    item = (await _stored(tools))[_id(ZEZIMA, "A1")]
    assert item.ts == T0 + 60 + APPLE_EPOCH_OFFSET


# ── Names ────────────────────────────────────────────────────────────────────


async def test_group_senders_are_named(tools, chatstorage):
    await _sync(tools, chatstorage)
    stored = await _stored(tools)
    gertrude = stored[_id(GROUP, "B1")]
    assert gertrude.content == "Gertrude: who took my party hat"
    assert gertrude.participants == ["Gertrude"]
    assert gertrude.metadata["sender_jid"] == GERTRUDE_LID
    assert gertrude.metadata["chat_name"] == "Grand Exchange traders"
    # No contact name: her own push name.
    aggie = stored[_id(GROUP, "B2")]
    assert aggie.content == "Aggie: not me"
    assert aggie.metadata["sender_phone"] == "+12025550143"


async def test_anonymous_ids_find_names_and_numbers(tools, chatstorage):
    await _sync(tools, chatstorage)
    stored = await _stored(tools)
    hans = stored[_id(HANS_LID, "C1")]
    assert hans.content.startswith("Hans: ")
    assert hans.metadata["sender_phone"] == "+12025550144"
    # Only LID.sqlite knows this one: the number is the best name there is.
    stranger = stored[_id(STRANGER_LID, "D1")]
    assert stranger.content == "+12025550199: hello?"


async def test_list_sources_shows_chats_by_name(tools, chatstorage):
    await _sync(tools, chatstorage)
    listed = (await tools.list_sources())["sources"]
    assert f"Zezima (whatsapp:{ZEZIMA})" in listed
    assert f"Grand Exchange traders (whatsapp:{GROUP})" in listed


async def test_reads_without_its_neighbours(tools, chatstorage, tmp_path):
    """An iPhone backup's ChatStorage.sqlite sits alone: names come from the chats."""
    alone = tmp_path / "alone"
    alone.mkdir()
    shutil.copy(chatstorage, alone / "ChatStorage.sqlite")
    result = await _sync(tools, alone / "ChatStorage.sqlite")
    assert result["synced"] == 9
    stored = await _stored(tools)
    assert stored[_id(ZEZIMA, "A1")].content == "Zezima: selling lobsters"
    assert stored[_id(HANS_LID, "C1")].content.startswith(f"{HANS_LID}: ")


# ── Incremental ──────────────────────────────────────────────────────────────


async def test_second_sync_reads_only_whats_new(tools, chatstorage):
    await _sync(tools, chatstorage)
    assert (await _sync(tools, chatstorage))["synced"] == 0

    conn = sqlite3.connect(chatstorage)
    conn.execute(
        "INSERT INTO ZWAMESSAGE (Z_PK, ZCHATSESSION, ZISFROMME, ZMESSAGETYPE, ZSTANZAID,"
        " ZTEXT, ZMESSAGEDATE) VALUES (15, 1, 0, 0, 'A9', 'buying gf', ?)",
        (T0 + 9999,),
    )
    conn.commit()
    conn.close()

    assert (await _sync(tools, chatstorage))["synced"] == 1
    reader = ChatStorageReader(chatstorage)
    assert await tools.db.get_whatsapp_checkpoint(reader.name) == 15


async def test_checkpoint_passes_skipped_rows(tools, chatstorage):
    """Stickers at the end aren't re-read every round."""
    await _sync(tools, chatstorage)
    assert await tools.db.get_whatsapp_checkpoint(ChatStorageReader(chatstorage).name) == 14


async def test_resyncing_from_scratch_adds_nothing(tools, chatstorage):
    await _sync(tools, chatstorage)
    count = await tools.db.count_memories()
    await tools.db.set_whatsapp_checkpoint(ChatStorageReader(chatstorage).name, 0)
    await _sync(tools, chatstorage)
    assert await tools.db.count_memories() == count


async def test_batches_checkpoint_as_they_go(tools, tmp_path, monkeypatch):
    folder = tmp_path / "big"
    folder.mkdir()
    db = _make_chatstorage(folder)
    conn = sqlite3.connect(db)
    conn.executemany(
        "INSERT INTO ZWAMESSAGE (Z_PK, ZCHATSESSION, ZISFROMME, ZMESSAGETYPE, ZSTANZAID,"
        " ZTEXT, ZMESSAGEDATE) VALUES (?, 1, 0, 0, ?, ?, ?)",
        [(100 + i, f"X{i}", f"message {i}", T0 + 100_000 + i) for i in range(250)],
    )
    conn.commit()
    conn.close()

    calls = 0
    real_embed = tools.embedder.embed

    async def failing_embed(texts):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("embedder fell over")
        return await real_embed(texts)

    monkeypatch.setattr(tools.embedder, "embed", failing_embed)
    result = await _sync(tools, db)
    assert "error" in result
    # Two batches of 100 made it, and the checkpoint says so.
    assert await tools.db.count_memories() == 200
    position = await tools.db.get_whatsapp_checkpoint(ChatStorageReader(db).name)
    assert 14 < position < 349


# ── When it can't read ───────────────────────────────────────────────────────


async def test_missing_database_is_an_error_without_its_path(tools, tmp_path):
    missing = tmp_path / "nowhere" / "ChatStorage.sqlite"
    result = await _sync(tools, missing)
    assert result["synced"] == 0
    assert "No WhatsApp database found" in result["error"]
    assert str(tmp_path) not in result["error"]


async def test_something_else_is_an_error(tools, tmp_path):
    other = tmp_path / "ChatStorage.sqlite"
    sqlite3.connect(other).execute("CREATE TABLE t (x)").connection.close()
    result = await _sync(tools, other)
    assert result["synced"] == 0
    assert "Can't open the WhatsApp database" in result["error"]


# ── Pieces ───────────────────────────────────────────────────────────────────


def _msg(**kw) -> WhatsAppMessage:
    base = dict(
        position=1, message_id="M", chat=Chat(jid=ZEZIMA, name="Zezima", is_group=False),
        ts=0, from_me=False, sender_name="Zezima", sender_jid=ZEZIMA, sender_phone=None,
        kind="text", text="hi",
    )
    return WhatsAppMessage(**{**base, **kw})


def test_link_title_already_in_the_text_isnt_repeated():
    msg = _msg(kind="link", text="Party hats for sale https://example.com", link_title="Party hats for sale")
    assert body(msg) == "Party hats for sale https://example.com"


def test_document_without_caption():
    assert body(_msg(kind="document", text="rota.pdf")) == "[Document] rota.pdf"


def test_configured_wherever_a_path_is_set(monkeypatch):
    from memoreei.config import Config

    assert "whatsapp" in Config(whatsapp_db_path="/x/ChatStorage.sqlite").configured_connectors()
    assert "whatsapp" not in Config().configured_connectors()


async def test_sync_manager_reports_a_failure_as_a_problem(tools, tmp_path, monkeypatch):
    from memoreei.config import Config
    from memoreei.sync_manager import SyncManager

    nowhere = str(tmp_path / "nowhere.sqlite")
    monkeypatch.setenv("WHATSAPP_DB_PATH", nowhere)
    monkeypatch.setattr("memoreei.config.get_config", lambda: Config(whatsapp_db_path=nowhere))
    result = await SyncManager().sync_everything(tools)
    assert "No WhatsApp database found" in result["connectors"]["whatsapp"]["error"]


async def test_newest_ts_by_kind(tools, chatstorage):
    await _sync(tools, chatstorage)
    assert await tools.db.newest_ts("whatsapp") == T0 + 13 * 60 + APPLE_EPOCH_OFFSET
    assert await tools.db.newest_ts("imessage") is None
