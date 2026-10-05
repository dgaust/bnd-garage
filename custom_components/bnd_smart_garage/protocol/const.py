"""Wire constants dictated by the hub firmware (none of these are choices)."""

from __future__ import annotations

CONTROL_PORT = 8989
"""Session-based control API: connect, status, door/light/lockout commands."""

SDK_PORT = 8991
"""Newer RPC protocol; only used here during pairing."""

CLOUD_URL = "https://version2.smartdoordevices.com"
"""Vendor cloud - contacted exactly once, to redeem the activation code."""

CLIENT_NAME = "Home Assistant"
"""How this integration shows up in the hub's user/phone list and logs."""

CONTROL_HEADERS = {
    "Content-Type": "application/json",
    "version": "2.21.1",
    "app-version": "1.2.3",
}
SDK_HEADERS = {
    "Content-Type": "application/json",
    "sdk": "3.7.0",
    "platform": "android",
}

SESSION_LIFETIME_SECONDS = 110
"""The hub expires a control session after ~120s; renew a little early."""

# Device command codes (app/res/action).
CMD_OPEN = 2
CMD_STOP = 3
CMD_CLOSE = 4
CMD_LIGHT = (16, 17)
"""(on, off). The hub's feature list advertises whichever one would flip the
current state, so the advertised command tells us the state."""
CMD_AUXILIARY = (18, 19)
CMD_REMOTE_LOCKOUT = (20, 21)
CMD_PHONE_LOCKOUT = (258, 257)
"""Codes >= 256 travel as {"base": code - 256} rather than {"cmd": code}."""

PERCENT_MIN = 5
PERCENT_MAX = 95
PERCENT_STEP = 5
PERCENT_CMD_BASE = 31
"""cmd = PERCENT_CMD_BASE + percent // PERCENT_STEP (50% -> 41)."""

# Advanced parameter codes -> the per-field name app/res/devices/edit wants.
PARAM_LIGHT_TIME = 0
PARAM_AUTO_CLOSE_TIME = 1
PARAM_PE_AUTO_CLOSE_TIME = 2
PARAM_AUX_OUTPUT_TIME = 3
ADVANCED_PARAMETER_FIELDS = {
    PARAM_LIGHT_TIME: "parameterLightTime",
    PARAM_AUTO_CLOSE_TIME: "parameterAutoCloseTime",
    PARAM_PE_AUTO_CLOSE_TIME: "parameterPEAutoCloseTime",
    PARAM_AUX_OUTPUT_TIME: "parameterAuxOutputTime",
}

HIDDEN_LOG_TYPES = frozenset((0, 21))
"""Log types the vendor app itself hides - noise."""
