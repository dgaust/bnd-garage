"""Data types and the pure parsing of hub payloads into them."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from .const import (
    CMD_AUXILIARY,
    CMD_LIGHT,
    CMD_PHONE_LOCKOUT,
    CMD_REMOTE_LOCKOUT,
    HIDDEN_LOG_TYPES,
)


@dataclass(frozen=True, kw_only=True)
class Credentials:
    """Long-lived credentials produced by pairing.

    Only the first six fields are needed for runtime control. The SDK fields
    are kept so future features (notification history, settings) don't need
    a re-pair.
    """

    hub_id: str
    phone_id: str
    phone_password: str
    control_secret: str
    """The secret issued by cloud registration. The hub only accepts *this*
    value for runtime control signing - not the ECDH-upgraded SDK secret."""
    user_password: str
    devices: tuple[str, ...]
    rsa_key_der_b64: str = ""
    sdk_phone_password: str = ""
    sdk_secret: str = ""
    user_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serialise for storage in a config entry."""
        data = asdict(self)
        data["devices"] = list(self.devices)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Credentials:
        """Inverse of as_dict (ignores unknown keys)."""
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        known["devices"] = tuple(known.get("devices", ()))
        return cls(**known)


class DoorState(StrEnum):
    """Coarse door state derived from position and rate."""

    OPEN = "open"
    CLOSED = "closed"
    OPENING = "opening"
    CLOSING = "closing"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True, kw_only=True)
class PresetAction:
    """A user-configured partial-open preset (label and position set in the app)."""

    command: int
    label: str


@dataclass(frozen=True, kw_only=True)
class ToggleState:
    """A toggle slot: the hub lists only the command that would flip it."""

    command: int
    is_on: bool


@dataclass(frozen=True, kw_only=True)
class ActivityEntry:
    """A hub log entry; `text` comes pre-attributed (e.g. "Closed by Remote")."""

    text: str
    log_id: int
    logged_at: int
    """Unix milliseconds, hub clock."""
    alert: int = 0


@dataclass(frozen=True, kw_only=True)
class HubInfo:
    """Identity/network info from app/res/base/info."""

    name: str = ""
    serial_number: str = ""
    version: str = ""
    firmware: str = ""
    ip_address: str = ""
    mac_address: str = ""
    wifi_signal: str = ""


@dataclass(frozen=True, kw_only=True)
class DeviceStatus:
    """Snapshot of one door opener, from a single status fetch."""

    device_id: str
    name: str = ""
    position: int = -1
    """0 = closed, 100 = open, -1 = unknown."""
    rate: float = 0
    """> 0 opening, < 0 closing, 0 stationary."""
    percent_supported: bool = True
    presets: tuple[PresetAction, ...] = ()
    light: ToggleState | None = None
    auxiliary: ToggleState | None = None
    remote_lockout: ToggleState | None = None
    phone_lockout: ToggleState | None = None
    activity: ActivityEntry | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def state(self) -> DoorState:
        """Classify the coarse door state."""
        if self.rate > 0:
            return DoorState.OPENING
        if self.rate < 0:
            return DoorState.CLOSING
        if self.position < 0:
            return DoorState.UNKNOWN
        if self.position == 0:
            return DoorState.CLOSED
        if self.position >= 100:
            return DoorState.OPEN
        return DoorState.PARTIAL

    @property
    def moving(self) -> bool:
        """True while the door is travelling."""
        return self.rate != 0


def effective_command(action: dict[str, Any]) -> int | None:
    """Resolve an action's command code whichever field it's encoded in."""
    if "cmd" in action:
        return int(action["cmd"])
    if "base" in action:
        return int(action["base"]) + 256
    return None


def action_for_command(command: int) -> dict[str, int]:
    """Encode a command for app/res/action (codes >= 256 use `base`)."""
    if command >= 256:
        return {"base": command - 256}
    return {"cmd": command}


def _toggle(command: int, pair: tuple[int, int]) -> ToggleState:
    # The hub advertises the *next* action: if "off" is offered, it's on.
    return ToggleState(command=command, is_on=command == pair[1])


def parse_activity(log: dict[str, Any] | None) -> ActivityEntry | None:
    """Parse a single log dict (the one bundled into each status fetch)."""
    if not log:
        return None
    return ActivityEntry(
        text=str(log.get("text", "")),
        log_id=int(log.get("logId", 0) or 0),
        logged_at=int(log.get("time", 0) or 0),
        alert=int(log.get("alert", 0) or 0),
    )


def parse_logs(logs: list[dict[str, Any]]) -> list[ActivityEntry]:
    """Parse app/res/log's full history, dropping types the vendor app hides."""
    return [
        entry
        for raw in logs
        if raw.get("logType") not in HIDDEN_LOG_TYPES
        and (entry := parse_activity(raw)) is not None
    ]


def parse_device(device_id: str, entry: dict[str, Any]) -> DeviceStatus:
    """Parse one element of app/res/devices/fetch's `devices` list."""
    device = entry.get("device", {}) or {}
    presets: list[PresetAction] = []
    toggles: dict[str, ToggleState] = {}
    for aux in entry.get("aux", []) or []:
        command = effective_command(aux.get("action", {}) or {})
        if command is None:
            continue
        if command in CMD_LIGHT:
            toggles["light"] = _toggle(command, CMD_LIGHT)
        elif command in CMD_AUXILIARY:
            toggles["auxiliary"] = _toggle(command, CMD_AUXILIARY)
        elif command in CMD_REMOTE_LOCKOUT:
            toggles["remote_lockout"] = _toggle(command, CMD_REMOTE_LOCKOUT)
        elif command in CMD_PHONE_LOCKOUT:
            toggles["phone_lockout"] = _toggle(command, CMD_PHONE_LOCKOUT)
        else:
            presets.append(PresetAction(command=command, label=str(aux.get("title", ""))))

    percent = device.get("openPercentageSupported", entry.get("openPercentageSupported"))
    return DeviceStatus(
        device_id=device_id,
        name=str(entry.get("name", "")),
        position=int(device.get("position", -1)),
        rate=float(device.get("rate", 0) or 0),
        percent_supported=True if percent is None else bool(percent),
        presets=tuple(presets),
        activity=parse_activity(entry.get("log")),
        raw=entry,
        **toggles,
    )


def device_id_of(entry: dict[str, Any]) -> str | None:
    """Extract a device ID from a devices/fetch list element."""
    device = entry.get("device", {}) or {}
    found = entry.get("deviceId") or device.get("deviceId") or device.get("id")
    return str(found) if found else None


def parse_hub_info(data: dict[str, Any]) -> HubInfo:
    """Parse app/res/base/info's human-readable-key response."""
    ap_name = str(data.get("AP Name", ""))
    return HubInfo(
        name=str(data.get("Hub Name", "")),
        serial_number=ap_name[3:],
        version=str(data.get("Hub Version", "")),
        firmware=str(data.get("Hub Firmware", "")),
        ip_address=str(data.get("IP Address", "")),
        mac_address=str(data.get("MAC Address", "")),
        wifi_signal=str(data.get("Wi-Fi Signal", "")),
    )
