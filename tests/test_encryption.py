"""memoreei.db is encrypted on disk, and sources' secrets are kept inside it."""
from __future__ import annotations

import contextlib
import sqlite3

import pytest

import memoreei.config as config_module
from memoreei.storage import key
from memoreei.storage.database import FTS_TRIGGER_INSERT, SCHEMA, Database
from memoreei.storage.models import MemoryItem
from memoreei.storage.vault import read_secrets, write_secrets

NEEDLE = "the zebra keeps its umbrella in the larder"


def _memory(content: str = NEEDLE, n: int = 1) -> MemoryItem:
    return MemoryItem(id=f"m{n}", source="manual", source_id=f"s{n}", content=content, summary=None,
                      participants=["Ada"], ts=1_700_000_000 + n, ingested_at=1_700_000_000,
                      metadata={}, embedding=[0.5] * 10)


async def _store(path: str, content: str = NEEDLE) -> None:
    async with Database(db_path=path) as db:
        await db.insert_memory(_memory(content))


def _on_disk(path) -> bytes:
    """The database and anything SQLite keeps beside it."""
    data = b""
    for suffix in ("", "-wal", "-shm", "-journal"):
        with contextlib.suppress(FileNotFoundError):
            data += open(f"{path}{suffix}", "rb").read()
    return data


async def test_a_new_database_is_encrypted(tmp_path):
    path = tmp_path / "memoreei.db"
    await _store(str(path))
    raw = _on_disk(path)
    assert not raw.startswith(key.SQLITE_HEADER)
    assert b"zebra" not in raw and b"memories" not in raw and b"Ada" not in raw
    assert (tmp_path / "memoreei.key").stat().st_mode & 0o777 == 0o600
    with pytest.raises(sqlite3.DatabaseError):  # ordinary SQLite sees noise
        sqlite3.connect(path).execute("SELECT * FROM sqlite_master").fetchall()
    async with Database(db_path=str(path)) as db:  # and memoreei reads it back
        assert [m.content for m in await db.search_fts("umbrella")] == [NEEDLE]


async def test_a_plain_database_is_converted_on_first_open(tmp_path):
    path = tmp_path / "memoreei.db"
    plain = sqlite3.connect(path)
    plain.execute("PRAGMA journal_mode=WAL")
    for statement in SCHEMA.strip().split(";"):
        if statement.strip():
            plain.execute(statement)
    plain.execute(FTS_TRIGGER_INSERT)
    plain.execute("INSERT INTO memories (id, source, source_id, content, participants, ts, "
                  "ingested_at, metadata) VALUES ('m1', 'manual', 's1', ?, '[]', 1, 1, '{}')", (NEEDLE,))
    plain.execute("PRAGMA user_version = 7")
    plain.commit()
    plain.close()
    assert key.db_state(path) == "plain"

    async with Database(db_path=str(path)) as db:
        assert [m.content for m in await db.search_fts("larder")] == [NEEDLE]  # FTS came across
        assert (await db.get_by_id("m1")).content == NEEDLE

    assert key.db_state(path) == "encrypted"
    assert b"zebra" not in _on_disk(path)
    assert not (tmp_path / "memoreei.db.encrypting").exists()
    conn = key.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
    conn.close()


async def test_a_wrong_key_says_so(tmp_path, monkeypatch):
    path = tmp_path / "memoreei.db"
    await _store(str(path))
    monkeypatch.setenv(key.DB_KEY_ENV, "ab" * 32)
    with pytest.raises(key.DatabaseKeyError, match="won't open with the key from MEMOREEI_DB_KEY"):
        await _store(str(path))


async def test_a_missing_key_says_where_it_looked(tmp_path, monkeypatch):
    path = tmp_path / "memoreei.db"
    await _store(str(path))
    (tmp_path / "memoreei.key").unlink()
    monkeypatch.setattr(key, "_keys", {})
    with pytest.raises(key.DatabaseKeyError, match="is encrypted, and its key isn't here") as err:
        key.connect(path)
    assert "memoreei.key" in str(err.value) and "MEMOREEI_DB_KEY" in str(err.value)
    assert not (tmp_path / "memoreei.key").exists()  # and no new key was made to lock it for good


async def test_the_environment_key_wins_and_leaves_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv(key.DB_KEY_ENV, "CD" * 32)
    path = tmp_path / "memoreei.db"
    await _store(str(path))
    assert not (tmp_path / "memoreei.key").exists()
    assert key.key_source(path) == key.DB_KEY_ENV
    monkeypatch.setenv(key.DB_KEY_ENV, "not a key")
    with pytest.raises(key.DatabaseKeyError, match="64 hex characters"):
        key.connect(path)


class FakeRing:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def get(self, path: str) -> str | None:
        return self.items.get(path)

    def put(self, path: str, value: str) -> bool:
        self.items[path] = value
        return True


async def test_a_new_key_goes_into_the_keyring_when_there_is_one(tmp_path, monkeypatch):
    ring = FakeRing()
    monkeypatch.setattr(key, "_keyring", lambda: contextlib.nullcontext(ring))
    path = tmp_path / "memoreei.db"
    await _store(str(path))
    assert list(ring.items) == [str(path.resolve())]
    assert not (tmp_path / "memoreei.key").exists()
    monkeypatch.setattr(key, "_keys", {})
    assert key.key_source(path) == "the keyring"
    async with Database(db_path=str(path)) as db:
        assert await db.get_by_id("m1") is not None


async def test_a_locked_keyring_is_named_in_the_error(tmp_path, monkeypatch):
    path = tmp_path / "memoreei.db"
    await _store(str(path))
    (tmp_path / "memoreei.key").unlink()
    monkeypatch.setattr(key, "_keys", {})
    monkeypatch.setattr(key, "_keyring_locked", lambda: True)
    with pytest.raises(key.DatabaseKeyError, match="the keyring is locked"):
        key.connect(path)


# ── secrets ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_load(monkeypatch):
    """Load settings as a starting server does."""
    monkeypatch.setattr(config_module, "_env_loaded", False)
    monkeypatch.setattr(config_module, "_from_vault", set())
    for name in ("GMAIL_APP_PASSWORD", "GMAIL_EMAIL", "TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_secrets_move_out_of_config_env(isolated_home, fresh_load):
    env = isolated_home / "config.env"
    env.write_text("GMAIL_EMAIL=me@example.com\nGMAIL_APP_PASSWORD=hunter2\n"
                   "# OPENAI_API_KEY=sk-commented\nTELEGRAM_BOT_TOKEN=\nAUTO_SYNC=true\n")
    cfg = config_module.get_config()
    assert cfg.gmail_app_password == "hunter2" and cfg.gmail_email == "me@example.com"
    text = env.read_text()
    assert "hunter2" not in text and "TELEGRAM_BOT_TOKEN" not in text
    assert "GMAIL_EMAIL=me@example.com" in text and "# OPENAI_API_KEY=sk-commented" in text
    assert env.stat().st_mode & 0o777 == 0o600
    db = str(isolated_home / "memoreei.db")
    assert read_secrets(db) == {"GMAIL_APP_PASSWORD": "hunter2"}
    assert b"hunter2" not in _on_disk(db)


def test_the_real_environment_still_wins(isolated_home, fresh_load, monkeypatch):
    write_secrets(str(isolated_home / "memoreei.db"), {"TELEGRAM_BOT_TOKEN": "from-the-database"})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-the-environment")
    assert config_module.get_config().telegram_token == "from-the-environment"
    config_module.save_settings([("TELEGRAM_BOT_TOKEN", "saved-later")])
    assert config_module.get_config().telegram_token == "from-the-environment"


def test_saving_and_clearing_a_secret_reaches_the_running_server(isolated_home, fresh_load):
    config_module.save_settings([("TELEGRAM_BOT_TOKEN", "t0ken"), ("TELEGRAM_CHAT_ID", "42")])
    cfg = config_module.get_config()
    assert cfg.telegram_token == "t0ken" and cfg.telegram_chat_id == "42"
    assert "t0ken" not in (isolated_home / "config.env").read_text()
    assert config_module.current_settings()["TELEGRAM_BOT_TOKEN"] == "t0ken"
    config_module.save_settings([("TELEGRAM_BOT_TOKEN", "")])
    assert config_module.get_config().telegram_token is None
    assert "TELEGRAM_BOT_TOKEN" not in config_module.current_settings()


def test_reading_secrets_makes_no_database(isolated_home, fresh_load):
    assert config_module.get_config().telegram_token is None
    assert not (isolated_home / "memoreei.db").exists()
    assert not (isolated_home / "memoreei.key").exists()
