"""Shared base entity and device info."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import BndCoordinator
from .protocol import DeviceStatus


def hub_device_info(coordinator: BndCoordinator) -> DeviceInfo:
    """The hub (Basestation) itself; doors hang off it via via_device."""
    info = coordinator.hub_info
    device = DeviceInfo(
        identifiers={(DOMAIN, coordinator.hub_id)},
        manufacturer=MANUFACTURER,
        model="SmartDoorDevices hub",
        name=(info.name if info and info.name else "B&D garage hub"),
    )
    if info:
        if info.firmware or info.version:
            device["sw_version"] = info.firmware or info.version
        if info.serial_number:
            device["serial_number"] = info.serial_number
        if info.mac_address:
            device["connections"] = {(CONNECTION_NETWORK_MAC, format_mac(info.mac_address))}
    return device


class BndEntity(CoordinatorEntity[BndCoordinator]):
    """An entity belonging to one door opener on the hub."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BndCoordinator, device_id: str, key: str) -> None:
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = f"{coordinator.hub_id}_{device_id}_{key}"
        status = coordinator.data.get(device_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.hub_id}_{device_id}")},
            manufacturer=MANUFACTURER,
            model="Garage door opener",
            name=(status.name if status and status.name else "Garage door"),
        )
        # No via_device here: deprecated (and an error on 2026.10+). The door
        # is linked to the hub with via_device_id in __init__ after setup.

    @property
    def status(self) -> DeviceStatus | None:
        """This door's latest status."""
        return self.coordinator.data.get(self.device_id)

    @property
    def available(self) -> bool:
        """Available while polling works and the hub reports this door."""
        return super().available and self.status is not None
