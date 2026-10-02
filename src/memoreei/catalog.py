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

# "icon" is a monogram: the dashboard draws it on a tile, teal once the source is set up.
# Tuple fields per variable: (name, label, secret, hint[, default]).
CONNECTORS: dict[str, dict] = {
    "gmail": {
        "name": "Gmail (IMAP)",
        "short": "Gmail",
        "icon": "G",
        "blurb": "Reads your mail over IMAP. Nothing is sent anywhere.",
        "vars": [
            ("GMAIL_EMAIL", "Gmail address", False, "e.g. you@gmail.com"),
            ("GMAIL_APP_PASSWORD", "App Password", True,
             "Generate at https://myaccount.google.com/apppasswords (requires 2FA)"),
        ],
        "sync_name": "email",
    },
    "discord": {
        "name": "Discord (Bot API)",
        "short": "Discord",
        "icon": "D",
        "blurb": "Reads one channel through a bot you add to your server.",
        "vars": [
            ("DISCORD_BOT_TOKEN", "Bot token", True, "From https://discord.com/developers/applications"),
            ("DISCORD_CHANNEL_ID", "Channel ID", False, "Right-click channel → Copy ID (enable Developer Mode)"),
        ],
    },
    "telegram": {
        "name": "Telegram",
        "short": "Telegram",
        "icon": "T",
        "blurb": "Reads one chat through a bot you add to it.",
        "vars": [
            ("TELEGRAM_BOT_TOKEN", "Bot token", True, "From @BotFather on Telegram"),
            ("TELEGRAM_CHAT_ID", "Chat ID", False, "Use @userinfobot or check API updates"),
        ],
    },
    "slack": {
        "name": "Slack",
        "short": "Slack",
        "icon": "S",
        "blurb": "Reads one channel through a Slack app you install.",
        "vars": [
            ("SLACK_BOT_TOKEN", "Bot token", True, "From https://api.slack.com/apps → OAuth & Permissions"),
            ("SLACK_CHANNEL_ID", "Channel ID", False, "Right-click channel → View channel details → copy ID"),
        ],
    },
    "matrix": {
        "name": "Matrix",
        "short": "Matrix",
        "icon": "M",
        "blurb": "Reads one room with your access token.",
        "vars": [
            ("MATRIX_HOMESERVER", "Homeserver URL", False, "e.g. https://matrix.org"),
            ("MATRIX_ACCESS_TOKEN", "Access token", True, "Settings → Help & About → Access Token in Element"),
            ("MATRIX_ROOM_ID", "Room ID", False, "e.g. !abc123:matrix.org"),
        ],
    },
    "mastodon": {
        "name": "Mastodon",
        "short": "Mastodon",
        "icon": "M",
        "blurb": "Reads your timeline, or one hashtag.",
        "vars": [
            ("MASTODON_INSTANCE", "Instance URL", False, "e.g. https://mastodon.social"),
            ("MASTODON_HASHTAG", "Hashtag to track (optional)", False, "Without the # sign"),
            ("MASTODON_ACCESS_TOKEN", "Access token", True,
             "Preferences → Development → New Application → copy token"),
        ],
    },
    "signal": {
        "name": "Signal Desktop",
        "short": "Signal Desktop",
        "icon": "S",
        "blurb": "Reads Signal Desktop's database on this computer.",
        "vars": [
            ("SIGNAL_DB_PATH", "Signal DB path (optional)", False,
             "Leave blank for auto-detect (~/.config/Signal/sql/db.sqlite)"),
            ("SIGNAL_CONFIG_PATH", "Signal config path (optional)", False,
             "Leave blank for auto-detect (~/.config/Signal/config.json)"),
        ],
    },
    "imessage": {
        "name": "iMessage (macOS only)",
        "short": "iMessage",
        "icon": "I",
        "blurb": "Reads Messages on this Mac, texts included. Nothing is sent anywhere.",
        "vars": [
            ("IMESSAGE_DB_PATH", "Messages DB path", False,
             "Press Enter to use the default macOS location.",
             "~/Library/Messages/chat.db"),
        ],
    },
    "whatsapp": {
        "name": "WhatsApp (from WhatsApp for Mac)",
        "short": "WhatsApp",
        "icon": "W",
        "blurb": "Reads the chats WhatsApp for Mac keeps on this Mac. Nothing is sent anywhere.",
        "vars": [
            ("WHATSAPP_DB_PATH", "WhatsApp database path", False,
             "Press Enter to use WhatsApp for Mac's. An iPhone backup's ChatStorage.sqlite works too.",
             "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"),
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
    "sms": {"name": "Android SMS backup", "short": "Android SMS", "icon": "S",
            "what": "A backup .xml", "accept": ".xml",
            "hint": "An XML file from SMS Backup & Restore"},
    "contacts-vcf": {"name": "Contacts (vCard)", "short": "Contacts", "icon": "C",
                     "what": "A vCard .vcf, so names replace numbers", "accept": ".vcf",
                     "hint": "Contacts → File → Export → Export vCard"},
}

# What the dashboard offers. The rest stay CLI-only until each is tested there.
DASHBOARD_CONNECTORS: tuple[str, ...] = ("imessage", "whatsapp")
DASHBOARD_UPLOADS: tuple[str, ...] = ()


# What a stored message's source prefix ("imessage:+1…") is called on screen.
KIND_NAMES: dict[str, str] = {
    "imessage": "iMessage", "sms": "SMS", "email": "Gmail", "gmail": "Gmail",
    "whatsapp": "WhatsApp", "discord": "Discord", "slack": "Slack", "telegram": "Telegram",
    "matrix": "Matrix", "mastodon": "Mastodon", "signal": "Signal", "messenger": "Messenger",
    "instagram": "Instagram", "manual": "Notes",
}


def kind_name(kind: str) -> str:
    return KIND_NAMES.get(kind, kind.capitalize())
