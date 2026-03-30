"""Tests for the config module."""
from __future__ import annotations

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


def test_default_db_path():
    cfg = Config()
    assert cfg.db_path == "./memoreei.db"


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


def test_configured_connectors_multiple():
    cfg = Config(
        discord_token="tok",
        discord_channel_id="chan",
        telegram_token="tok2",
    )
    connectors = cfg.configured_connectors()
    assert "discord" in connectors
    assert "telegram" in connectors
