# B&D Smart Garage — Claude Code project guide

Home Assistant custom integration (domain `bnd_smart_garage`) for **B&D
SmartDoorDevices** garage hubs (Basestation, "B&D Smart Garage Access" app),
controlled locally over the hub's LAN API. Deliberately a different domain from
haids3's `bnd_garage` so both can be installed side by side.

## Layout

```
custom_components/bnd_smart_garage/
  protocol/          HA-free protocol client (unit-testable on its own)
    const.py         ports, headers, command codes - all dictated by the hub
    crypto.py        AES-128 (control) / AES-256 + RSA + zero-IV reply (SDK)
    transport.py     permissive TLS context; hub ID = cert CN
    models.py        Credentials, DeviceStatus, parsing of hub payloads
    client.py        HubClient - session-based control API on :8989
    pairing.py       one-time pairing (cloud redeem + v3migrate + SDK auth)
  __init__.py        setup; registers hub device; reloads on new doors
  coordinator.py     one per hub; adaptive polling; activity events
  cover/light/switch/button/sensor.py, diagnostics.py, config_flow.py
tests/test_protocol.py   protocol tests (no HA needed)
```

## Protocol essentials

- **Control API (:8989)**: `app/connect` → `sessionId`/`sessionSecret` (~120s).
  Each call POSTs `{bsid, sessionId, time, data, processId:"0", sessionSig,
  phoneSig, isEncrypted:true}`: `data` = AES-128-CBC(key=MD5(control_secret),
  iv=MD5(str(time_ms))) of the JSON request; both sigs = HMAC-SHA256 over
  `"{time}:{data}"` (session secret / control secret). 403 → renew once.
  Replies: `messages` (JSON string) of `{processState, data}`; 0 done, 1 async
  (poll `app/res/messages`), -1 error.
- **Command codes**: open 2, stop 3, close 4, presets 5-7, light 16/17,
  aux 18/19, remote lockout 20/21, phone lockout 258/257 (≥256 sent as
  `{"base": code-256}`), exact position 32-50 = 5%..95%.
- **Toggles**: the device's `aux` list offers the command that would *flip*
  the toggle, so offering "off" means it's currently on.
- **Control secret**: runtime signing uses the cloud-issued `phoneSecret`,
  never the ECDH-upgraded SDK secret (the hub rejects that).
- **SDK replies** decrypt with a zero IV → first 16 bytes garbled. For `auth`
  that's exactly `{"data":{"key":"`, so the key's *name* is lost; `_repair()`
  in pairing.py guesses the lost prefix. Tested.

- **Mid-travel position (live-verified on an SDO-7, fw FW101-123)**: while
  moving the hub reports the *start* position plus a constant `rate` in %/s
  (+7.143 open = 14s, -5.263 close = 19s) and only reports the real position
  once stopped. `motion.py` (HA-free, tested) extrapolates; the cover
  re-publishes every second while travelling. `statusTime` is an unknown
  encoding and the log entry is written at the *end* of travel, so neither
  anchors the start - HA's command time (or half the poll gap) does.

## Status / validation

Running on a real SDO-7 hub (fw FW101-123) in HA 2026.10 since v0.1.1:
pairing, status, presets, light and aux discovered fine. Validate after changes:

```bash
pytest tests                              # needs cryptography, aiohttp, pytest
python -m py_compile custom_components/bnd_smart_garage/*.py custom_components/bnd_smart_garage/protocol/*.py
```

The real test is pairing a hub in HA. When doing that, capture a diagnostics
download (secrets are redacted) to confirm payload shapes - especially
`openPercentageSupported` location, `aux` titles, and whether
`app/res/devices/fetch {}` returns full status (would allow one call per poll).

## Conventions

- Bump `manifest.json` `version` on every integration change.
- Keep `protocol/` free of `homeassistant` imports.
- Never move the real door in testing without asking the user first.
