"""Runtime client for the hub's session-based control API (port 8989).

Only ever talks to the hub on the LAN; the vendor cloud is used once, during
pairing (pairing.py), and never again.

Every call is: an `app/connect` session (sessionId + sessionSecret, ~120s
lifetime), then a POST whose JSON body carries the request AES-encrypted with
the long-lived control secret (IV seeded by the millisecond timestamp) and
signed twice - HMAC with the session secret and HMAC with the control secret.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from typing import Any

import aiohttp

from .const import (
    ADVANCED_PARAMETER_FIELDS,
    CMD_CLOSE,
    CMD_OPEN,
    CMD_STOP,
    CONTROL_HEADERS,
    CONTROL_PORT,
    PERCENT_CMD_BASE,
    PERCENT_MAX,
    PERCENT_MIN,
    PERCENT_STEP,
    SESSION_LIFETIME_SECONDS,
)
from .crypto import encrypt_control, sign_hmac
from .errors import AuthenticationError, HubCommandError, HubUnreachableError
from .models import (
    ActivityEntry,
    Credentials,
    DeviceStatus,
    HubInfo,
    action_for_command,
    device_id_of,
    parse_device,
    parse_hub_info,
    parse_logs,
)
from .transport import hub_ssl_context

_LOGGER = logging.getLogger(__name__)

_ASYNC_RESULT_DELAY = 1.5
"""processState 1 = accepted, result posted to app/res/messages shortly after."""


def percent_command(percent: int) -> int:
    """Command code for an exact percent-open position (5..95, step 5)."""
    if percent % PERCENT_STEP or not PERCENT_MIN <= percent <= PERCENT_MAX:
        raise ValueError(
            f"percent must be a multiple of {PERCENT_STEP} in "
            f"{PERCENT_MIN}..{PERCENT_MAX}, got {percent}"
        )
    return PERCENT_CMD_BASE + percent // PERCENT_STEP


def _raise_for_error(message: dict[str, Any]) -> None:
    try:
        body = json.loads(message.get("data") or "{}")
    except ValueError:
        body = {}
    raise HubCommandError(body.get("code", "?"), body.get("description", "unknown error"))


def _ok_payloads(messages: list[dict[str, Any]]) -> list[Any]:
    """Decode the `data` of every message that completed (processState 0)."""
    payloads = []
    for message in messages:
        if message.get("processState") != 0:
            continue
        try:
            payloads.append(json.loads(message.get("data") or "{}"))
        except ValueError:
            continue
    return payloads


class HubClient:
    """Async client for one paired hub. Safe to share across tasks."""

    def __init__(
        self,
        host: str,
        credentials: Credentials,
        session: aiohttp.ClientSession,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.host = host
        self.credentials = credentials
        self._http = session
        self._ssl = ssl_context or hub_ssl_context()
        self._token = ""
        self._token_secret = ""
        self._token_at = 0.0
        # The session state isn't safe for concurrent negotiation, and the
        # hub dislikes overlapping requests anyway - serialise everything.
        self._lock = asyncio.Lock()

    @property
    def _base(self) -> str:
        return f"https://{self.host}:{CONTROL_PORT}"

    # ------------------------------------------------------------------ reads

    async def connect(self) -> None:
        """Open a session now, validating the stored credentials."""
        async with self._lock:
            await self._establish()

    async def get_device_ids(self) -> tuple[str, ...]:
        """Every device ID these credentials can see on the hub."""
        for body in _ok_payloads(await self._call("app/res/devices/fetch", {})):
            return tuple(
                device_id
                for entry in body.get("devices", [])
                if (device_id := device_id_of(entry))
            )
        return ()

    async def get_status(self, device_id: str) -> DeviceStatus:
        """Fetch one door's status, presets, toggles and last activity."""
        for body in _ok_payloads(
            await self._call("app/res/devices/fetch", {"deviceId": device_id})
        ):
            if devices := body.get("devices"):
                return parse_device(device_id, devices[0])
        return DeviceStatus(device_id=device_id)

    async def get_hub_info(self) -> HubInfo | None:
        """Hub name, firmware, MAC, Wi-Fi signal."""
        for body in _ok_payloads(await self._call("app/res/base/info", {})):
            if isinstance(body, dict):
                return parse_hub_info(body)
        return None

    async def get_logs(self, device_id: str) -> list[ActivityEntry]:
        """Full activity log for one device."""
        for body in _ok_payloads(await self._call("app/res/log", {"deviceId": device_id})):
            return parse_logs(body.get("logs", []))
        return []

    # --------------------------------------------------------------- commands

    async def open_door(self, device_id: str) -> None:
        """Open fully."""
        await self.send_command(device_id, CMD_OPEN)

    async def close_door(self, device_id: str) -> None:
        """Close fully."""
        await self.send_command(device_id, CMD_CLOSE)

    async def stop_door(self, device_id: str) -> None:
        """Stop mid-travel."""
        await self.send_command(device_id, CMD_STOP)

    async def set_percent(self, device_id: str, percent: int) -> None:
        """Move to an exact position (multiple of 5, 5..95)."""
        await self.send_command(device_id, percent_command(percent))

    async def set_advanced_parameter(self, device_id: str, code: int, value: int) -> None:
        """Set e.g. the light-on time or auto-close time (seconds)."""
        field_name = ADVANCED_PARAMETER_FIELDS[code]
        for message in await self._call(
            "app/res/devices/edit", {"deviceId": device_id, field_name: value}
        ):
            if message.get("processState") == -1:
                _raise_for_error(message)

    async def send_command(self, device_id: str, command: int) -> None:
        """Send a raw command code (door, preset, toggle)."""
        messages = await self._call(
            "app/res/action",
            {"deviceId": device_id, "action": action_for_command(command)},
        )
        for message in messages:
            state = message.get("processState", -1)
            if state == -1:
                _raise_for_error(message)
            if state == 1:
                await asyncio.sleep(_ASYNC_RESULT_DELAY)
                for polled in await self._call("app/res/messages", None):
                    if polled.get("processState") == -1:
                        _raise_for_error(polled)
                return

    # -------------------------------------------------------------- transport

    async def _establish(self) -> None:
        creds = self.credentials
        try:
            async with self._http.post(
                f"{self._base}/app/connect",
                ssl=self._ssl,
                json={
                    "bsid": creds.hub_id,
                    "phoneId": creds.phone_id,
                    "phonePassword": creds.phone_password,
                    "userPassword": creds.user_password,
                    "communicationType": 1,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("hub rejected the stored credentials")
                if response.status != 200:
                    text = await response.text()
                    raise HubUnreachableError(
                        f"connect failed: HTTP {response.status} {text[:150]}"
                    )
                reply = await response.json(content_type=None)
        except TimeoutError as err:
            raise HubUnreachableError(f"timed out connecting to {self.host}") from err
        except aiohttp.ClientError as err:
            raise HubUnreachableError(f"could not reach {self.host}: {err}") from err

        try:
            self._token = reply["sessionId"]
            self._token_secret = reply["sessionSecret"]
        except (KeyError, TypeError) as err:
            raise AuthenticationError("hub returned no session") from err
        self._token_at = time.monotonic()
        await self._respect_rate_limit(reply.get("data"))

    @staticmethod
    async def _respect_rate_limit(data: Any) -> None:
        """Honour the hub's `userAccess.nextAccess` back-off, if it sent one."""
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                data = None
        if not isinstance(data, dict):
            return
        next_access = (data.get("userAccess") or {}).get("nextAccess", 0) or 0
        wait_ms = next_access - int(time.time() * 1000)
        if 0 < wait_ms < 30_000:
            _LOGGER.debug("Hub asked us to wait %sms before the next call", wait_ms)
            await asyncio.sleep(wait_ms / 1000 + 0.1)

    async def _call(self, endpoint: str, request: dict[str, Any] | None) -> list[dict[str, Any]]:
        """POST an encrypted, signed request; renew the session once on 403."""
        request_json = "" if request is None else json.dumps(request, separators=(",", ":"))
        creds = self.credentials
        async with self._lock:
            for retry in (False, True):
                if not self._token or time.monotonic() - self._token_at > SESSION_LIFETIME_SECONDS:
                    await self._establish()
                timestamp = int(time.time() * 1000)
                encrypted = encrypt_control(creds.control_secret, str(timestamp), request_json)
                signing_input = f"{timestamp}:{encrypted}"
                body = {
                    "bsid": creds.hub_id,
                    "sessionId": self._token,
                    "time": timestamp,
                    "data": encrypted,
                    "processId": "0",
                    "sessionSig": sign_hmac(self._token_secret, signing_input),
                    "phoneSig": sign_hmac(creds.control_secret, signing_input),
                    "isEncrypted": True,
                }
                try:
                    async with self._http.post(
                        f"{self._base}/{endpoint}",
                        ssl=self._ssl,
                        headers=CONTROL_HEADERS,
                        json=body,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as response:
                        if response.status == 403 and not retry:
                            _LOGGER.debug("%s: session rejected (403), renewing", endpoint)
                            self._token = ""
                            continue
                        if response.status in (401, 403):
                            raise AuthenticationError(f"{endpoint} rejected the session")
                        if response.status != 200:
                            text = await response.text()
                            raise HubUnreachableError(
                                f"{endpoint} HTTP {response.status}: {text[:120]}"
                            )
                        reply = await response.json(content_type=None)
                except TimeoutError as err:
                    raise HubUnreachableError(f"timed out calling {endpoint}") from err
                except aiohttp.ClientError as err:
                    raise HubUnreachableError(f"could not reach {self.host}: {err}") from err
                try:
                    messages = json.loads(reply.get("messages") or "[]")
                except (ValueError, AttributeError) as err:
                    raise HubUnreachableError(f"{endpoint}: malformed reply") from err
                return messages if isinstance(messages, list) else []
        raise AuthenticationError(f"{endpoint} rejected even after a new session")
