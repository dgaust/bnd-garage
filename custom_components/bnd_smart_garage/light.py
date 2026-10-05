"""Opener courtesy light (only for doors whose hub advertises one)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BndConfigEntry
from .entity import BndEntity
from .protocol.const import CMD_LIGHT

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BndConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """A light per door that has one."""
    coordinator = entry.runtime_data
    async_add_entities(
        BndLight(coordinator, device_id)
        for device_id, status in coordinator.data.items()
        if status.light is not None
    )


class BndLight(BndEntity, LightEntity):
    """The opener's light."""

    _attr_translation_key = "light"
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator, device_id: str) -> None:
        super().__init__(coordinator, device_id, "light")

    @property
    def is_on(self) -> bool | None:
        light = self.status.light if self.status else None
        return None if light is None else light.is_on

    async def _set(self, on: bool) -> None:
        if self.is_on is on:
            return
        command = CMD_LIGHT[0] if on else CMD_LIGHT[1]
        await self.coordinator.async_command(
            self.coordinator.client.send_command(self.device_id, command)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
