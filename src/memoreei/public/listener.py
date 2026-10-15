"""A second uvicorn server inside the running one, for the public gate.

It shares the process, the event loop and the database with the LAN listener,
so the tools, the index and the sync loop are the same ones. We bind its socket
ourselves (uvicorn would sys.exit() the whole server on a bind failure) and keep
its TLS context, so a renewed certificate takes effect on the next handshake
without dropping anyone.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import ssl
from typing import Any, Iterator

import uvicorn

log = logging.getLogger("memoreei.public")


class _Embedded(uvicorn.Server):
    """A uvicorn Server that leaves signals to the server it lives inside."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield

    def install_signal_handlers(self) -> None:  # uvicorn before 0.29
        pass


class Listener:
    def __init__(self, app: Any, host: str, port: int, cert: str | None = None, key: str | None = None) -> None:
        self.app, self.host, self.port = app, host, port
        self.cert, self.key = cert, key
        self.ssl: ssl.SSLContext | None = None
        self._server: _Embedded | None = None
        self._task: asyncio.Task | None = None

    @property
    def tls(self) -> bool:
        return self.cert is not None

    async def start(self) -> None:
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.host, self.port))
        except OSError:
            sock.close()
            raise
        sock.listen(128)
        sock.setblocking(False)
        self.port = sock.getsockname()[1]

        config = uvicorn.Config(
            self.app, lifespan="off", access_log=False, log_level="warning", server_header=False,
            proxy_headers=False, ssl_certfile=self.cert, ssl_keyfile=self.key,
            timeout_keep_alive=30,
        )
        config.load()
        self.ssl = config.ssl
        self._server = _Embedded(config)
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]), name="public-listener")
        for _ in range(200):
            if self._server.started or self._task.done():
                break
            await asyncio.sleep(0.01)
        if self._task.done():
            self._task.result()

    def reload_certificate(self) -> None:
        """Serve a renewed certificate from the next handshake on."""
        if self.ssl is not None and self.cert and self.key:
            self.ssl.load_cert_chain(self.cert, self.key)
            log.info("public listener: certificate reloaded")

    async def stop(self) -> None:
        if self._server is None or self._task is None:
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self._task), 5)
        except asyncio.TimeoutError:
            self._server.force_exit = True
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._task, 2)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("public listener stopped badly: %r", e)
        self._server = self._task = None
