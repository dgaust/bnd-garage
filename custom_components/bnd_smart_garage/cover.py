"""Garage door cover."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BndConfigEntry
from .entity import BndEntity
from .protocol import DoorState
from .protocol.const import PERCENT_MAX, PERCENT_MIN, PERCENT_STEP

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BndConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One cover per door."""
    coordinator = entry.runtime_data
    async_add_entities(BndGarageDoor(coordinator, device_id) for device_id in coordinator.device_ids)


def snap_position(position: int) -> int | None:
    """Map a requested 0-100 position onto what the hub can do.

    Returns 0 / 100 for a full close / open, otherwise the nearest multiple of
    5 clamped to 5..95 (the hub's exact-position commands).
    """
    if position <= 0:
        return 0
    if position >= 100:
        return 100
    snapped = int(round(position / PERCENT_STEP) * PERCENT_STEP)
    return max(PERCENT_MIN, min(PERCENT_MAX, snapped))


class BndGarageDoor(BndEntity, CoverEntity):
    """The door itself."""

    _attr_name = None
    _attr_device_class = CoverDeviceClass.GARAGE

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "door")

    @property
    def supported_features(self) -> CoverEntityFeature:
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        if self.status and self.status.percent_supported:
            features |= CoverEntityFeature.SET_POSITION
        return features

    @property
    def current_cover_position(self) -> int | None:
        if self.status is None or self.status.position < 0:
            return None
        return max(0, min(100, self.status.position))

    @property
    def is_closed(self) -> bool | None:
        if self.status is None or self.status.state is DoorState.UNKNOWN:
            return None
        return self.status.position == 0

    @property
    def is_opening(self) -> bool:
        return bool(self.status and self.status.state is DoorState.OPENING)

    @property
    def is_closing(self) -> bool:
        return bool(self.status and self.status.state is DoorState.CLOSING)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.status is None:
            return None
        return {"door_state": self.status.state.value, "rate": self.status.rate}

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(self.coordinator.client.open_door(self.device_id))

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(self.coordinator.client.close_door(self.device_id))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(self.coordinator.client.stop_door(self.device_id))

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        target = snap_position(int(kwargs[ATTR_POSITION]))
        if target == 0:
            await self.async_close_cover()
        elif target == 100:
            await self.async_open_cover()
        else:
            await self.coordinator.async_command(
                self.coordinator.client.set_percent(self.device_id, target)
            )
