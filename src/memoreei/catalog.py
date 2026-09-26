"""What can be set up, for `memoreei setup` and the dashboard alike.

Each connector names the config.env variables it needs. The dashboard draws its forms
from this table, so a connector reaches it by being listed in DASHBOARD_CONNECTORS once
it has been tested there, not by growing a screen of its own.

A row is one of two kinds: "config" (variables in config.env, such as a token or a path)
or "upload" (a file the user hands over, imported and registered for `sync`). No upload
kind is enabled yet, but the dashboard's table and routes already take both.
"""
from __future__ import annotations

import os
from pathlib import Path

# Tuple fields per variable: (name, label, secret, hint[, default]).
CONNECTORS: dict[str, dict] = {
    "gmail": {
        "name": "Gmail (IMAP)",
        "icon": "📧",
        "vars": [
            ("GMAIL_EMAIL", "Gmail address", False, "e.g. you@gmail.com"),
            ("GMAIL_APP_PASSWORD", "App Password", True,
             "Generate at https://myaccount.google.com/apppasswords (requires 2FA)"),
        ],
        "sync_name": "email",
    },
    "discord": {
        "name": "Discord (Bot API)",
        "icon": "🎮",
        "vars": [
            ("DISCORD_BOT_TOKEN", "Bot token", True, "From https://discord.com/developers/applications"),
            ("DISCORD_CHANNEL_ID", "Channel ID", False, "Right-click channel → Copy ID (enable Developer Mode)"),
        ],
    },
    "telegram": {
        "name": "Telegram",
        "icon": "✈️",
        "vars": [
            ("TELEGRAM_BOT_TOKEN", "Bot token", True, "From @BotFather on Telegram"),
            ("TELEGRAM_CHAT_ID", "Chat ID", False, "Use @userinfobot or check API updates"),
        ],
    },
    "slack": {
        "name": "Slack",
        "icon": "💬",
        "vars": [
            ("SLACK_BOT_TOKEN", "Bot token", True, "From https://api.slack.com/apps → OAuth & Permissions"),
            ("SLACK_CHANNEL_ID", "Channel ID", False, "Right-click channel → View channel details → copy ID"),
        ],
    },
    "matrix": {
        "name": "Matrix",
        "icon": "🟩",
        "vars": [
            ("MATRIX_HOMESERVER", "Homeserver URL", False, "e.g. https://matrix.org"),
            ("MATRIX_ACCESS_TOKEN", "Access token", True, "Settings → Help & About → Access Token in Element"),
            ("MATRIX_ROOM_ID", "Room ID", False, "e.g. !abc123:matrix.org"),
        ],
    },
    "mastodon": {
        "name": "Mastodon",
        "icon": "🐘",
        "vars": [
            ("MASTODON_INSTANCE", "Instance URL", False, "e.g. https://mastodon.social"),
            ("MASTODON_HASHTAG", "Hashtag to track (optional)", False, "Without the # sign"),
            ("MASTODON_ACCESS_TOKEN", "Access token", True,
             "Preferences → Development → New Application → copy token"),
        ],
    },
    "signal": {
        "name": "Signal Desktop",
        "icon": "🔒",
        "vars": [
            ("SIGNAL_DB_PATH", "Signal DB path (optional)", False,
             "Leave blank for auto-detect (~/.config/Signal/sql/db.sqlite)"),
            ("SIGNAL_CONFIG_PATH", "Signal config path (optional)", False,
             "Leave blank for auto-detect (~/.config/Signal/config.json)"),
        ],
    },
    "imessage": {
        "name": "iMessage (macOS only)",
        "icon": "🍎",
        "vars": [
            ("IMESSAGE_DB_PATH", "Messages DB path", False,
             "Press Enter to use the default macOS location.",
             "~/Library/Messages/chat.db"),
        ],
    },
}


def read_env_lines(env_path: Path) -> list[str]:
    if env_path.exists():
        return env_path.read_text().splitlines()
    return []


def parse_env_vars(env_lines: list[str]) -> dict[str, str]:
    """Parse .env lines into a dict of KEY -> VALUE (non-commented, non-empty)."""
    result: dict[str, str] = {}
    for line in env_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip()
            if key and value:
                result[key] = value
    return result


def is_connector_configured(key: str, env_vars: dict[str, str]) -> bool:
    """Check if all vars for a connector have non-empty values in env_vars."""
    info = CONNECTORS[key]
    return all(var_name in env_vars for var_name, *_ in info["vars"])


def write_env_updates(
    env_path: Path, env_lines: list[str], updates: list[tuple[str, str]]
) -> None:
    for var_name, value in updates:
        found = False
        for i, line in enumerate(env_lines):
            stripped = line.lstrip("# ").strip()
            if stripped.startswith(f"{var_name}=") or stripped.startswith(f"{var_name} ="):
                env_lines[i] = f"{var_name}={value}"
                found = True
                break
        if not found:
            env_lines.append(f"{var_name}={value}")
    # config.env holds tokens: create it private, and keep it that way.
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(env_lines) + "\n")
    os.chmod(env_path, 0o600)



# File imports, the "upload" kind: the importer (imports.py) and what the file is.
UPLOADS: dict[str, dict] = {
    "whatsapp": {"name": "WhatsApp chat export", "icon": "💚", "accept": ".txt",
                 "hint": "In a chat: ⋯ → More → Export chat → Without media"},
    "sms": {"name": "Android SMS backup", "icon": "📱", "accept": ".xml",
            "hint": "An XML file from SMS Backup & Restore"},
    "contacts-vcf": {"name": "Contacts (vCard)", "icon": "👥", "accept": ".vcf",
                     "hint": "Contacts → File → Export → Export vCard"},
}

# What the dashboard offers. The rest stay CLI-only until each is tested there.
DASHBOARD_CONNECTORS: tuple[str, ...] = ("imessage",)
DASHBOARD_UPLOADS: tuple[str, ...] = ()
