"""One-time pairing: register Home Assistant as a new "phone" on the hub.

Flow (each step is required by the hub; order matters):

1. Read the hub ID from its TLS certificate CN.
2. Redeem the app-issued activation code + user password at the vendor cloud
   (`/app/remoteregister`) -> phoneId, phonePassword, control secret, userId.
3. `app/connect` once to confirm the hub accepts the new phone.
4. `app/v3migrate`: register an RSA key and do an ECDH exchange, producing the
   SDK secret. Needed for step 5 even though runtime control never uses it.
5. SDK `auth` + `setUserPassword(old == new)`: clears the hub's forced
   first-login password-change flag, which would otherwise block a headless
   client.
6. Determine the device IDs this phone may control.

Where the hub doesn't say which signing key a step wants, several candidates
are tried in turn - an empirical finding from the reference research.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import ssl
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric import padding as rsa_padding

from .client import HubClient
from .const import CLIENT_NAME, CLOUD_URL, CONTROL_PORT, SDK_HEADERS, SDK_PORT
from .crypto import decrypt_control, decrypt_sdk_reply, encrypt_control, encrypt_sdk, sign_hmac, sign_rsa
from .errors import AuthenticationError, GarageError, HubUnreachableError, PairingError
from .models import Credentials
from .transport import hub_ssl_context, read_hub_id

_P256_SPKI_PREFIX = bytes.fromhex("3059301306072a8648ce3d020106082a8648ce3d030107034200")
"""Standard DER SubjectPublicKeyInfo header for a raw uncompressed P-256 point;
the hub exchanges bare 65-byte points."""


@dataclass
class _Keys:
    rsa_key: rsa.RSAPrivateKey
    rsa_der_b64: str
    rsa_public_b64: str
    ec_key: ec.EllipticCurvePrivateKey
    ec_public_b64: str


def _generate_keys() -> _Keys:
    """CPU-heavy (RSA-2048) - run in a thread."""
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    rsa_der_b64 = base64.b64encode(
        rsa_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    ).decode()
    # The hub wants the bare PKCS#1 RSAPublicKey, not the SPKI wrapper.
    rsa_public_b64 = base64.b64encode(
        rsa_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.PKCS1
        )
    ).decode()
    ec_key = ec.generate_private_key(ec.SECP256R1())
    point = ec_key.public_key().public_numbers()
    ec_public_b64 = base64.b64encode(
        b"\x04" + point.x.to_bytes(32, "big") + point.y.to_bytes(32, "big")
    ).decode()
    return _Keys(rsa_key, rsa_der_b64, rsa_public_b64, ec_key, ec_public_b64)


class _Pairing:
    def __init__(self, session: aiohttp.ClientSession, host: str, ssl_ctx: ssl.SSLContext) -> None:
        self._http = session
        self._host = host
        self._ssl = ssl_ctx
        self.hub_id = ""
        self.phone_id = ""

    def _url(self, port: int, path: str) -> str:
        return f"https://{self._host}:{port}/{path}"

    async def _post(self, url: str, *, ssl_arg: Any, timeout: float, **kwargs: Any) -> tuple[int, Any]:
        try:
            async with self._http.post(
                url, ssl=ssl_arg, timeout=aiohttp.ClientTimeout(total=timeout), **kwargs
            ) as response:
                if response.status != 200:
                    return response.status, await response.text()
                return 200, await response.json(content_type=None)
        except TimeoutError as err:
            raise HubUnreachableError(f"timed out calling {url}") from err
        except aiohttp.ClientError as err:
            raise HubUnreachableError(f"could not reach {url}: {err}") from err

    # -- step 2
    async def register_with_cloud(self, activation_code: str, user_password: str) -> tuple[str, str, str]:
        # The vendor cloud's certificate chain has been seen expired; the
        # activation code itself is the trust anchor here.
        status, reply = await self._post(
            f"{CLOUD_URL}/app/remoteregister",
            ssl_arg=False,
            timeout=20,
            json={
                "bsid": self.hub_id,
                "remoteRegistrationCode": activation_code,
                "userPassword": user_password,
                "phoneName": CLIENT_NAME,
                "phoneModel": CLIENT_NAME,
            },
        )
        if status in (400, 401, 403, 404):
            raise AuthenticationError(f"activation code or password rejected (HTTP {status})")
        if status != 200 or not isinstance(reply, dict) or "phoneId" not in reply:
            raise PairingError(f"cloud registration failed: HTTP {status} {str(reply)[:120]}")
        self.phone_id = reply["phoneId"]
        return reply["phonePassword"], reply.get("phoneSecret", ""), str(reply.get("userId", ""))

    # -- step 3
    async def verify(self, phone_password: str, user_password: str) -> None:
        status, reply = await self._post(
            self._url(CONTROL_PORT, "app/connect"),
            ssl_arg=self._ssl,
            timeout=10,
            json={
                "bsid": self.hub_id,
                "phoneId": self.phone_id,
                "phonePassword": phone_password,
                "userPassword": user_password,
                "communicationType": 3,
            },
        )
        if status in (401, 403):
            raise AuthenticationError("hub rejected the newly registered phone")
        if status != 200:
            raise PairingError(f"hub connect failed: HTTP {status} {str(reply)[:150]}")

    # -- step 4
    async def migrate(
        self, keys: _Keys, control_secret: str, phone_password: str, user_password: str, sdk_password: str
    ) -> str:
        """Register the RSA key; return the ECDH-derived SDK secret."""
        timestamp = int(time.time() * 1000)
        payload = json.dumps(
            {
                "phoneKey": keys.rsa_public_b64,
                "newPhoneSecretPhoneHalf": keys.ec_public_b64,
                "newPhonePassword": sdk_password,
            },
            separators=(",", ":"),
        )
        encrypted = encrypt_control(control_secret, str(timestamp), payload)
        signature = base64.b64encode(
            keys.rsa_key.sign(encrypted.encode(), rsa_padding.PKCS1v15(), hashes.SHA512())
        ).decode()
        status, reply = await self._post(
            self._url(CONTROL_PORT, "app/v3migrate"),
            ssl_arg=self._ssl,
            timeout=20,
            headers=SDK_HEADERS,
            json={
                "bsid": self.hub_id,
                "phoneId": self.phone_id,
                "phoneKey": keys.rsa_public_b64,
                "phonePassword": phone_password,
                "userPassword": user_password,
                "data": encrypted,
                "time": timestamp,
                "signature": signature,
            },
        )
        if status != 200:
            raise PairingError(f"key upgrade failed: HTTP {status} {str(reply)[:150]}")

        secret = control_secret
        if migration := (reply or {}).get("migrationData"):
            try:
                decoded = json.loads(
                    decrypt_control(control_secret, reply.get("phoneId", self.phone_id), migration)
                )
                if hub_half := decoded.get("newPhoneSecretHubHalf"):
                    hub_key = serialization.load_der_public_key(
                        _P256_SPKI_PREFIX + base64.b64decode(hub_half)
                    )
                    shared = keys.ec_key.exchange(ec.ECDH(), hub_key)
                    secret = base64.b64encode(shared).decode()
            except (ValueError, KeyError, TypeError):
                pass  # best effort: keep the original secret
        return secret

    # -- step 5 (SDK protocol)
    async def _hub_clock(self) -> int:
        try:
            status, reply = await self._post(
                self._url(SDK_PORT, "sdk/info"), ssl_arg=self._ssl, timeout=10, headers=SDK_HEADERS, data=""
            )
            if status == 200 and isinstance(reply, dict) and (mono := reply.get("mono", 0)) > 0:
                return int(mono)
        except HubUnreachableError:
            pass
        return int(time.time() * 1000)

    async def sdk_call(
        self, rsa_der_b64: str, sdk_secret: str, command: dict[str, Any], keys: Sequence[str]
    ) -> dict[str, Any]:
        """Send one SDK RPC, trying each candidate MAC key until one is accepted."""
        command_json = json.dumps(command, separators=(",", ":"))
        for key in keys:
            if not key:
                continue
            timestamp = await self._hub_clock()
            request_id = "req" + uuid.uuid4().hex[:12]
            encrypted = encrypt_sdk(sdk_secret, str(timestamp), command_json)
            signing_input = f"{self.hub_id}:{self.phone_id}:{timestamp}:{request_id}:{encrypted}"
            try:
                status, reply = await self._post(
                    self._url(SDK_PORT, "sdk/message"),
                    ssl_arg=self._ssl,
                    timeout=15,
                    headers=SDK_HEADERS,
                    json={
                        "hubId": self.hub_id,
                        "phoneId": self.phone_id,
                        "requestId": request_id,
                        "time": timestamp,
                        "request": encrypted,
                        "signature": sign_rsa(rsa_der_b64, signing_input),
                        "mac": "NOKEY" if key == "NOKEY" else sign_hmac(key, signing_input),
                    },
                )
            except HubUnreachableError:
                continue
            if status == 200 and isinstance(reply, dict) and reply.get("mac") != "INVALID":
                return reply
        return {}

    # -- step 6 fallback
    async def discover_devices(self, phone_password: str, user_password: str, control_secret: str) -> tuple[str, ...]:
        creds = Credentials(
            hub_id=self.hub_id,
            phone_id=self.phone_id,
            phone_password=phone_password,
            control_secret=control_secret,
            user_password=user_password,
            devices=(),
        )
        try:
            return await HubClient(self._host, creds, self._http, self._ssl).get_device_ids()
        except GarageError:
            return ()


def _reply_text(reply: dict[str, Any], secret: str) -> str:
    """Best-effort plaintext of an SDK reply (plain JSON or zero-IV decrypted)."""
    raw = reply.get("response", "")
    if not raw:
        return ""
    try:
        json.loads(raw)
        return raw
    except ValueError:
        pass
    try:
        return decrypt_sdk_reply(secret, raw)
    except (ValueError, TypeError):
        return ""


_LOST_PREFIXES = ('{"data":{"', '{"data":[{"', '{"data":"', '{"data":{"key":"')
"""Candidate reconstructions of a reply's garbled first 16 bytes. The last
one matters for `auth`: `{"data":{"key":"` is *exactly* one AES block, so
when the session key is data's first field its name is lost entirely and
only the value survives at the start of the tail."""


def _repair(text: str) -> dict[str, Any] | None:
    """Rebuild a zero-IV-decrypted reply into JSON by guessing its lost prefix."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    for prefix in _LOST_PREFIXES:
        try:
            parsed = json.loads(prefix + text)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _session_key(text: str) -> str | None:
    if match := re.search(r'"key"\s*:\s*"([^"]+)"', text):
        return match.group(1)
    repaired = _repair(text) or {}
    data = repaired.get("data")
    if isinstance(data, dict) and isinstance(data.get("key"), str):
        return data["key"]
    return None


def _error_code(text: str) -> int | None:
    match = re.search(r'"errorCode"\s*:\s*(-?\d+)', text)
    return int(match.group(1)) if match else None


def _device_ids(text: str) -> tuple[str, ...]:
    """The devicePermissions map is keyed by device ID: {"cWepe5Rn": {...}}."""
    if match := re.search(r'"devicePermissions"\s*:\s*(?=\{)', text):
        try:
            permissions, _ = json.JSONDecoder().raw_decode(text, match.end())
            if isinstance(permissions, dict):
                return tuple(permissions)
        except ValueError:
            pass
    first = re.search(r'"devicePermissions"\s*:\s*\{"([^"]+)"', text)
    return (first.group(1),) if first else ()


async def pair_new_phone(
    session: aiohttp.ClientSession,
    host: str,
    activation_code: str,
    user_password: str,
    ssl_context: ssl.SSLContext | None = None,
) -> Credentials:
    """Pair Home Assistant with the hub at `host`; return runtime Credentials."""
    ssl_ctx = ssl_context or hub_ssl_context()
    pairing = _Pairing(session, host, ssl_ctx)
    pairing.hub_id = await read_hub_id(host)

    phone_password, control_secret, user_id = await pairing.register_with_cloud(
        activation_code.strip(), user_password
    )
    await pairing.verify(phone_password, user_password)

    keys = await asyncio.to_thread(_generate_keys)
    sdk_password = secrets.token_urlsafe(24)
    sdk_secret = await pairing.migrate(keys, control_secret, phone_password, user_password, sdk_password)

    auth = await pairing.sdk_call(
        keys.rsa_der_b64,
        sdk_secret,
        {"path": "auth", "data": {"userPassword": user_password, "phonePassword": sdk_password, "temporary": False}},
        ("NOKEY", sdk_password, sdk_secret),
    )
    auth_text = _reply_text(auth, sdk_secret)
    device_ids = _device_ids(auth_text)

    if session_key := _session_key(auth_text):
        data: dict[str, Any] = {"oldPassword": user_password, "newPassword": user_password}
        if user_id:
            data["userId"] = user_id
        reply = await pairing.sdk_call(
            keys.rsa_der_b64,
            sdk_secret,
            {"path": "setUserPassword", "data": data},
            (session_key, sdk_password, sdk_secret, "NOKEY"),
        )
        text = _reply_text(reply, sdk_secret)
        if _error_code(text) == 0:
            device_ids = device_ids or _device_ids(text)

    # The control API's device list is complete (every door on a multi-door
    # hub); the SDK replies are partially garbled, so they're only a fallback.
    device_ids = (
        await pairing.discover_devices(phone_password, user_password, control_secret)
        or device_ids
    )
    if not device_ids:
        raise PairingError("paired, but could not determine the hub's door IDs")

    return Credentials(
        hub_id=pairing.hub_id,
        phone_id=pairing.phone_id,
        phone_password=phone_password,
        control_secret=control_secret,
        user_password=user_password,
        devices=device_ids,
        rsa_key_der_b64=keys.rsa_der_b64,
        sdk_phone_password=sdk_password,
        sdk_secret=sdk_secret,
        user_id=user_id,
    )

