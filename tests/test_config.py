"""Tests for the config module."""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import memoreei.config as cfg_module
from memoreei.config import Config, get_config


@pytest.fixture(autouse=True)
def reset_config_singleton():
    """Reset the config singleton before and after each test."""
    original = cfg_module._config
    cfg_module._config = None
    yield
    cfg_module._config = original


def test_default_db_path_is_in_home(isolated_home, monkeypatch):
    monkeypatch.delenv("MEMOREEI_DB_PATH", raising=False)
    cfg = get_config()
    assert cfg.db_path == str(isolated_home / "memoreei.db")


def test_default_home_is_the_mac_convention_on_macos():
    assert cfg_module.default_home("darwin") == "~/Library/Application Support/Memoreei"


def test_default_home_is_a_dot_directory_elsewhere():
    assert cfg_module.default_home("linux") == "~/.memoreei"


def test_home_without_env_uses_platform_default(monkeypatch):
    monkeypatch.delenv("MEMOREEI_HOME", raising=False)
    monkeypatch.setattr(cfg_module.sys, "platform", "darwin")
    assert str(cfg_module.memoreei_home()).endswith("Library/Application Support/Memoreei")
    monkeypatch.setattr(cfg_module.sys, "platform", "linux")
    assert str(cfg_module.memoreei_home()).endswith("/.memoreei")


def test_reload_picks_up_edited_config_env(isolated_home, monkeypatch):
    monkeypatch.delenv("AUTO_SYNC", raising=False)
    (isolated_home / "config.env").write_text("AUTO_SYNC=false\n")
    cfg_module.reload_config()
    assert get_config().auto_sync is False
    (isolated_home / "config.env").write_text("AUTO_SYNC=true\n")
    cfg_module.reload_config()
    assert get_config().auto_sync is True


def test_reload_never_overrides_the_real_environment(isolated_home, monkeypatch):
    monkeypatch.setenv("AUTO_SYNC", "false")
    monkeypatch.setattr(cfg_module, "_process_env_keys", frozenset({"AUTO_SYNC"}))
    (isolated_home / "config.env").write_text("AUTO_SYNC=true\n")
    cfg_module.reload_config()
    assert get_config().auto_sync is False


def test_default_network_settings(monkeypatch):
    for var in ("MEMOREEI_HOST", "MEMOREEI_PORT", "MEMOREEI_PUBLIC_URL"):
        monkeypatch.delenv(var, raising=False)
    cfg = get_config()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 3679
    assert cfg.public_url is None


def test_default_embedding_provider():
    cfg = Config()
    assert cfg.embedding_provider == "fastembed"


def test_default_auto_sync():
    cfg = Config()
    assert cfg.auto_sync is False


def test_default_sync_interval():
    cfg = Config()
    assert cfg.sync_interval == 300


def test_default_tokens_are_none():
    cfg = Config()
    assert cfg.discord_token is None
    assert cfg.telegram_token is None
    assert cfg.matrix_homeserver is None
    assert cfg.slack_bot_token is None
    assert cfg.gmail_email is None
    assert cfg.openai_api_key is None


def test_env_var_db_path(monkeypatch):
    monkeypatch.setenv("MEMOREEI_DB_PATH", "/tmp/custom.db")
    cfg = get_config()
    assert cfg.db_path == "/tmp/custom.db"


def test_env_var_embedding_provider(monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    cfg = get_config()
    assert cfg.embedding_provider == "openai"


def test_env_var_auto_sync_true(monkeypatch):
    monkeypatch.setenv("AUTO_SYNC", "true")
    cfg = get_config()
    assert cfg.auto_sync is True


def test_env_var_auto_sync_one(monkeypatch):
    monkeypatch.setenv("AUTO_SYNC", "1")
    cfg = get_config()
    assert cfg.auto_sync is True


def test_env_var_auto_sync_false(monkeypatch):
    monkeypatch.setenv("AUTO_SYNC", "false")
    cfg = get_config()
    assert cfg.auto_sync is False


def test_env_var_sync_interval(monkeypatch):
    monkeypatch.setenv("SYNC_INTERVAL", "60")
    cfg = get_config()
    assert cfg.sync_interval == 60


def test_env_var_discord(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok123")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "chan456")
    cfg = get_config()
    assert cfg.discord_token == "tok123"
    assert cfg.discord_channel_id == "chan456"


def test_get_config_singleton(monkeypatch):
    cfg1 = get_config()
    cfg2 = get_config()
    assert cfg1 is cfg2


def test_configured_connectors_empty():
    cfg = Config()
    assert cfg.configured_connectors() == []


def test_configured_connectors_discord():
    cfg = Config(discord_token="tok", discord_channel_id="chan")
    assert "discord" in cfg.configured_connectors()


def test_configured_connectors_discord_requires_both():
    cfg = Config(discord_token="tok")
    assert "discord" not in cfg.configured_connectors()


def test_configured_connectors_telegram():
    cfg = Config(telegram_token="tok")
    assert "telegram" in cfg.configured_connectors()


def test_configured_connectors_matrix():
    cfg = Config(
        matrix_homeserver="https://example.com",
        matrix_access_token="token",
        matrix_room_id="!room:example.com",
    )
    assert "matrix" in cfg.configured_connectors()


def test_configured_connectors_matrix_requires_all_three():
    cfg = Config(matrix_homeserver="https://example.com", matrix_access_token="token")
    assert "matrix" not in cfg.configured_connectors()


def test_configured_connectors_slack():
    cfg = Config(slack_bot_token="tok", slack_channel_id="chan")
    assert "slack" in cfg.configured_connectors()


def test_configured_connectors_email():
    cfg = Config(gmail_email="test@gmail.com", gmail_app_password="pass")
    assert "email" in cfg.configured_connectors()


def test_configured_connectors_mastodon_instance():
    cfg = Config(mastodon_instance="https://mastodon.social")
    assert "mastodon" in cfg.configured_connectors()


def test_configured_connectors_mastodon_hashtag():
    cfg = Config(mastodon_hashtag="rust")
    assert "mastodon" in cfg.configured_connectors()


def test_env_var_auto_sync_interval(monkeypatch):
    monkeypatch.setenv("AUTO_SYNC_INTERVAL", "120")
    cfg = get_config()
    assert cfg.sync_interval == 120


def test_auto_sync_interval_takes_priority_over_sync_interval(monkeypatch):
    monkeypatch.setenv("AUTO_SYNC_INTERVAL", "120")
    monkeypatch.setenv("SYNC_INTERVAL", "999")
    cfg = get_config()
    assert cfg.sync_interval == 120


def test_configured_connectors_imessage_on_macos():
    with patch.object(sys, "platform", "darwin"):
        cfg = Config(imessage_db_path="/some/path/chat.db")
        assert "imessage" in cfg.configured_connectors()


def test_configured_connectors_imessage_not_without_db_path():
    with patch.object(sys, "platform", "darwin"):
        cfg = Config()  # imessage_db_path defaults to None
        assert "imessage" not in cfg.configured_connectors()


def test_configured_connectors_imessage_not_on_non_macos():
    with patch.object(sys, "platform", "linux"):
        cfg = Config(imessage_db_path="/some/path/chat.db")
        assert "imessage" not in cfg.configured_connectors()


def test_configured_connectors_multiple():
    cfg = Config(
        discord_token="tok",
        discord_channel_id="chan",
        telegram_token="tok2",
    )
    connectors = cfg.configured_connectors()
    assert "discord" in connectors
    assert "telegram" in connectors


# ---------------------------------------------------------------------------
# Home directory and config file resolution
# ---------------------------------------------------------------------------


@pytest.fixture
def scratch_environ(monkeypatch):
    """Let a test load dotenv files without leaking their values into later tests."""
    import os

    env = dict(os.environ)
    for var in ("MEMOREEI_HOME", "MEMOREEI_DB_PATH", "MEMOREEI_PORT", "EMBEDDING_PROVIDER"):
        env.pop(var, None)
    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(cfg_module, "_env_loaded", False)
    return env


def test_home_default_is_dot_memoreei(scratch_environ, monkeypatch, tmp_path):
    scratch_environ["HOME"] = str(tmp_path)
    assert cfg_module.memoreei_home() == tmp_path / ".memoreei"


def test_home_from_env(scratch_environ, tmp_path):
    scratch_environ["MEMOREEI_HOME"] = str(tmp_path / "from-env")
    assert cfg_module.memoreei_home() == tmp_path / "from-env"


def test_home_flag_beats_env(scratch_environ, tmp_path):
    scratch_environ["MEMOREEI_HOME"] = str(tmp_path / "from-env")
    cfg_module.set_home(str(tmp_path / "from-flag"))
    try:
        assert cfg_module.memoreei_home() == tmp_path / "from-flag"
    finally:
        cfg_module.set_home(None)


def test_ensure_home_creates_private_dir(scratch_environ, tmp_path):
    scratch_environ["MEMOREEI_HOME"] = str(tmp_path / "h")
    home = cfg_module.ensure_home()
    assert home.is_dir()
    assert home.stat().st_mode & 0o777 == 0o700


def test_config_env_in_home_is_loaded(scratch_environ, tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    (home / "config.env").write_text("MEMOREEI_PORT=4000\n")
    scratch_environ["MEMOREEI_HOME"] = str(home)
    monkeypatch.chdir(tmp_path)
    assert get_config().port == 4000
    assert get_config().db_path == str(home / "memoreei.db")


def test_cwd_env_overrides_config_env(scratch_environ, tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    (home / "config.env").write_text("MEMOREEI_PORT=4000\nEMBEDDING_PROVIDER=openai\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("MEMOREEI_PORT=5000\n")
    scratch_environ["MEMOREEI_HOME"] = str(home)
    monkeypatch.chdir(work)
    cfg = get_config()
    assert cfg.port == 5000  # cwd .env wins
    assert cfg.embedding_provider == "openai"  # config.env still fills the rest


def test_real_environment_beats_both_files(scratch_environ, tmp_path, monkeypatch):
    home = tmp_path / "h"
    home.mkdir()
    (home / "config.env").write_text("MEMOREEI_PORT=4000\n")
    (tmp_path / ".env").write_text("MEMOREEI_PORT=5000\n")
    scratch_environ["MEMOREEI_HOME"] = str(home)
    scratch_environ["MEMOREEI_PORT"] = "6000"
    monkeypatch.chdir(tmp_path)
    assert get_config().port == 6000


def test_cwd_env_can_set_home(scratch_environ, tmp_path, monkeypatch):
    home = tmp_path / "elsewhere"
    home.mkdir()
    (home / "config.env").write_text("MEMOREEI_PORT=4100\n")
    (tmp_path / ".env").write_text(f"MEMOREEI_HOME={home}\n")
    monkeypatch.chdir(tmp_path)
    assert get_config().port == 4100
