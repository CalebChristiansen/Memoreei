"""Connecting this computer to Memoreei Cloud, and disconnecting it.

Connect is the device-code flow: memoreei asks the service for a code, the person
opens the link (or types the code at the address shown) and signs in there, and
memoreei collects its device token, then asks for a certificate with a key it
made itself. The dashboard's Connect button and `memoreei cloud connect` both
come here; the running server notices the result within a few seconds.

Nothing in this module runs unless the person asks: there is no background
contact with Memoreei Cloud before they do.
"""
from __future__ import annotations

import inspect
import socket
import subprocess
import sys
import time
from typing import Any, Awaitable, Callable

from memoreei.cloud import certs
from memoreei.cloud.service import CloudError, DeviceCode, Service
from memoreei.cloud.state import CloudState, Device, tunnel_url_for
from memoreei.config import get_config

Step = Callable[[str, Any], "Awaitable[None] | None"]


def computer_name() -> str:
    """What to call this computer on the account: the Mac's own name, else the hostname."""
    if sys.platform == "darwin":
        try:
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True,
                                  text=True, timeout=5).stdout.strip()
            if name:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    return socket.gethostname().split(".")[0] or "A computer"


async def _call(step: Step | None, what: str, detail: Any = None) -> None:
    if step is not None:
        result = step(what, detail)
        if inspect.isawaitable(result):
            await result


def refuse_reason() -> str | None:
    """Why Connect can't run here, if it can't."""
    if get_config().oauth_issuer:
        return ("This memoreei trusts its own sign-in server (MEMOREEI_OAUTH_ISSUER), and its public "
                "listener has one identity at a time. Remove that setting to use Memoreei Cloud instead.")
    return None


async def connect(name: str | None = None, step: Step | None = None, state: CloudState | None = None,
                  service_for: Callable[[str], Service] | None = None) -> Device:
    """Enroll (if not already) and get a certificate (if there isn't one).

    ``step(what, detail)`` hears "code" (a DeviceCode to show), "approved", and
    "certificate" (asking; takes about half a minute).
    """
    if reason := refuse_reason():
        raise CloudError(reason)
    service_for = service_for or Service
    cfg = get_config()
    state = state or CloudState()
    device, token = state.device(), state.token()

    if device is not None and token is not None:
        service = service_for(device.auth_url)
        if not state.has_certificate():
            try:
                await service.whoami(token)
            except CloudError as e:
                if "doesn't know this computer" not in str(e):
                    raise
                state.forget()  # removed on the account since: start afresh
                device = token = None

    if device is None or token is None:
        service = service_for(cfg.cloud_url)
        code: DeviceCode = await service.start_enrollment(name or computer_name())
        await _call(step, "code", code)
        enrolled = await service.wait_for_approval(code)
        await _call(step, "approved", enrolled.hostname)
        state.save_token(enrolled.token)
        token = enrolled.token
        device = Device(auth_url=cfg.cloud_url, tunnel_url=cfg.cloud_tunnel_url or tunnel_url_for(cfg.cloud_url),
                        hostname=enrolled.hostname, device_id=enrolled.device_id,
                        name=name or computer_name(), enrolled_at=int(time.time()))
        state.save_device(device)

    if not state.has_certificate():
        await _call(step, "certificate", device.hostname)
        await certs.obtain(state, service_for(device.auth_url), device.hostname, token)
    return device


async def disconnect(state: CloudState | None = None,
                     service_for: Callable[[str], Service] | None = None) -> str:
    """Remove this computer from the account, then forget the enrollment here.

    Returns what to tell the person. Offline, it forgets anyway and says where to
    finish the job.
    """
    service_for = service_for or Service
    state = state or CloudState()
    device, token = state.device(), state.token()
    if device is None and token is None:
        state.forget()
        return "This computer wasn't connected to Memoreei Cloud."
    auth_url = device.auth_url if device else get_config().cloud_url
    removed = False
    if token:
        try:
            removed = await service_for(auth_url).revoke(token)
        except CloudError:
            removed = False
    state.forget()
    if removed:
        return "Disconnected: this computer is off your Memoreei Cloud account, and its connectors stop working."
    return (f"Disconnected here, but Memoreei Cloud couldn't be told. To take this computer off your "
            f"account, remove it at {auth_url}/device.")
