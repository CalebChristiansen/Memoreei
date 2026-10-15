"""The dashboard's Cloud page, and the offer on the status page."""
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


def test_the_status_page_offers_it_without_calling_anyone(server, nobody_called):
    client = signed_in(server)
    r = client.get("/admin/")
    assert "Connect to Memoreei Cloud" in r.text and 'href="/admin/cloud"' in r.text
    r = client.get("/admin/cloud")
    assert r.status_code == 200 and "Connect to Memoreei Cloud" in r.text
    assert "your-own-tunnel-and-sign-in" in r.text


@pytest.mark.parametrize("setting", [("MEMOREEI_CLOUD_OFFER", "0"), ("MEMOREEI_PUBLIC_PORT", "0"),
                                     ("MEMOREEI_OAUTH_ISSUER", "https://id.example.test")])
def test_the_offer_can_be_hidden(server, monkeypatch, setting):
    monkeypatch.setenv(*setting)
    monkeypatch.setattr(config_module, "_config", None)
    client = signed_in(server)
    assert "Connect to Memoreei Cloud" not in client.get("/admin/").text


def test_a_custom_issuer_explains_why_cloud_is_off(server, monkeypatch):
    monkeypatch.setenv("MEMOREEI_OAUTH_ISSUER", "https://id.example.test")
    monkeypatch.setattr(config_module, "_config", None)
    r = signed_in(server).get("/admin/cloud")
    assert "MEMOREEI_OAUTH_ISSUER" in r.text and "<button>Connect to Memoreei Cloud" not in r.text


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
    assert f"https://{HOST}/mcp" in r.text and "Who has called" in r.text


def test_disconnect_revokes_and_says_so_once(server, fake):
    import asyncio

    from memoreei.cloud.connect import connect

    asyncio.run(connect(None))
    client = signed_in(server)
    r = client.post("/admin/cloud/disconnect", headers={**ORIGIN, "HX-Request": "true"})
    assert r.headers["hx-redirect"] == "/admin/cloud"
    assert fake.revoked == ["mdt_secret"] and CloudState().device() is None
    assert "off your Memoreei Cloud account" in client.get("/admin/cloud").text
    assert "off your Memoreei Cloud account" not in client.get("/admin/cloud").text


def test_cloud_pages_need_a_session(server):
    client = server()
    assert client.get("/admin/cloud").status_code == 401
    assert client.post("/admin/cloud/connect", headers=ORIGIN).status_code == 401
