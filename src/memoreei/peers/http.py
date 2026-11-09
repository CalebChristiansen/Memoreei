"""The receiver's endpoints, on the LAN listener and the public one alike.

  POST /peer/pair/<token>/claim     a source asks to pair: {memoreei_id, name} → the code
  POST /peer/pair/<token>/confirm   its person approved: {claim} → the push key
  POST /peer/push                   Bearer mpk_…: messages, edits and deletes → counts
  POST /peer/end                    Bearer mpk_…: the source ends the pairing

Nothing here reads messages back out. A push key opens /peer/push and /peer/end and no
other door: the MCP endpoints and the dashboard never accept it.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

from memoreei.auth import _bearer_token
from memoreei.peers import store
from memoreei.peers.receive import BadPush, apply_push

log = logging.getLogger("memoreei.peers")

MAX_PUSH_BODY = 4 << 20
MAX_PAIR_BODY = 4 << 10
_PAIR = re.compile(r"^/peer/pair/([A-Za-z0-9_-]{16,64})/(claim|confirm)$")

GetTools = Callable[[], Awaitable[Any]]


def is_peer_path(path: str) -> bool:
    return path == "/peer" or path.startswith("/peer/")


async def _body(receive: Callable, limit: int) -> bytes | None:
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    return b"".join(chunks)


async def _reply(send: Callable, status: int, obj: dict[str, Any]) -> None:
    body = json.dumps(obj).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


class PeerApp:
    """ASGI app for /peer/*. *status* afterwards is what was answered, for callers that
    count failures (the public gate)."""

    def __init__(self, get_tools: GetTools, on_push: Callable[[], None] | None = None) -> None:
        self.get_tools = get_tools
        self.on_push = on_push

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        path, method = scope.get("path", ""), scope.get("method", "")
        if method != "POST":
            return await _reply(send, 405 if is_peer_path(path) else 404, {"error": "method_not_allowed"})
        m = _PAIR.match(path)
        if m:
            return await self._pair(m.group(1), m.group(2), receive, send)
        if path in ("/peer/push", "/peer/end"):
            return await self._keyed(path, scope, receive, send)
        return await _reply(send, 404, {"error": "not_found"})

    async def _pair(self, token: str, step: str, receive: Callable, send: Callable) -> None:
        raw = await _body(receive, MAX_PAIR_BODY)
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return await _reply(send, 400, {"error": "invalid_request", "error_description": "send a JSON object"})
        db = (await self.get_tools()).db
        try:
            if step == "claim":
                out = await store.claim_invite(db, token, str(body.get("memoreei_id") or ""),
                                               str(body.get("name") or ""))
                log.info("pairing: %s asked to send here (code %s)", body.get("name"), out["code"])
            else:
                out = await store.confirm_invite(db, token, str(body.get("claim") or ""))
                log.info("pairing: a memoreei now sends here (pairing %s)", out["link"])
        except store.PeerError as e:
            return await _reply(send, e.status, {"error": "refused", "error_description": str(e)})
        await _reply(send, 200, out)

    async def _keyed(self, path: str, scope: dict, receive: Callable, send: Callable) -> None:
        tools = await self.get_tools()
        token = _bearer_token(scope.get("headers", []))
        link = await store.link_for_key(tools.db, token) if token else None
        if link is None:
            await _body(receive, MAX_PUSH_BODY)
            return await _reply(send, 401, {"error": "invalid_token",
                                            "error_description": "this memoreei doesn't take pushes with that key"})
        if path == "/peer/end":
            await store.end_link(tools.db, link["id"], f"{link['name']} ended it")
            log.info("pairing: %s ended it", link["name"])
            return await _reply(send, 200, {"ended": True})
        raw = await _body(receive, MAX_PUSH_BODY)
        if raw is None:
            return await _reply(send, 413, {"error": "too_large", "error_description": "send smaller pages"})
        try:
            counts = await apply_push(tools.db, tools.embedder, link, json.loads(raw))
        except (ValueError, BadPush) as e:
            return await _reply(send, 400, {"error": "invalid_request", "error_description": str(e)[:300]})
        if counts["added"] or counts["deleted"] or counts["updated"]:
            log.info("push from %s: %s", link["name"], counts)
            if self.on_push is not None:
                self.on_push()  # pass it on, for chains
        await _reply(send, 200, counts)
