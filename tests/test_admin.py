"""The dashboard at /admin: who gets in, and what it does once they're in."""
from __future__ import annotations

import asyncio
import re

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

import memoreei.server as server_module
from memoreei.admin import auth
from memoreei.auth import KeyStore, generate_key, hash_key
from memoreei.cli import app
from memoreei.config import config_env_path, get_config
from memoreei.server import build_http_app
from memoreei.storage.database import Database

runner = CliRunner()
LOCAL = ("127.0.0.1", 50000)
ORIGIN = {"Origin": "http://localhost:3679"}


@pytest.fixture
def server(tmp_path, mock_embedder, monkeypatch):
    """The whole HTTP app (MCP and dashboard) on a fresh DB with one API key."""
    db_path = str(tmp_path / "admin.db")
    monkeypatch.setenv("MEMOREEI_DB_PATH", db_path)
    monkeypatch.delenv("MEMOREEI_APP", raising=False)
    # These tests are about the Mac's dashboard, with iMessage, and no systemd to ask.
    # tests/test_linux_app.py covers what Linux shows instead.
    monkeypatch.setattr("memoreei.admin.app._on_mac", lambda: True)

    async def no_service():
        return None

    monkeypatch.setattr("memoreei.admin.app._service_context", no_service)
    monkeypatch.setattr(server_module, "get_provider", lambda: mock_embedder)
    monkeypatch.setattr(server_module, "_db", None)
    monkeypatch.setattr(server_module, "_tools", None)
    key = generate_key()

    async def _seed() -> None:
        async with Database(db_path=db_path) as db:
            await db.add_api_key("laptop", hash_key(key))

    asyncio.run(_seed())

    async def verify(k: str) -> str | None:
        return await KeyStore(await server_module._get_db()).verify(k)

    apps: list[TestClient] = []

    def make(client=LOCAL, base_url="http://localhost:3679") -> TestClient:
        c = TestClient(build_http_app(verify), base_url=base_url, client=client)
        c.__enter__()
        apps.append(c)
        return c

    make.api_key = key  # type: ignore[attr-defined]
    make.db_path = db_path  # type: ignore[attr-defined]
    yield make
    for c in apps:
        if server_module._db is not None:
            c.portal.call(server_module._db.close)
            server_module._db = None
        c.__exit__(None, None, None)


def _login_token(db_path: str) -> str:
    async def _run() -> str:
        async with Database(db_path=db_path) as db:
            return await auth.create_login_token(db)

    return asyncio.run(_run())


def signed_in(server) -> TestClient:
    client = server()
    r = client.get(f"/admin/login?token={_login_token(server.db_path)}", follow_redirects=False)
    assert r.status_code == 303
    return client


# ── Getting in ───────────────────────────────────────────────────────────────


def test_no_session_is_refused(server):
    r = server().get("/admin/")
    assert r.status_code == 401
    assert "Signed out" in r.text


def test_login_link_signs_in_and_works_once(server):
    client = server()
    token = _login_token(server.db_path)
    r = client.get(f"/admin/login?token={token}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/"
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Path=/admin" in cookie and "SameSite=lax" in cookie
    assert client.get("/admin/").status_code == 200

    again = server().get(f"/admin/login?token={token}")
    assert again.status_code == 401
    assert "expired" in again.text


def test_bad_login_token_is_refused(server):
    assert server().get("/admin/login?token=nope").status_code == 401
    assert server().get("/admin/login").status_code == 401


def test_expired_login_token_is_refused(server, monkeypatch):
    monkeypatch.setattr(auth, "LOGIN_TTL", -1)
    token = _login_token(server.db_path)
    assert server().get(f"/admin/login?token={token}").status_code == 401


def test_sign_out_ends_the_session(server):
    client = signed_in(server)
    session = client.cookies.get(auth.COOKIE)
    assert client.post("/admin/logout", headers=ORIGIN).status_code == 401  # redirected, signed out
    fresh = server()
    fresh.cookies.set(auth.COOKIE, session, path="/admin")
    assert fresh.get("/admin/").status_code == 401


def test_api_key_is_not_a_dashboard_login(server):
    r = server().get("/admin/", headers={"Authorization": f"Bearer {server.api_key}"})
    assert r.status_code == 401


def test_session_cookie_is_not_an_api_key(server):
    client = signed_in(server)
    session = client.cookies.get(auth.COOKIE)
    r = client.post("/mcp", headers={"Authorization": f"Bearer {session}"}, json={})
    assert r.status_code == 401


def test_login_tokens_are_stored_hashed(server):
    import sqlite3

    token = _login_token(server.db_path)
    rows = sqlite3.connect(server.db_path).execute("SELECT token_hash FROM admin_tokens").fetchall()
    assert rows == [(hash_key(token),)]


# ── From where ───────────────────────────────────────────────────────────────


def test_remote_client_is_refused_even_with_a_session(server):
    session_client = signed_in(server)
    remote = server(client=("192.0.2.50", 50000))
    remote.cookies.set(auth.COOKIE, session_client.cookies.get(auth.COOKIE), path="/admin")
    r = remote.get("/admin/")
    assert r.status_code == 403
    assert "only answers on the computer it runs on" in r.text


def test_remote_client_cannot_even_redeem_a_login_link(server):
    token = _login_token(server.db_path)
    assert server(client=("192.0.2.50", 50000)).get(f"/admin/login?token={token}").status_code == 403


def test_foreign_host_header_is_refused(server):
    # DNS rebinding: a page on evil.example resolving to 127.0.0.1
    r = server(base_url="http://evil.example:3679").get("/admin/")
    assert r.status_code == 403


@pytest.mark.parametrize("base", ["http://127.0.0.1:3679", "http://localhost"])
def test_loopback_host_names_are_accepted(server, base):
    assert server(base_url=base).get("/admin/").status_code == 401  # guard passed, no session


@pytest.mark.parametrize(
    "host, name",
    [("[::1]:3679", "::1"), ("localhost:3679", "localhost"), ("LOCALHOST", "localhost"),
     ("127.0.0.1", "127.0.0.1"), ("evil.example:3679", "evil.example")],
)
def test_host_name_strips_port(host, name):
    # Starlette's TestClient can't send an IPv6 Host, so [::1] is checked here.
    assert auth.host_name(host) == name
    assert auth.local_request("::1", host) == (name in auth.LOOPBACK_HOSTS)


def test_mcp_is_still_open_to_other_machines(server):
    r = server(client=("192.0.2.50", 50000), base_url="http://192.0.2.10:3679").post("/mcp", json={})
    assert r.status_code == 401  # bearer auth, not the dashboard's loopback rule


# ── Writes need the same origin ──────────────────────────────────────────────


def test_post_without_origin_is_refused(server):
    client = signed_in(server)
    assert client.post("/admin/keys", data={"name": "x"}).status_code == 403


def test_post_from_another_origin_is_refused(server):
    client = signed_in(server)
    r = client.post("/admin/keys", data={"name": "x"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_post_without_origin_but_same_origin_fetch_site_is_allowed(server):
    client = signed_in(server)
    r = client.post("/admin/keys", data={"name": "x"}, headers={"Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200


def test_null_origin_from_the_dashboard_itself_is_allowed(server):
    # What Firefox sends for a plain form post under a no-referrer policy.
    client = signed_in(server)
    r = client.post("/admin/keys", data={"name": "x"},
                    headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200


def test_null_origin_from_elsewhere_is_refused(server):
    client = signed_in(server)
    r = client.post("/admin/keys", data={"name": "x"},
                    headers={"Origin": "null", "Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    r = client.post("/admin/keys", data={"name": "x"}, headers={"Origin": "null"})
    assert r.status_code == 403


def test_pages_keep_their_origin_on_form_posts(server):
    r = signed_in(server).get("/admin/")
    assert r.headers["referrer-policy"] == "same-origin"


def test_cross_site_fetch_site_is_refused(server):
    client = signed_in(server)
    r = client.post("/admin/keys", data={"name": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_origin_must_match_host_port_too(server):
    client = signed_in(server)
    r = client.post("/admin/keys", data={"name": "x"}, headers={"Origin": "http://localhost:8080"})
    assert r.status_code == 403


# ── Keys ─────────────────────────────────────────────────────────────────────


def test_create_key_shows_it_once_with_client_config(server):
    client = signed_in(server)
    r = client.post("/admin/keys", data={"name": "desktop"}, headers=ORIGIN)
    assert r.status_code == 200
    key = re.search(r"mem_[A-Za-z0-9_-]+", r.text).group(0)
    assert "Shown once" in r.text and "can't show it again" in r.text
    assert "claude mcp add --transport http memoreei" in r.text
    assert asyncio.run(_verify(server.db_path, key)) == "desktop"
    assert key not in client.get("/admin/keys").text


async def _verify(db_path: str, key: str) -> str | None:
    async with Database(db_path=db_path) as db:
        return await KeyStore(db).verify(key)


def test_create_key_refuses_bad_and_duplicate_names(server):
    client = signed_in(server)
    assert "Use letters" in client.post("/admin/keys", data={"name": "a b"}, headers=ORIGIN).text
    assert "already a key" in client.post("/admin/keys", data={"name": "laptop"}, headers=ORIGIN).text


def test_revoke_key(server):
    client = signed_in(server)
    r = client.post("/admin/keys/laptop/revoke", headers=ORIGIN)
    assert r.status_code == 200 and "No keys yet" in r.text
    assert asyncio.run(_verify(server.db_path, server.api_key)) is None


# ── Sources ──────────────────────────────────────────────────────────────────


def test_sources_lists_only_enabled_connectors(server):
    text = signed_in(server).get("/admin/sources").text
    assert "iMessage" in text
    assert "Discord" not in text and "Gmail" not in text


def test_connector_not_enabled_is_unreachable(server):
    client = signed_in(server)
    assert client.get("/admin/sources/discord").status_code == 404
    r = client.post("/admin/sources/discord", data={"DISCORD_BOT_TOKEN": "x"}, headers=ORIGIN)
    assert r.status_code == 404
    assert not config_env_path().exists()


def test_upload_kind_not_enabled_is_unreachable(server):
    client = signed_in(server)
    r = client.post("/admin/upload/whatsapp", files={"file": ("chat.txt", b"hi")}, headers=ORIGIN)
    assert r.status_code == 404


def test_setting_up_imessage_writes_config_and_turns_on_auto_sync(server, monkeypatch):
    monkeypatch.delenv("IMESSAGE_DB_PATH", raising=False)
    monkeypatch.delenv("AUTO_SYNC", raising=False)
    client = signed_in(server)
    form = client.get("/admin/sources/imessage").text
    assert 'value="~/Library/Messages/chat.db"' in form
    r = client.post(
        "/admin/sources/imessage", data={"IMESSAGE_DB_PATH": "~/Library/Messages/chat.db"},
        headers=ORIGIN, follow_redirects=False,
    )
    assert r.status_code == 303
    text = config_env_path().read_text()
    assert "IMESSAGE_DB_PATH=~/Library/Messages/chat.db" in text
    assert "AUTO_SYNC=true" in text
    assert oct(config_env_path().stat().st_mode & 0o777) == "0o600"
    assert get_config().auto_sync is True  # the running server sees it without a restart
    assert "Connected" in client.get("/admin/sources").text


def test_saving_a_source_starts_a_sync_and_shows_the_status_page(server, monkeypatch):
    from memoreei.admin import app as admin_app

    started = []

    async def fake_start() -> None:
        started.append(True)

    monkeypatch.setattr(admin_app, "_start_sync", fake_start)
    r = signed_in(server).post(
        "/admin/sources/imessage", data={"IMESSAGE_DB_PATH": "/x/chat.db"},
        headers=ORIGIN, follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/admin/"
    assert started == [True]


def test_status_page_walks_a_new_install_through_setup(server, monkeypatch):
    monkeypatch.delenv("IMESSAGE_DB_PATH", raising=False)
    client = signed_in(server)
    client.post("/admin/keys/laptop/revoke", headers=ORIGIN)  # the fixture's key
    text = client.get("/admin/").text
    assert "Welcome to Memoreei" in text
    assert 'href="/admin/sources">Add a source' in text
    assert 'href="/admin/keys">Add a client' in text


def test_setup_steps_disappear_once_a_source_and_a_client_exist(server, monkeypatch):
    from memoreei.admin import app as admin_app

    async def no_sync() -> None:
        pass

    monkeypatch.setattr(admin_app, "_start_sync", no_sync)
    client = signed_in(server)
    text = client.get("/admin/").text
    assert "Welcome to Memoreei" in text and "Done: 1 key" in text  # the fixture's key: step 2 done
    client.post("/admin/sources/imessage", data={"IMESSAGE_DB_PATH": "/x/chat.db"}, headers=ORIGIN)
    assert "Welcome to Memoreei" not in client.get("/admin/").text


def test_removing_a_connector_clears_its_settings(server, monkeypatch):
    monkeypatch.delenv("IMESSAGE_DB_PATH", raising=False)
    client = signed_in(server)
    client.post("/admin/sources/imessage", data={"IMESSAGE_DB_PATH": "/x/chat.db"}, headers=ORIGIN)
    client.post("/admin/sources/imessage/remove", headers=ORIGIN)
    assert "IMESSAGE_DB_PATH=\n" in config_env_path().read_text()
    assert "Not set up" in client.get("/admin/sources").text


def test_secret_fields_are_never_sent_back(server, monkeypatch):
    from memoreei import catalog

    monkeypatch.setattr(catalog, "DASHBOARD_CONNECTORS", ("imessage", "telegram"))
    monkeypatch.setattr("memoreei.admin.app.DASHBOARD_CONNECTORS", ("imessage", "telegram"))
    client = signed_in(server)
    client.post("/admin/sources/telegram", data={"TELEGRAM_BOT_TOKEN": "sekrit-123"}, headers=ORIGIN)
    page = client.get("/admin/sources/telegram").text
    assert "sekrit-123" not in page
    assert "Saved. Leave blank to keep it." in page
    # Saving again with the secret left blank keeps it.
    client.post("/admin/sources/telegram", data={"TELEGRAM_CHAT_ID": "1"}, headers=ORIGIN)
    assert "TELEGRAM_BOT_TOKEN=sekrit-123" in config_env_path().read_text()


# ── Status and sync ──────────────────────────────────────────────────────────


def test_status_page(server):
    text = signed_in(server).get("/admin/").text
    assert "Welcome to Memoreei" in text and "Done: 1 key" in text
    assert "Add a source" in text  # no sources set up


def test_sync_now_runs_and_reports(server, monkeypatch):
    client = signed_in(server)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")  # any configured connector enables the button
    from memoreei.config import reload_config

    reload_config()

    async def fake_sync(tools):
        server_module._sync_manager.last_run = {
            "finished_at": __import__("time").time(),
            "result": {"connectors": {"telegram": 3}, "imports": [], "new_messages": 3},
        }
        return {}

    monkeypatch.setattr(server_module._sync_manager, "sync_everything", fake_sync)
    r = client.post("/admin/sync", headers=ORIGIN)
    assert r.status_code == 200
    assert "3 new" in client.get("/admin/status").text


def test_pages_carry_security_headers(server):
    r = signed_in(server).get("/admin/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"


def test_static_files_need_no_session(server):
    r = server().get("/admin/static/htmx.min.js")
    assert r.status_code == 200 and "htmx" in r.text


def test_bare_admin_redirects(server):
    r = server().get("/admin", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/admin/"


# ── admin-url ────────────────────────────────────────────────────────────────


def test_admin_url_prints_a_working_one_time_link(server):
    result = runner.invoke(app, ["admin-url"])
    assert result.exit_code == 0, result.output
    url = next(w for w in result.output.split() if w.startswith("http"))
    assert url.startswith("http://localhost:3679/admin/login?token=")
    client = server()
    assert client.get(url.replace("http://localhost:3679", "")).status_code == 200


def test_refused_writes_are_logged(server, caplog):
    client = signed_in(server)
    with caplog.at_level("WARNING", logger="memoreei.admin"):
        client.post("/admin/keys", data={"name": "x"}, headers={"Origin": "http://evil.example"})
    assert "cross-origin" in caplog.text and "evil.example" in caplog.text


# ── Memoreei.app's switches ──────────────────────────────────────────────────


@pytest.fixture
def in_app(server, tmp_path, monkeypatch):
    """The dashboard as Memoreei.app's server serves it: the app's switches in the Server card."""
    import memoreei.admin.app as admin_app
    from memoreei.service import _app

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MEMOREEI_APP", "1")
    monkeypatch.setenv("MEMOREEI_APP_EXECUTABLE", "/Applications/Memoreei.app/Contents/MacOS/Memoreei")
    monkeypatch.setattr(admin_app.sys, "platform", "darwin")
    monkeypatch.setattr(_app, "_mark_decided", lambda: None)  # `defaults write`
    monkeypatch.setattr(admin_app, "_service_context", _real_service_context)
    return home


from memoreei.admin.app import _service_context as _real_service_context  # noqa: E402


def _with_a_source(client) -> None:
    client.post("/admin/sources/imessage", data={"IMESSAGE_DB_PATH": "/x/chat.db"}, headers=ORIGIN)


def test_app_start_at_login_writes_the_apps_launch_agent(server, in_app, monkeypatch):
    import plistlib

    from memoreei.admin import app as admin_app

    async def no_sync() -> None:
        pass

    monkeypatch.setattr(admin_app, "_start_sync", no_sync)
    client = signed_in(server)
    _with_a_source(client)
    assert 'aria-label="Start at login"' in client.get("/admin/").text
    client.post("/admin/service/autostart", data={"on": "1"}, headers=ORIGIN)
    agent = in_app / "Library/LaunchAgents/cafe.caleb.Memoreei.plist"
    plist = plistlib.loads(agent.read_bytes())
    assert plist["ProgramArguments"] == ["/Applications/Memoreei.app/Contents/MacOS/Memoreei"]
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["EnvironmentVariables"]["MEMOREEI_LAUNCHD"] == "1"
    assert 'aria-pressed="true"' in client.get("/admin/").text
    client.post("/admin/service/autostart", data={"on": "0"}, headers=ORIGIN)
    assert not agent.exists()


def test_app_stop_asks_the_app_to_quit(server, in_app, monkeypatch):
    import memoreei.admin.app as admin_app

    killed = []
    monkeypatch.setattr(admin_app.os, "kill", lambda pid, sig: killed.append(sig))
    r = signed_in(server).post("/admin/service/stop", headers=ORIGIN)
    assert "Memoreei is taking a nap" in r.text
    assert (in_app / "Library/Caches/Memoreei/quit-requested").exists()
    assert killed


def test_app_log_is_the_servers_log_file(server, in_app):
    log = in_app / "Library/Logs/Memoreei/memoreei.log"
    log.parent.mkdir(parents=True)
    log.write_text("2026-09-27 09:12:04 INFO first\n2026-09-27 09:27:58 WARNING careful\n")
    text = signed_in(server).get("/admin/log").text
    assert '<span class="warn">2026-09-27 09:27:58 WARNING careful</span>' in text
    assert "~/Library/Logs/Memoreei/memoreei.log" in text


# ── The status page, set up ──────────────────────────────────────────────────


def test_a_set_up_install_gets_counts_not_steps(server, monkeypatch):
    from memoreei.admin import app as admin_app

    async def no_sync() -> None:
        pass

    monkeypatch.setattr(admin_app, "_start_sync", no_sync)
    client = signed_in(server)
    _with_a_source(client)
    text = client.get("/admin/").text
    assert "Welcome to Memoreei" not in text
    assert "All quiet" in text and "Running on port 3679" in text
    assert "Manage keys →" in text and "iMessage" in text


def test_a_finished_sync_reloads_the_page(server):
    client = signed_in(server)
    r = client.get("/admin/status", headers={"HX-Request": "true"})
    assert r.headers["hx-refresh"] == "true"
    assert "hx-refresh" not in client.get("/admin/status").headers
