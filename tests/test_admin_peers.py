"""The Sources page's other memoreeis, and /peer/* on the real HTTP app."""
from __future__ import annotations

import asyncio
import re

import pytest

from memoreei.peers import client as peer_client
from memoreei.peers import store
from memoreei.storage.database import Database
from tests.test_admin import ORIGIN, server, signed_in  # noqa: F401 - the fixture

OTHER = "otherpeer2345678"


def _db(server, fn):
    async def run():
        async with Database(db_path=server.db_path) as db:
            return await fn(db)
    return asyncio.run(run())


def test_the_sources_page_lists_other_memoreeis(server):
    r = signed_in(server).get("/admin/sources")
    assert r.status_code == 200
    assert "Other memoreeis" in r.text and "None yet." in r.text
    assert "Add another memoreei" in r.text and "Send to another memoreei" in r.text


def test_add_another_memoreei_from_the_dashboard(server):
    c = signed_in(server)
    r = c.post("/admin/memoreeis/invite", headers=ORIGIN)
    assert r.status_code == 200
    code = re.search(r'<b class="mono">(\w{4})</b>', r.text).group(1)
    addresses = re.findall(r"memoreei sources add-to (\S+/peer/pair/[\w-]+)", r.text)
    assert addresses[-1].startswith("http://localhost:3679/peer/pair/")  # this computer, last
    address = addresses[-1]
    watch = re.search(r'/admin/memoreeis/invite/(\w+)"', r.text).group(1)
    assert "Waiting for it to ask" in c.get(f"/admin/memoreeis/invite/{watch}").text

    path = address.removeprefix("http://localhost:3679")
    claimed = c.post(path + "/claim", json={"memoreei_id": OTHER, "name": "laptop"})
    assert claimed.status_code == 200 and claimed.json()["code"] == code
    assert "laptop asked" in c.get(f"/admin/memoreeis/invite/{watch}").text
    done = c.post(path + "/confirm", json={"claim": claimed.json()["claim"]})
    key = done.json()["push_key"]
    assert "now sends its messages here" in c.get(f"/admin/memoreeis/invite/{watch}").text

    # The key pushes, and opens nothing else on this listener
    push = c.post("/peer/push", headers={"authorization": f"Bearer {key}"}, json={"upserts": [{
        "origin": OTHER, "origin_source": "signal:x", "origin_id": "1", "source": "signal:x",
        "source_id": "signal:x:1", "content": "hello from the laptop", "ts": 1_700_000_000}]})
    assert push.status_code == 200 and push.json()["added"] == 1
    mcp = c.post("/mcp", headers={"authorization": f"Bearer {key}", "accept": "application/json, text/event-stream"},
                 json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert mcp.status_code == 401
    page = c.get("/admin/sources").text
    assert "From laptop" in page and "1 messages" in page

    link_id = _db(server, lambda db: store.links(db, "from"))[0]["id"]
    r = c.post(f"/admin/memoreeis/{link_id}/end", headers=ORIGIN)
    assert "None yet." in r.text and "removed here" in r.text
    assert c.post("/peer/push", headers={"authorization": f"Bearer {key}"}, json={}).status_code == 401


def test_send_to_another_memoreei_from_the_dashboard(server, monkeypatch):
    async def claim(db, address):
        return {"address": address, "base": "https://other.example", "token": "t" * 32, "code": "BCDF",
                "claim": "c", "name": "the server", "memoreei_id": OTHER}

    async def confirm(db, claimed):
        return await store.add_receiver(db, OTHER, claimed["name"], claimed["base"], "mpk_x")

    monkeypatch.setattr(peer_client, "claim", claim)
    monkeypatch.setattr(peer_client, "confirm", confirm)
    c = signed_in(server)
    r = c.post("/admin/memoreeis/add", headers=ORIGIN, data={"address": "https://other.example/peer/pair/" + "t" * 32})
    assert "the server shows the code" in r.text and "BCDF" in r.text
    pid = re.search(r"/admin/memoreeis/add/([\w-]+)/approve", r.text).group(1)
    assert _db(server, lambda db: store.links(db)) == []  # nothing until it's approved
    r = c.post(f"/admin/memoreeis/add/{pid}/approve", headers=ORIGIN)
    assert "Sending to the server" in r.text
    assert c.post(f"/admin/memoreeis/add/{pid}/approve", headers=ORIGIN).text.count("gone") == 1  # once

    page = c.get("/admin/memoreeis").text
    assert "Sending to the server" in page and "What goes" in page
    (link,) = _db(server, lambda db: store.links(db, "to"))
    r = c.post(f"/admin/memoreeis/{link['id']}/what", headers=ORIGIN,
               data={"offered": ["imessage", "slack"], "send": ["imessage"]})
    assert r.status_code == 200
    (link,) = _db(server, lambda db: store.links(db, "to"))
    assert link["excluded"] == ["slack"]

    async def no_network(db, link, why):
        await store.end_link(db, link["id"], why)

    monkeypatch.setattr(peer_client, "end", no_network)
    r = c.post(f"/admin/memoreeis/{link['id']}/end", headers=ORIGIN)
    assert "ended here" in r.text


def test_a_cancelled_request_sends_nothing(server, monkeypatch):
    async def claim(db, address):
        return {"address": address, "base": "https://x", "token": "t" * 32, "code": "BCDF", "claim": "c",
                "name": "a stranger", "memoreei_id": OTHER}

    monkeypatch.setattr(peer_client, "claim", claim)
    c = signed_in(server)
    r = c.post("/admin/memoreeis/add", headers=ORIGIN, data={"address": "x"})
    pid = re.search(r"/admin/memoreeis/add/([\w-]+)/cancel", r.text).group(1)
    assert "Nothing was sent to a stranger" in c.post(f"/admin/memoreeis/add/{pid}/cancel", headers=ORIGIN).text
    assert _db(server, lambda db: store.links(db)) == []


@pytest.mark.parametrize("bad", ["not an address", "http://memoreei.example.com/peer/pair/" + "a" * 32])
def test_a_bad_address_says_why(server, bad):
    r = signed_in(server).post("/admin/memoreeis/add", headers=ORIGIN, data={"address": bad})
    assert 'class="bad"' in r.text
