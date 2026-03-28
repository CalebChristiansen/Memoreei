from __future__ import annotations

import os
import time
from typing import Any

import aiohttp

from ulid import ULID

from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem

SOURCE_PREFIX = "matrix"
FETCH_LIMIT = 100  # messages per /messages request


class MatrixConnector:
    """Fetch Matrix room messages using the Matrix Client-Server API.

    Uses the ``/rooms/{roomId}/messages`` endpoint to paginate through
    room history. A per-room ``prev_batch`` token is stored as a checkpoint
    so subsequent syncs only fetch new messages.

    Requires:
        MATRIX_HOMESERVER  - e.g. https://matrix.org
        MATRIX_ACCESS_TOKEN - user access token from login/registration
        MATRIX_ROOM_ID      - room ID to sync (e.g. !abc123:matrix.org)
    """

    def __init__(
        self, homeserver: str, access_token: str, db: Database, embedder: Any
    ) -> None:
        self.homeserver = homeserver.rstrip("/")
        self.access_token = access_token
        self.db = db
        self.embedder = embedder
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

    async def sync_room(self, room_id: str) -> dict[str, Any]:
        """Fetch new messages from ``room_id``, embed and store.

        Returns a dict with ``synced`` count and ``room_id``.
        """
        from_token = await self.db.get_matrix_checkpoint(room_id)

        events, next_token = await self._fetch_messages(room_id, from_token=from_token)
        if not events:
            return {"synced": 0, "room_id": room_id}

        items = [self._to_memory_item(event, room_id) for event in events]
        # Filter out events with empty content (e.g. redacted)
        items = [item for item in items if item.content.strip()]

        if items:
            texts = [item.content for item in items]
            embeddings = await self.embedder.embed(texts)
            for item, emb in zip(items, embeddings):
                item.embedding = emb
            await self.db.bulk_insert(items)

        if next_token:
            await self.db.set_matrix_checkpoint(room_id, next_token)

        return {"synced": len(items), "room_id": room_id}

    async def _fetch_messages(
        self, room_id: str, from_token: str | None = None
    ) -> tuple[list[dict], str | None]:
        """Call ``/rooms/{roomId}/messages`` and return (events, next_batch_token)."""
        import urllib.parse

        encoded_room = urllib.parse.quote(room_id, safe="")
        url = f"{self.homeserver}/_matrix/client/v3/rooms/{encoded_room}/messages"
        params: dict[str, Any] = {
            "dir": "b" if from_token is None else "f",
            "limit": FETCH_LIMIT,
        }
        if from_token:
            params["from"] = from_token

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=self._headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    events = [
                        e
                        for e in data.get("chunk", [])
                        if e.get("type") == "m.room.message"
                    ]
                    # When paginating backwards (initial fetch), reverse so oldest is first
                    if params["dir"] == "b":
                        events = list(reversed(events))
                    return events, data.get("end")
                elif resp.status == 401:
                    raise ValueError("Matrix access token is invalid or unauthorized")
                elif resp.status == 403:
                    raise ValueError(f"Access denied to room {room_id}")
                else:
                    text = await resp.text()
                    raise RuntimeError(f"Matrix API error {resp.status}: {text}")

    def _to_memory_item(self, event: dict, room_id: str) -> MemoryItem:
        sender = event.get("sender", "unknown")
        # Shorten @username:homeserver to just the localpart for readability
        display_name = sender.split(":")[0].lstrip("@") if ":" in sender else sender

        content = event.get("content", {})
        msg_type = content.get("msgtype", "")
        if msg_type == "m.text":
            text = content.get("body", "")
        elif msg_type == "m.image":
            text = f"[image: {content.get('body', 'image')}]"
        elif msg_type == "m.file":
            text = f"[file: {content.get('body', 'file')}]"
        elif msg_type == "m.audio":
            text = f"[audio: {content.get('body', 'audio')}]"
        elif msg_type == "m.video":
            text = f"[video: {content.get('body', 'video')}]"
        elif msg_type == "m.emote":
            text = f"* {display_name} {content.get('body', '')}"
        else:
            text = content.get("body", f"[{msg_type}]") if content else "[unsupported]"

        event_id = event.get("event_id", "")
        # Matrix timestamps are in milliseconds
        ts_ms = event.get("origin_server_ts", int(time.time() * 1000))
        ts = ts_ms // 1000

        source = f"{SOURCE_PREFIX}:{room_id}"
        source_id = f"{source}:{event_id}"

        return MemoryItem(
            id=str(ULID()),
            source=source,
            source_id=source_id,
            content=f"{display_name}: {text}",
            summary=None,
            participants=[display_name],
            ts=ts,
            ingested_at=int(time.time()),
            metadata={
                "room_id": room_id,
                "event_id": event_id,
                "sender": sender,
                "msg_type": msg_type,
            },
            embedding=None,
        )

    async def create_room(self, alias: str | None = None) -> str:
        """Create a new Matrix room and return its room_id."""
        url = f"{self.homeserver}/_matrix/client/v3/createRoom"
        body: dict[str, Any] = {"preset": "private_chat"}
        if alias:
            body["room_alias_name"] = alias

        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self._headers, json=body) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["room_id"]
                else:
                    text = await resp.text()
                    raise RuntimeError(f"Failed to create room: {resp.status}: {text}")

    async def send_message(self, room_id: str, text: str) -> str:
        """Send a text message to ``room_id`` and return the event_id."""
        import urllib.parse

        encoded_room = urllib.parse.quote(room_id, safe="")
        txn_id = str(ULID())
        url = (
            f"{self.homeserver}/_matrix/client/v3/rooms/"
            f"{encoded_room}/send/m.room.message/{txn_id}"
        )
        body = {"msgtype": "m.text", "body": text}

        async with aiohttp.ClientSession() as session:
            async with session.put(url, headers=self._headers, json=body) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("event_id", "")
                else:
                    text_resp = await resp.text()
                    raise RuntimeError(
                        f"Failed to send message: {resp.status}: {text_resp}"
                    )


async def sync_matrix(
    db: Database, embedder: Any, room_id: str | None = None
) -> dict:
    """Top-level sync function used by the MCP server tool."""
    homeserver = os.environ.get("MATRIX_HOMESERVER", "")
    access_token = os.environ.get("MATRIX_ACCESS_TOKEN", "")

    if not homeserver:
        return {"error": "MATRIX_HOMESERVER not set in environment", "synced": 0}
    if not access_token:
        return {"error": "MATRIX_ACCESS_TOKEN not set in environment", "synced": 0}

    target_room = room_id or os.environ.get("MATRIX_ROOM_ID")
    if not target_room:
        return {"error": "No room_id provided and MATRIX_ROOM_ID not set", "synced": 0}

    connector = MatrixConnector(
        homeserver=homeserver, access_token=access_token, db=db, embedder=embedder
    )
    try:
        result = await connector.sync_room(room_id=target_room)
        return result
    except Exception as e:
        return {"error": str(e), "synced": 0}
