"""Tests for the `memoreei setup` CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from memoreei.cli import app

runner = CliRunner()


def _make_mock_questionary(text_values: list[str], password_values: list[str] | None = None, checkbox_values: list[str] | None = None):
    """Build a mock questionary module that returns values in order."""
    mock_q = MagicMock()
    text_iter = iter(text_values)
    password_iter = iter(password_values or [])

    def _text_side_effect(prompt, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_iter)
        return m

    def _password_side_effect(prompt, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_iter)
        return m

    def _checkbox_side_effect(prompt, **kwargs):
        m = MagicMock()
        m.ask.return_value = checkbox_values
        return m

    mock_q.text.side_effect = _text_side_effect
    mock_q.password.side_effect = _password_side_effect
    mock_q.checkbox.side_effect = _checkbox_side_effect
    mock_q.Choice = lambda **kw: kw  # just pass through
    return mock_q


class TestSetupGmail:
    """Test `memoreei setup gmail` — single connector mode."""

    def test_setup_gmail_writes_env(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Gmail needs: text for DB path, text for GMAIL_EMAIL, password for GMAIL_APP_PASSWORD
        mock_q = _make_mock_questionary(
            text_values=[str(tmp_path / "test.db"), "user@gmail.com"],
            password_values=["secret123"],
        )
        with patch.dict("sys.modules", {"questionary": mock_q}):
            result = runner.invoke(app, ["setup", "gmail"])

        assert result.exit_code == 0
        env_content = (tmp_path / ".env").read_text()
        assert "GMAIL_EMAIL=user@gmail.com" in env_content
        assert "GMAIL_APP_PASSWORD=secret123" in env_content


class TestSetupInteractive:
    """Test `memoreei setup` with no arg — interactive mode."""

    def test_setup_interactive_multi_connector(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # DB path prompt, then gmail vars (text + password), then discord vars (password + text)
        mock_q = _make_mock_questionary(
            text_values=[str(tmp_path / "test.db"), "user@gmail.com", "chan123"],
            password_values=["gmailpass", "discordtoken"],
            checkbox_values=["gmail", "discord"],
        )
        with patch.dict("sys.modules", {"questionary": mock_q}):
            result = runner.invoke(app, ["setup"])

        assert result.exit_code == 0
        env_content = (tmp_path / ".env").read_text()
        assert "GMAIL_EMAIL=user@gmail.com" in env_content
        assert "GMAIL_APP_PASSWORD=gmailpass" in env_content
        assert "DISCORD_BOT_TOKEN=discordtoken" in env_content
        assert "DISCORD_CHANNEL_ID=chan123" in env_content


class TestSetupUnknownConnector:
    """Test `memoreei setup nonexistent` — should exit with error."""

    def test_unknown_connector_exits_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Still need DB path prompt before connector check
        mock_q = _make_mock_questionary(text_values=[str(tmp_path / "test.db")])
        with patch.dict("sys.modules", {"questionary": mock_q}):
            result = runner.invoke(app, ["setup", "nonexistent"])

        assert result.exit_code == 1
        assert "Unknown connector: nonexistent" in result.output


class TestSetupFirstTimeDbPath:
    """Test first-time setup prompts for DB path."""

    def test_first_time_prompts_db_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        custom_db = str(tmp_path / "custom" / "memoreei.db")
        mock_q = _make_mock_questionary(
            text_values=[custom_db, "user@gmail.com"],
            password_values=["pass"],
        )
        with patch.dict("sys.modules", {"questionary": mock_q}):
            result = runner.invoke(app, ["setup", "gmail"])

        assert result.exit_code == 0
        env_content = (tmp_path / ".env").read_text()
        assert f"MEMOREEI_DB_PATH={custom_db}" in env_content
        # Parent dir should have been created
        assert (tmp_path / "custom").is_dir()


class TestSetupPreservesExistingEnv:
    """Test that existing .env values are preserved when adding new connectors."""

    def test_existing_env_preserved(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Pre-populate .env
        env_file = tmp_path / ".env"
        env_file.write_text("MEMOREEI_DB_PATH=/some/db\nEXISTING_VAR=keep_me\n")

        # Since DB path exists, no DB prompt — just gmail vars
        mock_q = _make_mock_questionary(
            text_values=["user@gmail.com"],
            password_values=["pass"],
        )
        with patch.dict("sys.modules", {"questionary": mock_q}):
            result = runner.invoke(app, ["setup", "gmail"])

        assert result.exit_code == 0
        env_content = (tmp_path / ".env").read_text()
        assert "EXISTING_VAR=keep_me" in env_content
        assert "MEMOREEI_DB_PATH=/some/db" in env_content
        assert "GMAIL_EMAIL=user@gmail.com" in env_content
        assert "GMAIL_APP_PASSWORD=pass" in env_content


class TestSetupResetFlag:
    """Test `memoreei setup gmail --reset` clears old values."""

    def test_reset_clears_existing_values(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        env_file = tmp_path / ".env"
        env_file.write_text(
            "MEMOREEI_DB_PATH=/some/db\nGMAIL_EMAIL=old@test.com\nGMAIL_APP_PASSWORD=oldpass\n"
        )

        mock_q = _make_mock_questionary(
            text_values=["new@test.com"],
            password_values=["newpass"],
        )
        with patch.dict("sys.modules", {"questionary": mock_q}):
            result = runner.invoke(app, ["setup", "gmail", "--reset"])

        assert result.exit_code == 0
        env_content = (tmp_path / ".env").read_text()
        assert "GMAIL_EMAIL=new@test.com" in env_content
        assert "old@test.com" not in env_content
        assert "GMAIL_APP_PASSWORD=newpass" in env_content
        assert "oldpass" not in env_content
        assert "MEMOREEI_DB_PATH=/some/db" in env_content
