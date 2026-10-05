"""B&D Smart Garage: local control of B&D SmartDoorDevices garage hubs."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_CREDENTIALS, CONF_PUSH_PROBE, DOMAIN
from .coordinator import BndCoordinator
from .entity import hub_device_info
from .probe import run_push_probe
from .protocol import AuthenticationError, Credentials, GarageError, HubClient
from .protocol.transport import hub_ssl_context

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.COVER,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type BndConfigEntry = ConfigEntry[BndCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: BndConfigEntry) -> bool:
    """Connect to the hub, do a first poll, and set up platforms."""
    credentials = Credentials.from_dict(entry.data[CONF_CREDENTIALS])
    ssl_context = await hass.async_add_executor_job(hub_ssl_context)
    client = HubClient(
        entry.data[CONF_HOST], credentials, async_get_clientsession(hass), ssl_context
    )

    try:
        await client.connect()
    except AuthenticationError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except GarageError as err:
        raise ConfigEntryNotReady(str(err)) from err

    try:
        hub_info = await client.get_hub_info()
    except GarageError as err:
        _LOGGER.debug("Hub info unavailable: %s", err)
        hub_info = None

    coordinator = BndCoordinator(hass, entry, client, hub_info)
    await coordinator.async_load_presets()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Register the hub first so each door's via_device link resolves.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, **hub_device_info(coordinator)
    )

    @callback
    def _check_new_devices() -> None:
        # A door added on the hub after pairing: remember it and reload so
        # every platform builds its entities.
        if not coordinator.new_devices:
            return
        added = coordinator.new_devices
        coordinator.new_devices = ()
        updated = credentials.as_dict()
        updated["devices"] = list(dict.fromkeys([*credentials.devices, *added]))
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_CREDENTIALS: updated}
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(coordinator.async_add_listener(_check_new_devices))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if entry.options.get(CONF_PUSH_PROBE):
        _LOGGER.warning(
            "B&D push probe is ON (experimental). Enable debug logging for this "
            "integration to see its output; turn it off in the options when done"
        )
        entry.async_create_background_task(
            hass, run_push_probe(coordinator), "bnd_smart_garage push probe"
        )

    # Hang each door device off the hub device.
    registry = dr.async_get(hass)
    if hub := registry.async_get_device_by_identifier(
        identifier=(DOMAIN, coordinator.hub_id), config_entry_id=entry.entry_id
    ):
        for device_id in coordinator.device_ids:
            door = registry.async_get_device_by_identifier(
                identifier=(DOMAIN, f"{coordinator.hub_id}_{device_id}"),
                config_entry_id=entry.entry_id,
            )
            if door and door.via_device_id != hub.id:
                registry.async_update_device(door.id, via_device_id=hub.id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BndConfigEntry) -> bool:
    """Unload platforms."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
