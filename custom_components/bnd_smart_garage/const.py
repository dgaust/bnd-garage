"""Constants for the B&D Smart Garage integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "bnd_smart_garage"
MANUFACTURER: Final = "B&D"

CONF_ACTIVATION_CODE: Final = "activation_code"
CONF_USER_PASSWORD: Final = "user_password"
CONF_CREDENTIALS: Final = "credentials"
"""Entry data key holding protocol.Credentials.as_dict()."""

CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 5
CONF_PUSH_PROBE: Final = "push_probe"
"""Experimental: run probe.py's message-queue logger (debug aid)."""
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 300

FAST_SCAN_INTERVAL: Final = timedelta(seconds=1)
"""Polling cadence while a door is moving or just after we sent a command."""
FAST_POLL_AFTER_COMMAND: Final = timedelta(seconds=30)
"""How long to keep fast-polling after a command even if motion isn't seen
yet - the hub can take a few seconds to report the door moving."""

ESTIMATE_REFRESH_INTERVAL: Final = timedelta(seconds=1)
"""How often the cover re-publishes its estimated position while moving."""

DEVICE_RESCAN_INTERVAL: Final = timedelta(minutes=30)
"""How often to check the hub for doors added after pairing."""

EVENT_ACTIVITY: Final = f"{DOMAIN}_activity"
"""Fired on the HA bus for every new hub log entry (e.g. "Opened by Remote")."""
