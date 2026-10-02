"""Registered imports and the argument-free `sync`.

`sync` re-reads registered files, so every importer must give the same message the
same source_id on every run. One that doesn't would duplicate every message on every
sync; the import-twice tests below are what catch it.
"""
from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from memoreei.cli import app
from memoreei.imports import IMPORTERS, import_and_register, resync_imports, run_import
from memoreei.sync_manager import SyncManager
from memoreei.tools.memory_tools import MemoryTools

runner = CliRunner()


@pytest.fixture
def tools(temp_db, mock_embedder) -> MemoryTools:
    return MemoryTools(db=temp_db, embedder=mock_embedder)


# ── One small export per importer ────────────────────────────────────────────


def _sms(tmp: Path) -> tuple[Path, dict]:
    f = tmp / "backup.xml"
    f.write_text(
        textwrap.dedent(
            """\
            <?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
            <smses count="2">
              <sms protocol="0" address="+12025550142" date="1609459200000" type="1"
                   body="Selling lobsters, 2gp each" read="1" status="-1" locked="0"
                   date_sent="0" contact_name="Hans" />
              <sms protocol="0" address="+12025550142" date="1609459260000" type="2"
                   body="I have been walking around this castle for years" read="1"
                   status="-1" locked="0" date_sent="0" contact_name="Hans" />
            </smses>
            """
        )
    )
    return f, {}


def _meta_export(tmp: Path, root: str) -> Path:
    inbox = tmp / root / "inbox" / "gertrude_cat"
    inbox.mkdir(parents=True)
    (inbox / "message_1.json").write_text(
        json.dumps(
            {
                "participants": [{"name": "Gertrude"}, {"name": "You"}],
                "messages": [
                    {"sender_name": "Gertrude", "content": "Have you seen Fluffs?", "timestamp_ms": 1_700_000_000_000, "type": "Generic"},
                    {"sender_name": "You", "content": "Checking the lumber yard", "timestamp_ms": 1_700_000_060_000, "type": "Generic"},
                ],
            }
        )
    )
    return tmp


def _messenger(tmp: Path) -> tuple[Path, dict]:
    return _meta_export(tmp, "messages"), {}


def _instagram(tmp: Path) -> tuple[Path, dict]:
    return _meta_export(tmp, "your_instagram_activity/messages"), {}


def _discord_package(tmp: Path) -> tuple[Path, dict]:
    pkg = tmp / "package"
    msgs = pkg / "messages"
    (msgs / "c111").mkdir(parents=True)
    (msgs / "index.json").write_text(json.dumps({"111": "grand-exchange"}))
    (msgs / "c111" / "messages.json").write_text(
        json.dumps(
            [
                {"ID": "1", "Timestamp": "2024-01-15 12:00:00+00:00", "Contents": "buying gf", "Attachments": ""},
                {"ID": "2", "Timestamp": "2024-01-15 12:01:00+00:00", "Contents": "selling party hat", "Attachments": ""},
            ]
        )
    )
    return pkg, {}


def _json(tmp: Path) -> tuple[Path, dict]:
    f = tmp / "chat.json"
    f.write_text(
        json.dumps(
            [
                {"text": "The Titans stir beneath Atlantis", "from": "Arkantos", "ts": "2024-02-01T10:00:00"},
                {"text": "Then we sail at dawn", "from": "Amanra", "ts": "2024-02-01T10:05:00"},
            ]
        )
    )
    return f, {"content_field": "text", "sender_field": "from", "timestamp_field": "ts", "source_label": "atlantis"}


def _csv(tmp: Path) -> tuple[Path, dict]:
    f = tmp / "chat.csv"
    f.write_text(
        "body,who,when\n"
        "Gargarensis has the relic,Chiron,2024-03-01 09:00\n"
        "Then we follow him,Ajax,2024-03-01 09:02\n"
    )
    return f, {"content_column": "body", "sender_column": "who", "timestamp_column": "when", "source_label": "voyage"}


def _council(tmp: Path) -> tuple[Path, dict]:
    """A CSV the registration and re-read tests can append a row to."""
    f = tmp / "hyrule_council.csv"
    f.write_text(
        "text,who,when\n"
        "has anyone seen my sword,Link,2026-03-20 12:01:14\n"
        "\"it's in the pedestal, where you left it\",Zelda,2026-03-20 12:02:38\n"
        "again,Impa,2026-03-20 12:03:52\n"
    )
    return f, {"content_column": "text", "sender_column": "who", "timestamp_column": "when", "source_label": "hyrule_council"}


COUNCIL_FLAGS = ["--content-column", "text", "--sender-column", "who", "--timestamp-column", "when", "--source-label", "hyrule_council"]


def _vcf(tmp: Path) -> tuple[Path, dict]:
    f = tmp / "contacts.vcf"
    f.write_text(
        "BEGIN:VCARD\nVERSION:3.0\nFN:Saria Kokiri\nTEL:+12025550142\nEND:VCARD\n"
    )
    return f, {}


BUILDERS = {
    "sms": _sms,
    "discord-package": _discord_package,
    "messenger": _messenger,
    "instagram": _instagram,
    "json": _json,
    "csv": _csv,
    "contacts-vcf": _vcf,
}


def test_every_importer_has_a_fixture():
    assert set(BUILDERS) == set(IMPORTERS)


@pytest.mark.parametrize("kind", [k for k in IMPORTERS if k != "contacts-vcf"])
async def test_import_twice_counts_once(kind, tools, tmp_path):
    path, options = BUILDERS[kind](tmp_path)
    first = await run_import(tools, kind, str(path), options)
    assert "error" not in first, first
    assert first["new"] > 0
    count = await tools.db.count_memories()

    second = await run_import(tools, kind, str(path), options)
    assert "error" not in second, second
    assert second["new"] == 0
    assert await tools.db.count_memories() == count


async def test_contacts_import_twice_keeps_one_row(tools, tmp_path):
    path, _ = _vcf(tmp_path)
    await run_import(tools, "contacts-vcf", str(path))
    await run_import(tools, "contacts-vcf", str(path))
    contacts = await tools.db.get_contacts()
    assert contacts == {"+12025550142": "Saria Kokiri"}


# ── Registration ─────────────────────────────────────────────────────────────


async def test_import_registers_absolute_path_and_options(tools, tmp_path, monkeypatch):
    path, options = _csv(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = await import_and_register(tools, "csv", path.name, options)
    entries = await tools.db.list_imports()
    assert len(entries) == 1
    assert entries[0]["id"] == result["import_id"]
    assert entries[0]["kind"] == "csv"
    assert entries[0]["path"] == str(path.resolve())
    assert entries[0]["options"] == options


async def test_failed_import_is_not_registered(tools, tmp_path):
    result = await import_and_register(tools, "sms", str(tmp_path / "nope.xml"))
    assert "error" in result
    assert await tools.db.list_imports() == []


async def test_reimporting_same_file_keeps_one_registration(tools, tmp_path):
    path, options = _council(tmp_path)
    await import_and_register(tools, "csv", str(path), options)
    await import_and_register(tools, "csv", str(path), options)
    assert len(await tools.db.list_imports()) == 1


async def test_forget_import(tools, tmp_path):
    path, options = _council(tmp_path)
    result = await import_and_register(tools, "csv", str(path), options)
    assert await tools.db.forget_import(result["import_id"]) is True
    assert await tools.db.list_imports() == []
    assert await tools.db.forget_import(result["import_id"]) is False


# ── Re-reading on sync ───────────────────────────────────────────────────────


async def test_unchanged_file_is_skipped(tools, tmp_path):
    path, options = _council(tmp_path)
    await import_and_register(tools, "csv", str(path), options)
    with patch("memoreei.imports.run_import") as ran:
        report = await resync_imports(tools)
    ran.assert_not_called()
    assert report[0]["status"] == "unchanged"


async def test_changed_file_is_reread(tools, tmp_path):
    path, options = _council(tmp_path)
    await import_and_register(tools, "csv", str(path), options)
    with path.open("a") as f:
        f.write("found it,Link,2026-03-20 12:09:00\n")
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))

    report = await resync_imports(tools)
    assert report[0]["status"] == "imported"
    assert report[0]["new"] == 1

    again = await resync_imports(tools)
    assert again[0]["status"] == "unchanged"


async def test_changed_directory_is_reread(tools, tmp_path):
    root, _ = _messenger(tmp_path)
    await import_and_register(tools, "messenger", str(root))
    part = root / "messages" / "inbox" / "gertrude_cat" / "message_2.json"
    part.write_text(
        json.dumps(
            {
                "participants": [{"name": "Gertrude"}, {"name": "You"}],
                "messages": [{"sender_name": "Gertrude", "content": "Found him!", "timestamp_ms": 1_700_000_900_000, "type": "Generic"}],
            }
        )
    )
    st = part.stat()
    os.utime(part, (st.st_atime, st.st_mtime + 10))
    report = await resync_imports(tools)
    assert report[0]["status"] == "imported"
    assert report[0]["new"] == 1


async def test_missing_file_is_reported_not_fatal(tools, tmp_path):
    gone, options = _council(tmp_path)
    await import_and_register(tools, "csv", str(gone), options)
    kept_dir = tmp_path / "kept"
    kept_dir.mkdir()
    kept, _ = _sms(kept_dir)
    await import_and_register(tools, "sms", str(kept))
    gone.unlink()
    st = kept.stat()
    os.utime(kept, (st.st_atime, st.st_mtime + 10))

    report = {r["kind"]: r for r in await resync_imports(tools)}
    assert report["csv"]["status"] == "missing"
    assert report["sms"]["status"] == "imported"


async def test_report_names_files_not_paths(tools, tmp_path):
    path, options = _council(tmp_path)
    await import_and_register(tools, "csv", str(path), options)
    report = await resync_imports(tools)
    assert report[0]["file"] == path.name
    assert str(tmp_path) not in json.dumps(report)


async def test_sync_everything_runs_connectors_and_imports(tools, tmp_path, monkeypatch):
    path, options = _council(tmp_path)
    await import_and_register(tools, "csv", str(path), options)
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))
    await tools.db.delete_by_source("hyrule_council")

    manager = SyncManager()

    async def fake_sync_source(source, _tools):
        return 7

    monkeypatch.setattr(manager, "sync_source", fake_sync_source)
    from memoreei.config import Config

    with patch("memoreei.config.get_config", return_value=Config(telegram_token="t")):
        result = await manager.sync_everything(tools)

    assert result["connectors"] == {"telegram": 7}
    assert result["imports"][0]["status"] == "imported"
    assert result["imports"][0]["new"] == 3
    assert result["new_messages"] == 10


# ── CLI ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def cli_env(monkeypatch, mock_embedder):
    monkeypatch.setattr("memoreei.search.embeddings.get_provider", lambda: mock_embedder)


def test_cli_import_registers_and_lists(tmp_path, cli_env):
    path, options = _council(tmp_path)
    result = runner.invoke(app, ["import", "csv", str(path), *COUNCIL_FLAGS])
    assert result.exit_code == 0, result.output
    listed = runner.invoke(app, ["import", "list"])
    assert listed.exit_code == 0
    assert "csv" in listed.output
    assert str(path) in listed.output


def test_cli_import_csv_takes_column_options(tmp_path, cli_env):
    path, _ = _csv(tmp_path)
    result = runner.invoke(
        app,
        ["import", "csv", str(path), "--content-column", "body", "--sender-column", "who", "--source-label", "voyage"],
    )
    assert result.exit_code == 0, result.output
    assert '"new": 2' in result.output


def test_cli_import_failure_exits_nonzero(tmp_path, cli_env):
    result = runner.invoke(app, ["import", "sms", str(tmp_path / "missing.xml")])
    assert result.exit_code == 1
    assert "not registered" not in result.output
    assert "No registered imports" in runner.invoke(app, ["import", "list"]).output


def test_cli_import_forget(tmp_path, cli_env):
    path, options = _council(tmp_path)
    runner.invoke(app, ["import", "csv", str(path), *COUNCIL_FLAGS])
    result = runner.invoke(app, ["import", "forget", "1"])
    assert result.exit_code == 0
    assert "No registered imports" in runner.invoke(app, ["import", "list"]).output
    assert runner.invoke(app, ["import", "forget", "1"]).exit_code == 1


def test_cli_sync_rereads_registered_imports(tmp_path, cli_env):
    path, options = _council(tmp_path)
    runner.invoke(app, ["import", "csv", str(path), *COUNCIL_FLAGS])
    with path.open("a") as f:
        f.write("found it,Link,2026-03-20 12:09:00\n")
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 10))
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "1 new" in result.output


async def test_csv_reordered_reexport_adds_only_new_rows(tools, tmp_path):
    """Exports are often regenerated newest-first; position must not be identity."""
    path, options = _csv(tmp_path)
    await run_import(tools, "csv", str(path), options)
    path.write_text(
        "body,who,when\n"
        "The relic is in the Underworld,Chiron,2024-03-02 08:00\n"
        "Then we follow him,Ajax,2024-03-01 09:02\n"
        "Gargarensis has the relic,Chiron,2024-03-01 09:00\n"
    )
    result = await run_import(tools, "csv", str(path), options)
    assert result["new"] == 1


async def test_json_without_timestamp_is_stable(tools, tmp_path):
    path, options = _json(tmp_path)
    options = {**options, "timestamp_field": ""}
    await run_import(tools, "json", str(path), options)
    second = await run_import(tools, "json", str(path), options)
    assert second["new"] == 0


async def test_dropped_import_kind_says_how_to_forget_it(tools, tmp_path):
    """A registration left by an older version reports itself instead of failing sync."""
    old = tmp_path / "hyrule_council.txt"
    old.write_text("[03/20/26, 12:01:14] Link: has anyone seen my sword\n")
    import_id = await tools.db.register_import("whatsapp", str(old), {}, None)
    report = await resync_imports(tools)
    assert report[0]["status"] == "error"
    assert f"memoreei import forget {import_id}" in report[0]["error"]
