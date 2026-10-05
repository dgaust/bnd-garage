"""Buttons for the partial-open presets configured in the B&D app."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BndConfigEntry
from .entity import BndEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BndConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One button per preset slot the door advertises at setup.

    Keyed by command code (the preset slot), not label, so renaming a preset
    in the app updates the button name rather than orphaning it.
    """
    coordinator = entry.runtime_data
    async_add_entities(
        BndPresetButton(coordinator, device_id, preset.command)
        for device_id, status in coordinator.data.items()
        for preset in status.presets
    )


class BndPresetButton(BndEntity, ButtonEntity):
    """Drive the door to a preset position."""

    _attr_icon = "mdi:garage-open-variant"

    def __init__(self, coordinator, device_id: str, command: int) -> None:
        super().__init__(coordinator, device_id, f"preset_{command}")
        self._command = command

    @property
    def name(self) -> str:
        label = next(
            (p.label for p in (self.status.presets if self.status else ()) if p.command == self._command),
            "",
        )
        return label or f"Preset {self._command}"

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        learned = self.coordinator.presets.target(self.device_id, self._command)
        return None if learned is None else {"learned_position": learned}

    @property
    def available(self) -> bool:
        # A preset removed in the app disappears from the hub's list.
        return super().available and any(
            p.command == self._command for p in self.status.presets
        )

    async def async_press(self) -> None:
        await self.coordinator.async_command(
            self.coordinator.client.send_command(self.device_id, self._command),
            device_id=self.device_id,
            preset=self._command,
        )
