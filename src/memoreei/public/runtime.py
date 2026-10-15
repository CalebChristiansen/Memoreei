"""The public listener's life inside the server, and Memoreei Cloud's with it.

Three ways it can be:

  off      nothing set: the public listener doesn't exist, and nothing ever
           contacts Memoreei Cloud. A self-hoster who never asks sees no change
  cloud    <home>/cloud has an enrollment and a certificate: the listener runs on
           a loopback port with that certificate, the tunnel to the relay feeds it,
           tokens come from Memoreei Cloud's token server, and the certificate is
           renewed here
  custom   MEMOREEI_PUBLIC_PORT is set (and no enrollment): the listener runs where
           it's told, for the expert's own tunnel or proxy, trusting
           MEMOREEI_OAUTH_ISSUER's tokens if one is set, and mem_ keys either way

Every few seconds it looks again (config.env, and the cloud folder that `memoreei
cloud connect` and the dashboard write), so connecting or disconnecting needs no
restart. It has one identity at a time: cloud wins over a public port, and `cloud
connect` refuses while MEMOREEI_OAUTH_ISSUER is set.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

from memoreei.auth import Verifier
from memoreei.cloud.state import CloudState
from memoreei.config import get_config
from memoreei.public.gate import FailureLimiter, PublicGate
from memoreei.public.listener import Listener
from memoreei.public.tokens import TokenVerifier

log = logging.getLogger("memoreei.public")

TICK = 5
RETRY_START = 60
RENEW_RETRY = (3600, 12 * 3600)
LOOPBACK = ("127.0.0.1", "::1", "localhost")


@dataclass(frozen=True)
class Plan:
    mode: str                  # "cloud" or "custom"
    host: str
    port: int
    resource: str              # the public /mcp URL: tokens' audience
    issuer: str | None
    cert: str | None
    key: str | None
    trusted_proxy: str | None = None
    tunnel_url: str | None = None
    token: str | None = None
    problem: str | None = None  # set when the settings can't work as given


def plan(state: CloudState) -> Plan | None:
    cfg = get_config()
    device = state.device()
    if device is not None and state.ready() and not cfg.oauth_issuer:
        return Plan(mode="cloud", host="127.0.0.1", port=cfg.public_port or 0, resource=device.mcp_url,
                    issuer=device.auth_url, cert=str(state.cert_path), key=str(state.key_path),
                    tunnel_url=cfg.cloud_tunnel_url or device.tunnel_url, token=state.token())
    if cfg.public_port is None:
        return None
    problem = None
    if cfg.oauth_issuer and not cfg.public_mcp_url:
        problem = ("MEMOREEI_OAUTH_ISSUER is set but MEMOREEI_PUBLIC_MCP_URL isn't: tokens are checked "
                   "against that URL, so nothing could get in. Set it to the address clients use, "
                   "ending in /mcp.")
    if bool(cfg.public_tls_cert) != bool(cfg.public_tls_key):
        problem = "MEMOREEI_PUBLIC_TLS_CERT and MEMOREEI_PUBLIC_TLS_KEY go together."
    return Plan(mode="custom", host=cfg.public_host, port=cfg.public_port, resource=cfg.public_mcp_url or "",
                issuer=cfg.oauth_issuer, cert=cfg.public_tls_cert, key=cfg.public_tls_key,
                trusted_proxy=cfg.trusted_proxy, problem=problem)


class PublicRuntime:
    def __init__(self, mcp_app: Any, keys: Verifier,
                 record: Callable[[dict], Awaitable[None]] | None = None,
                 state: CloudState | None = None) -> None:
        self.mcp_app = mcp_app
        self.keys = keys
        self._record = record
        self.state = state or CloudState()
        self.limiter = FailureLimiter()
        self.current: Plan | None = None
        self.listener: Listener | None = None
        self.tunnel: Any = None
        self._tasks: list[asyncio.Task] = []
        self._cert_sig: tuple | None = None
        self._failed: tuple[Plan, float] | None = None
        self.error: str | None = None
        self.last_call_at: int | None = None
        self.renewal: dict[str, Any] = {}
        self._saved_at = float("-inf")
        self._poke = asyncio.Event()

    # ── what the dashboard and the CLI see ──────────────────────────────────

    def status(self) -> dict[str, Any]:
        from memoreei.cloud import certs

        p = self.current
        out: dict[str, Any] = {"mode": p.mode if p else "off", "error": self.error,
                               "last_call_at": self.last_call_at}
        if p:
            out.update(url=p.resource, issuer=p.issuer, port=self.listener.port if self.listener else None)
        if p and p.mode == "cloud":
            out["tunnel"] = self.tunnel.summary() if self.tunnel else None
            out["certificate"] = {**certs.describe(self.state), **self.renewal}
        return out

    def poke(self) -> None:
        """Look again now, rather than at the next tick."""
        self._poke.set()

    # ── the loop ────────────────────────────────────────────────────────────

    async def run(self) -> None:
        try:
            while True:
                await self.reconcile()
                self._poke.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._poke.wait(), TICK)
        finally:
            await self._stop()

    async def reconcile(self) -> None:
        want = plan(self.state)
        if want == self.current:
            if want is not None and want.mode == "cloud":
                self._maybe_reload_certificate()
                if time.monotonic() - self._saved_at > 60:  # `cloud status` reads it as a heartbeat
                    self._save_status()
            return
        if want is not None and self._failed and self._failed[0] == want \
                and time.monotonic() - self._failed[1] < RETRY_START:
            return
        await self._stop()
        if want is None:
            self.error = None
            return
        if want.problem:
            self.error = want.problem
            log.warning("public listener not started: %s", want.problem)
            self.current = want
            return
        try:
            await self._start(want)
            self.current, self.error, self._failed = want, None, None
        except Exception as e:
            await self._stop()
            self.error = f"The public listener couldn't start on {want.host}:{want.port}: {e}"
            log.warning("%s", self.error)
            self._failed = (want, time.monotonic())
        self._save_status()

    async def _start(self, p: Plan) -> None:
        tokens = TokenVerifier(p.issuer, p.resource) if p.issuer else None
        gate = PublicGate(self.mcp_app, resource=p.resource, keys=self.keys, tokens=tokens,
                          limiter=self.limiter, record=self.record, peer_of=self.peer_of)
        self.listener = Listener(gate, p.host, p.port, p.cert, p.key)
        await self.listener.start()
        scheme = "https" if p.cert else "http"
        log.info("public listener (%s) on %s://%s:%d for %s", p.mode, scheme, p.host, self.listener.port,
                 p.resource or "mem_ keys")
        if p.mode == "cloud":
            from memoreei.cloud.tunnel import Tunnel

            self._cert_sig = self._cert_signature()
            self.tunnel = Tunnel(p.tunnel_url or "", p.token or "", self.listener.port,
                                 on_status=lambda state, message: self._save_status())
            self._tasks = [asyncio.create_task(self.tunnel.run(), name="cloud-tunnel"),
                           asyncio.create_task(self._renew(), name="cloud-renew")]

    async def _stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._tasks = []
        self.tunnel = None
        if self.listener is not None:
            await self.listener.stop()
            self.listener = None
        self.current = None

    # ── pieces ──────────────────────────────────────────────────────────────

    def peer_of(self, scope: dict) -> str:
        host, port = (scope.get("client") or ("?", 0))[:2]
        if host in LOOPBACK and self.tunnel is not None:
            peer = self.tunnel.peer_for(port)
            if peer:
                return peer
        p = self.current
        if p is not None and p.trusted_proxy and host == p.trusted_proxy:
            for name, value in scope.get("headers", []):
                if name == b"x-forwarded-for":
                    return value.decode("latin-1").split(",")[-1].strip() or host
        return host

    async def record(self, entry: dict) -> None:
        self.last_call_at = int(time.time())
        if self._record is not None:
            await self._record(entry)

    def _cert_signature(self) -> tuple:
        return self.state.signature()[2:]

    def _maybe_reload_certificate(self) -> None:
        sig = self._cert_signature()
        if sig != self._cert_sig and self.listener is not None:
            self._cert_sig = sig
            try:
                self.listener.reload_certificate()
            except Exception as e:
                log.warning("couldn't load the renewed certificate: %r", e)

    async def _renew(self) -> None:
        from memoreei.cloud import certs
        from memoreei.cloud.service import CloudError, Service

        retry, next_try = RENEW_RETRY[0], 0
        while True:
            now = int(time.time())
            target = max(certs.renew_at(self.state) or now, next_try)
            if now < target:
                self.renewal = {**self.renewal, "next_attempt_at": target}
                # Short sleeps: a laptop asleep for days must still notice it's due.
                await asyncio.sleep(min(600, target - now))
                continue
            device, token = self.state.device(), self.state.token()
            if device is None or token is None:
                return
            try:
                not_after = await certs.obtain(self.state, Service(device.auth_url), device.hostname, token)
                log.info("certificate renewed, valid until %s", time.strftime("%Y-%m-%d", time.gmtime(not_after)))
                self.renewal, retry = {}, RENEW_RETRY[0]
                self._maybe_reload_certificate()
            except CloudError as e:
                wait = max(getattr(e, "retry_after", None) or 0, retry)
                log.warning("certificate renewal failed: %s; trying again in %d min", e, wait // 60)
                next_try = int(time.time()) + wait
                self.renewal = {"last_error": str(e), "next_attempt_at": next_try}
                self._save_status()
                retry = min(retry * 2, RENEW_RETRY[1])

    def _save_status(self) -> None:
        if (self.current is not None and self.current.mode == "cloud") or self.state.device() is not None:
            self._saved_at = time.monotonic()
            self.state.save_status(self.status())


RUNTIME: PublicRuntime | None = None


@contextlib.asynccontextmanager
async def public_runtime(keys: Verifier) -> AsyncIterator[PublicRuntime]:
    """Run the public side for as long as the server runs."""
    global RUNTIME
    from memoreei.server import _get_db, build_public_server

    async def record(entry: dict) -> None:
        await (await _get_db()).record_access(entry)

    app = build_public_server().streamable_http_app()
    async with app.router.lifespan_context(app):
        runtime = RUNTIME = PublicRuntime(app, keys, record)
        task = asyncio.create_task(runtime.run(), name="public-runtime")
        try:
            yield runtime
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            RUNTIME = None
