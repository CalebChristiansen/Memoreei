"""Another memoreei as a source (memoreei.peers): pairing, pushes, origins, deletes."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import httpx
import pytest
from ulid import ULID

from memoreei.peers import client, push, store
from memoreei.peers.http import PeerApp
from memoreei.peers.receive import apply_push
from memoreei.storage.database import Database
from memoreei.storage.models import MemoryItem


class Embedder:
    MODEL_NAME = "test-model"
    dimension = 4
    calls = 0

    async def embed(self, texts):
        Embedder.calls += len(texts)
        return [[float(len(t)), 1.0, 0.0, 0.0] for t in texts]

    async def embed_query(self, text):
        return [float(len(text)), 1.0, 0.0, 0.0]


class Other(Embedder):
    MODEL_NAME = "another-model"


class Net:
    """Memoreeis by base URL, each answering /peer/* in process."""

    def __init__(self) -> None:
        self.apps: dict[str, PeerApp] = {}
        self.bases: dict[int, str] = {}
        self.down: set[str] = set()

    def add(self, name: str, db: Database, embedder=None) -> str:
        base = f"http://{name}.lan"
        tools = SimpleNamespace(db=db, embedder=embedder or Embedder())

        async def get_tools():
            return tools

        self.apps[base] = PeerApp(get_tools)
        self.bases[id(db)] = base
        return base

    def handler(self):
        async def asgi(scope, receive, send):
            base = f"http://{dict(scope['headers'])[b'host'].decode()}"
            if base in self.down:
                raise httpx.ConnectError("asleep")
            await self.apps[base](scope, receive, send)
        return httpx.ASGITransport(app=asgi)


@pytest.fixture
def net(monkeypatch):
    n = Net()
    real = httpx.AsyncClient

    def make(*args, **kwargs):
        kwargs.setdefault("transport", n.handler())
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", make)
    return n


@pytest.fixture
async def memoreeis(tmp_path, monkeypatch):
    opened: list[Database] = []

    async def make(name: str) -> Database:
        db = Database(db_path=str(tmp_path / name / "memoreei.db"))
        await db.connect()
        db.name = name
        opened.append(db)
        return db

    yield make
    for db in opened:
        await db.close()


def msg(source: str, n: int, text: str | None = None) -> MemoryItem:
    return MemoryItem(id=str(ULID()), source=source, source_id=f"{source}:{n}", content=text or f"message {n}",
                      summary=None, participants=["Ada"], ts=1_700_000_000 + n, ingested_at=int(time.time()),
                      metadata={"sender": "Ada"}, embedding=[1.0, 2.0, 3.0, 4.0])


async def pair(net: Net, source: Database, receiver: Database, monkeypatch) -> str:
    """Pair *source* to push to *receiver*, the way the CLI does."""
    base = net.bases[id(receiver)]
    token, code = await store.create_invite(receiver)
    monkeypatch.setenv("MEMOREEI_NAME", source.name)
    claimed = await client.claim(source, f"{base}/peer/pair/{token}")
    assert claimed["code"] == code
    return await client.confirm(source, claimed)


async def push_all(*dbs: Database) -> None:
    """Push rounds until nothing moves, as the background pushers would."""
    for _ in range(6):
        for db in dbs:
            await push.push_all(db, Embedder(), force=True)


async def contents(db: Database) -> list[str]:
    async with db._db.execute("SELECT content FROM memories ORDER BY content") as c:
        return [r[0] for r in await c.fetchall()]


# ── origins ─────────────────────────────────────────────────────────────────


async def test_every_message_is_stamped_with_this_memoreeis_origin(memoreeis):
    a = await memoreeis("a")
    m = msg("signal:x", 1)
    await a.insert_memory(m)
    got = await a.get_by_id(m.id)
    assert (got.origin, got.origin_source, got.origin_id, got.via) == (a.memoreei_id, "signal:x", m.id, None)
    assert len(a.memoreei_id) == 16


async def test_the_migration_stamps_existing_rows_and_keeps_the_id(tmp_path):
    db = Database(db_path=str(tmp_path / "old.db"))
    await db.connect()
    await db.insert_memory(msg("imessage:a", 1))
    await db._db.execute("UPDATE memories SET origin = NULL, origin_source = NULL, origin_id = NULL")
    await db._db.execute("DELETE FROM memoreei_meta WHERE key = 'origins_stamped'")  # as before the migration
    await db._db.commit()
    first = db.memoreei_id
    await db.close()
    db = Database(db_path=str(tmp_path / "old.db"))
    await db.connect()
    async with db._db.execute("SELECT origin, origin_source, origin_id = id FROM memories") as c:
        assert tuple(await c.fetchone()) == (first, "imessage:a", 1)
    assert db.memoreei_id == first
    await db.close()


# ── pairing ─────────────────────────────────────────────────────────────────


async def test_pairing_shows_the_same_code_and_gives_a_push_key(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("a", a)
    base = net.add("b", b)
    token, code = await store.create_invite(b)
    monkeypatch.setenv("MEMOREEI_NAME", "laptop")
    claimed = await client.claim(a, f"{base}/peer/pair/{token}")
    assert claimed["code"] == code
    link_id = await client.confirm(a, claimed)
    (to,) = await store.links(a, "to")
    assert to["id"] == link_id and to["push_key"].startswith("mpk_") and to["peer_id"] == b.memoreei_id
    (frm,) = await store.links(b, "from")
    assert frm["name"] == "laptop" and frm["peer_id"] == a.memoreei_id and frm["push_key"] is None


async def test_an_invitation_is_for_one_memoreei_once(net, memoreeis, monkeypatch):
    a, b, c = await memoreeis("a"), await memoreeis("b"), await memoreeis("c")
    base = net.add("b", b)
    token, _ = await store.create_invite(b)
    claimed = await client.claim(a, f"{base}/peer/pair/{token}")
    with pytest.raises(client.PairingError, match="already asked"):
        await client.claim(c, f"{base}/peer/pair/{token}")
    with pytest.raises(client.PairingError, match="didn't claim"):
        await client.confirm(c, {**claimed, "claim": "x" * 32})
    await client.confirm(a, claimed)
    with pytest.raises(client.PairingError, match="used"):
        await client.claim(a, f"{base}/peer/pair/{token}")
    with pytest.raises(client.PairingError, match="no such"):
        await client.claim(a, f"{base}/peer/pair/{'z' * 32}")


async def test_an_expired_invitation_is_refused(net, memoreeis):
    a, b = await memoreeis("a"), await memoreeis("b")
    base = net.add("b", b)
    token, _ = await store.create_invite(b, now=int(time.time()) - store.INVITE_TTL - 1)
    with pytest.raises(client.PairingError, match="expired"):
        await client.claim(a, f"{base}/peer/pair/{token}")


def test_a_pairing_address_is_checked():
    assert client.parse_address("https://x.dev.memoreei.net/peer/pair/" + "a" * 32) == (
        "https://x.dev.memoreei.net", "a" * 32)
    assert client.parse_address("http://192.168.1.20:3679/peer/pair/" + "a" * 32)[0] == "http://192.168.1.20:3679"
    with pytest.raises(client.PairingError, match="https"):
        client.parse_address("http://memoreei.example.com/peer/pair/" + "a" * 32)
    with pytest.raises(client.PairingError, match="isn't a pairing address"):
        client.parse_address("https://x.dev.memoreei.net/mcp")


# ── pushing ─────────────────────────────────────────────────────────────────


async def test_a_push_copies_messages_with_their_embeddings_once(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("a", a)
    net.add("b", b)
    for n in range(5):
        await a.insert_memory(msg("signal:x", n))
    link_id = await pair(net, a, b, monkeypatch)
    Embedder.calls = 0
    await push_all(a)
    await push_all(a)
    assert await contents(b) == [f"message {n}" for n in range(5)]
    assert Embedder.calls == 0  # same model: the source's embeddings were used
    (frm,) = await store.links(b, "from")
    async with b._db.execute("SELECT origin, via, embedding FROM memories") as c:
        rows = await c.fetchall()
    assert {r[0] for r in rows} == {a.memoreei_id} and {r[1] for r in rows} == {frm["id"]}
    assert all(r[2] for r in rows)
    (to,) = await store.links(a, "to")
    assert to["id"] == link_id and to["sent"] == 5 and to["last_ok_at"]


async def test_the_receiver_re_embeds_with_another_model(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("b", b, Other())
    await a.insert_memory(msg("signal:x", 1, "hello"))
    await pair(net, a, b, monkeypatch)
    Embedder.calls = 0
    await push_all(a)
    assert Embedder.calls == 1
    (m,) = [x async for x in _all(b)]
    assert m.embedding == [5.0, 1.0, 0.0, 0.0]


async def _all(db):
    async with db._db.execute("SELECT * FROM memories") as c:
        for r in await c.fetchall():
            yield MemoryItem.from_row(dict(r))


async def test_a_and_b_both_ways_copy_each_message_once(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("a", a)
    net.add("b", b)
    await a.insert_memory(msg("signal:a", 1, "from a"))
    await b.insert_memory(msg("imessage:b", 1, "from b"))
    await pair(net, a, b, monkeypatch)
    await pair(net, b, a, monkeypatch)
    await push_all(a, b)
    assert await contents(a) == await contents(b) == ["from a", "from b"]
    # Nothing came back to where it started
    async with a._db.execute("SELECT count(*) FROM memories WHERE origin = ? AND via IS NOT NULL",
                             (a.memoreei_id,)) as c:
        assert (await c.fetchone())[0] == 0


async def test_a_ring_copies_each_message_once(net, memoreeis, monkeypatch):
    a, b, c = await memoreeis("a"), await memoreeis("b"), await memoreeis("c")
    for name, db in (("a", a), ("b", b), ("c", c)):
        net.add(name, db)
        await db.insert_memory(msg(f"signal:{name}", 1, f"from {name}"))
    await pair(net, a, b, monkeypatch)
    await pair(net, b, c, monkeypatch)
    await pair(net, c, a, monkeypatch)
    await push_all(a, b, c)
    for db in (a, b, c):
        assert await contents(db) == ["from a", "from b", "from c"]


async def test_a_message_reaching_c_by_two_paths_is_held_once(net, memoreeis, monkeypatch):
    a, b, d, c = [await memoreeis(n) for n in "abdc"]
    for name, db in (("a", a), ("b", b), ("d", d), ("c", c)):
        net.add(name, db)
    await a.insert_memory(msg("signal:a", 1, "from a"))
    for src, dst in ((a, b), (a, d), (b, c), (d, c)):
        await pair(net, src, dst, monkeypatch)
    await push_all(a, b, d, c)
    assert await contents(c) == ["from a"]


async def test_a_message_the_receiver_read_itself_is_held_once(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("b", b)
    same = msg("signal:x", 7, "same message")
    await a.insert_memory(same)
    await b.insert_memory(msg("signal:x", 7, "same message"))
    await pair(net, a, b, monkeypatch)
    await push_all(a)
    assert await contents(b) == ["same message"]


async def test_a_push_is_idempotent(memoreeis):
    b = await memoreeis("b")
    link = {"id": "L"}
    payload = {"from": "a" * 16, "model": "test-model", "upserts": [{
        "origin": "a" * 16, "origin_source": "signal:x", "origin_id": "1", "source": "signal:x",
        "source_id": "signal:x:1", "content": "hi", "summary": None, "participants": [], "ts": 1,
        "metadata": {}, "embedding": None}], "deletes": []}
    first = await apply_push(b, Embedder(), link, payload)
    second = await apply_push(b, Embedder(), link, payload)
    assert first["added"] == 1 and second == {"added": 0, "updated": 0, "held": 1, "skipped": 0, "deleted": 0}


async def test_a_receiver_never_takes_back_or_deletes_its_own(memoreeis):
    b = await memoreeis("b")
    mine = msg("signal:x", 1, "mine")
    await b.insert_memory(mine)
    out = await apply_push(b, Embedder(), {"id": "L"}, {
        "upserts": [{"origin": b.memoreei_id, "origin_source": "signal:x", "origin_id": "new", "source": "signal:x",
                     "content": "spoofed", "ts": 1}],
        "deletes": [{"origin": b.memoreei_id, "origin_id": mine.id}]})
    assert out["skipped"] == 2
    assert await contents(b) == ["mine"]


async def test_an_asleep_receiver_is_caught_up_later_with_backoff(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    base = net.add("b", b)
    await pair(net, a, b, monkeypatch)
    await a.insert_memory(msg("signal:x", 1))
    net.down.add(base)
    out = await push.push_all(a, Embedder())
    (to,) = await store.links(a, "to")
    assert "error" in next(iter(out.values()))
    assert to["failures"] == 1 and to["next_try_at"] > time.time() and to["last_error"]
    assert await push.push_all(a, Embedder()) == {}  # backing off
    net.down.clear()
    await push.push_all(a, Embedder(), force=True)
    assert await contents(b) == ["message 1"]
    (to,) = await store.links(a, "to")
    assert to["failures"] == 0 and to["last_error"] is None


async def test_edits_travel_down(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("b", b)
    m = msg("signal:x", 1, "before")
    await a.insert_memory(m)
    await pair(net, a, b, monkeypatch)
    await push_all(a)
    m.content, m.metadata = "after", {"sender": "Ada", "deleted": True}
    await a.update_memory(m, reembedded=False)
    await push_all(a)
    (copy,) = [x async for x in _all(b)]
    assert copy.content == "after" and copy.metadata["deleted"] is True


# ── what goes ───────────────────────────────────────────────────────────────


async def test_unticking_a_source_deletes_it_there_and_ticking_sends_it_again(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("b", b)
    await a.insert_memory(msg("signal:x", 1, "signal"))
    await a.insert_memory(msg("slack:work", 1, "work"))
    link_id = await pair(net, a, b, monkeypatch)
    await push_all(a)
    assert await contents(b) == ["signal", "work"]
    await push.set_excluded(a, link_id, ["slack"])
    await a.insert_memory(msg("slack:work", 2, "more work"))
    await a.insert_memory(msg("whatsapp:new", 1, "a new source"))
    await push_all(a)
    assert await contents(b) == ["a new source", "signal"]
    await push.set_excluded(a, link_id, [])
    await push_all(a)
    assert await contents(b) == ["a new source", "more work", "signal", "work"]


async def test_copies_from_another_memoreei_are_a_tick_box_of_their_own(net, memoreeis, monkeypatch):
    a, b, c = await memoreeis("a"), await memoreeis("b"), await memoreeis("c")
    net.add("b", b)
    net.add("c", c)
    await a.insert_memory(msg("signal:a", 1, "from a"))
    await b.insert_memory(msg("signal:b", 1, "from b"))
    await pair(net, a, b, monkeypatch)
    to_c = await pair(net, b, c, monkeypatch)
    (from_a,) = await store.links(b, "from")
    await push.set_excluded(b, to_c, [f"via:{from_a['id']}"])
    await push_all(a, b)
    assert await contents(c) == ["from b"]


# ── deletes ─────────────────────────────────────────────────────────────────


async def test_deletes_travel_down_a_chain(net, memoreeis, monkeypatch):
    a, b, c = await memoreeis("a"), await memoreeis("b"), await memoreeis("c")
    net.add("b", b)
    net.add("c", c)
    net.add("a", a)
    for n in range(3):
        await a.insert_memory(msg("signal:x", n))
    await a.insert_memory(msg("slack:y", 1, "slack"))
    await pair(net, a, b, monkeypatch)
    await pair(net, b, c, monkeypatch)
    await pair(net, c, a, monkeypatch)  # and round again, which must end
    await push_all(a, b, c)
    assert len(await contents(c)) == 4
    async with a._db.execute("SELECT id FROM memories WHERE source_id = 'signal:x:1'") as cur:
        (gone,) = await cur.fetchone()
    await a._db.execute("DELETE FROM memories WHERE id = ?", (gone,))
    await a._db.commit()
    await a.delete_by_source("slack:y")
    await push_all(a, b, c)
    for db in (a, b, c):
        assert await contents(db) == ["message 0", "message 2"]
    for db in (a, b, c):
        async with db._db.execute("SELECT count(*) FROM memory_changes") as cur:
            assert (await cur.fetchone())[0] == 0  # trimmed once everyone had them


async def test_removing_a_source_on_the_receiver_deletes_its_copies(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("b", b)
    await a.insert_memory(msg("signal:x", 1))
    await b.insert_memory(msg("signal:b", 1, "b's own"))
    await pair(net, a, b, monkeypatch)
    await push_all(a)
    (frm,) = await store.links(b, "from")
    assert await store.remove_source(b, frm["id"]) == 1
    assert await contents(b) == ["b's own"]
    await a.insert_memory(msg("signal:x", 2))
    await push_all(a)  # the key is refused at the next push, and the source's side ends too
    (to,) = await store.links(a, "to")
    assert not to["active"] and "no longer takes" in to["ended_why"]


# ── ending and keys ─────────────────────────────────────────────────────────


async def test_a_revoked_pairing_stops_pushes(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("b", b)
    await pair(net, a, b, monkeypatch)
    (to,) = await store.links(a, "to")
    key = to["push_key"]
    await client.end(a, to, "stopped here")
    (frm,) = await store.links(b, "from")
    assert not frm["active"] and frm["ended_why"] == "a ended it"
    (to,) = await store.links(a, "to")
    assert not to["active"] and to["push_key"] is None
    await a.insert_memory(msg("signal:x", 1))
    await push_all(a)
    assert await contents(b) == []
    async with httpx.AsyncClient() as h:
        r = await h.post("http://b.lan/peer/push", json={"upserts": []}, headers={"authorization": f"Bearer {key}"})
    assert r.status_code == 401


async def test_pairing_again_after_ending_carries_on(net, memoreeis, monkeypatch):
    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("b", b)
    await a.insert_memory(msg("signal:x", 1))
    await pair(net, a, b, monkeypatch)
    await push_all(a)
    (to,) = await store.links(a, "to")
    await client.end(a, to, "stopped")
    await pair(net, a, b, monkeypatch)
    await a.insert_memory(msg("signal:x", 2))
    await push_all(a)
    assert await contents(b) == ["message 1", "message 2"]
    assert len(await store.links(b, "from")) == 1 and len(await store.links(a, "to")) == 1


async def test_a_push_key_reads_nothing(net, memoreeis, monkeypatch):
    """The push endpoint answers counts only, and the key opens no other door."""
    from memoreei.auth import BearerAuthMiddleware, KeyStore
    from memoreei.public.gate import PublicGate

    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("b", b)
    await b.insert_memory(msg("signal:b", 1, "secret"))
    await pair(net, a, b, monkeypatch)
    (to,) = await store.links(a, "to")
    auth = {"authorization": f"Bearer {to['push_key']}"}
    async with httpx.AsyncClient() as h:
        r = await h.post("http://b.lan/peer/push", json={"upserts": [], "deletes": []}, headers=auth)
        assert r.status_code == 200 and "secret" not in r.text
        assert set(r.json()) == {"added", "updated", "held", "skipped", "deleted"}
        for path in ("/peer/pull", "/peer/messages", "/peer/push/../mcp"):
            assert (await h.post("http://b.lan" + path, headers=auth)).status_code == 404
        assert (await h.get("http://b.lan/peer/push", headers=auth)).status_code == 405

    async def mcp(scope, receive, send):  # stands in for the MCP app: reached means let in
        raise AssertionError("a push key reached MCP")

    keys = KeyStore(b)
    lan = BearerAuthMiddleware(mcp, keys.verify)
    gate = PublicGate(mcp, resource="https://b.example/mcp", keys=keys.verify, peer=net.apps["http://b.lan"])
    for app in (lan, gate):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://b") as h:
            r = await h.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=auth)
            assert r.status_code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://b") as h:
        r = await h.post("/peer/push", json={"upserts": [], "deletes": []}, headers=auth)
        assert r.status_code == 200  # the gate passes pushes through


async def test_the_gate_counts_refused_pairing_as_failures(net, memoreeis):
    from memoreei.public.gate import FailureLimiter, PublicGate

    b = await memoreeis("b")
    net.add("b", b)

    async def mcp(scope, receive, send):
        raise AssertionError

    limiter = FailureLimiter(per_address=(3, 60))
    gate = PublicGate(mcp, resource="https://b.example/mcp", keys=lambda k: None, limiter=limiter,
                      peer=net.apps["http://b.lan"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gate), base_url="http://b") as h:
        codes = [(await h.post(f"/peer/pair/{'q' * 32}/claim", json={"memoreei_id": "x", "name": "y"})).status_code
                 for _ in range(4)]
    assert codes == [404, 404, 404, 429]


async def test_results_say_which_memoreei_a_message_came_through(net, memoreeis, monkeypatch):
    from memoreei.tools.memory_tools import MemoryTools

    a, b = await memoreeis("a"), await memoreeis("b")
    net.add("b", b)
    await a.insert_memory(msg("signal:x", 1, "pizza on friday"))
    await b.insert_memory(msg("signal:y", 1, "pizza on saturday"))
    await pair(net, a, b, monkeypatch)
    await push_all(a)
    tools = MemoryTools(db=b, embedder=Embedder())
    out = await tools.search_memoreei("pizza")
    via = {r["content"]: r.get("via") for r in out["results"]}
    assert via == {"pizza on friday": "a", "pizza on saturday": None}
    sources = await tools.list_sources_tool() if hasattr(tools, "list_sources_tool") else None
    if sources is not None:
        assert any(m["name"] == "a" for m in sources.get("memoreeis", []))


# ── the CLI ─────────────────────────────────────────────────────────────────


def test_the_cli_invites_and_refuses_a_wrong_code(net, monkeypatch, tmp_path):
    import asyncio

    from typer.testing import CliRunner

    from memoreei.cli import app

    runner = CliRunner()
    r = runner.invoke(app, ["sources", "list"])
    assert r.exit_code == 0 and "No other memoreeis" in r.output
    r = runner.invoke(app, ["sources", "invite", "--no-wait"])
    assert r.exit_code == 0
    address = r.output.split("memoreei sources add-to ")[-1].split()[0]
    assert "/peer/pair/" in address and "It will show the code" in r.output

    # The other memoreei, answering for the address the invitation gave
    other = Database(db_path=str(tmp_path / "other" / "memoreei.db"))
    asyncio.run(other.connect())
    try:
        base = net.add("other", other)
        token, code = asyncio.run(store.create_invite(other))
        r = runner.invoke(app, ["sources", "add-to", f"{base}/peer/pair/{token}", "--code", "ZZZZ"])
        assert r.exit_code == 1 and "isn't the code" in r.output
        r = runner.invoke(app, ["sources", "add-to", "https://nowhere.example/mcp"])
        assert r.exit_code == 1 and "isn't a pairing address" in r.output
    finally:
        asyncio.run(other.close())
