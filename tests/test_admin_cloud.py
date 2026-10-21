"""The dashboard's Connections page: Memoreei Cloud, chatbots and keys in one place."""
from __future__ import annotations

import time

import pytest

import memoreei.config as config_module
from memoreei.cloud.state import CloudState
from tests.test_admin import ORIGIN, server, signed_in  # noqa: F401  (server is a fixture)
from tests.test_cloud import AUTH, HOST, FakeService


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv("MEMOREEI_CLOUD_URL", AUTH)
    monkeypatch.setattr(config_module, "_config", None)
    fake = FakeService()

    async def short_wait(seconds):
        import asyncio

        await asyncio.sleep(0.01)

    monkeypatch.setattr("memoreei.cloud.service._sleep", short_wait)
    monkeypatch.setattr("memoreei.cloud.connect.Service", fake.factory)
    return fake


@pytest.fixture
def nobody_called(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("contacted Memoreei Cloud without being asked")

    monkeypatch.setattr("memoreei.cloud.connect.Service", refuse)


def test_the_connections_page_offers_it_without_calling_anyone(server, nobody_called):
    client = signed_in(server)
    r = client.get("/admin/connections")
    assert r.status_code == 200 and "Connect to Memoreei Cloud" in r.text
    assert "keeps memoreei itself free and open source" in r.text
    assert 'href="https://docs.memoreei.com/self-host"' in r.text
    # The status page sells nothing once set up; the welcome steps point here instead.
    assert "Connect to Memoreei Cloud" not in client.get("/admin/").text


@pytest.mark.parametrize("setting", [("MEMOREEI_CLOUD_OFFER", "0"), ("MEMOREEI_PUBLIC_PORT", "0")])
def test_the_offer_can_be_hidden(server, monkeypatch, setting):
    monkeypatch.setenv(*setting)
    monkeypatch.setattr(config_module, "_config", None)
    client = signed_in(server)
    r = client.get("/admin/connections")
    assert r.status_code == 200 and "Connect to Memoreei Cloud" not in r.text


@pytest.mark.parametrize("old", ["/admin/cloud", "/admin/keys"])
def test_the_old_pages_lead_to_connections(server, old):
    r = signed_in(server).get(old, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/connections"


def test_the_sidebar_has_connections_and_not_the_old_two(server):
    text = signed_in(server).get("/admin/connections").text
    assert 'href="/admin/connections" aria-current="page"' in text
    assert 'href="/admin/keys"' not in text and 'href="/admin/cloud"' not in text



def test_connect_shows_the_code_then_the_address(server, fake):
    fake.approve_after = 10**9  # until we have seen the code
    client = signed_in(server)
    r = client.post("/admin/cloud/connect", headers=ORIGIN)
    assert r.status_code == 200
    assert "ABCD-EFGH" in r.text and f"{AUTH}/device?code=ABCD-EFGH" in r.text
    fake.approve_after = 0
    for _ in range(100):
        r = client.get("/admin/cloud/progress", headers={"HX-Request": "true"})
        if r.headers.get("hx-refresh"):
            break
        time.sleep(0.02)
    assert r.headers.get("hx-refresh") == "true"
    assert CloudState().ready()
    r = client.get("/admin/cloud")
    assert f"https://{HOST}/mcp" in r.text and "Connected" in r.text


def test_disconnect_revokes_and_says_so_once(server, fake):
    import asyncio

    from memoreei.cloud.connect import connect

    asyncio.run(connect(None))
    client = signed_in(server)
    r = client.post("/admin/cloud/disconnect", headers={**ORIGIN, "HX-Request": "true"})
    assert r.headers["hx-redirect"] == "/admin/connections"
    assert fake.revoked == ["mdt_secret"] and CloudState().device() is None
    assert "off your Memoreei Cloud account" in client.get("/admin/cloud").text
    assert "off your Memoreei Cloud account" not in client.get("/admin/cloud").text


def test_cloud_pages_need_a_session(server):
    client = server()
    assert client.get("/admin/cloud").status_code == 401
    assert client.post("/admin/cloud/connect", headers=ORIGIN).status_code == 401


# ── approving and removing chatbots ──────────────────────────────────────────


def _store_call(db_path, fn):
    """Run fn(store) on a connection of its own, as another process would."""
    import asyncio

    from memoreei.oauth.store import OAuthStore
    from memoreei.storage.database import Database

    async def go():
        async with Database(db_path=db_path) as db:
            return await fn(OAuthStore(db))

    return asyncio.run(go())


def _ask(store, name="Claude", host="claude.ai"):
    return store.add_request("authorize", client_id=f"https://{host}/client.json", client_name=name,
                             client_host=host, self_registered=False, peer="203.0.113.7",
                             resource="https://abcdefghjk23.dev.memoreei.net/mcp", redirect_uri=f"https://{host}/cb",
                             code_challenge="c" * 43, browser_secret="b")


def test_a_waiting_chatbot_shows_on_every_page_and_is_approved_on_the_cloud_page(server):
    client = signed_in(server)
    assert "hx-get=\"/admin/chatbots/waiting?banner=1\"" in client.get("/admin/").text
    assert "asking" not in client.get("/admin/chatbots/waiting?banner=1").text
    row = _store_call(server.db_path, _ask)
    r = client.get("/admin/chatbots/waiting?banner=1")
    assert "Claude is asking to use memoreei" in r.text and row["user_code"] in r.text
    r = client.get("/admin/cloud")
    assert "Waiting for you" in r.text and row["user_code"] in r.text and "203.0.113.7" in r.text
    r = client.post(f"/admin/chatbots/requests/{row['id']}/approve", headers=ORIGIN)
    assert r.status_code == 200 and "Approved Claude" in r.text and "Waiting for you" not in r.text
    answered = _store_call(server.db_path, lambda s: s.request(row["id"]))
    assert answered["approved"] == 1
    # Once answered, it stays answered.
    r = client.post(f"/admin/chatbots/requests/{row['id']}/deny", headers=ORIGIN)
    assert "already been answered" in r.text
    assert _store_call(server.db_path, lambda s: s.request(row["id"]))["approved"] == 1


def test_denying_and_nonsense_answers(server):
    client = signed_in(server)
    row = _store_call(server.db_path, _ask)
    assert client.post(f"/admin/chatbots/requests/{row['id']}/maybe", headers=ORIGIN).status_code == 404
    assert _store_call(server.db_path, lambda s: s.request(row["id"]))["decided_at"] is None
    r = client.post(f"/admin/chatbots/requests/{row['id']}/deny", headers=ORIGIN)
    assert "Denied Claude" in r.text
    assert _store_call(server.db_path, lambda s: s.request(row["id"]))["approved"] == 0


def test_approving_needs_the_dashboards_own_page(server):
    client = signed_in(server)
    row = _store_call(server.db_path, _ask)
    r = client.post(f"/admin/chatbots/requests/{row['id']}/approve", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert server().post(f"/admin/chatbots/requests/{row['id']}/approve", headers=ORIGIN).status_code == 401
    assert _store_call(server.db_path, lambda s: s.request(row["id"]))["decided_at"] is None


def test_removing_a_chatbot(server, monkeypatch):
    monkeypatch.setenv("MEMOREEI_PUBLIC_PORT", "0")
    monkeypatch.setattr(config_module, "_config", None)

    async def connected(store):
        row = await _ask(store)
        return await store.add_grant(row)

    grant = _store_call(server.db_path, connected)
    client = signed_in(server)
    r = client.get("/admin/connections")
    assert "Claude" in r.text and f"/admin/chatbots/{grant['id']}/remove" in r.text
    r = client.post(f"/admin/chatbots/{grant['id']}/remove", headers=ORIGIN)
    assert r.status_code == 200 and 'id="connection-list"' in r.text
    assert f"/admin/chatbots/{grant['id']}/remove" not in r.text and "laptop" in r.text  # the fixture's key stays
    assert _store_call(server.db_path, lambda s: s.grant(grant["id"]))["revoked_at"]


def test_chatbots_and_keys_share_one_table(server, monkeypatch):
    monkeypatch.setenv("MEMOREEI_PUBLIC_PORT", "0")
    monkeypatch.setattr(config_module, "_config", None)

    async def connected(store):
        return await store.add_grant(await _ask(store))

    grant = _store_call(server.db_path, connected)
    client = signed_in(server)
    r = client.get("/admin/connections")
    table = r.text.split('id="connection-list"', 1)[1].split("</section>", 1)[0]
    assert "Claude" in table and "Signed in" in table
    assert "laptop" in table and ">Key<" in table
    assert f"/admin/chatbots/{grant['id']}/remove" in table and "/admin/keys/laptop/revoke" in table
    # Revoking a key answers with the same table, the chatbot still in it.
    r = client.post("/admin/keys/laptop/revoke", headers=ORIGIN)
    assert 'id="connection-list"' in r.text and "Claude" in r.text and "/admin/keys/laptop/revoke" not in r.text
    # The welcome steps count a chatbot as a connection, without any key.
    assert "Done: 1 connection." in client.get("/admin/").text
