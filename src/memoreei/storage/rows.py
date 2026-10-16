"""The few helpers a store of rows needs, over the server's one connection to memoreei.db.

OAuthStore (memoreei.oauth.store) and AccountStore (memoreei.admin.account) keep their
tables this way. Every write commits, and returns its rowcount, so a change that must
happen once is a single UPDATE with the condition in its WHERE.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memoreei.storage.database import Database


class Rows:
    def __init__(self, db: "Database") -> None:
        self.db = db

    @property
    def conn(self) -> Any:
        assert self.db._db is not None
        return self.db._db

    async def _one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        async with self.conn.execute(sql, args) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def _all(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        async with self.conn.execute(sql, args) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def _write(self, sql: str, args: tuple = ()) -> int:
        cursor = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cursor.rowcount
