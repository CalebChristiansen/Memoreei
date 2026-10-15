"""Memoreei Cloud's device API, from the computer's side.

  POST /v1/device/code         ask to enroll; get a code for the person
  POST /v1/device/token        poll until they approve; the device token comes once
  GET  /v1/device              who this token is (hostname, device id)
  POST /v1/device/certificate  a CSR in, a certificate chain out (about 30 s)
  POST /v1/device/revoke       Disconnect: this device, and nothing else

The person signs in, approves and consents on the service's own pages, never
here; memoreei only ever holds its device token.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

CLIENT_ID = "memoreei"
GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
_sleep = asyncio.sleep  # tests replace it


class CloudError(Exception):
    """Something the person should be told, in their words."""


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class Enrolled:
    token: str
    hostname: str
    device_id: str


def _error(r: httpx.Response) -> tuple[str | None, str]:
    try:
        body = r.json()
        return body.get("error"), body.get("error_description") or body.get("error") or r.text[:200]
    except ValueError:
        return None, f"HTTP {r.status_code}"


def _unreachable(url: str, e: Exception) -> CloudError:
    return CloudError(f"couldn't reach {url}: {e}. Check this computer is online.")


class Service:
    def __init__(self, auth_url: str, http: httpx.AsyncClient | None = None) -> None:
        self.auth_url = auth_url.rstrip("/")
        self._http = http

    async def _post(self, path: str, token: str | None = None, timeout: float = 30, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            if self._http is not None:
                return await self._http.post(self.auth_url + path, headers=headers, timeout=timeout, **kw)
            async with httpx.AsyncClient(timeout=timeout) as http:
                return await http.post(self.auth_url + path, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise _unreachable(self.auth_url, e)

    async def start_enrollment(self, name: str) -> DeviceCode:
        r = await self._post("/v1/device/code", data={"client_id": CLIENT_ID, "name": name})
        if r.status_code != 200:
            raise CloudError(f"Memoreei Cloud didn't start the sign-in: {_error(r)[1]}")
        b = r.json()
        return DeviceCode(b["device_code"], b["user_code"], b["verification_uri"],
                          b["verification_uri_complete"], int(b["expires_in"]), int(b["interval"]))

    async def wait_for_approval(self, code: DeviceCode,
                                sleep: Callable[[float], Awaitable[None]] | None = None,
                                clock: Callable[[], float] = time.monotonic) -> Enrolled:
        sleep = sleep or _sleep
        interval, deadline = code.interval, clock() + code.expires_in + 30
        while clock() < deadline:
            await sleep(interval)
            r = await self._post("/v1/device/token", data={
                "grant_type": GRANT_TYPE, "device_code": code.device_code, "client_id": CLIENT_ID})
            if r.status_code == 200:
                b = r.json()
                return Enrolled(b["device_token"], b["hostname"], b["device_id"])
            error, description = _error(r)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error == "access_denied":
                raise CloudError("The sign-in was cancelled, so this computer wasn't added.")
            if error == "expired_token":
                raise CloudError("The code expired before it was approved. Start again.")
            raise CloudError(f"Memoreei Cloud refused: {description}")
        raise CloudError("The code expired before it was approved. Start again.")

    async def whoami(self, token: str) -> dict:
        headers = {"Authorization": f"Bearer {token}"}
        try:
            if self._http is not None:
                r = await self._http.get(self.auth_url + "/v1/device", headers=headers)
            else:
                async with httpx.AsyncClient(timeout=30) as http:
                    r = await http.get(self.auth_url + "/v1/device", headers=headers)
        except httpx.HTTPError as e:
            raise _unreachable(self.auth_url, e)
        if r.status_code == 401:
            raise CloudError("Memoreei Cloud doesn't know this computer any more (it was removed).")
        if r.status_code != 200:
            raise CloudError(f"Memoreei Cloud: {_error(r)[1]}")
        return r.json()

    async def certificate(self, token: str, csr_pem: str) -> tuple[str, int]:
        """(PEM chain, not_after). Raises CloudError, with ``retry_after`` set on a 429."""
        r = await self._post("/v1/device/certificate", token=token, timeout=300, json={"csr": csr_pem})
        if r.status_code == 200:
            b = r.json()
            return b["certificate_chain"], int(b["not_after"])
        error, description = _error(r)
        e = CloudError(f"no certificate yet: {description}")
        if r.status_code == 401:
            e = CloudError("Memoreei Cloud doesn't know this computer any more (it was removed).")
        e.retry_after = int(r.headers.get("retry-after") or 0) or None  # type: ignore[attr-defined]
        e.status = r.status_code  # type: ignore[attr-defined]
        raise e

    async def revoke(self, token: str) -> bool:
        """True if the service removed this device (or had already)."""
        r = await self._post("/v1/device/revoke", token=token)
        return r.status_code in (200, 401)
