"""Signal, read from a database laid out like Signal Desktop 8's.

The fixture builds a real SQLCipher database with the columns the reader uses, the
message json Signal keeps beside them, and attachments in their own table, with people
from the usual cast. Keys are sealed the way Electron's safeStorage seals them.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from memoreei.connectors import signal as signal_pkg
from memoreei.connectors.signal import KeyUnavailable, connect, sync_signal
from memoreei.connectors.signal import keys as keys_module
from memoreei.connectors.signal.keys import read_db_key, unseal
from memoreei.connectors.signal.reader import SignalReader, attachment_label
from memoreei.connectors.signal.sync import CHECKPOINT, DISAPPEARING_SKIPPED
from memoreei.tools.memory_tools import MemoryTools

sqlcipher3 = pytest.importorskip("sqlcipher3")

KEY = "0123456789abcdef" * 4
ME = "aaaaaaaa-0000-4000-8000-000000000001"
ZEZIMA = "aaaaaaaa-0000-4000-8000-000000000002"
HANS = "aaaaaaaa-0000-4000-8000-000000000003"
GERTRUDE = "aaaaaaaa-0000-4000-8000-000000000004"
T0 = 1_780_000_000_000  # ms, 2026-05-28

_SCHEMA = """
CREATE TABLE conversations (id TEXT PRIMARY KEY, json TEXT, active_at INTEGER, type TEXT,
    members TEXT, name TEXT, profileName TEXT, profileFamilyName TEXT, profileFullName TEXT,
    e164 TEXT, serviceId TEXT, groupId TEXT);
CREATE TABLE messages (rowid INTEGER PRIMARY KEY ASC, id TEXT UNIQUE, json TEXT,
    sent_at INTEGER, conversationId TEXT, type TEXT, body TEXT, expireTimer INTEGER,
    isViewOnce INTEGER, isErased INTEGER, sourceServiceId TEXT, source TEXT,
    timestamp INTEGER, hasAttachments INTEGER);
CREATE TABLE message_attachments (messageId TEXT, editHistoryIndex INTEGER,
    attachmentType TEXT, orderInMessage INTEGER, contentType TEXT, caption TEXT,
    fileName TEXT, flags INTEGER, duration REAL, storyTextAttachmentJson TEXT);
CREATE TABLE callsHistory (callId TEXT PRIMARY KEY, peerId TEXT, ringerId TEXT, mode TEXT,
    type TEXT, direction TEXT, status TEXT, timestamp INTEGER);
CREATE TABLE items (id TEXT PRIMARY KEY, json TEXT);
"""


class FakeSignal:
    """A Signal Desktop data folder: config.json and an encrypted sql/db.sqlite."""

    def __init__(self, folder: Path, key: str = KEY) -> None:
        self.folder = folder
        (folder / "sql").mkdir(parents=True)
        (folder / "config.json").write_text(json.dumps({"key": key}))
        self.conn = sqlcipher3.connect(str(folder / "sql" / "db.sqlite"))
        self.conn.execute(f"PRAGMA key = \"x'{key}'\"")
        self.conn.executescript(_SCHEMA)
        self.conn.execute("INSERT INTO items VALUES ('uuid_id', ?)",
                          (json.dumps({"id": "uuid_id", "value": f"{ME}.2"}),))
        self._n = 0
        self.chat("c-me", serviceId=ME, profileName="Caloo")
        self.chat("c-zez", serviceId=ZEZIMA, profileName="Zezima")
        self.chat("c-hans", serviceId=HANS, name="Hans")
        self.chat("c-gert", serviceId=GERTRUDE, profileName="Gertrude")
        self.chat("c-ge", type="group", name="Grand Exchange traders")
        self.conn.commit()

    def chat(self, conv_id: str, type: str = "private", **fields: str) -> None:
        self.conn.execute(
            "INSERT INTO conversations (id, json, type, name, serviceId) VALUES (?, ?, ?, ?, ?)",
            (conv_id, json.dumps({"id": conv_id, "type": type, **fields}), type,
             fields.get("name"), fields.get("serviceId")),
        )

    def message(self, conv: str, type: str = "incoming", body: str | None = None,
                sender: str | None = None, attachments: list[dict] | None = None,
                expire: int | None = None, **extra: object) -> str:
        self._n += 1
        mid = f"m{self._n}"
        self.conn.execute(
            "INSERT INTO messages (id, json, sent_at, conversationId, type, body, expireTimer, "
            "sourceServiceId, hasAttachments) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (mid, json.dumps(extra), T0 + self._n * 60_000, conv, type, body, expire,
             sender, int(bool(attachments))),
        )
        for n, att in enumerate(attachments or []):
            self.conn.execute(
                "INSERT INTO message_attachments (messageId, editHistoryIndex, attachmentType, "
                "orderInMessage, contentType, caption, fileName, flags, duration, "
                "storyTextAttachmentJson) VALUES (?, -1, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, att.get("attachmentType", "attachment"), n, att.get("contentType"),
                 att.get("caption"), att.get("fileName"), att.get("flags"),
                 att.get("duration"), att.get("story")),
            )
        self.conn.commit()
        return mid

    def update(self, mid: str, body: str | None = None, **json_fields: object) -> None:
        row = self.conn.execute("SELECT json FROM messages WHERE id = ?", (mid,)).fetchone()
        data = json.loads(row[0])
        data.update(json_fields)
        self.conn.execute("UPDATE messages SET json = ? WHERE id = ?", (json.dumps(data), mid))
        if body is not None:
            self.conn.execute("UPDATE messages SET body = ? WHERE id = ?", (body or None, mid))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    f = FakeSignal(tmp_path / "Signal")
    monkeypatch.setenv("SIGNAL_DIR", str(f.folder))
    yield f
    f.close()


@pytest.fixture
def tools(temp_db, mock_embedder) -> MemoryTools:
    return MemoryTools(db=temp_db, embedder=mock_embedder)


async def _sync(tools: MemoryTools) -> dict:
    return await sync_signal(db=tools.db, embedder=tools.embedder, key=KEY)


async def _contents(tools: MemoryTools) -> dict[str, str]:
    async with tools.db._db.execute("SELECT source_id, content FROM memories") as cur:
        return {row[0].rsplit(":", 1)[1]: row[1] for row in await cur.fetchall()}


async def _meta(tools: MemoryTools, mid: str) -> dict:
    async with tools.db._db.execute(
        "SELECT metadata FROM memories WHERE source_id LIKE ?", (f"%:{mid}",)
    ) as cur:
        return json.loads((await cur.fetchone())[0])


# ── Keys ───────────────────────────────────────────────────────────────────────


def _seal(key: str, password: bytes, iterations: int, prefix: bytes = b"v10") -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    aes = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", iterations, 16)
    pad = 16 - len(key) % 16
    enc = Cipher(algorithms.AES(aes), modes.CBC(b" " * 16)).encryptor()
    return (prefix + enc.update(key.encode() + bytes([pad]) * pad) + enc.finalize()).hex()


def test_unseals_a_mac_key():
    sealed = bytes.fromhex(_seal(KEY, b"from-the-keychain", 1003))
    assert unseal(sealed, [b"from-the-keychain"], iterations=1003) == KEY


def test_the_wrong_password_says_so():
    sealed = bytes.fromhex(_seal(KEY, b"right", 1003))
    with pytest.raises(KeyUnavailable, match="doesn't open"):
        unseal(sealed, [b"wrong"], iterations=1003)


def _config(folder: Path, **fields: object) -> Path:
    folder.mkdir(exist_ok=True)
    (folder / "config.json").write_text(json.dumps(fields))
    return folder


def test_linux_libsecret(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keys_module, "_libsecret_password", lambda: b"gnome-secret")
    folder = _config(tmp_path / "s", encryptedKey=_seal(KEY, b"gnome-secret", 1, b"v11"),
                     safeStorageBackend="gnome_libsecret")
    assert read_db_key(folder) == KEY


def test_linux_kwallet(tmp_path, monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(keys_module, "_kwallet_password", lambda b: asked.append(b) or b"kde-secret")
    folder = _config(tmp_path / "s", encryptedKey=_seal(KEY, b"kde-secret", 1, b"v11"),
                     safeStorageBackend="kwallet6")
    assert read_db_key(folder) == KEY and asked == ["kwallet6"]


def test_linux_without_a_keyring(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    v10 = _config(tmp_path / "a", encryptedKey=_seal(KEY, b"peanuts", 1, b"v10"))
    basic = _config(tmp_path / "b", encryptedKey=_seal(KEY, b"", 1, b"v11"),
                    safeStorageBackend="basic_text")
    assert read_db_key(v10) == KEY and read_db_key(basic) == KEY


def test_mac_asks_the_keychain(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keys_module, "_keychain_password", lambda: b"from-the-keychain")
    folder = _config(tmp_path / "s", encryptedKey=_seal(KEY, b"from-the-keychain", 1003))
    assert read_db_key(folder) == KEY


def test_a_declined_keychain_prompt_explains(tmp_path, monkeypatch):
    def declined() -> bytes:
        raise KeyUnavailable(keys_module._KEYCHAIN_ERRORS[-128])

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(keys_module, "_keychain_password", declined)
    folder = _config(tmp_path / "s", encryptedKey=_seal(KEY, b"x", 1003))
    with pytest.raises(KeyUnavailable, match="choose Allow"):
        read_db_key(folder)


def test_signal_not_set_up(tmp_path):
    with pytest.raises(KeyUnavailable, match="link it to your phone"):
        read_db_key(tmp_path / "nothing-here")


def test_connect_proves_the_key_opens_the_database(fake):
    assert connect() == KEY


def test_connect_with_a_key_that_doesnt_open_it(fake):
    (fake.folder / "config.json").write_text(json.dumps({"key": "f" * 64}))
    with pytest.raises(KeyUnavailable, match="didn't open"):
        connect()


def test_offered_only_where_signal_desktop_is(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNAL_DIR", str(tmp_path / "Signal"))
    assert not signal_pkg.app_present()
    _config(tmp_path / "Signal", key=KEY)
    assert signal_pkg.app_present() == (sys.platform != "win32")


# ── What becomes a memoreei ──────────────────────────────────────────────────


async def test_messages_say_who_said_what(tools, fake):
    a = fake.message("c-zez", body="Selling lobsters, 200 each", sender=ZEZIMA)
    b = fake.message("c-zez", "outgoing", body="I'll take 50")
    c = fake.message("c-ge", body="Party hats in stock", sender=HANS)
    assert (await _sync(tools))["synced"] == 3
    got = await _contents(tools)
    assert got[a] == "Zezima: Selling lobsters, 200 each"
    assert got[b] == "me: I'll take 50"
    assert got[c] == "Hans: Party hats in stock"
    assert (await _meta(tools, c))["chat_name"] == "Grand Exchange traders"


async def test_attachments_are_words_not_files(tools, fake):
    photo = fake.message("c-zez", sender=ZEZIMA, attachments=[
        {"contentType": "image/jpeg", "caption": "my bank"}])
    voice = fake.message("c-zez", sender=ZEZIMA, attachments=[
        {"contentType": "audio/aac", "flags": 1, "duration": 42}])
    gif = fake.message("c-zez", sender=ZEZIMA, attachments=[{"contentType": "video/mp4", "flags": 8}])
    doc = fake.message("c-zez", body="the rules", sender=ZEZIMA, attachments=[
        {"contentType": "application/pdf", "fileName": "duel-arena.pdf"}])
    link = fake.message("c-zez", body="look", sender=ZEZIMA,
                        preview=[{"url": "https://example.com", "title": "Varrock news"}])
    previews_only = fake.message("c-zez", sender=ZEZIMA, attachments=[
        {"contentType": "image/png", "attachmentType": "preview"}])
    await _sync(tools)
    got = await _contents(tools)
    assert got[photo] == "Zezima: [Photo] my bank"
    assert got[voice] == "Zezima: [Voice note 0:42]"
    assert got[gif] == "Zezima: [GIF]"
    assert got[doc] == "Zezima: [Document] duel-arena.pdf\nthe rules"
    assert got[link] == "Zezima: look\n[Link] Varrock news"
    assert previews_only not in got


def test_attachment_labels():
    assert attachment_label({"contentType": "video/quicktime"}) == "[Video]"
    assert attachment_label({"contentType": "audio/mpeg", "fileName": "sea shanty.mp3"}) == "[Audio] sea shanty.mp3"
    assert attachment_label({"contentType": "application/zip"}) == "[Document]"


async def test_stories(tools, fake):
    text = fake.message("c-zez", "story", sender=ZEZIMA, attachments=[
        {"story": json.dumps({"text": "Maxed at last"})}])
    media = fake.message("c-zez", "story", sender=ZEZIMA, attachments=[
        {"contentType": "image/jpeg", "caption": "cape"}])
    await _sync(tools)
    got = await _contents(tools)
    assert got[text] == "Zezima: [Story] Maxed at last"
    assert got[media] == "Zezima: [Story] [Photo] cape"


async def test_group_changes_read_as_sentences(tools, fake):
    renamed = fake.message("c-ge", "group-v2-change", groupV2Change={
        "from": HANS, "details": [{"type": "title", "newTitle": "GE flippers"}]})
    added = fake.message("c-ge", "group-v2-change", groupV2Change={
        "from": HANS, "details": [{"type": "member-add", "aci": GERTRUDE}]})
    joined = fake.message("c-ge", "group-v2-change", groupV2Change={
        "details": [{"type": "member-add", "aci": ZEZIMA}]})
    left = fake.message("c-ge", "group-v2-change", groupV2Change={
        "from": GERTRUDE, "details": [{"type": "member-remove", "aci": GERTRUDE}]})
    await _sync(tools)
    got = await _contents(tools)
    assert got[renamed] == "Hans changed the group name to GE flippers"
    assert got[added] == "Hans added Gertrude"
    assert got[joined] == "Zezima joined the group"
    assert got[left] == "Gertrude left the group"


async def test_calls_and_timers(tools, fake):
    fake.conn.execute("INSERT INTO callsHistory VALUES ('call1', 'c-zez', NULL, 'Direct', 'Video', "
                      "'Incoming', 'Missed', 0)")
    fake.conn.commit()
    call = fake.message("c-zez", "call-history", callId="call1")
    timer = fake.message("c-zez", "timer-notification", expirationTimerUpdate={
        "expireTimer": 604800, "sourceServiceId": ZEZIMA})
    await _sync(tools)
    got = await _contents(tools)
    assert got[call] == "Missed video call from Zezima"
    assert got[timer] == "Zezima set disappearing messages to 1 week"


async def test_reactions_live_on_the_message(tools, fake):
    mid = fake.message("c-zez", body="Gz on 99", sender=ZEZIMA,
                       reactions=[{"emoji": "🎉", "fromId": "c-me", "targetTimestamp": 1, "timestamp": 2},
                                  {"emoji": "❤️", "fromId": "c-hans", "targetTimestamp": 1, "timestamp": 3}])
    assert (await _sync(tools))["synced"] == 1
    assert (await _meta(tools, mid))["reactions"] == [
        {"emoji": "🎉", "from": "me"}, {"emoji": "❤️", "from": "Hans"}]


async def test_disappearing_messages_are_never_kept(tools, fake):
    gone = fake.message("c-zez", body="my bank pin is", sender=ZEZIMA, expire=3600)
    kept = fake.message("c-zez", body="ordinary", sender=ZEZIMA)
    result = await _sync(tools)
    assert result["synced"] == 1 and result["skipped_disappearing"] == 1
    got = await _contents(tools)
    assert gone not in got and kept in got
    assert await tools.db.get_signal_checkpoint(DISAPPEARING_SKIPPED) == 1


async def test_system_noise_is_skipped(tools, fake):
    fake.message("c-zez", "keychange")
    fake.message("c-zez", "verified-change")
    assert (await _sync(tools))["synced"] == 0
    assert await tools.db.get_signal_checkpoint(CHECKPOINT) == 2


# ── Keeping up with changes ───────────────────────────────────────────────────


async def test_second_sync_reads_only_whats_new(tools, fake):
    fake.message("c-zez", body="one", sender=ZEZIMA)
    await _sync(tools)
    fake.message("c-zez", body="two", sender=ZEZIMA)
    result = await _sync(tools)
    assert result["synced"] == 1 and result["updated"] == 0
    assert (await _sync(tools)) == {"synced": 0, "updated": 0, "skipped_disappearing": 0}


async def test_a_later_reaction_updates_the_memoreei(tools, fake):
    mid = fake.message("c-zez", body="Gz on 99", sender=ZEZIMA)
    await _sync(tools)
    fake.update(mid, reactions=[{"emoji": "👍", "fromId": "c-gert", "targetTimestamp": 1, "timestamp": 2}])
    result = await _sync(tools)
    assert result == {"synced": 0, "updated": 1, "skipped_disappearing": 0}
    assert (await _meta(tools, mid))["reactions"] == [{"emoji": "👍", "from": "Gertrude"}]
    assert (await _contents(tools))[mid] == "Zezima: Gz on 99"


async def test_an_edit_keeps_both_wordings_searchable(tools, fake):
    mid = fake.message("c-zez", body="meet at Varrock", sender=ZEZIMA)
    await _sync(tools)
    fake.update(mid, body="meet at Falador", editHistory=[
        {"body": "meet at Falador", "timestamp": T0 + 2}, {"body": "meet at Varrock", "timestamp": T0 + 1}])
    assert (await _sync(tools))["updated"] == 1
    assert (await _contents(tools))[mid] == "Zezima: meet at Varrock\n[edited] meet at Falador"
    assert (await _meta(tools, mid))["edits"] == 1
    hits = await tools.db.search_fts("Falador")
    assert [h.source_id.rsplit(":", 1)[1] for h in hits] == [mid]


async def test_delete_for_everyone_is_flagged_not_forgotten(tools, fake):
    mid = fake.message("c-zez", body="wrong chat, sorry", sender=ZEZIMA)
    await _sync(tools)
    fake.update(mid, body="", deletedForEveryone=True)
    assert (await _sync(tools))["updated"] == 1
    assert (await _contents(tools))[mid] == "Zezima: wrong chat, sorry"
    assert (await _meta(tools, mid))["deleted"] is True


async def test_a_message_gone_from_signal_is_flagged(tools, fake):
    mid = fake.message("c-me", "outgoing", body="note to self")
    keep = fake.message("c-me", "outgoing", body="still here")
    await _sync(tools)
    fake.conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
    fake.conn.commit()
    assert (await _sync(tools))["updated"] == 1
    assert (await _meta(tools, mid))["deleted"] is True
    assert "deleted" not in await _meta(tools, keep)
    assert (await _sync(tools))["updated"] == 0  # flagged once, then forgotten


async def test_a_caption_sent_as_the_body_joins_its_label(tools, fake):
    mid = fake.message("c-zez", body="my bank", sender=ZEZIMA, attachments=[{"contentType": "image/jpeg"}])
    await _sync(tools)
    assert (await _contents(tools))[mid] == "Zezima: [Photo] my bank"


async def test_deleted_before_it_was_read_leaves_nothing(tools, fake):
    fake.message("c-zez", sender=ZEZIMA, deletedForEveryone=True)
    assert (await _sync(tools))["synced"] == 0


# ── When it can't ─────────────────────────────────────────────────────────────


async def test_not_connected(tools, fake):
    result = await sync_signal(db=tools.db, embedder=tools.embedder, key="")
    assert "Connect it on the Sources page" in result["error"]


async def test_a_changed_key_says_to_reconnect(tools, fake):
    result = await sync_signal(db=tools.db, embedder=tools.embedder, key="f" * 64)
    assert "Reconnect Signal" in result["error"]


async def test_a_changed_schema_says_so(tools, fake):
    fake.conn.execute("ALTER TABLE messages RENAME COLUMN conversationId TO chatId")
    fake.conn.commit()
    result = await _sync(tools)
    assert "has changed" in result["error"] and "conversationId" in result["error"]


async def test_sync_manager_reports_a_failure_as_a_problem(tools, fake, monkeypatch):
    from memoreei.sync_manager import SyncManager

    monkeypatch.setenv("SIGNAL_DB_KEY", "f" * 64)
    with pytest.raises(RuntimeError, match="Reconnect Signal"):
        await SyncManager().sync_source("signal", tools)


def test_configured_once_a_key_is_saved(monkeypatch):
    from memoreei.config import get_config

    assert "signal" not in get_config().configured_connectors()
    monkeypatch.setenv("SIGNAL_DB_KEY", KEY)
    monkeypatch.setattr("memoreei.config._config", None)
    assert "signal" in get_config().configured_connectors()


async def test_reader_names_without_items_table(tmp_path):
    f = FakeSignal(tmp_path / "S")
    f.conn.execute("DROP TABLE items")
    f.conn.commit()
    f.message("c-zez", "outgoing", body="hi")
    with SignalReader(f.folder / "sql" / "db.sqlite", KEY) as reader:
        assert [m.sender_name for m in reader.messages(0, reader.end())] == ["me"]
    f.close()
