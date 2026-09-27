from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

DEFAULT_PORT = 3679  # "DORY" on a phone keypad
DEFAULT_HOST = "0.0.0.0"

# Set by the CLI's --home option; beats MEMOREEI_HOME and the default.
_home_override: str | None = None
_env_loaded = False
# Variables set in the real environment before any file was read; a reload never
# overrides them, so they keep winning over config.env as they did at startup.
_process_env_keys: frozenset[str] = frozenset()


def set_home(path: str | None) -> None:
    """Point memoreei at a different home directory (the CLI's --home)."""
    global _home_override, _config, _env_loaded
    _home_override = path
    _config = None
    _env_loaded = False


def default_home(platform: str | None = None) -> str:
    """Where memoreei keeps its data when nothing says otherwise.

    macOS follows the Mac convention (and is where Memoreei.app looks), so the app and
    the pip-installed CLI share one set of keys and one database. Elsewhere, a dot
    directory in the home folder. Docker sets MEMOREEI_HOME=/data.
    """
    if (platform or sys.platform) == "darwin":
        return "~/Library/Application Support/Memoreei"
    return "~/.memoreei"


def model_cache_dir(platform: str | None = None) -> Path:
    """Where the fastembed model is downloaded to: a per-user cache.

    fastembed's own default is /tmp/fastembed_cache, which one user's download makes
    unwritable for every other user on the machine, and a reboot empties. The
    ``FASTEMBED_CACHE_PATH`` variable (which Memoreei.app and the Docker image set)
    still wins.
    """
    if custom := os.environ.get("FASTEMBED_CACHE_PATH"):
        return Path(custom).expanduser()
    if (platform or sys.platform) == "darwin":
        return Path("~/Library/Caches/Memoreei/models").expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME") or "~/.cache"
    return Path(xdg).expanduser() / "memoreei" / "models"


def memoreei_home() -> Path:
    """The directory holding config.env and memoreei.db.

    ``--home`` › ``MEMOREEI_HOME`` › default_home(). Not created here; see ensure_home().
    """
    raw = _home_override or os.environ.get("MEMOREEI_HOME") or default_home()
    return Path(raw).expanduser()


def ensure_home() -> Path:
    """Create the home directory (mode 700) if it doesn't exist, and return it."""
    home = memoreei_home()
    if not home.exists():
        home.mkdir(parents=True, mode=0o700)
    return home


def config_env_path() -> Path:
    return memoreei_home() / "config.env"


def load_env() -> None:
    """Load settings into os.environ, once.

    Real environment variables win, then a ``.env`` in the current directory (for
    development), then ``$MEMOREEI_HOME/config.env``. The cwd ``.env`` is read first so it
    may itself set MEMOREEI_HOME.
    """
    global _env_loaded, _process_env_keys
    if _env_loaded:
        return
    _env_loaded = True
    _process_env_keys = frozenset(os.environ)
    cwd_env = Path(".env")
    if cwd_env.is_file():
        load_dotenv(cwd_env)
    home_env = config_env_path()
    if home_env.is_file():
        load_dotenv(home_env)


def reload_config() -> None:
    """Re-read config.env after it has been edited, for a server that's already running.

    Values from config.env replace what was loaded from it before; variables from the
    real environment still win, as they did at startup.
    """
    global _config
    path = config_env_path()
    if path.is_file():
        for key, value in dotenv_values(path).items():
            if key not in _process_env_keys:
                os.environ[key] = value or ""
    _config = None


@dataclass
class Config:
    # Core
    db_path: str = ""
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    public_url: str | None = None
    tls_cert: str | None = None
    tls_key: str | None = None
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

    # iMessage (macOS only)
    imessage_db_path: str | None = None

    # Signal Desktop (local encrypted DB)
    signal_db_path: str | None = None
    signal_config_path: str | None = None

    def configured_connectors(self) -> list[str]:
        """Return names of connectors that have sufficient config to operate."""
        import sys
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
        if sys.platform == "darwin" and self.imessage_db_path:
            connectors.append("imessage")
        return connectors


_config: Config | None = None


def get_config() -> Config:
    """Return the singleton Config instance, building it from env vars on first call."""
    global _config
    if _config is None:
        load_env()
        _config = Config(
            db_path=os.path.expanduser(
                os.environ.get("MEMOREEI_DB_PATH") or str(memoreei_home() / "memoreei.db")
            ),
            host=os.environ.get("MEMOREEI_HOST") or DEFAULT_HOST,
            port=int(os.environ.get("MEMOREEI_PORT") or DEFAULT_PORT),
            public_url=(os.environ.get("MEMOREEI_PUBLIC_URL") or "").rstrip("/") or None,
            tls_cert=os.environ.get("MEMOREEI_TLS_CERT") or None,
            tls_key=os.environ.get("MEMOREEI_TLS_KEY") or None,
            embedding_provider=os.environ.get("EMBEDDING_PROVIDER", "fastembed").lower(),
            openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
            auto_sync=os.environ.get("AUTO_SYNC", "").lower() in ("1", "true", "yes"),
            sync_interval=int(os.environ.get("AUTO_SYNC_INTERVAL") or os.environ.get("SYNC_INTERVAL") or "300"),
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
            imessage_db_path=os.environ.get("IMESSAGE_DB_PATH") or None,
            signal_db_path=os.environ.get("SIGNAL_DB_PATH") or None,
            signal_config_path=os.environ.get("SIGNAL_CONFIG_PATH") or None,
        )
    return _config
