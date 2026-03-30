from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env once at import time
_here = Path(__file__).parent
for _candidate in [_here.parent.parent.parent / ".env", Path(".env")]:
    if _candidate.exists():
        load_dotenv(_candidate)
        break


@dataclass
class Config:
    # Core
    db_path: str = "./memoreei.db"
    embedding_provider: str = "fastembed"
    openai_api_key: str | None = None
    auto_sync: bool = False
    sync_interval: int = 300

    # Discord
    discord_token: str | None = None
    discord_channel_id: str | None = None

    # Telegram
    telegram_token: str | None = None
    telegram_chat_id: str | None = None

    # Matrix
    matrix_homeserver: str | None = None
    matrix_access_token: str | None = None
    matrix_room_id: str | None = None

    # Slack
    slack_bot_token: str | None = None
    slack_channel_id: str | None = None

    # Email (Gmail)
    gmail_email: str | None = None
    gmail_app_password: str | None = None

    # Mastodon
    mastodon_instance: str | None = None
    mastodon_hashtag: str | None = None
    mastodon_access_token: str | None = None

    def configured_connectors(self) -> list[str]:
        """Return names of connectors that have sufficient config to operate."""
        connectors: list[str] = []
        if self.discord_token and self.discord_channel_id:
            connectors.append("discord")
        if self.telegram_token:
            connectors.append("telegram")
        if self.matrix_homeserver and self.matrix_access_token and self.matrix_room_id:
            connectors.append("matrix")
        if self.slack_bot_token and self.slack_channel_id:
            connectors.append("slack")
        if self.gmail_email and self.gmail_app_password:
            connectors.append("email")
        if self.mastodon_instance or self.mastodon_hashtag:
            connectors.append("mastodon")
        return connectors


_config: Config | None = None


def get_config() -> Config:
    """Return the singleton Config instance, building it from env vars on first call."""
    global _config
    if _config is None:
        _config = Config(
            db_path=os.environ.get("MEMOREEI_DB_PATH", "./memoreei.db"),
            embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "fastembed").lower(),
            openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
            auto_sync=os.environ.get("AUTO_SYNC", "").lower() in ("1", "true", "yes"),
            sync_interval=int(os.environ.get("SYNC_INTERVAL", "300")),
            discord_token=os.environ.get("DISCORD_BOT_TOKEN") or None,
            discord_channel_id=os.environ.get("DISCORD_CHANNEL_ID") or None,
            telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID") or None,
            matrix_homeserver=os.environ.get("MATRIX_HOMESERVER") or None,
            matrix_access_token=os.environ.get("MATRIX_ACCESS_TOKEN") or None,
            matrix_room_id=os.environ.get("MATRIX_ROOM_ID") or None,
            slack_bot_token=os.environ.get("SLACK_BOT_TOKEN") or None,
            slack_channel_id=os.environ.get("SLACK_CHANNEL_ID") or None,
            gmail_email=os.environ.get("GMAIL_EMAIL") or None,
            gmail_app_password=os.environ.get("GMAIL_APP_PASSWORD") or os.environ.get("GMAIL_PASSWORD") or None,
            mastodon_instance=os.environ.get("MASTODON_INSTANCE") or None,
            mastodon_hashtag=os.environ.get("MASTODON_HASHTAG") or None,
            mastodon_access_token=os.environ.get("MASTODON_ACCESS_TOKEN") or None,
        )
    return _config
