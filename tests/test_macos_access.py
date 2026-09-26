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


def test_framework_python_needs_its_python_app(tmp_path):
    venv_python, app_bundle = _framework_python(tmp_path)
    assert binary_needing_access(str(venv_python)) == app_bundle.resolve()


def test_plain_python_needs_itself(tmp_path):
    real = tmp_path / "usr/bin/python3"
    real.parent.mkdir(parents=True)
    real.write_text("")
    link = tmp_path / "venv/bin/python"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    assert binary_needing_access(str(link)) == real.resolve()


def test_grant_access_opens_pane_and_reveals_file(tmp_path):
    venv_python, app_bundle = _framework_python(tmp_path)
    with patch.object(sys, "platform", "darwin"), \
         patch.object(sys, "executable", str(venv_python)), \
         patch("subprocess.run") as run:
        result = runner.invoke(app, ["service", "grant-access"])
    assert result.exit_code == 0, result.output
    calls = [c.args[0] for c in run.call_args_list]
    assert ["open", FULL_DISK_ACCESS_PANE] in calls
    assert ["open", "-R", str(app_bundle.resolve())] in calls
    assert str(app_bundle.resolve()) in result.output


def test_grant_access_is_a_no_op_off_macos():
    with patch.object(sys, "platform", "linux"), patch("subprocess.run") as run:
        result = runner.invoke(app, ["service", "grant-access"])
    assert result.exit_code == 0
    run.assert_not_called()
