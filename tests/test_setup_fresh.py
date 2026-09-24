"""Test that `memoreei setup gmail` works from scratch with no existing .env."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from memoreei.cli import app

runner = CliRunner()


def _make_mock_questionary(
    text_values: list[str],
    password_values: list[str] | None = None,
    select_values: list[str] | None = None,
    confirm_values: list[bool] | None = None,
):
    """Build a mock questionary module that returns values in order."""
    mock_q = MagicMock()
    text_iter = iter(text_values)
    password_iter = iter(password_values or [])
    select_iter = iter(select_values or [])
    confirm_iter = iter(confirm_values or [])

    def _text_side_effect(prompt, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_iter)
        return m

    def _password_side_effect(prompt, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(password_iter)
        return m

    def _select_side_effect(prompt, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_iter)
        return m

    def _confirm_side_effect(prompt, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(confirm_iter)
        return m

    mock_q.text.side_effect = _text_side_effect
    mock_q.password.side_effect = _password_side_effect
    mock_q.select.side_effect = _select_side_effect
    mock_q.confirm.side_effect = _confirm_side_effect
    return mock_q


def test_setup_gmail_fresh_no_env(tmp_path, monkeypatch):
    """No .env exists at all — setup should create one from scratch."""
    monkeypatch.chdir(tmp_path)

    env_file = tmp_path / ".env"
    assert not env_file.exists(), "precondition: no .env"

    db_path = str(tmp_path / "fresh.db")
    mock_q = _make_mock_questionary(
        text_values=[db_path, "zezima@gmail.com"],
        password_values=["s3cret"],
        select_values=["fastembed"],
        confirm_values=[False],
    )

    with patch.dict("sys.modules", {"questionary": mock_q}):
        result = runner.invoke(app, ["setup", "gmail"])

    assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    # .env must now exist
    assert env_file.exists(), ".env was not created"
    content = env_file.read_text()

    # Must contain DB path (first-time prompt) and gmail credentials
    assert f"MEMOREEI_DB_PATH={db_path}" in content
    assert "GMAIL_EMAIL=zezima@gmail.com" in content
    assert "GMAIL_APP_PASSWORD=s3cret" in content

    # Validate every non-blank line is proper KEY=VALUE
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "=" in stripped, f"malformed line: {stripped!r}"
        key, _, value = stripped.partition("=")
        assert key.isidentifier(), f"bad key: {key!r}"
