"""Sources' secrets, kept in the (encrypted) database rather than in config.env.

Tokens and passwords (the catalog's rows marked secret, and OPENAI_API_KEY) live in the
database's ``secrets`` table, so they're as safe as the messages they unlock. config.env
keeps settings: ports, paths, flags. A secret found in config.env, put there by hand or
by a memoreei from before, is moved in at the next start and removed from the file.

These are plain synchronous calls on a connection of their own: they run at startup,
before the server's connection exists, and when the dashboard saves a form.
"""
from __future__ import annotations

import contextlib
import os
import re
from pathlib import Path

from dotenv import dotenv_values

from memoreei.catalog import CONNECTORS
from memoreei.storage import key

SECRETS_TABLE = """
CREATE TABLE IF NOT EXISTS secrets (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (unixepoch())
)
"""

SECRET_VARS: frozenset[str] = frozenset(
    {name for c in CONNECTORS.values() for name, _label, secret, *_ in c["vars"] if secret}
    | {"OPENAI_API_KEY", "GMAIL_PASSWORD"}  # the embedding key; Gmail's older name
)


def _connect(db_path: str) -> object:
    conn = key.connect(db_path, timeout=10)
    conn.execute(SECRETS_TABLE)
    return conn


def read_secrets(db_path: str) -> dict[str, str]:
    """Every stored secret. Nothing (and no database made) if there's no database yet."""
    if key.db_state(db_path) == "missing":
        return {}
    conn = _connect(db_path)
    try:
        return dict(conn.execute("SELECT name, value FROM secrets").fetchall())
    finally:
        conn.close()


def write_secrets(db_path: str, updates: dict[str, str]) -> None:
    """Store secrets; an empty value removes one."""
    if not updates:
        return
    Path(db_path).parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    conn = _connect(db_path)
    try:
        with conn:
            for name, value in updates.items():
                if value:
                    conn.execute(
                        "INSERT INTO secrets (name, value) VALUES (?, ?) ON CONFLICT(name) "
                        "DO UPDATE SET value = excluded.value, updated_at = unixepoch()",
                        (name, value))
                else:
                    conn.execute("DELETE FROM secrets WHERE name = ?", (name,))
    finally:
        conn.close()


def move_out_of_env(db_path: str, env_path: Path) -> list[str]:
    """Move secrets from config.env into the database, and take them out of the file.

    The database is written first, so a crash between the two leaves a secret in both
    places rather than in neither. Commented-out lines are left alone. Returns the names
    moved (an empty line, ``NAME=``, is removed without moving anything).
    """
    if not env_path.is_file():
        return []
    text = env_path.read_text()
    lines = text.splitlines()
    pattern = re.compile(r"\s*(?:export\s+)?(" + "|".join(sorted(SECRET_VARS)) + r")\s*=")
    if not any(pattern.match(line) for line in lines):
        return []
    values = {k: v for k, v in dotenv_values(env_path).items() if k in SECRET_VARS and v}
    write_secrets(db_path, values)
    kept = [line for line in lines if not pattern.match(line)]
    tmp = env_path.with_name(env_path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(kept) + "\n" if kept else "")
    try:
        os.replace(tmp, env_path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
    return sorted(values)
