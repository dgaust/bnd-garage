"""Sensors: door state and the hub's last-activity log entry."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import BndConfigEntry
from .entity import BndEntity
from .protocol import DoorState

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BndConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """State + activity sensors per door."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = []
    for device_id in coordinator.device_ids:
        entities.append(BndDoorStateSensor(coordinator, device_id))
        entities.append(BndActivitySensor(coordinator, device_id))
    async_add_entities(entities)


class BndDoorStateSensor(BndEntity, SensorEntity):
    """Door state including 'partial', which a cover can't express."""

    _attr_translation_key = "door_state"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [state.value for state in DoorState if state is not DoorState.UNKNOWN]

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "door_state")

    @property
    def native_value(self) -> str | None:
        if self.status is None or self.status.state is DoorState.UNKNOWN:
            return None
        return self.status.state.value


class BndActivitySensor(BndEntity, SensorEntity):
    """The hub's most recent log entry, e.g. "Closed by Remote".

    Every new entry is also fired as a `bnd_smart_garage_activity` event,
    which is the better automation trigger (repeat actions keep this
    sensor's text the same).
    """

    _attr_translation_key = "last_activity"
    _attr_icon = "mdi:history"

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "last_activity")

    @property
    def native_value(self) -> str | None:
        activity = self.status.activity if self.status else None
        return activity.text[:255] if activity else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        activity = self.status.activity if self.status else None
        if activity is None:
            return None
        return {
            "log_id": activity.log_id,
            "time": dt_util.utc_from_timestamp(activity.logged_at / 1000).isoformat()
            if activity.logged_at
            else None,
        }
