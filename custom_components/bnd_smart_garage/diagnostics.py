"""Diagnostics download (secrets redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import BndConfigEntry
from .const import CONF_CREDENTIALS

TO_REDACT = {
    CONF_HOST,
    "phone_password",
    "control_secret",
    "user_password",
    "rsa_key_der_b64",
    "sdk_phone_password",
    "sdk_secret",
    "mac_address",
    "ip_address",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: BndConfigEntry) -> dict[str, Any]:
    """Entry config, hub info and each door's raw status payload."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(
            {**entry.data, CONF_CREDENTIALS: dict(entry.data[CONF_CREDENTIALS])}, TO_REDACT
        ),
        "options": dict(entry.options),
        "hub_info": async_redact_data(asdict(coordinator.hub_info), TO_REDACT)
        if coordinator.hub_info
        else None,
        "doors": {
            device_id: {
                "state": status.state.value,
                "position": status.position,
                "rate": status.rate,
                "raw": status.raw,
            }
            for device_id, status in (coordinator.data or {}).items()
        },
    }
