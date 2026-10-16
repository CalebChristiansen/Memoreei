"""The dashboard's password, 2FA and access from anywhere (memoreei.admin.account).

Who gets in where: on the computer as before; away from it only with a password and
"from anywhere" on; and through the public listener, never as "the computer",
whatever its client address or Host say.
"""
from __future__ import annotations

import asyncio
import html
import json
import re

import httpx
import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

import memoreei.admin.app as admin_app_module
import memoreei.server as server_module
from memoreei.admin import auth, credentials
from memoreei.admin.account import AccountStore, SignInLimits, describe_browser
from memoreei.cli import app
from memoreei.public.gate import FailureLimiter, PublicGate
from memoreei.storage.database import Database
from tests.test_admin import ORIGIN, _login_token, server  # noqa: F401  (server is a fixture)
from tests.test_public import ISSUER, KEY, MCP_HEADERS, RESOURCE, echo_mcp, keys  # noqa: F401
from tests.test_public import public_address, public_server  # noqa: F401  (fixtures)

runner = CliRunner()
PASSWORD = "correct horse battery"
LAN = ("192.168.1.20", 50000)
LAN_BASE = "http://192.168.1.5:3679"
LAN_ORIGIN = {"Origin": LAN_BASE}
HOST = ISSUER.split("://")[1]


@pytest.fixture(autouse=True)
def fresh_limits(monkeypatch):
    monkeypatch.setattr(admin_app_module, "SIGNIN_LIMITS", None)


def _account(db_path: str, fn):
    async def _run():
        async with Database(db_path=db_path) as db:
            return await fn(AccountStore(db))

    return asyncio.run(_run())


def _set_password(db_path: str, password: str = PASSWORD, remote: bool = False, totp: bool = False) -> str | None:
    """Give the account a password (and the switch, and 2FA); returns the 2FA secret."""
    async def go(store: AccountStore):
        await store.set_password(credentials.hash_password(password))
        if remote:
            await store.set_remote(True)
        if totp:
            secret = await store.start_totp()
            await store.confirm_totp(credentials.totp(secret, credentials.current_step() - 1))
            return secret
        return None

    return _account(db_path, go)


def _sign_in(client: TestClient, origin: dict, password: str = PASSWORD, code: str = "", next_path: str = ""):
    return client.post("/admin/signin", data={"password": password, "code": code, "next": next_path},
                       headers=origin, follow_redirects=False)


# ── credentials ──────────────────────────────────────────────────────────────


def test_totp_matches_rfc_6238():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890", the RFC's SHA-1 key
    for when, expected in ((59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924")):
        assert credentials.totp(secret, when // 30, digits=8) == expected


def test_a_totp_code_works_for_half_a_minute_either_side_and_only_once():
    secret = credentials.new_totp_secret()
    now = 1_700_000_000
    step = now // 30
    for drift in (-1, 0, 1):
        assert credentials.check_totp(secret, credentials.totp(secret, step + drift), None, now) == step + drift
    assert credentials.check_totp(secret, credentials.totp(secret, step - 2), None, now) is None
    assert credentials.check_totp(secret, credentials.totp(secret, step), step, now) is None  # used already
    assert credentials.check_totp(secret, "abcdef", None, now) is None
    spaced = credentials.totp(secret, step)
    assert credentials.check_totp(secret, f"{spaced[:3]} {spaced[3:]}", None, now) == step


def test_passwords_are_scrypt_salted_and_checked_in_constant_form():
    h = credentials.hash_password(PASSWORD)
    assert h.startswith("scrypt$32768$8$1$") and PASSWORD not in h
    assert h != credentials.hash_password(PASSWORD)  # salted
    assert credentials.check_password(PASSWORD, h)
    assert not credentials.check_password(PASSWORD + "!", h)
    assert not credentials.check_password(PASSWORD, None)
    assert not credentials.check_password(PASSWORD, "md5$whatever")
    assert not credentials.check_password(PASSWORD, "scrypt$1$2$3$!!$??")


def test_what_a_password_must_be():
    assert credentials.password_problem("short") is not None
    assert credentials.password_problem(" " + PASSWORD) is not None
    assert credentials.password_problem("x" * 2000) is not None
    assert credentials.password_problem(PASSWORD) is None


def test_recovery_codes_forgive_case_and_dashes():
    code = credentials.new_recovery_codes()[0]
    assert re.fullmatch(r"[a-z0-9]{5}-[a-z0-9]{5}", code)
    assert credentials.recovery_hash(code) == credentials.recovery_hash(code.upper().replace("-", " "))


def test_the_qr_code_is_inline_svg():
    svg = credentials.qr_svg(credentials.totp_uri("ABCDEFGH", "Memoreei (test)"))
    assert svg.startswith("<svg") and "script" not in svg
    assert credentials.totp_uri("ABCDEFGH", "Memoreei (test)").startswith(
        "otpauth://totp/Memoreei%20%28test%29?secret=ABCDEFGH&issuer=Memoreei")


def test_browser_descriptions():
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1"
    assert describe_browser(iphone) == "Safari on iPhone"
    assert describe_browser("Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36 Edg/130.0") == "Edge on Windows"
    assert describe_browser(None) == "a browser"


# ── the account ──────────────────────────────────────────────────────────────


async def test_from_anywhere_needs_a_password(temp_db):
    store = AccountStore(temp_db)
    assert not await store.remote_open()
    assert not await store.set_remote(True)
    await store.set_password(credentials.hash_password(PASSWORD))
    assert not await store.remote_open()  # a password alone opens nothing
    assert await store.set_remote(True) and await store.remote_open()
    await store.reset()
    assert not await store.remote_open() and not await store.has_password()


async def test_checking_a_password_and_a_code(temp_db):
    store = AccountStore(temp_db)
    assert await store.check(PASSWORD) == (False, "no password is set")
    await store.set_password(credentials.hash_password(PASSWORD))
    assert await store.check(PASSWORD) == (True, "password")
    assert await store.check("wrong password!") == (False, "wrong password")

    secret = await store.start_totp()
    assert not (await store.account())["totp_secret"]  # not until a code proves the app has it
    codes = await store.confirm_totp(credentials.totp(secret, credentials.current_step() - 1))
    assert codes is not None and len(codes) == 10
    assert await store.check(PASSWORD) == (False, "no code")
    assert await store.check(PASSWORD, "123456") == (False, "wrong code")
    now = credentials.totp(secret, credentials.current_step())
    assert await store.check(PASSWORD, now) == (True, "password and code")
    assert (await store.check(PASSWORD, now))[0] is False  # the same code can't sign in twice

    assert await store.check("wrong password!", codes[0]) == (False, "wrong password")
    assert await store.recovery_codes_left() == 10  # never spent on a wrong password
    assert await store.check(PASSWORD, codes[0].upper()) == (True, "password and a recovery code")
    assert (await store.check(PASSWORD, codes[0]))[0] is False
    assert await store.recovery_codes_left() == 9


async def test_a_new_password_or_2fa_signs_out_everyone_else(temp_db):
    store = AccountStore(temp_db)
    await store.set_password(credentials.hash_password(PASSWORD))
    mine, theirs = await store.add_session("password"), await store.add_session("password")
    link = await store.add_session("link")
    browser = await store.add_browser()
    await store.set_password(credentials.hash_password(PASSWORD + "2"), keep_session=mine)
    assert await store.session(mine) and await store.session(link)
    assert await store.session(theirs) is None
    assert not await store.browser_known(browser)

    theirs = await store.add_session("password")
    secret = await store.start_totp()
    await store.confirm_totp(credentials.totp(secret, credentials.current_step()), keep_session=mine)
    assert await store.session(mine) and await store.session(theirs) is None

    await store.reset()
    assert await store.session(mine) is None and await store.session(link)
    assert (await store.account())["totp_secret"] is None and await store.recovery_codes_left() == 0


async def test_sessions_from_before_this_version_are_link_sessions(temp_db):
    await temp_db.add_admin_token(auth.hash_key("old-session"), "session", 3600)
    session = await AccountStore(temp_db).session("old-session")
    assert session is not None and session["how"] == "link"
    await AccountStore(temp_db).end_session("old-session")
    assert await AccountStore(temp_db).session("old-session") is None


def test_sign_in_limits_per_address_and_for_everyone():
    t = [0.0]
    limits = SignInLimits((3, 60), (5, 60), clock=lambda: t[0])
    for _ in range(3):
        limits.fail("198.51.100.1")
    assert limits.blocked("198.51.100.1")
    assert limits.blocked("198.51.100.1", known_browser=True)  # its own address is still its own
    assert limits.blocked("198.51.100.2") is None
    limits.fail("198.51.100.2")
    limits.fail("198.51.100.3")
    assert limits.blocked("198.51.100.4") == "too many failed sign-ins from everyone"
    assert limits.blocked("198.51.100.4", known_browser=True) is None  # the owner's browser gets through
    t[0] = 61
    assert limits.blocked("198.51.100.1") is None and limits.blocked("198.51.100.4") is None


def test_an_attempt_counts_while_it_is_checked_and_is_forgiven_if_right():
    """Attempts sent all at once each take a place first, so they can't all find an
    empty count: the eleventh concurrent guess waits, before any of them has failed."""
    limits = SignInLimits((3, 60), (100, 60))
    assert all(limits.take("198.51.100.1") is None for _ in range(3))
    assert limits.take("198.51.100.1") == "too many failed sign-ins from this address"
    limits.forgive("198.51.100.1")  # one of the three was right after all
    assert limits.take("198.51.100.1") is None


def test_the_lan_listener_knows_this_computers_names(monkeypatch):
    monkeypatch.setattr("socket.gethostname", lambda: "studio.home.arpa")
    for host in ("192.168.1.5:3679", "[fe80::1]:3679", "studio:3679", "studio.local:3679",
                 "studio.home.arpa", "memories.example.com"):
        assert auth.names_this_computer(host, "https://memories.example.com"), host
    for host in ("evil.example:3679", "studio.evil.example", "", "memories.example.com.evil.example"):
        assert not auth.names_this_computer(host, "https://memories.example.com"), host


def test_safe_next():
    assert auth.safe_next("/admin/chatbots/requests/abc_DEF-1") == "/admin/chatbots/requests/abc_DEF-1"
    for bad in ("//evil.example", "https://evil.example/admin/", "/admin//evil.example", "/admin/../x",
                "/admin/x?y=1", "/elsewhere", "", None, "/admin/\\evil"):
        assert auth.safe_next(bad) == "/admin/"


# ── on the computer ──────────────────────────────────────────────────────────


def test_without_a_password_nothing_changes(server):
    client = server()
    r = client.get("/admin/")
    assert r.status_code == 401 and "memoreei admin-url" in r.text and "password" not in r.text.lower()
    assert _sign_in(client, ORIGIN).status_code == 401
    assert server(client=LAN, base_url=LAN_BASE).get("/admin/").status_code == 403


def test_with_a_password_the_computer_can_sign_in_with_it_or_a_link(server):
    _set_password(server.db_path)
    client = server()
    r = client.get("/admin/keys")
    assert r.status_code == 401 and 'name="password"' in r.text and "memoreei admin-url" in r.text
    assert 'value="/admin/keys"' in r.text  # where it goes next
    r = _sign_in(client, ORIGIN, next_path="/admin/keys")
    assert r.status_code == 303 and r.headers["location"] == "/admin/keys"
    cookie = r.headers["set-cookie"]
    assert "memoreei_admin=" in cookie and "HttpOnly" in cookie and "Path=/admin" in cookie
    assert "Secure" not in cookie  # plain http on localhost
    assert client.get("/admin/keys").status_code == 200

    other = server()
    r = other.get(f"/admin/login?token={_login_token(server.db_path)}", follow_redirects=False)
    assert r.status_code == 303 and other.get("/admin/").status_code == 200


def test_a_wrong_password_is_refused_logged_and_recorded(server, caplog):
    _set_password(server.db_path)
    client = server()
    r = _sign_in(client, ORIGIN, password="not the password")
    assert r.status_code == 401 and "didn't work" in html.unescape(r.text) and "memoreei_admin" not in r.headers.get("set-cookie", "")
    assert "dashboard sign-in from" in caplog.text and "wrong password" in caplog.text
    rows = _account(server.db_path, lambda s: s.signins())
    assert rows[0]["ok"] == 0 and rows[0]["why"] == "wrong password" and rows[0]["peer"] == "127.0.0.1"


def test_the_sign_in_form_needs_the_same_origin_too(server):
    _set_password(server.db_path)
    client = server()
    assert _sign_in(client, {"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/admin/signin", data={"password": PASSWORD}).status_code == 403  # no Origin at all


def test_signing_in_never_keeps_a_session_it_was_handed(server):
    """Session fixation: whatever cookie the browser arrives with, it leaves with a new one."""
    _set_password(server.db_path)
    client = server()
    client.cookies.set("memoreei_admin", "planted-by-someone-else", path="/admin")
    r = _sign_in(client, ORIGIN)
    new = re.search(r"memoreei_admin=([^;]+)", r.headers["set-cookie"]).group(1)
    assert new != "planted-by-someone-else"

    before = new
    r = _sign_in(client, ORIGIN)  # signing in again, with a real session this time
    after = re.search(r"memoreei_admin=([^;]+)", r.headers["set-cookie"]).group(1)
    assert after != before
    assert _account(server.db_path, lambda s: s.session(before)) is None


def test_two_step_sign_in(server):
    secret = _set_password(server.db_path, totp=True)
    client = server()
    r = client.get("/admin/")
    assert 'name="code"' in r.text and "one-time-code" in r.text
    assert _sign_in(client, ORIGIN).status_code == 401
    assert _sign_in(client, ORIGIN, code="000000").status_code == 401
    r = _sign_in(client, ORIGIN, code=credentials.totp(secret, credentials.current_step()))
    assert r.status_code == 303


# ── away from the computer, on the LAN listener ──────────────────────────────


def test_away_from_the_computer_needs_the_password_and_the_switch(server):
    remote = server(client=LAN, base_url=LAN_BASE)
    _set_password(server.db_path)
    assert remote.get("/admin/").status_code == 403  # a password alone opens nothing
    _account(server.db_path, lambda s: s.set_remote(True))
    r = remote.get("/admin/")
    assert r.status_code == 401 and 'name="password"' in r.text and "memoreei admin reset" in r.text
    assert "memoreei admin-url" not in r.text
    assert _sign_in(remote, LAN_ORIGIN).status_code == 303
    assert remote.get("/admin/").status_code == 200

    _account(server.db_path, lambda s: s.set_remote(False))
    assert remote.get("/admin/").status_code == 403


def test_a_rebinding_page_is_refused_even_once_open(server):
    """A page on evil.example that resolves to this computer sends Host: evil.example,
    and its Origin matches that Host: so a name that isn't this computer's is refused."""
    _set_password(server.db_path, remote=True)
    for client in (LAN, ("127.0.0.1", 50000)):
        rebound = server(client=client, base_url="http://evil.example:3679")
        assert rebound.get("/admin/").status_code == 403
        assert _sign_in(rebound, {"Origin": "http://evil.example:3679"}).status_code == 403


def test_the_computer_itself_is_never_rate_limited(server, monkeypatch):
    monkeypatch.setattr(admin_app_module, "SIGNIN_LIMITS", SignInLimits((2, 900), (2, 3600)))
    _set_password(server.db_path)
    client = server()
    for _ in range(5):
        assert _sign_in(client, ORIGIN, password="not the password").status_code == 401
    assert _sign_in(client, ORIGIN).status_code == 303


def test_one_time_links_never_work_away_from_the_computer(server):
    _set_password(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    token = _login_token(server.db_path)
    assert remote.get(f"/admin/login?token={token}", follow_redirects=False).status_code == 403
    # The link wasn't spent by the refusal, and still works where it should.
    local = server()
    assert local.get(f"/admin/login?token={token}", follow_redirects=False).status_code == 303


def test_a_link_session_does_not_travel(server):
    _set_password(server.db_path, remote=True)
    local = server()
    local.get(f"/admin/login?token={_login_token(server.db_path)}")
    cookie = local.cookies.get("memoreei_admin")
    remote = server(client=LAN, base_url=LAN_BASE)
    remote.cookies.set("memoreei_admin", cookie, path="/admin")
    r = remote.get("/admin/")
    assert r.status_code == 401 and 'name="password"' in r.text


def test_remote_writes_need_their_own_origin(server):
    _set_password(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    assert _sign_in(remote, LAN_ORIGIN).status_code == 303
    assert remote.post("/admin/keys", data={"name": "x"}, headers=ORIGIN).status_code == 403  # localhost's origin
    assert remote.post("/admin/keys", data={"name": "x"}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert remote.post("/admin/keys", data={"name": "phone"}, headers=LAN_ORIGIN).status_code == 200


def test_behind_your_own_proxy_its_address_is_an_origin(server, monkeypatch):
    monkeypatch.setenv("MEMOREEI_PUBLIC_URL", "https://memories.example.com")
    monkeypatch.setattr("memoreei.config._config", None)
    _set_password(server.db_path, remote=True)
    proxied = server(client=("127.0.0.1", 40000), base_url="http://memories.example.com")
    r = _sign_in(proxied, {"Origin": "https://memories.example.com"})
    assert r.status_code == 303 and "Secure" in r.headers["set-cookie"]


def test_failed_sign_ins_are_limited_without_locking_the_owner_out(server, monkeypatch):
    monkeypatch.setattr(admin_app_module, "SIGNIN_LIMITS", SignInLimits((3, 900), (5, 3600)))
    _set_password(server.db_path, remote=True)
    owner = server(client=("198.51.100.1", 1), base_url=LAN_BASE)
    assert _sign_in(owner, LAN_ORIGIN).status_code == 303  # once, so this browser is known
    owner.cookies.delete("memoreei_admin", path="/admin")

    for n in range(5):  # a stranger, from two addresses
        guesser = server(client=(f"203.0.113.{n % 2}", 1), base_url=LAN_BASE)
        assert _sign_in(guesser, LAN_ORIGIN, password="guess guess guess").status_code in (401, 429)
    guesser = server(client=("203.0.113.0", 1), base_url=LAN_BASE)
    r = _sign_in(guesser, LAN_ORIGIN)  # even the right password waits, from there
    assert r.status_code == 429 and "Too many" in r.text
    newcomer = server(client=("192.0.2.99", 1), base_url=LAN_BASE)
    assert _sign_in(newcomer, LAN_ORIGIN).status_code == 429  # everyone's limit is full
    assert _sign_in(owner, LAN_ORIGIN).status_code == 303  # but a known browser gets in


def test_away_from_the_computer_the_password_and_2fa_cannot_be_changed(server):
    _set_password(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    _sign_in(remote, LAN_ORIGIN)
    page = remote.get("/admin/security").text
    assert "can only be changed there" in page and 'action="/admin/security/password"' not in page
    for path in ("/admin/security/password", "/admin/security/password/remove", "/admin/security/2fa",
                 "/admin/security/2fa/off", "/admin/security/recovery"):
        assert remote.post(path, data={"password": "a new password!", "confirm": "a new password!"},
                           headers=LAN_ORIGIN).status_code == 403, path
    assert remote.post("/admin/security/remote", data={"on": "1"}, headers=LAN_ORIGIN).status_code == 403
    assert _account(server.db_path, lambda s: s.check(PASSWORD))[0]

    # Turning it off is fine from anywhere, and signs this browser out with it.
    r = remote.post("/admin/security/remote", data={"on": "0"}, headers=LAN_ORIGIN, follow_redirects=False)
    assert r.status_code == 303 and remote.get("/admin/").status_code == 403


# ── the Security page, on the computer ───────────────────────────────────────


def _signed_in_locally(server) -> TestClient:
    client = server()
    client.get(f"/admin/login?token={_login_token(server.db_path)}")
    return client


def test_setting_and_removing_a_password_on_the_computer(server):
    client = _signed_in_locally(server)
    page = client.get("/admin/security").text
    assert "Set password" in page and "This computer only" in page
    r = client.post("/admin/security/password", data={"password": PASSWORD, "confirm": PASSWORD + "x"}, headers=ORIGIN)
    assert r.status_code == 400 and "don't match" in html.unescape(r.text)
    r = client.post("/admin/security/password", data={"password": "short", "confirm": "short"}, headers=ORIGIN)
    assert r.status_code == 400 and "at least 10" in r.text
    r = client.post("/admin/security/password", data={"password": PASSWORD, "confirm": PASSWORD}, headers=ORIGIN)
    assert r.status_code == 200 and "Password set." in r.text and "Manage memoreei from anywhere" in r.text
    assert _account(server.db_path, lambda s: s.check(PASSWORD))[0]
    assert client.get("/admin/").status_code == 200  # the link's session carries on

    r = client.post("/admin/security/remote", data={"on": "1"}, headers=ORIGIN)
    assert "answers away from this computer" in r.text and "Open from anywhere" in r.text
    r = client.post("/admin/security/password/remove", headers={**ORIGIN, "HX-Request": "true"})
    assert r.headers["hx-redirect"] == "/admin/security"
    assert not _account(server.db_path, lambda s: s.remote_open())


def test_setting_up_2fa_on_the_computer(server):
    _set_password(server.db_path)
    client = _signed_in_locally(server)
    r = client.post("/admin/security/2fa", headers=ORIGIN)
    assert r.status_code == 200 and "<svg" in r.text and "otpauth://totp/" in r.text
    secret = _account(server.db_path, lambda s: s.account())["totp_pending"]
    r = client.post("/admin/security/2fa/confirm", data={"code": "000000"}, headers=ORIGIN)
    assert r.status_code == 400 and "didn't match" in html.unescape(r.text)
    r = client.post("/admin/security/2fa/confirm", data={"code": credentials.totp(secret, credentials.current_step())},
                    headers=ORIGIN)
    assert r.status_code == 200 and "Shown once" in r.text
    codes = re.findall(r"\b[a-z0-9]{5}-[a-z0-9]{5}\b", r.text)
    assert len(codes) == 10
    assert "10 recovery codes left" in client.get("/admin/security").text

    r = client.post("/admin/security/recovery", headers=ORIGIN)
    assert "The old ones no longer work" in r.text
    assert not _account(server.db_path, lambda s: s.use_recovery_code(codes[0]))
    client.post("/admin/security/2fa/off", headers=ORIGIN)
    assert _account(server.db_path, lambda s: s.account())["totp_secret"] is None


def test_sign_out_every_other_browser(server):
    _set_password(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    _sign_in(remote, LAN_ORIGIN)
    local = _signed_in_locally(server)
    page = local.get("/admin/security").text
    assert "Password" in page and "192.168.1.20" in page and "(this one)" in page
    r = local.post("/admin/security/sign-out-others", headers=ORIGIN)
    assert "Signed out 1 other browser" in r.text
    assert remote.get("/admin/").status_code == 401 and local.get("/admin/").status_code == 200


def test_the_welcome_offers_a_password_as_an_optional_third_step(server):
    client = _signed_in_locally(server)
    client.post("/admin/keys/laptop/revoke", headers=ORIGIN)
    text = client.get("/admin/").text
    assert "Set a password" in text and "Optional" in text


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_the_cli_sets_a_password_switches_and_resets(server):
    out = runner.invoke(app, ["admin", "status"])
    assert "Password: none" in out.output
    assert runner.invoke(app, ["admin", "remote", "on"]).exit_code == 1
    out = runner.invoke(app, ["admin", "password"], input="short\nshort\n")
    assert out.exit_code == 1 and "at least 10" in out.output
    out = runner.invoke(app, ["admin", "password"], input=f"{PASSWORD}\n{PASSWORD}\n")
    assert out.exit_code == 0 and "Password set" in out.output and "memoreei admin remote on" in out.output
    out = runner.invoke(app, ["admin", "password", "--stdin"], input=f"{PASSWORD} 2\n")
    assert out.exit_code == 0 and "changed" in out.output
    assert runner.invoke(app, ["admin", "remote", "on"]).exit_code == 0
    out = runner.invoke(app, ["admin", "status"])
    assert "From anywhere: on" in out.output and "2FA: off" in out.output
    assert _account(server.db_path, lambda s: s.check(f"{PASSWORD} 2"))[0]

    out = runner.invoke(app, ["admin", "reset", "--yes"])
    assert out.exit_code == 0 and "No password" in out.output
    assert not _account(server.db_path, lambda s: s.has_password())


# ── through the public listener ──────────────────────────────────────────────


@pytest.fixture
def public_gate(server):
    """The gate with the dashboard's public copy behind it, on the server fixture's
    database. Everything arrives from 127.0.0.1, as the relay's tunnel delivers it."""
    from memoreei.admin.app import build_admin_app

    async def admin_open() -> bool:
        return await AccountStore(await server_module._get_db()).remote_open()

    gate = PublicGate(echo_mcp, resource=RESOURCE, keys=keys, limiter=FailureLimiter((50, 60), (500, 60)),
                      peer_of=lambda scope: "203.0.113.50", admin=build_admin_app(public_origin=ISSUER),
                      admin_open=admin_open)
    client = TestClient(gate, base_url=ISSUER, client=("127.0.0.1", 40000))
    client.__enter__()
    yield client
    if server_module._db is not None:
        client.portal.call(server_module._db.close)
        server_module._db = None
    client.__exit__(None, None, None)


PUBLIC_ORIGIN = {"Origin": ISSUER}


def test_the_public_listener_hides_the_dashboard_until_it_is_opened(server, public_gate):
    assert public_gate.get("/admin/").status_code == 404
    _set_password(server.db_path)
    assert public_gate.get("/admin/").status_code == 404  # a password alone: still nothing
    _account(server.db_path, lambda s: s.set_remote(True))
    r = public_gate.get("/admin", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/admin/"
    r = public_gate.get("/admin/")
    assert r.status_code == 401 and 'name="password"' in r.text and "memoreei admin reset" in r.text


def test_through_the_public_listener_nothing_is_on_the_computer(server, public_gate):
    """The tunnel delivers every request from 127.0.0.1. Even with Host: localhost, a
    request through the public listener is never local: no links, no link sessions."""
    _set_password(server.db_path, remote=True)
    token = _login_token(server.db_path)
    as_local = {"Host": "localhost:3679"}
    assert public_gate.get(f"/admin/login?token={token}", headers=as_local).status_code == 404  # not its address
    assert public_gate.get(f"/admin/login?token={token}").status_code == 403

    local = server()
    local.get(f"/admin/login?token={token}")
    public_gate.cookies.set("memoreei_admin", local.cookies.get("memoreei_admin"), domain=HOST, path="/admin")
    assert public_gate.get("/admin/").status_code == 401
    assert local.get("/admin/").status_code == 200  # and the same session still works where it belongs


def test_the_public_dashboard_answers_only_to_its_own_address_and_origin(server, public_gate):
    _set_password(server.db_path, remote=True)
    assert public_gate.get("/admin/", headers={"Host": "evil.example"}).status_code == 404
    assert _sign_in(public_gate, {"Origin": "http://localhost:3679"}).status_code == 403
    assert _sign_in(public_gate, {"Origin": f"http://{HOST}"}).status_code == 403  # the scheme counts too
    r = _sign_in(public_gate, PUBLIC_ORIGIN)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "Domain" not in cookie  # this hostname only
    rows = _account(server.db_path, lambda s: s.signins())
    assert rows[0]["ok"] == 1 and rows[0]["peer"] == "203.0.113.50"  # the caller, not the tunnel


def test_the_public_dashboard_bodies_are_bounded(server, public_gate):
    _set_password(server.db_path, remote=True)
    r = public_gate.post("/admin/signin", content=b"x" * (2 << 20), headers=PUBLIC_ORIGIN)
    assert r.status_code == 413


def test_a_chatbot_is_approved_from_anywhere(public_address, public_server):
    """The whole of it, through a real public listener: the chatbot's waiting page offers
    to sign in, the person does, approves, and is sent straight back to the chatbot."""
    import base64
    import hashlib
    import secrets
    from urllib.parse import parse_qs, urlsplit

    base = public_server.public_url
    out = runner.invoke(app, ["admin", "password", "--stdin"], input=f"{PASSWORD}\n")
    assert out.exit_code == 0
    assert runner.invoke(app, ["admin", "remote", "on"]).exit_code == 0
    host = {"Host": HOST}

    reg = httpx.post(base + "/register", json={"client_name": "Test Bot",
                                               "redirect_uris": ["http://127.0.0.1:9/cb"]}).json()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    r = httpx.get(base + "/authorize", params={
        "response_type": "code", "client_id": reg["client_id"], "redirect_uri": "http://127.0.0.1:9/cb",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s", "resource": RESOURCE})
    wait = base + r.headers["location"]
    chatbot_cookie = r.headers["set-cookie"].split(";")[0]
    page = httpx.get(wait, headers={"Cookie": chatbot_cookie}).text
    link = re.search(r"<a href='(/admin/chatbots/requests/[^']+)'>Sign in to approve it here</a>", page).group(1)
    code = re.search(r"<p class=code>(\w+)</p>", page).group(1)

    r = httpx.get(base + link, headers=host)
    assert r.status_code == 401 and f'value="{link}"' in r.text
    r = httpx.post(base + "/admin/signin", data={"password": PASSWORD, "next": link},
                   headers={**host, "Origin": ISSUER})
    assert r.status_code == 303 and r.headers["location"] == link
    admin_cookie = re.search(r"memoreei_admin=[^;]+", r.headers["set-cookie"]).group(0)
    cookies = f"{chatbot_cookie}; {admin_cookie}"
    r = httpx.get(base + link, headers={**host, "Cookie": cookies})
    assert r.status_code == 200 and code in r.text and "Test Bot" in r.text
    request_id = link.rsplit("/", 1)[1]
    r = httpx.post(base + f"{link}/approve", data={"page": "1"}, headers={**host, "Origin": ISSUER, "Cookie": cookies})
    assert r.status_code == 303 and r.headers["location"] == f"/authorize/wait?request={request_id}"

    r = httpx.get(base + r.headers["location"], headers={"Cookie": chatbot_cookie})
    assert r.status_code == 303
    assert parse_qs(urlsplit(r.headers["location"]).query)["code"]  # back to the chatbot, connected


def test_without_the_dashboard_open_the_waiting_page_offers_only_the_computer(public_address, public_server):
    base = public_server.public_url
    reg = httpx.post(base + "/register", json={"client_name": "Test Bot",
                                               "redirect_uris": ["http://127.0.0.1:9/cb"]}).json()
    r = httpx.get(base + "/authorize", params={
        "response_type": "code", "client_id": reg["client_id"], "redirect_uri": "http://127.0.0.1:9/cb",
        "code_challenge": "A" * 43, "code_challenge_method": "S256", "resource": RESOURCE})
    page = httpx.get(base + r.headers["location"], headers={"Cookie": r.headers["set-cookie"].split(";")[0]}).text
    assert "Approve it on your computer" in page and "Sign in to approve" not in page
    assert "/admin" not in httpx.get(base + "/device").text
