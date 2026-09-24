"""CLI-level tests for `memoreei service` — verifies delegation and guards."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from memoreei.cli import app

runner = CliRunner()

_DETECT = "memoreei.service._detect.get_backend"


def _mock_backend():
    return MagicMock()


# ---------------------------------------------------------------------------
# Unsupported platform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subcmd", ["install", "uninstall", "status", "logs"])
def test_unsupported_platform_exits(tmp_path, monkeypatch, subcmd):
    monkeypatch.chdir(tmp_path)
    with patch.object(sys, "platform", "win32"):
        result = runner.invoke(app, ["service", subcmd])
    assert result.exit_code == 1
    assert "not supported" in result.output.lower()


# ---------------------------------------------------------------------------
# install — CLI guards
# ---------------------------------------------------------------------------


def test_install_requires_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with patch.object(sys, "platform", "darwin"):
        result = runner.invoke(app, ["service", "install"])
    assert result.exit_code == 1
    assert ".env" in result.output


def test_install_delegates_with_default_port(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MEMOREEI_DB_PATH=./memoreei.db\n")
    backend = _mock_backend()

    with patch(_DETECT, return_value=backend):
        result = runner.invoke(app, ["service", "install"])

    assert result.exit_code == 0
    backend.install.assert_called_once()
    _, _, port = backend.install.call_args[0]
    assert port == 8080


def test_install_delegates_with_custom_port(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MEMOREEI_DB_PATH=./memoreei.db\n")
    backend = _mock_backend()

    with patch(_DETECT, return_value=backend):
        result = runner.invoke(app, ["service", "install", "--port", "9090"])

    assert result.exit_code == 0
    _, _, port = backend.install.call_args[0]
    assert port == 9090


def test_install_passes_env_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("MEMOREEI_DB_PATH=./memoreei.db\n")
    backend = _mock_backend()

    with patch(_DETECT, return_value=backend):
        runner.invoke(app, ["service", "install"])

    _, env_path, _ = backend.install.call_args[0]
    assert env_path.name == ".env"


# ---------------------------------------------------------------------------
# uninstall / status / logs — delegation
# ---------------------------------------------------------------------------


def test_uninstall_delegates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backend = _mock_backend()
    with patch(_DETECT, return_value=backend):
        result = runner.invoke(app, ["service", "uninstall"])
    assert result.exit_code == 0
    backend.uninstall.assert_called_once()


def test_status_delegates(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backend = _mock_backend()
    with patch(_DETECT, return_value=backend):
        result = runner.invoke(app, ["service", "status"])
    assert result.exit_code == 0
    backend.status.assert_called_once()


def test_logs_delegates_with_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backend = _mock_backend()
    with patch(_DETECT, return_value=backend):
        result = runner.invoke(app, ["service", "logs"])
    assert result.exit_code == 0
    backend.logs.assert_called_once_with(follow=True, lines=50)


def test_logs_no_follow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    backend = _mock_backend()
    with patch(_DETECT, return_value=backend):
        result = runner.invoke(app, ["service", "logs", "--no-follow"])
    assert result.exit_code == 0
    backend.logs.assert_called_once_with(follow=False, lines=50)
