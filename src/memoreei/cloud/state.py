"""What this computer keeps about its Memoreei Cloud enrollment.

All of it is in ``<home>/cloud/`` (mode 700):

  device.json   the hostname, device id, name, and the service's URLs
  token         the device token (mode 600): the one secret that proves this
                computer is enrolled. The service keeps only its hash
  key.pem       the TLS private key (mode 600), made here and never sent anywhere
  cert.pem      the certificate chain the service signed for the hostname
  status.json   what the running server last saw, for `memoreei cloud status`

device.json is written once enrollment has worked; the server starts the tunnel
when device.json and cert.pem are both there, and notices either appearing or
going within a few seconds.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from memoreei.config import memoreei_home


def cloud_dir() -> Path:
    return memoreei_home() / "cloud"


@dataclass(frozen=True)
class Device:
    auth_url: str      # the service: enrollment and certificates (memoreei signs chatbots in itself)
    tunnel_url: str
    hostname: str
    device_id: str
    name: str
    enrolled_at: int

    @property
    def mcp_url(self) -> str:
        return f"https://{self.hostname}/mcp"


def tunnel_url_for(auth_url: str) -> str:
    """wss://tunnel.<domain> beside https://auth.<domain>: how the service is laid out."""
    host = auth_url.split("://", 1)[-1].split("/", 1)[0]
    if host.startswith("auth."):
        host = "tunnel." + host[len("auth."):]
    return f"wss://{host}"


def _write(path: Path, data: str | bytes, mode: int) -> None:
    """Atomically, and never readable by anyone else even for a moment."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


class CloudState:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or cloud_dir()

    @property
    def device_path(self) -> Path:
        return self.root / "device.json"

    @property
    def token_path(self) -> Path:
        return self.root / "token"

    @property
    def key_path(self) -> Path:
        return self.root / "key.pem"

    @property
    def cert_path(self) -> Path:
        return self.root / "cert.pem"

    @property
    def status_path(self) -> Path:
        return self.root / "status.json"

    def device(self) -> Device | None:
        try:
            raw = json.loads(self.device_path.read_text())
            return Device(**{k: raw[k] for k in Device.__dataclass_fields__})
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def token(self) -> str | None:
        try:
            return self.token_path.read_text().strip() or None
        except OSError:
            return None

    def has_certificate(self) -> bool:
        return self.cert_path.is_file() and self.key_path.is_file()

    def ready(self) -> bool:
        """Enrolled, with a certificate: what the server needs to go online."""
        return self.device() is not None and self.token() is not None and self.has_certificate()

    def save_token(self, token: str) -> None:
        _write(self.token_path, token + "\n", 0o600)

    def save_device(self, device: Device) -> None:
        _write(self.device_path, json.dumps(asdict(device), indent=2) + "\n", 0o600)

    def save_key(self, pem: bytes) -> None:
        _write(self.key_path, pem, 0o600)

    def save_certificate(self, chain: str) -> None:
        _write(self.cert_path, chain, 0o644)

    def save_status(self, status: dict[str, Any]) -> None:
        try:
            _write(self.status_path, json.dumps({**status, "written_at": int(time.time())}) + "\n", 0o600)
        except OSError:
            pass

    def status(self) -> dict[str, Any] | None:
        try:
            return json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return None

    def signature(self) -> tuple:
        """Changes whenever anything the server acts on does."""
        out = []
        for p in (self.device_path, self.token_path, self.cert_path, self.key_path):
            try:
                st = p.stat()
                out.append((st.st_mtime_ns, st.st_size))
            except OSError:
                out.append(None)
        return tuple(out)

    def forget(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
