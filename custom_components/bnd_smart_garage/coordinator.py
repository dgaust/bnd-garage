"""Polling coordinator: one per hub, covering every door the hub reports."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from datetime import datetime, timedelta
import logging
import time
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_RESCAN_INTERVAL,
    DOMAIN,
    EVENT_ACTIVITY,
    EVENT_ERROR_BACKOFF,
    EVENT_POLL_INTERVAL,
    FAST_POLL_AFTER_COMMAND,
    FAST_SCAN_INTERVAL,
)
from .motion import MotionTracker, PresetLearner
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
    """Tracks every door on one hub and runs commands against it.

    Updates come from two sources sharing the hub's single session (the hub
    allows one session per paired user):

    - an event listener draining the hub's change queue (app/res/messages)
      every second - it receives a full status within ~1s of any change,
      including wall-button and remote presses (how the vendor app does it);
    - a slower full refresh (configured interval, default 30s; faster while a
      door moves or right after a command) as a safety net.

    The hub doesn't report position mid-travel, so per-door MotionTrackers
    turn start-position + rate into a live estimate (see motion.py).
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
        self.motion: dict[str, MotionTracker] = {d: MotionTracker() for d in self.device_ids}
        self.presets = PresetLearner()
        self._preset_store: Store[dict[str, int]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.preset_positions"
        )
        self.new_devices: tuple[str, ...] = ()
        """Doors found on the hub after pairing; __init__ reloads to add them."""

    async def async_load_presets(self) -> None:
        """Restore learned preset positions from storage."""
        self.presets = PresetLearner(await self._preset_store.async_load() or {})

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

        self._process(data)
        return data

    @callback
    def _process(self, data: dict[str, DeviceStatus]) -> None:
        """Feed fresh statuses to motion tracking, preset learning and events."""
        now = time.monotonic()
        wall = time.time()
        for device_id, status in data.items():
            self.motion.setdefault(device_id, MotionTracker()).update(
                now, status.position, status.rate, _started_ago(status, wall)
            )
            if self.presets.observe(device_id, now, status.position, status.moving):
                _LOGGER.debug("Learned preset positions: %s", self.presets.positions)
                self._preset_store.async_delay_save(lambda: self.presets.positions, 5)
        self._fire_activity_events(data)
        self._tune_interval(data)

    async def async_listen_events(self) -> None:
        """Drain the hub's change queue every second until unloaded."""
        while True:
            try:
                events = await self.client.get_events()
            except GarageError as err:
                _LOGGER.debug("Event listener: %s", err)
                await asyncio.sleep(EVENT_ERROR_BACKOFF)
                continue
            known = [e for e in events if e.device_id in self.device_ids]
            if known and self.data is not None:
                data = dict(self.data)
                for status in known:  # oldest first, so the latest wins
                    data[status.device_id] = status
                _LOGGER.debug(
                    "Hub event(s): %s",
                    [(s.device_id, s.state.value, s.position, s.rate) for s in known],
                )
                self._process(data)
                self.async_set_updated_data(data)
            await asyncio.sleep(EVENT_POLL_INTERVAL)

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

    def estimated_position(self, device_id: str) -> float | None:
        """Live position estimate while the door travels, else None."""
        tracker = self.motion.get(device_id)
        return tracker.position(time.monotonic()) if tracker else None

    async def async_command(
        self,
        action: Awaitable[None],
        *,
        device_id: str | None = None,
        target: float | None = None,
        preset: int | None = None,
    ) -> None:
        """Run a hub command, then poll fast so the result shows promptly.

        Door-moving commands pass `device_id` (and `target` when known) so the
        motion estimate can anchor on the moment the command went out; a
        preset passes its command code so its stop position can be learned
        and used as the target.
        """
        if device_id is not None:
            if preset is not None:
                self.presets.sent(device_id, preset, time.monotonic())
                target = self.presets.target(device_id, preset)
            else:
                self.presets.cancel(device_id)
        if device_id is not None and device_id in self.motion:
            # Before awaiting: a scheduled poll may see the motion first.
            self.motion[device_id].command_sent(time.monotonic(), target)
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


_TRAVEL_LOG_PREFIXES = ("Opening", "Closing")
_MAX_START_AGE = 60.0


def _started_ago(status: DeviceStatus, wall_now: float) -> float | None:
    """Seconds since travel began, from the hub's "Opening/Closing by ..." log.

    Live-verified: the hub logs the start of travel with a millisecond
    timestamp, and its clock agreed with HA's to well under a second.
    """
    activity = status.activity
    if not status.moving or activity is None or not activity.logged_at:
        return None
    if not activity.text.startswith(_TRAVEL_LOG_PREFIXES):
        return None
    ago = wall_now - activity.logged_at / 1000
    if -2.0 <= ago <= _MAX_START_AGE:
        return max(0.0, ago)
    return None
