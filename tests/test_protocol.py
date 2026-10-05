"""Protocol unit tests - run without Home Assistant installed.

    pip install cryptography aiohttp pytest
    pytest tests
"""

from __future__ import annotations

import base64
import importlib.util
import json
import pathlib
import sys

import pytest

# Import the protocol package directly so the HA-dependent integration
# __init__ isn't executed.
_PKG = pathlib.Path(__file__).parent.parent / "custom_components" / "bnd_smart_garage" / "protocol"
_spec = importlib.util.spec_from_file_location(
    "bnd_protocol", _PKG / "__init__.py", submodule_search_locations=[str(_PKG)]
)
protocol = importlib.util.module_from_spec(_spec)
sys.modules["bnd_protocol"] = protocol
_spec.loader.exec_module(protocol)

from bnd_protocol import crypto, models  # noqa: E402
from bnd_protocol.client import percent_command  # noqa: E402
from bnd_protocol.pairing import _device_ids, _generate_keys, _session_key  # noqa: E402

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding  # noqa: E402


def test_control_roundtrip() -> None:
    blob = crypto.encrypt_control("secret", "1700000000000", '{"deviceId":"abc"}')
    assert crypto.decrypt_control("secret", "1700000000000", blob) == '{"deviceId":"abc"}'


def test_control_iv_depends_on_timestamp() -> None:
    a = crypto.encrypt_control("secret", "1", "{}")
    b = crypto.encrypt_control("secret", "2", "{}")
    assert a != b


def test_sdk_zero_iv_reply_recovers_all_but_first_block() -> None:
    plaintext = '{"data":{"key":"SESSIONKEY123","devicePermissions":{"dev1":{}}},"errorCode":0}'
    blob = crypto.encrypt_sdk("sdksecret", "whatever-iv", plaintext)
    tail = crypto.decrypt_sdk_reply("sdksecret", blob)
    assert tail == plaintext[16:]
    assert _session_key(tail) == "SESSIONKEY123"
    assert _device_ids(tail) == ("dev1",)


def test_session_key_when_not_first_field() -> None:
    plaintext = '{"data":{"expiresIn":3600,"key":"K2"},"errorCode":0}'
    tail = crypto.decrypt_sdk_reply("s", crypto.encrypt_sdk("s", "iv", plaintext))
    assert _session_key(tail) == "K2"


def test_device_ids_multiple_doors() -> None:
    text = 'xx","devicePermissions":{"a1":{"x":{"y":1}},"b2":{"x":2}},"errorCode":0}'
    assert _device_ids(text) == ("a1", "b2")


def test_hmac_known_value() -> None:
    # RFC 4231-style sanity: deterministic and base64.
    sig = crypto.sign_hmac("key", "msg")
    assert sig == crypto.sign_hmac("key", "msg")
    assert len(base64.b64decode(sig)) == 32


def test_generated_keys_shape_and_rsa_signature() -> None:
    keys = _generate_keys()
    # Bare PKCS#1 RSAPublicKey for RSA-2048 is 270 bytes (what the hub expects).
    assert len(base64.b64decode(keys.rsa_public_b64)) == 270
    point = base64.b64decode(keys.ec_public_b64)
    assert len(point) == 65 and point[0] == 4
    sig = base64.b64decode(crypto.sign_rsa(keys.rsa_der_b64, "hello"))
    keys.rsa_key.public_key().verify(sig, b"hello", padding.PKCS1v15(), hashes.SHA512())
    # PKCS1 export equals the tail of the SPKI export (reference implementation).
    spki = keys.rsa_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert spki[-270:] == base64.b64decode(keys.rsa_public_b64)


@pytest.mark.parametrize(("percent", "cmd"), [(5, 32), (50, 41), (95, 50)])
def test_percent_command(percent: int, cmd: int) -> None:
    assert percent_command(percent) == cmd


@pytest.mark.parametrize("bad", [0, 3, 100, 97])
def test_percent_command_rejects(bad: int) -> None:
    with pytest.raises(ValueError):
        percent_command(bad)


def test_action_encoding() -> None:
    assert models.action_for_command(2) == {"cmd": 2}
    assert models.action_for_command(258) == {"base": 2}
    assert models.effective_command({"base": 1}) == 257
    assert models.effective_command({"cmd": 16}) == 16


SAMPLE_DEVICE = {
    "name": "Main door",
    "device": {"position": 0, "rate": 0},
    "aux": [
        {"title": "Light", "action": {"cmd": 17}},
        {"title": "Aux", "action": {"cmd": 18}},
        {"title": "Lockout", "action": {"cmd": 20}},
        {"title": "Lockout", "action": {"base": 1}},
        {"title": "Pet", "action": {"cmd": 5}},
        {"title": "Parcel", "action": {"cmd": 6}},
    ],
    "log": {"text": "Closed by Remote", "logId": 42, "time": 1700000000000, "alert": 0},
}


def test_parse_device() -> None:
    status = models.parse_device("dev1", SAMPLE_DEVICE)
    assert status.name == "Main door"
    assert status.state is models.DoorState.CLOSED
    assert status.light and status.light.is_on  # "off" (17) offered -> currently on
    assert status.auxiliary and not status.auxiliary.is_on
    assert status.remote_lockout and not status.remote_lockout.is_on
    assert status.phone_lockout and status.phone_lockout.is_on  # 257 = off offered
    assert [p.label for p in status.presets] == ["Pet", "Parcel"]
    assert status.activity and status.activity.log_id == 42


@pytest.mark.parametrize(
    ("position", "rate", "state"),
    [
        (0, 0, "closed"),
        (100, 0, "open"),
        (40, 0, "partial"),
        (40, 1.5, "opening"),
        (40, -1.5, "closing"),
        (-1, 0, "unknown"),
    ],
)
def test_door_state(position: int, rate: float, state: str) -> None:
    status = models.parse_device("d", {"device": {"position": position, "rate": rate}})
    assert status.state.value == state


def test_credentials_roundtrip() -> None:
    creds = models.Credentials(
        hub_id="h", phone_id="p", phone_password="pp", control_secret="s",
        user_password="u", devices=("a", "b"), sdk_secret="x",
    )
    stored = json.loads(json.dumps(creds.as_dict()))
    assert models.Credentials.from_dict({**stored, "future_field": 1}) == creds


def test_parse_logs_hides_noise() -> None:
    logs = models.parse_logs(
        [{"logType": 0, "text": "x"}, {"logType": 5, "text": "Opened", "logId": 1}]
    )
    assert [entry.text for entry in logs] == ["Opened"]


def _event(entry: dict) -> dict:
    """Envelope shape live-captured from an SDO-7's app/res/messages queue."""
    body = {"deviceOrder": [entry.get("deviceId", "Zr0kaOXH")], "devices": [entry]}
    return {"data": json.dumps(body), "time": 30903446, "type": 1, "isEncrypted": False}


def test_parse_events_full_status() -> None:
    entry = {
        **SAMPLE_DEVICE,
        "device": {"position": 0, "rate": 7.143},
        "log": {"text": "Opening by David", "time": 1791194369107, "logId": 7},
        "deviceId": "Zr0kaOXH",
    }
    (status,) = models.parse_events([_event(entry)])
    assert status.device_id == "Zr0kaOXH"
    assert status.state is models.DoorState.OPENING
    assert status.activity.text == "Opening by David"
    assert [p.label for p in status.presets] == ["Pet", "Parcel"]


def test_parse_events_falls_back_to_device_order() -> None:
    entry = {"device": {"position": 100, "rate": 0}}
    message = {"data": json.dumps({"deviceOrder": ["abc"], "devices": [entry]}), "type": 1}
    assert [s.device_id for s in models.parse_events([message])] == ["abc"]


def test_parse_events_ignores_other_messages() -> None:
    assert models.parse_events([{"processState": -1, "data": "{}"}]) == []
    assert models.parse_events([{"processState": 0, "data": "not json"}]) == []
    assert models.parse_events([{"processState": 0, "data": '{"code": 1}'}]) == []
