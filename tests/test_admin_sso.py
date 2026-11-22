"""Signing in to the dashboard by SSO (memoreei.admin.sso).

A fake OpenID provider with its own RSA key stands in for WorkOS, Authentik or Google:
it answers discovery, its key set and its token endpoint, and checks PKCE the way a
real one does. Everything memoreei does with it is real: the redirect, the state and
its cookie, the code exchange, and the ID token's signature and claims.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from typer.testing import CliRunner

from memoreei.admin import sso
from memoreei.cli import app
from tests.test_admin import ORIGIN, _login_token, server  # noqa: F401  (server is a fixture)
from tests.test_admin_account import (  # noqa: F401  (fixtures)
    LAN, LAN_BASE, LAN_ORIGIN, PASSWORD, PUBLIC_ORIGIN, _account, _set_password, _sign_in, fresh_limits,
    public_gate,
)
from tests.test_public import ISSUER  # noqa: F401
from tests.test_public import keys  # noqa: F401

runner = CliRunner()
IDP = "https://login.example.com"
CLIENT = "client_memoreei"
ADA = {"sub": "user_01ADA", "email": "ada@example.com", "email_verified": True}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FakeProvider:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "k1"
        self.codes: dict[str, dict] = {}
        self.person = dict(ADA)
        self.extra: dict = {}
        self.token_requests: list[dict] = []
        self.issuer = IDP  # Authentik's ends in a /, Google's and WorkOS's don't
        self.discovery_issuer: str | None = None

    def jwk(self) -> dict:
        n = self.key.public_key().public_numbers()
        return {"kty": "RSA", "kid": self.kid, "use": "sig", "alg": "RS256",
                "n": b64(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")), "e": b64(n.e.to_bytes(3, "big"))}

    def sign(self, claims: dict, alg: str = "RS256", kid: str | None = None, key=None) -> str:
        h = b64(json.dumps({"alg": alg, "kid": kid or self.kid, "typ": "JWT"}).encode())
        p = b64(json.dumps(claims).encode())
        sig = (key or self.key).sign(f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{h}.{p}.{b64(sig)}"

    def claims(self, nonce: str, /, **over) -> dict:
        now = int(time.time())
        return {"iss": self.issuer, "aud": CLIENT, "iat": now, "exp": now + 300, "nonce": nonce, **self.person,
                **self.extra, **over}

    async def request(self, method: str, url: str, what: str, **kwargs) -> dict:
        if url == self.issuer.rstrip("/") + "/.well-known/openid-configuration":
            return {"issuer": self.discovery_issuer or self.issuer, "authorization_endpoint": IDP + "/oauth2/authorize",
                    "token_endpoint": IDP + "/oauth2/token", "jwks_uri": IDP + "/oauth2/jwks",
                    "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"]}
        if url == IDP + "/oauth2/jwks":
            return {"keys": [self.jwk()]}
        if url == IDP + "/oauth2/token":
            data = kwargs["data"]
            self.token_requests.append(data)
            entry = self.codes.pop(data.get("code"), None)
            if entry is None or data.get("redirect_uri") != entry["redirect_uri"] \
                    or b64(hashlib.sha256(data.get("code_verifier", "").encode()).digest()) != entry["challenge"]:
                raise sso.SSOError("The token endpoint answered 400 (invalid_grant)")
            return {"access_token": "at", "token_type": "Bearer", "id_token": self.sign(self.claims(entry["nonce"]))}
        raise sso.SSOError(f"{url}: 404")

    def authorize(self, location: str) -> tuple[str, str]:
        """The person signs in at the provider: it remembers the request and gives a code."""
        u = urlsplit(location)
        assert f"{u.scheme}://{u.netloc}{u.path}" == IDP + "/oauth2/authorize"
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        assert q["response_type"] == "code" and q["client_id"] == CLIENT and q["code_challenge_method"] == "S256"
        assert "openid" in q["scope"].split()
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"challenge": q["code_challenge"], "nonce": q["nonce"], "redirect_uri": q["redirect_uri"]}
        return code, q["state"]


@pytest.fixture
def idp(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(sso, "_request", provider.request)
    sso._discovery.clear()
    sso._jwks.clear()
    sso._pending.clear()
    yield provider
    sso._discovery.clear()
    sso._jwks.clear()
    sso._pending.clear()


def _sso_on(db_path: str, allow: str = "ada@example.com", remote: bool = False, secret: str | None = "s3cret",
            issuer: str = IDP):
    async def go(store):
        await sso.turn_on(store, issuer, CLIENT, tuple(a for a in allow.split(",") if a), secret, "Example")
        if remote:
            await store.set_remote(True)

    _account(db_path, go)


def _sso_sign_in(client, idp: FakeProvider, next_path: str = "/admin/keys"):
    r = client.get(f"/admin/sso/start?next={next_path}", follow_redirects=False)
    assert r.status_code == 303, r.text
    code, state = idp.authorize(r.headers["location"])
    return client.get(f"/admin/sso/callback?code={code}&state={state}", follow_redirects=False)


# ── the ID token ─────────────────────────────────────────────────────────────


SETTINGS = sso.Settings(IDP, CLIENT, "s3cret", ("ada@example.com",), None)


async def _verify(idp: FakeProvider, token: str, nonce: str = "n0nce") -> dict:
    return await sso.verify_id_token(SETTINGS, await sso.discover(IDP), token, nonce)


async def test_a_good_id_token_is_accepted(idp):
    claims = await _verify(idp, idp.sign(idp.claims("n0nce")))
    assert claims["sub"] == "user_01ADA"


@pytest.mark.parametrize("over, why", [
    ({"iss": "https://evil.example"}, "not"),
    ({"aud": "someone-else"}, "isn't for this client"),
    ({"aud": [CLIENT, "other"]}, "azp"),
    ({"azp": "other"}, "azp"),
    ({"exp": int(time.time()) - 600}, "expired"),
    ({"iat": int(time.time()) + 3600}, "future"),
    ({"nonce": "another"}, "nonce"),
    ({"sub": ""}, "subject"),
])
async def test_an_id_token_is_refused_for_each_claim_that_is_wrong(idp, over, why):
    with pytest.raises(sso.SSOError, match=why):
        await _verify(idp, idp.sign(idp.claims("n0nce", **over)))


async def test_an_id_token_with_two_audiences_needs_azp_to_be_us(idp):
    await _verify(idp, idp.sign(idp.claims("n0nce", aud=[CLIENT, "other"], azp=CLIENT)))


async def test_an_id_token_signed_by_another_key_or_unsigned_is_refused(idp):
    stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(sso.SSOError, match="signature"):
        await _verify(idp, idp.sign(idp.claims("n0nce"), key=stranger))
    good = idp.sign(idp.claims("n0nce"))
    h, p, _ = good.split(".")
    none = b64(json.dumps({"alg": "none"}).encode())
    with pytest.raises(sso.SSOError, match="doesn't accept"):
        await _verify(idp, f"{none}.{p}.")
    hs = b64(json.dumps({"alg": "HS256", "kid": "k1"}).encode())
    with pytest.raises(sso.SSOError, match="doesn't accept"):
        await _verify(idp, f"{hs}.{p}.{b64(b'x' * 32)}")


async def test_a_rotated_key_is_fetched_again(idp):
    await sso.discover(IDP)
    await sso._keys(IDP + "/oauth2/jwks", False)  # cached under the old key
    idp.key, idp.kid = rsa.generate_private_key(public_exponent=65537, key_size=2048), "k2"
    assert (await _verify(idp, idp.sign(idp.claims("n0nce"))))["sub"] == "user_01ADA"


async def test_the_discovery_document_must_name_the_same_issuer(idp):
    idp.discovery_issuer = "https://other.example.com"
    with pytest.raises(sso.SSOError, match="names the issuer"):
        await sso.discover(IDP)


def test_who_may_sign_in():
    s = sso.Settings(IDP, CLIENT, None, ("Ada@Example.com", "user_01BOB"), None)
    assert sso.allowed(s, {"sub": "x", "email": "ada@example.com", "email_verified": True})
    assert not sso.allowed(s, {"sub": "x", "email": "ada@example.com", "email_verified": False})
    assert not sso.allowed(s, {"sub": "x", "email": "ada@example.com"})  # unverified unless it says so
    assert sso.allowed(s, {"sub": "user_01BOB"})
    assert not sso.allowed(s, {"sub": "user_01bob"})  # subjects are exact
    assert not sso.allowed(s, {"sub": "ada@example.com"})  # an email entry never matches a subject


def test_what_an_issuer_may_be():
    assert sso.issuer_problem("https://login.example.com") is None
    assert sso.issuer_problem("http://127.0.0.1:9000/application/o/memoreei") is None
    assert sso.issuer_problem("http://localhost:9000") is None
    assert sso.issuer_problem("http://login.example.com") is not None
    assert sso.issuer_problem("https://u:p@login.example.com") is not None
    assert sso.issuer_problem("login.example.com") is not None
    assert sso.allow_problem(()) is not None and sso.allow_problem(("a b",)) is not None


# ── signing in ───────────────────────────────────────────────────────────────


def test_signing_in_by_sso_away_from_the_computer(server, idp):
    _sso_on(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    r = remote.get("/admin/keys")
    assert r.status_code == 401 and "Sign in with Example" in r.text and 'name="password"' not in r.text
    assert "/admin/sso/start?next=/admin/keys" in r.text
    r = remote.get("/admin/sso/start?next=/admin/keys", follow_redirects=False)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert "memoreei_sso=" in cookie and "Path=/admin/sso" in cookie and "HttpOnly" in cookie
    code, state = idp.authorize(r.headers["location"])
    assert idp.codes[code]["redirect_uri"] == LAN_BASE + "/admin/sso/callback"
    r = remote.get(f"/admin/sso/callback?code={code}&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/keys"
    assert idp.token_requests[-1]["client_secret"] == "s3cret"
    assert remote.get("/admin/keys").status_code == 200
    rows = _account(server.db_path, lambda s: s.signins())
    assert rows[0]["ok"] == 1 and rows[0]["how"] == "sso" and "ada@example.com" in rows[0]["why"]
    sessions = _account(server.db_path, lambda s: s.sessions())
    assert [s["how"] for s in sessions] == ["sso"]
    assert sessions[0]["expires_at"] - sessions[0]["created_at"] == 7 * 24 * 3600


def test_from_anywhere_still_needs_the_switch(server, idp):
    _sso_on(server.db_path)
    remote = server(client=LAN, base_url=LAN_BASE)
    assert remote.get("/admin/").status_code == 403
    _account(server.db_path, lambda s: s.set_remote(True))
    assert remote.get("/admin/").status_code == 401


def test_a_public_client_sends_no_secret(server, idp):
    _sso_on(server.db_path, remote=True, secret=None)
    remote = server(client=LAN, base_url=LAN_BASE)
    assert _sso_sign_in(remote, idp).status_code == 303
    assert "client_secret" not in idp.token_requests[-1] and idp.token_requests[-1]["client_id"] == CLIENT


def test_someone_not_allowed_is_refused_and_recorded(server, idp):
    _sso_on(server.db_path, remote=True)
    idp.person = {"sub": "user_01EVE", "email": "eve@example.com", "email_verified": True}
    remote = server(client=LAN, base_url=LAN_BASE)
    r = _sso_sign_in(remote, idp)
    assert r.status_code == 403 and "eve@example.com" in r.text and "isn&#39;t allowed" in r.text
    assert remote.get("/admin/").status_code == 401
    rows = _account(server.db_path, lambda s: s.signins())
    assert rows[0]["ok"] == 0 and "isn't allowed" in rows[0]["why"]


def test_an_unverified_email_is_not_enough(server, idp):
    _sso_on(server.db_path, remote=True)
    idp.person = {**ADA, "email_verified": False}
    assert _sso_sign_in(server(client=LAN, base_url=LAN_BASE), idp).status_code == 403


def test_a_subject_is_allowed_by_itself(server, idp):
    _sso_on(server.db_path, allow="user_01ADA", remote=True)
    idp.person = {"sub": "user_01ADA"}  # no email at all
    assert _sso_sign_in(server(client=LAN, base_url=LAN_BASE), idp).status_code == 303


def test_a_sign_in_finishes_once_and_only_in_the_browser_that_started_it(server, idp):
    _sso_on(server.db_path, remote=True)
    mine = server(client=LAN, base_url=LAN_BASE)
    r = mine.get("/admin/sso/start", follow_redirects=False)
    code, state = idp.authorize(r.headers["location"])
    other = server(client=LAN, base_url=LAN_BASE)  # someone handed my callback link
    r = other.get(f"/admin/sso/callback?code={code}&state={state}", follow_redirects=False)
    assert r.status_code == 403 and "different browser" in r.text
    # and the state is spent: not even the right browser can use it now
    r = mine.get(f"/admin/sso/callback?code={code}&state={state}", follow_redirects=False)
    assert r.status_code == 403 and "lapsed" in r.text
    assert mine.get("/admin/").status_code == 401


def test_a_sign_in_lapses_after_ten_minutes(server, idp, monkeypatch):
    _sso_on(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    r = remote.get("/admin/sso/start", follow_redirects=False)
    code, state = idp.authorize(r.headers["location"])
    sso._pending[state]["at"] -= sso.PENDING_TTL + 1
    r = remote.get(f"/admin/sso/callback?code={code}&state={state}", follow_redirects=False)
    assert r.status_code == 403 and "too long" in r.text


def test_the_provider_saying_no_is_shown(server, idp):
    _sso_on(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    r = remote.get("/admin/sso/start", follow_redirects=False)
    _, state = idp.authorize(r.headers["location"])
    r = remote.get(f"/admin/sso/callback?error=access_denied&state={state}", follow_redirects=False)
    assert r.status_code == 403 and "Example didn&#39;t sign you in" in r.text
    assert not idp.token_requests


def test_a_changed_code_verifier_is_refused_by_the_provider(server, idp):
    _sso_on(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    r = remote.get("/admin/sso/start", follow_redirects=False)
    code, state = idp.authorize(r.headers["location"])
    sso._pending[state]["verifier"] = "not-the-one"
    r = remote.get(f"/admin/sso/callback?code={code}&state={state}", follow_redirects=False)
    assert r.status_code == 403


def test_failed_sso_sign_ins_count_against_the_address(server, idp, monkeypatch):
    from memoreei.admin.account import SignInLimits
    import memoreei.admin.app as admin_app_module

    monkeypatch.setattr(admin_app_module, "SIGNIN_LIMITS", SignInLimits(per_address=(2, 900)))
    _sso_on(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    for _ in range(2):
        assert remote.get("/admin/sso/callback?code=x&state=nope", follow_redirects=False).status_code == 403
    r = remote.get("/admin/sso/start", follow_redirects=False)
    assert r.status_code == 429


def test_a_provider_that_cannot_be_reached_says_so(server, idp, monkeypatch):
    async def down(*a, **k):
        raise sso.SSOError("The discovery document at …: ConnectError")

    monkeypatch.setattr(sso, "_request", down)
    _sso_on(server.db_path, remote=True)
    r = server(client=LAN, base_url=LAN_BASE).get("/admin/sso/start", follow_redirects=False)
    assert r.status_code == 502 and "couldn&#39;t reach Example" in r.text


# ── one or the other ─────────────────────────────────────────────────────────


def test_setting_up_sso_removes_the_password_and_2fa(server, idp):
    _set_password(server.db_path, remote=True, totp=True)
    client = server()
    assert _sign_in(client, ORIGIN).status_code == 401  # 2FA on, no code: just checking it's there
    _sso_on(server.db_path)
    account = _account(server.db_path, lambda s: s.account())
    assert account["password_hash"] is None and account["totp_secret"] is None
    assert account["remote"] == 1  # from anywhere stays: SSO is the way in from there now


def test_a_password_left_beside_sso_is_removed_at_start_and_never_works(server, idp):
    from memoreei.config import save_settings

    _set_password(server.db_path, remote=True)
    save_settings([("MEMOREEI_SSO_ISSUER", IDP), ("MEMOREEI_SSO_CLIENT_ID", CLIENT),
                   ("MEMOREEI_SSO_ALLOW", "ada@example.com")])  # the config file, edited by hand
    remote = server(client=LAN, base_url=LAN_BASE)  # the server starts
    assert _sign_in(remote, LAN_ORIGIN).status_code == 401
    assert not _account(server.db_path, lambda s: s.has_password())


def test_a_password_session_ends_when_sso_comes_on(server, idp):
    _set_password(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    assert _sign_in(remote, LAN_ORIGIN).status_code == 303
    assert remote.get("/admin/").status_code == 200
    _sso_on(server.db_path)
    assert remote.get("/admin/").status_code == 401


def test_setting_a_password_turns_sso_off(server, idp):
    _sso_on(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    assert _sso_sign_in(remote, idp).status_code == 303
    local = server()
    local.get(f"/admin/login?token={_login_token(server.db_path)}")
    r = local.get("/admin/security")
    assert "Sign-in by SSO" in r.text and IDP in r.text and "ada@example.com" in r.text
    assert "Two-step sign-in" not in r.text
    r = local.post("/admin/security/password", data={"password": PASSWORD, "confirm": PASSWORD},
                   headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    assert "Sign-in by SSO is off" in local.get("/admin/security").text
    assert sso.settings().issuer is None and not sso.configured()
    assert remote.get("/admin/").status_code == 401  # its SSO session is over
    assert _account(server.db_path, lambda s: s.remote_open())  # from anywhere stays, to the password now
    assert _sign_in(remote, LAN_ORIGIN).status_code == 303


def test_turning_sso_off_is_for_the_computer(server, idp):
    _sso_on(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    assert _sso_sign_in(remote, idp).status_code == 303
    assert remote.post("/admin/security/sso/off", headers=LAN_ORIGIN).status_code == 403
    assert remote.post("/admin/security/password", data={"password": PASSWORD, "confirm": PASSWORD},
                       headers=LAN_ORIGIN).status_code == 403
    local = server()
    local.get(f"/admin/login?token={_login_token(server.db_path)}")
    assert local.post("/admin/security/sso/off", headers=ORIGIN, follow_redirects=False).status_code == 303
    assert not sso.configured()
    assert remote.get("/admin/").status_code == 403  # no password: the computer only again
    assert not _account(server.db_path, lambda s: s.account())["remote"]


def test_the_computer_answers_either_way(server, idp):
    _sso_on(server.db_path)
    local = server()
    r = local.get("/admin/")
    assert r.status_code == 401 and "Sign in with Example" in r.text and "memoreei admin-url" in r.text
    r = local.get(f"/admin/login?token={_login_token(server.db_path)}", follow_redirects=False)
    assert r.status_code == 303 and local.get("/admin/").status_code == 200
    home = local.get("/admin/").text  # the welcome's password step is settled by SSO, not offered
    assert "you sign in by SSO instead" in home and "Skip it, and the dashboard answers only" not in home


def test_through_the_public_listener(server, idp, public_gate):
    _sso_on(server.db_path)
    assert public_gate.get("/admin/").status_code == 404  # until it's opened from anywhere
    _account(server.db_path, lambda s: s.set_remote(True))
    r = public_gate.get("/admin/")
    assert r.status_code == 401 and "Sign in with Example" in r.text and "memoreei admin sso off" in r.text
    r = public_gate.get("/admin/sso/start", follow_redirects=False)
    assert "Secure" in r.headers["set-cookie"]
    code, state = idp.authorize(r.headers["location"])
    assert idp.codes[code]["redirect_uri"] == ISSUER + "/admin/sso/callback"
    r = public_gate.get(f"/admin/sso/callback?code={code}&state={state}", follow_redirects=False)
    assert r.status_code == 303 and "Secure" in r.headers["set-cookie"]
    assert public_gate.get("/admin/").status_code == 200
    rows = _account(server.db_path, lambda s: s.signins())
    assert rows[0]["peer"] == "203.0.113.50"  # the caller, not the tunnel


async def test_sessions_from_before_sso_are_kept_when_the_table_is_made_again(tmp_path):
    from memoreei.storage.database import Database
    from memoreei.storage.key import connect

    path = str(tmp_path / "old.db")
    async with Database(db_path=path):
        pass
    conn = connect(path)  # as 0.5.0rc3 left it
    conn.execute("DROP TABLE admin_sessions")
    conn.execute("CREATE TABLE admin_sessions (token_hash TEXT PRIMARY KEY, how TEXT NOT NULL CHECK "
                 "(how IN ('link', 'password')), peer TEXT, user_agent TEXT, created_at INTEGER NOT NULL, "
                 "expires_at INTEGER NOT NULL)")
    conn.execute("INSERT INTO admin_sessions VALUES ('h', 'password', 'p', 'ua', 1, 2)")
    conn.commit()
    conn.close()
    async with Database(db_path=path) as db:
        rows = await db._db.execute_fetchall("SELECT token_hash, how FROM admin_sessions")
        assert [tuple(r) for r in rows] == [("h", "password")]
        await db._db.execute("INSERT INTO admin_sessions VALUES ('s', 'sso', NULL, NULL, 1, 2)")
        tables = await db._db.execute_fetchall("SELECT name FROM sqlite_master WHERE name LIKE 'admin_sessions%'")
        assert [r[0] for r in tables] == ["admin_sessions"]


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_the_cli_sets_sso_up_and_a_password_turns_it_off(server, idp):
    _set_password(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)  # the server is running all along
    assert 'name="password"' in remote.get("/admin/").text
    r = runner.invoke(app, ["admin", "sso", "set", "--issuer", IDP, "--client-id", CLIENT,
                            "--allow", "ada@example.com, user_01BOB", "--name", "Example", "--secret-stdin"],
                      input="s3cret\n")
    assert r.exit_code == 0, r.output
    assert "Sign-in by SSO is on. The password and 2FA are removed." in r.output
    assert "/admin/sso/callback" in r.output
    s = sso.settings(with_secret=True)
    assert s.issuer == IDP and s.allow == ("ada@example.com", "user_01BOB") and s.client_secret == "s3cret"
    from memoreei.config import config_env_path

    assert "s3cret" not in config_env_path().read_text()  # the secret lives in the database
    # The running server follows at once, without a restart.
    assert "Sign in with Example" in remote.get("/admin/").text
    assert _sso_sign_in(remote, idp).status_code == 303
    assert idp.token_requests[-1]["client_secret"] == "s3cret"
    r = runner.invoke(app, ["admin", "status"])
    assert "Sign-in: by SSO, with Example" in r.output and "user_01BOB" in r.output and "no secret" not in r.output
    assert "(by SSO, as ada@example.com" in r.output and "sso: " not in r.output  # how, said once
    r = runner.invoke(app, ["admin", "password", "--stdin"], input=PASSWORD + "\n")
    assert r.exit_code == 0 and "Sign-in by SSO is off" in r.output
    assert not sso.configured()
    assert remote.get("/admin/").status_code == 401  # its SSO session ended with SSO
    assert 'name="password"' in remote.get("/admin/").text


def test_sso_from_the_environment_cannot_be_changed_here(server, idp, monkeypatch):
    import memoreei.config as config_module

    for name, value in (("MEMOREEI_SSO_ISSUER", IDP), ("MEMOREEI_SSO_CLIENT_ID", CLIENT),
                        ("MEMOREEI_SSO_ALLOW", "ada@example.com")):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(config_module, "_process_env_keys",
                        frozenset({"MEMOREEI_SSO_ISSUER", "MEMOREEI_SSO_CLIENT_ID", "MEMOREEI_SSO_ALLOW"}))
    assert sso.configured()
    r = runner.invoke(app, ["admin", "password", "--stdin"], input=PASSWORD + "\n")
    assert r.exit_code == 1 and "environment" in r.output
    assert runner.invoke(app, ["admin", "sso", "off"]).exit_code == 1
    local = server()
    local.get(f"/admin/login?token={_login_token(server.db_path)}")
    r = local.post("/admin/security/password", data={"password": PASSWORD, "confirm": PASSWORD}, headers=ORIGIN)
    assert r.status_code == 400 and "environment" in r.text
    r = local.post("/admin/security/sso/off", headers=ORIGIN)
    assert r.status_code == 400 and "environment" in r.text
    assert sso.configured() and not _account(server.db_path, lambda s: s.has_password())


def test_an_issuer_ending_in_a_slash_is_kept_as_it_is(server, idp):
    """Authentik's issuer is …/application/o/<slug>/, and its tokens say so exactly."""
    issuer = "https://login.example.com/application/o/memoreei/"
    idp.issuer = issuer
    r = runner.invoke(app, ["admin", "sso", "set", "--issuer", issuer, "--client-id", CLIENT,
                            "--allow", "ada@example.com", "--secret-stdin"], input="s3cret\n")
    assert r.exit_code == 0, r.output
    assert sso.settings().issuer == issuer
    _account(server.db_path, lambda s: s.set_remote(True))
    assert _sso_sign_in(server(client=LAN, base_url=LAN_BASE), idp).status_code == 303


def test_two_tabs_can_each_finish(server, idp):
    _sso_on(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    first = idp.authorize(remote.get("/admin/sso/start", follow_redirects=False).headers["location"])
    second = idp.authorize(remote.get("/admin/sso/start", follow_redirects=False).headers["location"])
    for code, state in (first, second):
        r = remote.get(f"/admin/sso/callback?code={code}&state={state}", follow_redirects=False)
        assert r.status_code == 303, r.text


def test_pending_sign_ins_are_capped_per_address_and_in_all(server, idp, monkeypatch):
    _sso_on(server.db_path, remote=True)
    monkeypatch.setattr(sso, "MAX_PENDING", 8)
    remote = server(client=LAN, base_url=LAN_BASE)
    for _ in range(sso.PER_ADDRESS_PENDING + 2):  # one address only ever holds five: its oldest give way
        assert remote.get("/admin/sso/start", follow_redirects=False).status_code == 303
    assert sum(p["peer"] == LAN[0] for p in sso._pending.values()) == sso.PER_ADDRESS_PENDING

    def start_from(last_octet: int) -> int:
        client = server(client=(f"192.168.1.{last_octet}", 5000), base_url=LAN_BASE)
        return client.get("/admin/sso/start", follow_redirects=False).status_code

    assert [start_from(n) for n in (30, 31, 32)] == [303, 303, 303]
    assert len(sso._pending) == 8
    assert start_from(33) == 429  # full: a newcomer waits
    assert remote.get("/admin/sso/start", follow_redirects=False).status_code == 303  # replaces its own
    # A browser that has signed in before gets through even when it's full.
    assert _sso_sign_in(remote, idp).status_code == 303
    remote.cookies.delete("memoreei_admin", path="/admin")
    assert start_from(34) == 303  # (the sign-in above freed one)
    assert len(sso._pending) == 8 and start_from(35) == 429
    assert remote.get("/admin/sso/start", follow_redirects=False).status_code == 303


def test_a_blocked_address_is_not_counted_again(server, idp, monkeypatch):
    from memoreei.admin.account import SignInLimits
    import memoreei.admin.app as admin_app_module

    limits = SignInLimits(per_address=(2, 900))
    monkeypatch.setattr(admin_app_module, "SIGNIN_LIMITS", limits)
    _sso_on(server.db_path, remote=True)
    remote = server(client=LAN, base_url=LAN_BASE)
    for _ in range(2):
        remote.get("/admin/sso/callback?code=x&state=nope")
    for _ in range(3):
        assert remote.get("/admin/sso/callback?code=x&state=nope").status_code == 429
    assert len(limits.address.seen[LAN[0]]) == 2


def test_the_cli_refuses_a_plain_http_issuer_and_needs_a_secret(server, idp):
    r = runner.invoke(app, ["admin", "sso", "set", "--issuer", "http://login.example.com", "--client-id", CLIENT,
                            "--allow", "ada@example.com", "--public"])
    assert r.exit_code == 1 and "https" in r.output
    r = runner.invoke(app, ["admin", "sso", "set", "--issuer", IDP, "--client-id", CLIENT,
                            "--allow", "ada@example.com", "--secret-stdin"], input="\n")
    assert r.exit_code == 1 and "--public" in r.output
    r = runner.invoke(app, ["admin", "sso", "off"])
    assert r.exit_code == 0
