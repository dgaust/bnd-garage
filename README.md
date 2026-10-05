# B&D Smart Garage for Home Assistant

Local control of **B&D SmartDoorDevices** garage door hubs (the Basestation used
by the *B&D Smart Garage Access* app) — no cloud at runtime.

> **Status: 0.1.0, untested on hardware.** The protocol follows published
> research (see [NOTICE.md](NOTICE.md)); please report what works.

## What you get

Per door opener on the hub:

| Entity | Notes |
|---|---|
| `cover` (garage) | open / close / stop / set position. Positions snap to the hub's 5% steps (5–95); 0 and 100 are full close/open. |
| `sensor` Door state | open, closed, opening, closing, partially open |
| `sensor` Last activity | the hub's latest log line, e.g. "Closed by Remote" |
| `light` | only if the opener has a light the hub controls |
| `switch` Auxiliary output | only if advertised (needs a non-zero aux output time on the hub) |
| `switch` Remote lockout | disables physical remotes / wall buttons |
| `switch` Phone lockout | disables app (and HA) commands — status still works; turning it off is never blocked |
| `button` per preset | the partial-open presets you set up in the app (e.g. "Pet") |

Polling is local: every 5 s idle (configurable), every 1 s while a door moves
or just after a command. The hub only reports where the door *started* and
its speed while it travels, so the cover's position is estimated live (updated
every second) and replaced by the hub's real position when the door stops.

### Activity event

Every new hub log entry fires `bnd_smart_garage_activity`:

```yaml
trigger:
  - trigger: event
    event_type: bnd_smart_garage_activity
    event_data:
      text: "Opened by Remote"
```

Event data: `hub_id`, `device_id`, `door`, `text`, `log_id`, `time`.

## Install

HACS → Integrations → ⋮ → Custom repositories → `https://github.com/dgaust/bnd-garage`
(category *Integration*) → install **B&D Smart Garage** → restart HA.

Manual: copy `custom_components/bnd_smart_garage` into `config/custom_components/`.

## Pair

1. Give the hub a DHCP reservation.
2. In the B&D app: **Settings → Users → your hub → Add new user**. Note the
   **activation code** and the **password** the app assigns.
3. Settings → Devices & services → Add → **B&D Smart Garage**; enter hub IP,
   activation code and password.

Pairing redeems the activation code once with B&D's cloud
(`version2.smartdoordevices.com`); everything afterwards is LAN-only.
Activation codes are single-use — if pairing fails, generate a new one.

If the hub's IP changes, use **Reconfigure** (no re-pair). If HA's user is
deleted in the app, HA asks you to re-pair with a new code.

## Limitations

- No auto-discovery (enter the IP).
- New presets added in the app appear after reloading the integration; new
  doors are picked up automatically within 30 minutes.
- Advanced parameters (light time, auto-close) are implemented in the protocol
  layer but not yet exposed as entities, because the hub doesn't report their
  current values.

## Development

```bash
pip install cryptography aiohttp pytest
pytest tests
```

See [CLAUDE.md](CLAUDE.md) for architecture notes.
