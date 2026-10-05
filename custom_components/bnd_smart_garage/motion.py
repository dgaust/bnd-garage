"""Live position estimation while a door travels.

The hub does not report position mid-travel. While moving, a status fetch
returns the position the door *started* from plus a constant `rate` in
percent per second (live-observed: +7.143 opening = 14s full travel, -5.263
closing = 19s), and only reports the real position once the door stops. So
the current position is extrapolated as start + rate * elapsed.

The hub gives no usable start timestamp, so elapsed is anchored on what HA
knows: the moment it sent the command, or - for motion started by a remote or
wall button - halfway between the last idle poll and the poll that first saw
the motion. Each time the door stops, the hub's real position replaces the
estimate. Kept free of Home Assistant imports so it is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

COMMAND_ANCHOR_WINDOW = 10.0
"""Seconds a sent command remains the anchor candidate for newly seen motion
(the hub can take a few seconds to report the door moving)."""


@dataclass
class _Travel:
    start_position: float
    rate: float
    anchor: float
    target: float | None


class MotionTracker:
    """Per-door travel state. Times are monotonic seconds supplied by the caller."""

    def __init__(self) -> None:
        self._travel: _Travel | None = None
        self._last_poll: float | None = None
        self._command_at: float | None = None
        self._command_target: float | None = None

    def command_sent(self, now: float, target: float | None = None) -> None:
        """Record that HA just commanded the door (target 0..100 if known)."""
        self._command_at = now
        self._command_target = target

    def update(self, now: float, position: int, rate: float) -> None:
        """Feed one status poll."""
        if rate == 0 or position < 0:
            self._travel = None
        elif (
            self._travel is None
            or self._travel.start_position != position
            or self._travel.rate != rate
        ):
            # New travel, or the hub re-based (reversal / restart mid-travel).
            self._travel = self._start_travel(now, position, rate)
        self._last_poll = now

    def _start_travel(self, now: float, position: int, rate: float) -> _Travel:
        command_at, target = self._command_at, self._command_target
        self._command_at = self._command_target = None
        if command_at is not None and 0 <= now - command_at <= COMMAND_ANCHOR_WINDOW:
            anchor = command_at
            # Only trust a target that agrees with the direction of travel.
            if target is not None and not ((rate > 0 and target > position) or (rate < 0 and target < position)):
                target = None
        else:
            target = None
            if self._last_poll is not None and now > self._last_poll:
                anchor = now - (now - self._last_poll) / 2
            else:
                anchor = now
        return _Travel(position, rate, anchor, target)

    @property
    def moving(self) -> bool:
        """True while a travel is being tracked."""
        return self._travel is not None

    def position(self, now: float) -> float | None:
        """Estimated current position, or None when not travelling."""
        travel = self._travel
        if travel is None:
            return None
        estimate = travel.start_position + travel.rate * max(0.0, now - travel.anchor)
        if travel.target is not None:
            estimate = min(estimate, travel.target) if travel.rate > 0 else max(estimate, travel.target)
        return max(0.0, min(100.0, estimate))


PRESET_LEARN_TIMEOUT = 120.0
"""Give up on learning a preset's position if the door hasn't finished
travelling this long after the command."""


class PresetLearner:
    """Learns where each partial-open preset stops.

    The hub never says what position a preset (e.g. "Ventilation") drives to;
    the user sets it in the app. So when a preset is sent, the door is
    watched until it has moved and stopped again, and that resting position
    is remembered as the preset's target - used next time to stop the live
    estimate at the right place instead of overshooting until the hub
    reports the stop. Re-learned on every use, so a preset moved in the app
    corrects itself after one press.
    """

    def __init__(self, positions: dict[str, int] | None = None) -> None:
        self.positions: dict[str, int] = dict(positions or {})
        self._pending: dict[str, tuple[int, float, bool]] = {}

    @staticmethod
    def key(device_id: str, command: int) -> str:
        """Storage key for one door's preset slot."""
        return f"{device_id}:{command}"

    def target(self, device_id: str, command: int) -> int | None:
        """The learned stop position for this preset, if known."""
        return self.positions.get(self.key(device_id, command))

    def sent(self, device_id: str, command: int, now: float) -> None:
        """A preset command was just sent."""
        self._pending[device_id] = (command, now, False)

    def observe(self, device_id: str, now: float, position: int, moving: bool) -> bool:
        """Feed a poll; returns True when a preset position was (re)learned."""
        pending = self._pending.get(device_id)
        if pending is None:
            return False
        command, sent_at, seen_motion = pending
        if now - sent_at > PRESET_LEARN_TIMEOUT:
            del self._pending[device_id]
            return False
        if moving:
            self._pending[device_id] = (command, sent_at, True)
            return False
        if not seen_motion or position < 0:
            return False  # not started yet (or already there: nothing to learn)
        del self._pending[device_id]
        key = self.key(device_id, command)
        if self.positions.get(key) == position:
            return False
        self.positions[key] = position
        return True

    def cancel(self, device_id: str) -> None:
        """Another command superseded the preset (e.g. stop, open)."""
        self._pending.pop(device_id, None)
