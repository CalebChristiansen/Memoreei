"""The source's half of pairing: claim an invitation, show its code, confirm."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from memoreei.peers import store
from memoreei.storage.database import Database

_ADDRESS = re.compile(r"^(https?://[^/?#]+)(/.*)?/peer/pair/([A-Za-z0-9_-]{16,64})/?$")


class PairingError(Exception):
    pass


def parse_address(address: str) -> tuple[str, str]:
    """(the receiver's base URL, the invitation's token) from what "Add another memoreei" showed."""
    m = _ADDRESS.match(address.strip())
    if not m:
        raise PairingError("That isn't a pairing address. It looks like https://…/peer/pair/… and comes from "
                           "\"Add another memoreei\" (or memoreei sources invite) on the memoreei to send to.")
    base = m.group(1) + (m.group(2) or "")
    if urlsplit(base).scheme == "http" and not _private(urlsplit(base).hostname or ""):
        raise PairingError("Over the internet the address must be https.")
    return base, m.group(3)


def _private(host: str) -> bool:
    import ipaddress

    if host in ("localhost",) or host.endswith((".local", ".lan", ".home.arpa", ".ts.net")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


async def _post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=body)
    except httpx.HTTPError as e:
        raise PairingError(f"Couldn't reach the other memoreei: {e or type(e).__name__}") from e
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200:
        raise PairingError(data.get("error_description") or f"The other memoreei answered {r.status_code}.")
    return data


async def claim(db: Database, address: str) -> dict[str, Any]:
    """Ask the receiver. Returns what to show the person: its name and the code, plus what
    confirm() needs."""
    base, token = parse_address(address)
    out = await _post(f"{base}/peer/pair/{token}/claim", {"memoreei_id": db.memoreei_id, "name": store.own_name()})
    if not isinstance(out.get("code"), str) or not isinstance(out.get("claim"), str):
        raise PairingError("The other memoreei's answer made no sense.")
    return {"address": address, "base": base, "token": token, "code": out["code"], "claim": out["claim"],
            "name": str(out.get("name") or "another memoreei")[:64], "memoreei_id": str(out.get("memoreei_id") or "")}


async def confirm(db: Database, claimed: dict[str, Any]) -> str:
    """The person approved: take the push key and start sending. Returns the pairing's id."""
    out = await _post(f"{claimed['base']}/peer/pair/{claimed['token']}/confirm", {"claim": claimed["claim"]})
    key, peer_id = out.get("push_key"), out.get("memoreei_id") or claimed["memoreei_id"]
    if not isinstance(key, str) or not key.startswith("mpk_") or not peer_id:
        raise PairingError("The other memoreei's answer made no sense.")
    return await store.add_receiver(db, str(peer_id), claimed["name"], claimed["base"], key)


async def end(db: Database, link: dict[str, Any], why: str) -> None:
    """End a pairing from this side: tell the other memoreei if it can be reached, and forget it here."""
    if link["direction"] == "to" and link.get("push_key"):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.post(link["address"].rstrip("/") + "/peer/end",
                                  headers={"authorization": f"Bearer {link['push_key']}"})
        except httpx.HTTPError:
            pass  # unreachable: nothing more is sent anyway, and it shows when we last pushed
    await store.end_link(db, link["id"], why)
