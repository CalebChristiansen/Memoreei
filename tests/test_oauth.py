"""memoreei's own OAuth sign-in (memoreei.oauth): chatbots ask, the person at the
computer approves, and tokens are memoreei's own."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from starlette.testclient import TestClient

from memoreei.oauth import clients, notify, store as store_module, tokens
from memoreei.oauth.clients import DEVICE_GRANT, ClientError
from memoreei.oauth.server import Rejected, SignIn
from memoreei.oauth.store import OAuthStore
from memoreei.storage.database import Database

HOST = "abcdefghjk23.devices.example.test"
ISSUER = f"https://{HOST}"
RESOURCE = f"{ISSUER}/mcp"
CLAUDE = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"
GPT = "https://chatgpt.com/oauth/abc123/client.json"
GPT_CB = "https://chatgpt.com/connector/oauth/abc123"
GPT_JWKS = "https://chatgpt.com/oauth/jwks.json"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


GPT_KEY = rsa.generate_private_key(65537, 2048)


def jwk(key, kid="k1"):
    n = key.public_key().public_numbers()
    return {"kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
            "n": b64url(n.n.to_bytes(256, "big")), "e": b64url(n.e.to_bytes(3, "big"))}


DOCS = {
    CLAUDE: {"client_id": CLAUDE, "client_name": "Claude", "redirect_uris": [CLAUDE_CB],
             "grant_types": ["authorization_code", "refresh_token"], "token_endpoint_auth_method": "none"},
    GPT: {"client_id": GPT, "client_name": "ChatGPT", "redirect_uris": [GPT_CB],
          "token_endpoint_auth_method": "private_key_jwt", "jwks_uri": GPT_JWKS},
    GPT_JWKS: {"keys": [jwk(GPT_KEY)]},
}
# What ChatGPT's document really says (2026-10-14): private_key_jwt, but none too, and it sends none.
GPT_REAL = "https://chatgpt.com/oauth/3Of7nZA9hfBi/client.json"
GPT_REAL_CB = "https://chatgpt.com/connector/oauth/3Of7nZA9hfBi"
DOCS[GPT_REAL] = {**DOCS[GPT], "client_id": GPT_REAL, "redirect_uris": [GPT_REAL_CB],
                  "token_endpoint_auth_methods_supported": ["none", "private_key_jwt"]}
HERMES_CIMD = "https://nousresearch.github.io/hermes-agent/docs/oauth/client-metadata.json"
DOCS[HERMES_CIMD] = {"client_id": HERMES_CIMD, "client_name": "Hermes Agent", "application_type": "native",
                     "grant_types": ["authorization_code", "refresh_token"], "token_endpoint_auth_method": "none",
                     "redirect_uris": [f"http://{h}:{p}/callback" for p in range(27890, 27895)
                                       for h in ("127.0.0.1", "localhost")]}


async def fake_fetch(url, max_bytes=65536):
    if url not in DOCS:
        raise ClientError(f"{url} answered 404")
    return DOCS[url]


def assertion(key=GPT_KEY, **over):
    claims = {"iss": GPT, "sub": GPT, "aud": f"{ISSUER}/token", "exp": int(time.time()) + 120,
              "jti": str(uuid.uuid4()), **over}
    h = b64url(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    p = b64url(json.dumps(claims).encode())
    sig = key.sign(f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{b64url(sig)}"


class Env:
    """A SignIn on a fresh database, a browser, and a hand on the computer's keyboard."""

    def __init__(self, tmp_path):
        self.db = Database(str(tmp_path / "oauth.db"))
        self._store: OAuthStore | None = None
        self.notified: list[dict] = []
        self.clock = [time.time()]

        async def notify_(row):
            self.notified.append(row)

        self.signin = SignIn(self.store, ISSUER, RESOURCE, fetch=fake_fetch, notify=notify_,
                             clock=lambda: self.clock[0])

    async def store(self) -> OAuthStore:
        if self._store is None:
            await self.db.connect()
            self._store = OAuthStore(self.db)
        return self._store

    def run(self, fn, *args):
        """Run `await fn(store, *args)` on the app's own event loop."""
        async def go():
            return await fn(await self.store(), *args)
        return self.http.portal.call(go)

    def approve(self, code, yes=True):
        async def go(store, code):
            row = await store.waiting_by_code(code)
            assert row is not None, f"nothing waiting with {code}"
            assert await store.decide(row["id"], yes)
        self.run(go, code)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    clients._jwks_cache.clear()
    with TestClient(e.signin, base_url=ISSUER, follow_redirects=False) as http:
        e.http = http
        yield e
        if e._store is not None:
            http.portal.call(e.db.close)


def pkce():
    verifier = secrets.token_urlsafe(48)
    return verifier, b64url(hashlib.sha256(verifier.encode()).digest())


def authorize(http, client=CLAUDE, redirect=CLAUDE_CB, challenge=None, **over):
    params = {"response_type": "code", "client_id": client, "redirect_uri": redirect, "state": "xyz",
              "code_challenge": challenge or pkce()[1], "code_challenge_method": "S256",
              "resource": RESOURCE, **over}
    return http.get("/authorize", params={k: v for k, v in params.items() if v is not None})


def query(response):
    return {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}


def shown_code(page_text):
    return re.search(r"<p class=code>(\w+)</p>", page_text).group(1)


def ask(env, http=None, client=CLAUDE, redirect=CLAUDE_CB):
    """Start a sign-in and land on the waiting page: (wait URL, code, PKCE verifier)."""
    http = http or env.http
    verifier, challenge = pkce()
    r = authorize(http, client, redirect, challenge)
    assert r.status_code == 303, r.text
    wait = r.headers["location"]
    assert wait.startswith("/authorize/wait?request=")
    r = http.get(wait)
    assert r.status_code == 200 and "Approve it on your computer" in r.text
    return wait, shown_code(r.text), verifier


def connect(env, client=CLAUDE, redirect=CLAUDE_CB):
    """The whole browser flow, approved: (authorization code, verifier)."""
    wait, code, verifier = ask(env, client=client, redirect=redirect)
    env.approve(code)
    r = env.http.get(wait)
    assert r.status_code == 303 and r.headers["location"].startswith(redirect)
    q = query(r)
    assert q["state"] == "xyz" and q["iss"] == ISSUER
    return q["code"], verifier


def token(http, **form):
    return http.post("/token", data=form)


def tokens_for(env, client=CLAUDE, redirect=CLAUDE_CB):
    code, verifier = connect(env, client, redirect)
    r = token(env.http, grant_type="authorization_code", code=code, client_id=client, code_verifier=verifier,
              redirect_uri=redirect, resource=RESOURCE)
    assert r.status_code == 200, r.text
    return r.json()


def verify(env, access):
    return env.http.portal.call(env.signin.verify, access)


# ── the happy path, Claude's way ─────────────────────────────────────────────


def test_claude_asks_the_person_approves_and_it_connects(env):
    wait, code, verifier = ask(env)
    assert len(code) == 4
    # The person's computer heard about it, and the request names who's asking.
    [row] = env.notified
    assert row["user_code"] == code and row["client_name"] == "Claude" and row["client_host"] == "claude.ai"
    # Until they answer, the page keeps waiting (and reloads itself).
    r = env.http.get(wait)
    assert r.status_code == 200 and code in r.text and "http-equiv=refresh" in r.text
    env.approve(code)
    r = env.http.get(wait)
    assert r.status_code == 303
    q = query(r)
    r = token(env.http, grant_type="authorization_code", code=q["code"], client_id=CLAUDE, code_verifier=verifier,
              redirect_uri=CLAUDE_CB)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 600
    assert body["access_token"].startswith("mat_") and body["refresh_token"].startswith("mrt_")
    caller = verify(env, body["access_token"])
    assert caller["kind"] == "oauth" and caller["client"] == "Claude"
    # The waiting page has done its job: the cookie is gone and so is the request.
    assert "expired" in env.http.get(wait).text


def test_metadata(env):
    m = env.http.get("/.well-known/oauth-authorization-server").json()
    assert m["issuer"] == ISSUER and m["authorization_endpoint"] == f"{ISSUER}/authorize"
    assert m["token_endpoint"] == f"{ISSUER}/token" and m["device_authorization_endpoint"] == f"{ISSUER}/device_authorization"
    assert m["client_id_metadata_document_supported"] is True and m["code_challenge_methods_supported"] == ["S256"]
    assert set(m["token_endpoint_auth_methods_supported"]) == {"none", "private_key_jwt"}
    assert "jwks_uri" not in m  # nothing outside memoreei checks its tokens


def test_saying_no(env):
    wait, code, _ = ask(env)
    env.approve(code, yes=False)
    r = env.http.get(wait)
    assert r.status_code == 303 and query(r)["error"] == "access_denied"


def test_cancelling_in_the_browser(env):
    wait, code, _ = ask(env)
    request_id = parse_qs(urlsplit(wait).query)["request"][0]
    r = env.http.post("/authorize/cancel", data={"request": request_id})
    assert r.status_code == 303 and query(r)["error"] == "access_denied"
    assert env.run(lambda s: s.waiting()) == []


def test_nobody_answering_in_time(env, monkeypatch):
    wait, code, _ = ask(env)
    later = int(time.time()) + store_module.REQUEST_TTL + 5
    monkeypatch.setattr(store_module, "now", lambda: later)
    monkeypatch.setattr("memoreei.oauth.server.now", lambda: later)
    r = env.http.get(wait)
    assert r.status_code == 303 and "in time" in query(r)["error_description"]
    with pytest.raises(AssertionError, match="nothing waiting"):
        env.approve(code)


def test_only_the_browser_that_asked_can_collect(env):
    wait, code, _ = ask(env)
    env.approve(code)
    mine = dict(env.http.cookies)
    env.http.cookies.clear()  # another browser: no cookie
    r = env.http.get(wait)
    assert r.status_code == 400 and "expired" in r.text
    env.http.cookies.update(mine)
    assert env.http.get(wait).status_code == 303  # the right one still can


def test_an_answer_is_given_once(env):
    _, code, _ = ask(env)
    row = env.run(lambda s, c: s.waiting_by_code(c), code)
    assert env.run(lambda s: s.decide(row["id"], True))
    assert not env.run(lambda s: s.decide(row["id"], False))


# ── bad requests ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("over", [
    {"client_id": None}, {"client_id": "https://claude.ai/nope"}, {"client_id": "mcl_unknown"},
    {"redirect_uri": "https://evil.example/cb"},
])
def test_bad_client_or_redirect_is_our_page(env, over):
    r = authorize(env.http, **over)
    assert r.status_code == 400 and "text/html" in r.headers["content-type"]
    assert "location" not in r.headers and env.notified == []


@pytest.mark.parametrize("over,error", [
    ({"response_type": "token"}, "unsupported_response_type"),
    ({"code_challenge_method": "plain"}, "invalid_request"),
    ({"code_challenge": "short"}, "invalid_request"),
    ({"resource": "https://someone-else.devices.example.test/mcp"}, "invalid_target"),
    ({"resource": f"{ISSUER}/admin"}, "invalid_target"),
])
def test_bad_request_goes_back_with_an_error(env, over, error):
    r = authorize(env.http, **over)
    assert r.status_code == 303 and query(r)["error"] == error and query(r)["iss"] == ISSUER
    assert env.notified == []


def test_resource_may_be_left_out(env):
    assert authorize(env.http, resource=None).status_code == 303


def test_the_waiting_page_cant_be_framed_and_posts_only_home_or_back(env):
    wait, _, _ = ask(env)
    csp = env.http.get(wait).headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "form-action 'self' https://claude.ai" in csp


def test_too_many_waiting(env):
    for _ in range(store_module.MAX_WAITING_PER_ADDRESS):
        ask(env)
    r = authorize(env.http)
    assert r.status_code == 303 and query(r)["error"] == "temporarily_unavailable"


# ── codes ────────────────────────────────────────────────────────────────────


def test_wrong_verifier(env):
    code, _ = connect(env)
    r = token(env.http, grant_type="authorization_code", code=code, client_id=CLAUDE, code_verifier="x" * 43)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_a_code_used_twice_revokes_the_grant(env):
    code, verifier = connect(env)
    form = dict(grant_type="authorization_code", code=code, client_id=CLAUDE, code_verifier=verifier)
    first = token(env.http, **form).json()
    r = token(env.http, **form)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    with pytest.raises(Rejected):
        verify(env, first["access_token"])
    r = token(env.http, grant_type="refresh_token", refresh_token=first["refresh_token"], client_id=CLAUDE)
    assert r.json()["error"] == "invalid_grant"


def test_a_code_for_one_client_is_useless_to_another(env):
    code, verifier = connect(env)
    r = token(env.http, grant_type="authorization_code", code=code, client_id=GPT_REAL, code_verifier=verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_an_expired_code(env, monkeypatch):
    code, verifier = connect(env)
    later = int(time.time()) + 120
    monkeypatch.setattr(store_module, "now", lambda: later)
    r = token(env.http, grant_type="authorization_code", code=code, client_id=CLAUDE, code_verifier=verifier)
    assert r.json()["error"] == "invalid_grant"


def test_resource_at_the_token_endpoint_must_match(env):
    code, verifier = connect(env)
    r = token(env.http, grant_type="authorization_code", code=code, client_id=CLAUDE, code_verifier=verifier,
              resource="https://other.devices.example.test/mcp")
    assert r.json()["error"] == "invalid_target"


def test_secrets_and_duplicates_are_refused(env):
    r = env.http.post("/token", data={"grant_type": "authorization_code", "client_id": CLAUDE},
                      headers={"Authorization": "Basic Zm9vOmJhcg=="})
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"
    r = env.http.post("/token", content="grant_type=a&grant_type=b&client_id=x",
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400 and "twice" in r.json()["error_description"]


# ── refresh tokens: signed, numbered, rotated ────────────────────────────────


def refresh(env, rt, client=CLAUDE):
    return token(env.http, grant_type="refresh_token", refresh_token=rt, client_id=client)


def test_refresh_rotates_and_a_stolen_token_ends_it(env):
    first = tokens_for(env)
    r = refresh(env, first["refresh_token"])
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first["refresh_token"]
    third = refresh(env, second["refresh_token"]).json()
    assert verify(env, third["access_token"])["client"] == "Claude"
    # An old one comes back long after it was replaced: someone else has it.
    env.clock[0] += 60
    r = refresh(env, first["refresh_token"])
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    # So the whole family goes: the newest refresh token, and the live access tokens.
    assert refresh(env, third["refresh_token"]).json()["error"] == "invalid_grant"
    with pytest.raises(Rejected):
        verify(env, third["access_token"])


def test_the_token_before_the_newest_works_for_thirty_seconds(env, monkeypatch):
    first = tokens_for(env)
    refresh(env, first["refresh_token"])
    # The answer was lost, or two refreshes raced: the old token again, straight away.
    r = refresh(env, first["refresh_token"])
    assert r.status_code == 200
    again = r.json()
    # It gets the newest generation, without moving it on: both of these work once more.
    assert refresh(env, again["refresh_token"]).status_code == 200
    # But not after the grace.
    fresh = tokens_for(env)
    nxt = refresh(env, fresh["refresh_token"]).json()
    later = int(time.time()) + store_module.REFRESH_GRACE + 5
    env.clock[0] = later
    assert refresh(env, fresh["refresh_token"]).json()["error"] == "invalid_grant"
    assert refresh(env, nxt["refresh_token"]).json()["error"] == "invalid_grant"


def test_a_refresh_token_idle_a_month_stops(env):
    first = tokens_for(env)
    env.clock[0] += tokens.REFRESH_IDLE + 10
    r = refresh(env, first["refresh_token"])
    assert r.json()["error"] == "invalid_grant" and "expired" in r.json()["error_description"]


def test_a_forged_or_tampered_refresh_token_is_refused(env):
    first = tokens_for(env)
    rt = first["refresh_token"]
    payload, _, mac = rt[4:].partition(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["n"] += 5
    forged = "mrt_" + b64url(json.dumps(claims).encode()) + "." + mac
    assert refresh(env, forged).json()["error"] == "invalid_grant"
    assert refresh(env, tokens.sign(claims["k"], b"k" * 32, claims["g"], 0, int(time.time()))).json()["error"] \
        == "invalid_grant"
    assert refresh(env, "mrt_nonsense").json()["error"] == "invalid_grant"
    # None of that was a reuse: the real one still works.
    assert refresh(env, rt).status_code == 200


def test_a_refresh_token_for_one_client_is_useless_to_another(env):
    first = tokens_for(env)
    assert refresh(env, first["refresh_token"], client=GPT_REAL).json()["error"] == "invalid_grant"


def test_the_database_holds_no_working_token(env):
    first = tokens_for(env)

    async def everything(store):
        out = []
        for table in ("oauth_grants", "oauth_codes", "oauth_access_tokens", "oauth_requests"):
            out += [json.dumps(dict(r), default=str) for r in await store._all(f"SELECT * FROM {table}")]
        return "\n".join(out)

    dump = env.run(everything)
    assert first["access_token"] not in dump and first["refresh_token"] not in dump
    assert first["refresh_token"].split(".")[0][4:] not in dump


# ── removing a chatbot ───────────────────────────────────────────────────────


def test_removing_a_chatbot_cuts_it_off_at_once(env):
    first = tokens_for(env)
    grant_id = verify(env, first["access_token"])["subject"]
    assert env.run(lambda s: s.revoke_grant(grant_id, "removed in the dashboard"))
    with pytest.raises(Rejected):
        verify(env, first["access_token"])  # no ten-minute tail
    assert refresh(env, first["refresh_token"]).json()["error"] == "invalid_grant"
    assert env.run(lambda s: s.grants()) == []


def test_the_client_can_revoke_too(env):
    first = tokens_for(env)
    assert env.http.post("/revoke", data={"token": first["refresh_token"], "client_id": GPT_REAL}).status_code == 200
    assert verify(env, first["access_token"])  # someone else's revoke did nothing
    assert env.http.post("/revoke", data={"token": first["refresh_token"], "client_id": CLAUDE}).status_code == 200
    with pytest.raises(Rejected):
        verify(env, first["access_token"])
    assert env.http.post("/revoke", data={"token": "whatever", "client_id": CLAUDE}).status_code == 200


def test_a_token_for_another_address_is_refused(env, tmp_path):
    first = tokens_for(env)
    other = SignIn(env.store, "https://other.devices.example.test", "https://other.devices.example.test/mcp")
    with pytest.raises(Rejected, match="another address"):
        env.http.portal.call(other.verify, first["access_token"])


def test_grants_list_their_calls(env):
    first = tokens_for(env)
    grant_id = verify(env, first["access_token"])["subject"]

    async def call(store):
        await store.db.record_access({"kind": "oauth", "client": "Claude", "subject": grant_id, "status": 200})
        return await store.grants()

    [g] = env.run(call)
    assert g["calls"] == 1 and g["client_name"] == "Claude" and g["last_used_at"]


# ── ChatGPT, and private_key_jwt ─────────────────────────────────────────────


def test_chatgpt_with_private_key_jwt(env):
    code, verifier = connect(env, GPT, GPT_CB)
    r = token(env.http, grant_type="authorization_code", code=code, code_verifier=verifier,
              client_assertion_type=clients.ASSERTION_TYPE, client_assertion=assertion())
    assert r.status_code == 200, r.text
    assert verify(env, r.json()["access_token"])["client"] == "ChatGPT"


@pytest.mark.parametrize("bad", [
    {"aud": "https://someone.else/token"}, {"exp": int(time.time()) - 600}, {"iss": "https://x.test/c.json"},
    {"key": rsa.generate_private_key(65537, 2048)},
])
def test_chatgpt_must_prove_itself(env, bad):
    code, verifier = connect(env, GPT, GPT_CB)
    key = bad.pop("key", GPT_KEY)
    r = token(env.http, grant_type="authorization_code", code=code, code_verifier=verifier,
              client_assertion_type=clients.ASSERTION_TYPE, client_assertion=assertion(key, **bad))
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"


def test_an_assertion_is_used_once(env):
    code, verifier = connect(env, GPT, GPT_CB)
    a = assertion()
    form = dict(grant_type="authorization_code", code=code, code_verifier=verifier,
                client_assertion_type=clients.ASSERTION_TYPE, client_assertion=a)
    assert token(env.http, **form).status_code == 200
    assert token(env.http, **form).json()["error"] == "invalid_client"


def test_chatgpt_as_it_really_is_sends_none(env):
    code, verifier = connect(env, GPT_REAL, GPT_REAL_CB)
    r = token(env.http, grant_type="authorization_code", code=code, client_id=GPT_REAL, code_verifier=verifier)
    assert r.status_code == 200, r.text


def test_a_jwt_only_client_cant_send_none(env):
    code, verifier = connect(env, GPT, GPT_CB)
    r = token(env.http, grant_type="authorization_code", code=code, client_id=GPT, code_verifier=verifier)
    assert r.status_code == 401 and r.json()["error"] == "invalid_client"


# ── clients that register themselves ─────────────────────────────────────────


def test_a_dcr_client_connects_and_is_shown_as_naming_itself(env):
    r = env.http.post("/register", json={"client_name": "Some IDE", "redirect_uris": ["http://127.0.0.1:33418/cb"],
                                         "token_endpoint_auth_method": "client_secret_basic"})
    assert r.status_code == 201
    reg = r.json()
    assert reg["token_endpoint_auth_method"] == "none" and "client_secret" not in reg
    wait, code, _ = ask(env, client=reg["client_id"], redirect="http://127.0.0.1:33418/cb")
    page = env.http.get(wait).text
    assert "Some IDE" in page and "it named itself" in page
    [row] = env.run(lambda s: s.waiting())
    assert row["self_registered"] == 1 and row["client_host"] == "127.0.0.1"


def test_names_are_cleaned_before_anyone_sees_them(env):
    r = env.http.post("/register", json={"client_name": "Claude‮\n<b>evil</b>",
                                         "redirect_uris": ["https://x.example/cb"]})
    assert r.json()["client_name"] == "Claude <b>evil</b>"
    wait, _, _ = ask(env, client=r.json()["client_id"], redirect="https://x.example/cb")
    assert "<b>evil" not in env.http.get(wait).text  # escaped on the page


@pytest.mark.parametrize("body", [
    {"redirect_uris": ["http://evil.example/cb"]},          # http, not loopback
    {"redirect_uris": ["https://x.example/cb#frag"]},
    {"redirect_uris": []},
    {"redirect_uris": ["https://x.example/cb"], "grant_types": ["implicit"]},
    {"redirect_uris": ["https://x.example/cb"], "token_endpoint_auth_method": "private_key_jwt"},
])
def test_dcr_refuses(env, body):
    r = env.http.post("/register", json=body)
    assert r.status_code == 400 and r.json()["error"] == "invalid_client_metadata"


@pytest.mark.parametrize("redirect,ok", [
    ("http://127.0.0.1:27892/callback", True),          # one it lists
    ("http://127.0.0.1:51234/callback", True),          # a port it doesn't: whichever was free
    ("http://localhost:61000/callback", True),
    ("http://127.0.0.1/callback", True),
    ("http://127.0.0.1:51234/other", False),            # the path still has to match
    ("http://127.0.0.1:51234/callback?x=1", False),
    ("http://[::1]:51234/callback", False),              # and the host, as spelled
    ("https://127.0.0.1:27890/callback", False),
    ("http://127.0.0.1:99999/callback", False),
])
def test_loopback_redirects_may_use_any_port(env, redirect, ok):
    r = authorize(env.http, client=HERMES_CIMD, redirect=redirect)
    assert (r.status_code == 303) == ok, r.text


def test_https_redirects_still_match_exactly(env):
    assert authorize(env.http, redirect="https://claude.ai:8443/api/mcp/auth_callback").status_code == 400


def test_up_to_32_redirect_uris(env):
    uris = [f"http://127.0.0.1:{p}/cb" for p in range(40000, 40033)]
    assert env.http.post("/register", json={"redirect_uris": uris[:32]}).status_code == 201
    assert env.http.post("/register", json={"redirect_uris": uris}).status_code == 400


# ── device codes, for agents (Hermes's --flow device) ────────────────────────


def device_client(env, grants=("urn:ietf:params:oauth:grant-type:device_code", "refresh_token")):
    r = env.http.post("/register", json={"client_name": "Hermes Agent", "grant_types": list(grants)})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def device_start(env, client_id, **over):
    r = env.http.post("/device_authorization", data={"client_id": client_id, "resource": RESOURCE, **over})
    return r


def poll(env, client_id, device_code):
    return token(env.http, grant_type=DEVICE_GRANT, device_code=device_code, client_id=client_id)


def test_hermes_signs_in_by_device_code(env):
    hermes = device_client(env)
    r = device_start(env, hermes)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["verification_uri"] == f"{ISSUER}/device" and d["interval"] == 5 and len(d["user_code"]) == 4
    assert "approve that code on the computer" in env.http.get("/device").text
    [row] = env.notified
    assert row["kind"] == "device" and row["user_code"] == d["user_code"]
    r = poll(env, hermes, d["device_code"])
    assert r.json()["error"] == "authorization_pending"
    env.approve(d["user_code"])
    env.clock[0] += 5
    r = poll(env, hermes, d["device_code"])
    assert r.status_code == 200, r.text
    assert verify(env, r.json()["access_token"])["client"] == "Hermes Agent"
    # Collected once. Again means two parties have the device code: the grant goes.
    env.clock[0] += 5
    first = r.json()
    assert poll(env, hermes, d["device_code"]).json()["error"] == "invalid_grant"
    with pytest.raises(Rejected):
        verify(env, first["access_token"])


def test_polling_too_fast_slows_down(env):
    hermes = device_client(env)
    d = device_start(env, hermes).json()
    assert poll(env, hermes, d["device_code"]).json()["error"] == "authorization_pending"
    r = poll(env, hermes, d["device_code"])
    assert r.json()["error"] == "slow_down" and "10" in r.json()["error_description"]


def test_device_code_denied(env):
    hermes = device_client(env)
    d = device_start(env, hermes).json()
    env.approve(d["user_code"], yes=False)
    assert poll(env, hermes, d["device_code"]).json()["error"] == "access_denied"


def test_device_code_expires(env, monkeypatch):
    hermes = device_client(env)
    d = device_start(env, hermes).json()
    later = int(time.time()) + store_module.REQUEST_TTL + 5
    monkeypatch.setattr(store_module, "now", lambda: later)
    monkeypatch.setattr("memoreei.oauth.server.now", lambda: later)
    assert poll(env, hermes, d["device_code"]).json()["error"] == "expired_token"


def test_only_a_client_that_lists_the_grant_may_ask(env):
    r = device_start(env, CLAUDE)
    assert r.status_code == 400 and r.json()["error"] == "unauthorized_client"
    assert env.notified == []


def test_a_device_only_client_cant_use_the_browser_flow(env):
    hermes = device_client(env)
    r = authorize(env.http, client=hermes, redirect="http://127.0.0.1:1/cb")
    assert r.status_code == 400 and "device code" in r.text


def test_device_codes_dont_cross_between_clients(env):
    hermes, other = device_client(env), device_client(env)
    d = device_start(env, hermes).json()
    env.approve(d["user_code"])
    assert poll(env, other, d["device_code"]).json()["error"] == "invalid_grant"
    assert poll(env, hermes, d["device_code"]).status_code == 200


def test_device_resource_must_be_ours(env):
    r = device_start(env, device_client(env), resource="https://elsewhere.example/mcp")
    assert r.json()["error"] == "invalid_target"


# ── rate limits ──────────────────────────────────────────────────────────────


def test_registration_is_limited(env):
    for _ in range(20):
        assert env.http.post("/register", json={"redirect_uris": ["https://x.example/cb"]}).status_code == 201
    r = env.http.post("/register", json={"redirect_uris": ["https://x.example/cb"]})
    assert r.status_code == 429 and r.headers["retry-after"]


def test_authorize_is_limited(env):
    for _ in range(60):
        authorize(env.http, response_type="token")  # refused, but counted all the same
    assert authorize(env.http).status_code == 429


def test_the_token_endpoint_counts_refusals(env):
    code, verifier = connect(env)
    for _ in range(60):
        assert refresh(env, "mrt_nonsense").status_code == 400
    r = token(env.http, grant_type="authorization_code", code=code, client_id=CLAUDE, code_verifier=verifier)
    assert r.status_code == 429 and r.json()["error"] == "temporarily_unavailable"


# ── fetching client documents ────────────────────────────────────────────────


@pytest.mark.parametrize("addresses", [
    ["127.0.0.1"], ["169.254.169.254"], ["10.20.1.5"], ["192.168.1.1"], ["::1"], ["::ffff:10.20.0.1"],
    ["fd00::1"], ["93.184.215.14", "192.168.1.1"],                          # one bad apple
])
def test_fetch_refuses_private_addresses(monkeypatch, addresses):
    async def resolve(host):
        return addresses
    monkeypatch.setattr(clients, "resolve", resolve)
    with pytest.raises(ClientError, match="won't fetch"):
        asyncio.run(clients.fetch_json("https://claude.ai/oauth/mcp-oauth-client-metadata"))


@pytest.mark.parametrize("url", [
    "http://claude.ai/x", "https://claude.ai:8443/x", "https://user@claude.ai/x", "https://127.0.0.1/x",
    "https://claude.ai/x#f",
])
def test_fetch_refuses_odd_urls(url):
    with pytest.raises(ClientError):
        asyncio.run(clients.fetch_json(url))


async def test_a_cimd_document_must_name_itself(temp_db):
    async def liar(url, max_bytes=65536):
        return {**DOCS[CLAUDE], "client_id": "https://claude.ai/someone-else"}
    with pytest.raises(ClientError, match="its own URL"):
        await clients.load(OAuthStore(temp_db), CLAUDE, liar)


async def test_cimd_is_cached_and_survives_an_outage(temp_db, monkeypatch):
    store = OAuthStore(temp_db)
    calls = []

    async def fetch(url, max_bytes=65536):
        calls.append(url)
        if len(calls) > 1:
            raise ClientError("down")
        return DOCS[CLAUDE]
    await clients.load(store, CLAUDE, fetch)
    await clients.load(store, CLAUDE, fetch)
    assert len(calls) == 1
    later = time.time() + clients.CIMD_TTL + 10
    monkeypatch.setattr(clients.time, "time", lambda: later)
    assert (await clients.load(store, CLAUDE, fetch))["client_name"] == "Claude"
    assert len(calls) == 2


# ── notifications ────────────────────────────────────────────────────────────


def test_notifications_pass_names_as_arguments(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "darwin")
    monkeypatch.setattr(notify.shutil, "which", lambda name: "/usr/bin/" + name)
    row = {"client_name": 'Evil" & do shell script "rm', "client_host": "x.example", "self_registered": 0,
           "user_code": "BCDF"}
    cmd = notify._command(*notify.message(row))
    assert cmd[:2] == ["osascript", "-e"] and 'Evil" & do shell script "rm' in cmd[-1]
    assert all("Evil" not in part for part in cmd[:-2])
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus")
    cmd = notify._command("t", "<b>x</b> & y")
    assert cmd[0] == "notify-send" and cmd[-1] == "&lt;b&gt;x&lt;/b&gt; &amp; y"


def test_no_desktop_no_notification(monkeypatch):
    monkeypatch.setattr(notify.sys, "platform", "linux")
    monkeypatch.setattr(notify.shutil, "which", lambda name: None)
    assert notify._command("t", "b") is None
