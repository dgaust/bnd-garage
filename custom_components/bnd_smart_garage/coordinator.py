"""Polling coordinator: one per hub, covering every door the hub reports."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_RESCAN_INTERVAL,
    DOMAIN,
    EVENT_ACTIVITY,
    FAST_POLL_AFTER_COMMAND,
    FAST_SCAN_INTERVAL,
)
from .protocol import (
    AuthenticationError,
    DeviceStatus,
    GarageError,
    HubClient,
    HubCommandError,
    HubInfo,
)

if TYPE_CHECKING:
    from . import BndConfigEntry

_LOGGER = logging.getLogger(__name__)


class BndCoordinator(DataUpdateCoordinator[dict[str, DeviceStatus]]):
    """Polls every door on one hub and runs commands against it.

    Polling is adaptive: the configured interval (default 10s) while idle,
    2s while any door is moving or for a short window after a command, so the
    cover's opening/closing/position track travel closely without hammering
    the hub when nothing is happening.
    """

    config_entry: BndConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BndConfigEntry,
        client: HubClient,
        hub_info: HubInfo | None,
    ) -> None:
        self._idle_interval = timedelta(
            seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.credentials.hub_id}",
            update_interval=self._idle_interval,
        )
        self.client = client
        self.hub_info = hub_info
        self.hub_id = client.credentials.hub_id
        self.device_ids: tuple[str, ...] = client.credentials.devices
        self._fast_until: datetime | None = None
        self._last_rescan: datetime = dt_util.utcnow()
        self._last_log_ids: dict[str, int] = {}
        self.new_devices: tuple[str, ...] = ()
        """Doors found on the hub after pairing; __init__ reloads to add them."""

    async def _async_update_data(self) -> dict[str, DeviceStatus]:
        try:
            data = {
                device_id: await self.client.get_status(device_id)
                for device_id in self.device_ids
            }
            await self._maybe_rescan_devices()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GarageError as err:
            raise UpdateFailed(str(err)) from err

        self._fire_activity_events(data)
        self._tune_interval(data)
        return data

    async def _maybe_rescan_devices(self) -> None:
        now = dt_util.utcnow()
        if now - self._last_rescan < DEVICE_RESCAN_INTERVAL:
            return
        self._last_rescan = now
        try:
            found = await self.client.get_device_ids()
        except GarageError as err:
            _LOGGER.debug("Device rescan failed: %s", err)
            return
        if added := tuple(d for d in found if d not in self.device_ids):
            _LOGGER.info("B&D hub %s reports new door(s): %s", self.hub_id, added)
            self.new_devices = added

    def _fire_activity_events(self, data: dict[str, DeviceStatus]) -> None:
        """Fire an HA event for each genuinely new hub log entry.

        The first poll only records the current entry (it's history, not
        news), so a restart doesn't replay the last action as an event.
        """
        for device_id, status in data.items():
            activity = status.activity
            if activity is None:
                continue
            previous = self._last_log_ids.get(device_id)
            self._last_log_ids[device_id] = activity.log_id
            if previous is None or previous == activity.log_id:
                continue
            self.hass.bus.async_fire(
                EVENT_ACTIVITY,
                {
                    "hub_id": self.hub_id,
                    "device_id": device_id,
                    "door": status.name,
                    "text": activity.text,
                    "log_id": activity.log_id,
                    "time": dt_util.utc_from_timestamp(activity.logged_at / 1000).isoformat()
                    if activity.logged_at
                    else None,
                },
            )

    @callback
    def _tune_interval(self, data: dict[str, DeviceStatus]) -> None:
        boosted = self._fast_until is not None and dt_util.utcnow() < self._fast_until
        moving = any(status.moving for status in data.values())
        self.update_interval = FAST_SCAN_INTERVAL if moving or boosted else self._idle_interval

    async def async_command(self, action: Awaitable[None]) -> None:
        """Run a hub command, then poll fast so the result shows promptly."""
        try:
            await action
        except HubCommandError as err:
            raise HomeAssistantError(
                f"The B&D hub rejected the command ({err.message}). "
                "If 'Phone lockout' is on, turn it off first."
            ) from err
        except AuthenticationError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"B&D hub authentication failed: {err}") from err
        except GarageError as err:
            raise HomeAssistantError(f"Could not reach the B&D hub: {err}") from err
        self._fast_until = dt_util.utcnow() + FAST_POLL_AFTER_COMMAND
        self.update_interval = FAST_SCAN_INTERVAL
        await self.async_request_refresh()
