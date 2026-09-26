"""memoreei service grant-access: finding the file macOS wants, and opening Settings."""
from __future__ import annotations

import sys
from unittest.mock import patch

from typer.testing import CliRunner

from memoreei.cli import app
from memoreei.service._macos_access import FULL_DISK_ACCESS_PANE, binary_needing_access

runner = CliRunner()


def _framework_python(tmp_path):
    """A Homebrew-style framework Python and a venv symlinked to it."""
    version = tmp_path / "Cellar/python@3.11/3.11.0/Frameworks/Python.framework/Versions/3.11"
    (version / "bin").mkdir(parents=True)
    (version / "Resources/Python.app/Contents/MacOS").mkdir(parents=True)
    real = version / "bin" / "python3.11"
    real.write_text("")
    venv_python = tmp_path / "venv/bin/python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(real)
    return venv_python, version / "Resources/Python.app"


def test_framework_python_needs_its_interpreter_not_python_app(tmp_path):
    """launchd holds the interpreter responsible, not the Python.app it re-execs into."""
    venv_python, _ = _framework_python(tmp_path)
    expected = (tmp_path / "Cellar/python@3.11/3.11.0/Frameworks/Python.framework"
                "/Versions/3.11/bin/python3.11").resolve()
    assert binary_needing_access(str(venv_python)) == expected


def test_plain_python_needs_itself(tmp_path):
    real = tmp_path / "usr/bin/python3"
    real.parent.mkdir(parents=True)
    real.write_text("")
    link = tmp_path / "venv/bin/python"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    assert binary_needing_access(str(link)) == real.resolve()


def test_grant_access_opens_pane_and_reveals_file(tmp_path):
    venv_python, _ = _framework_python(tmp_path)
    interpreter = venv_python.resolve()
    with patch.object(sys, "platform", "darwin"), \
         patch.object(sys, "executable", str(venv_python)), \
         patch("subprocess.run") as run:
        result = runner.invoke(app, ["service", "grant-access"])
    assert result.exit_code == 0, result.output
    calls = [c.args[0] for c in run.call_args_list]
    assert ["open", FULL_DISK_ACCESS_PANE] in calls
    assert ["open", "-R", str(interpreter)] in calls
    assert str(interpreter) in result.output


def test_grant_access_is_a_no_op_off_macos():
    with patch.object(sys, "platform", "linux"), patch("subprocess.run") as run:
        result = runner.invoke(app, ["service", "grant-access"])
    assert result.exit_code == 0
    run.assert_not_called()


# ── Dialogs and the guided flow ──────────────────────────────────────────────

from unittest.mock import MagicMock  # noqa: E402

from memoreei.service import _macos_access as fda  # noqa: E402


def test_dialog_returns_clicked_button():
    done = MagicMock(returncode=0, stdout="button returned:Done\n", stderr="")
    with patch("subprocess.run", return_value=done) as run:
        assert fda.dialog('Say "hi"', ["Cancel", "Done"], "Done") == "Done"
    script = run.call_args.args[0][2]
    assert 'display dialog "Say \\"hi\\""' in script


def test_dialog_cancel_and_no_gui_are_different():
    cancelled = MagicMock(returncode=1, stdout="", stderr="execution error: User canceled. (-128)")
    no_gui = MagicMock(returncode=1, stdout="", stderr="No user interaction allowed. (-1713)")
    with patch("subprocess.run", return_value=cancelled):
        assert fda.dialog("x", ["Cancel", "OK"], "OK") == "Cancel"
    with patch("subprocess.run", return_value=no_gui):
        assert fda.dialog("x", ["Cancel", "OK"], "OK") is None


def test_instructions_name_the_file():
    text = fda.instructions("Python")
    assert "“Python”" in text
    assert "Drag" in text and "Done" in text


def test_can_read_messages(tmp_path):
    import sqlite3

    db = tmp_path / "chat.db"
    sqlite3.connect(db).execute("CREATE TABLE message (x)").connection.close()
    assert fda.can_read_messages(str(db))
    assert not fda.can_read_messages(str(tmp_path / "nope" / "chat.db"))


def test_startup_report_only_counts_lines_after_offset(tmp_path):
    log = tmp_path / "memoreei.log"
    log.write_text(fda.ACCESS_MISSING + "\n")
    offset = log.stat().st_size
    with log.open("a") as f:
        f.write(fda.ACCESS_OK + "\n")
    assert fda.wait_for_startup_report(log, offset, timeout=2) is True
    assert fda.wait_for_startup_report(log, log.stat().st_size, timeout=1) is None


def _fake_paths(tmp_path):
    plist = tmp_path / "com.memoreei.server.plist"
    plist.write_text("")
    return tmp_path, plist, tmp_path / "memoreei.log"


def test_guided_flow_restarts_and_confirms(tmp_path):
    venv_python, app_bundle = _framework_python(tmp_path)
    paths = _fake_paths(tmp_path)
    paths[2].write_text(fda.ACCESS_MISSING + "\n")
    clicks = iter(["Open Settings", "Done", "OK"])

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["launchctl", "kickstart"]:
            with paths[2].open("a") as f:
                f.write(fda.ACCESS_OK + "\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(sys, "platform", "darwin"), \
         patch.object(sys, "executable", str(venv_python)), \
         patch("memoreei.service._launchd._launchd_paths", lambda: paths), \
         patch.object(fda, "dialog", side_effect=lambda *a, **k: next(clicks)), \
         patch("subprocess.run", side_effect=fake_run) as run:
        result = runner.invoke(app, ["service", "grant-access"])
    assert result.exit_code == 0, result.output
    assert "can read your messages" in result.output
    assert any(c.args[0][:2] == ["launchctl", "kickstart"] for c in run.call_args_list)


def test_guided_flow_says_when_still_missing(tmp_path):
    venv_python, _ = _framework_python(tmp_path)
    paths = _fake_paths(tmp_path)
    clicks = iter(["Open Settings", "Done", "Cancel"])

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["launchctl", "kickstart"]:
            with paths[2].open("a") as f:
                f.write(fda.ACCESS_MISSING + "\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(sys, "platform", "darwin"), \
         patch.object(sys, "executable", str(venv_python)), \
         patch("memoreei.service._launchd._launchd_paths", lambda: paths), \
         patch.object(fda, "dialog", side_effect=lambda *a, **k: next(clicks)), \
         patch("subprocess.run", side_effect=fake_run):
        result = runner.invoke(app, ["service", "grant-access"])
    assert result.exit_code == 1
    assert "Still no access" in result.output


def test_serve_reports_missing_access_at_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("IMESSAGE_DB_PATH", str(tmp_path / "no-access" / "chat.db"))
    runner.invoke(app, ["key", "create", "laptop"])
    with patch.object(sys, "platform", "darwin"), patch("uvicorn.run"):
        result = runner.invoke(app, ["serve", "--http"])
    assert result.exit_code == 0, result.output
    assert fda.ACCESS_MISSING in result.output
    assert "memoreei service grant-access" in result.output
