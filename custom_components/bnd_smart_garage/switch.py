"""Toggle switches: auxiliary relay, remote-control lockout, phone lockout."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BndConfigEntry
from .entity import BndEntity
from .protocol import DeviceStatus, ToggleState
from .protocol.const import CMD_AUXILIARY, CMD_PHONE_LOCKOUT, CMD_REMOTE_LOCKOUT

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class BndSwitchDescription(SwitchEntityDescription):
    """A hub toggle slot and its (on, off) command pair."""

    toggle: Callable[[DeviceStatus], ToggleState | None]
    commands: tuple[int, int]


SWITCHES: tuple[BndSwitchDescription, ...] = (
    BndSwitchDescription(
        key="auxiliary",
        translation_key="auxiliary",
        toggle=lambda s: s.auxiliary,
        commands=CMD_AUXILIARY,
    ),
    BndSwitchDescription(
        key="remote_lockout",
        translation_key="remote_lockout",
        entity_category=EntityCategory.CONFIG,
        toggle=lambda s: s.remote_lockout,
        commands=CMD_REMOTE_LOCKOUT,
    ),
    BndSwitchDescription(
        # Blocks app/HA commands (not status). Turning it off is never blocked.
        key="phone_lockout",
        translation_key="phone_lockout",
        entity_category=EntityCategory.CONFIG,
        toggle=lambda s: s.phone_lockout,
        commands=CMD_PHONE_LOCKOUT,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BndConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Only the toggles each door actually advertises."""
    coordinator = entry.runtime_data
    async_add_entities(
        BndSwitch(coordinator, device_id, description)
        for device_id, status in coordinator.data.items()
        for description in SWITCHES
        if description.toggle(status) is not None
    )


class BndSwitch(BndEntity, SwitchEntity):
    """One toggle slot."""

    entity_description: BndSwitchDescription

    def __init__(self, coordinator, device_id: str, description: BndSwitchDescription) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        toggle = self.entity_description.toggle(self.status) if self.status else None
        return None if toggle is None else toggle.is_on

    async def _set(self, on: bool) -> None:
        if self.is_on is on:
            return
        on_cmd, off_cmd = self.entity_description.commands
        await self.coordinator.async_command(
            self.coordinator.client.send_command(self.device_id, on_cmd if on else off_cmd)
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
