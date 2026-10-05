"""MotionTracker tests, using rates observed on a real SDO-7 (no HA needed)."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_PATH = pathlib.Path(__file__).parent.parent / "custom_components" / "bnd_smart_garage" / "motion.py"
_spec = importlib.util.spec_from_file_location("bnd_motion", _PATH)
motion = importlib.util.module_from_spec(_spec)
sys.modules["bnd_motion"] = motion
_spec.loader.exec_module(motion)

OPEN_RATE = 7.143  # % per second -> 14s full travel
CLOSE_RATE = -5.263  # -> 19s


def test_idle_has_no_estimate() -> None:
    tracker = motion.MotionTracker()
    tracker.update(0, 0, 0)
    assert tracker.position(1) is None and not tracker.moving


def test_command_anchored_open() -> None:
    tracker = motion.MotionTracker()
    tracker.update(0, 0, 0)
    tracker.command_sent(10, target=100)
    tracker.update(11.5, 0, OPEN_RATE)  # hub reports start position + rate
    assert tracker.position(17) == pytest.approx(7 * OPEN_RATE)
    assert tracker.position(30) == 100  # clamped at target
    tracker.update(25, 100, 0)  # stopped: real position takes over
    assert tracker.position(26) is None


def test_remote_started_anchors_halfway_between_polls() -> None:
    tracker = motion.MotionTracker()
    tracker.update(0, 100, 0)
    tracker.update(5, 100, CLOSE_RATE)  # started somewhere in 0..5s
    assert tracker.position(5) == pytest.approx(100 + CLOSE_RATE * 2.5)


def test_same_report_does_not_reanchor() -> None:
    tracker = motion.MotionTracker()
    tracker.command_sent(0, target=0)
    tracker.update(1, 75, CLOSE_RATE)
    tracker.update(2, 75, CLOSE_RATE)
    tracker.update(3, 75, CLOSE_RATE)
    assert tracker.position(4) == pytest.approx(75 + CLOSE_RATE * 4)


def test_reversal_reanchors_on_new_start() -> None:
    tracker = motion.MotionTracker()
    tracker.command_sent(0, target=100)
    tracker.update(1, 0, OPEN_RATE)
    tracker.command_sent(5, target=0)
    tracker.update(6, 35, CLOSE_RATE)
    assert tracker.position(7) == pytest.approx(35 + CLOSE_RATE * 2)


def test_partial_target_clamps() -> None:
    tracker = motion.MotionTracker()
    tracker.command_sent(0, target=50)
    tracker.update(1, 0, OPEN_RATE)
    assert tracker.position(20) == 50


def test_stale_command_is_ignored() -> None:
    tracker = motion.MotionTracker()
    tracker.update(0, 0, 0)
    tracker.command_sent(1, target=100)
    tracker.update(55, 0, 0)  # idle poll
    tracker.update(60, 0, OPEN_RATE)  # too long after the command: halfway anchor
    assert tracker.position(60) == pytest.approx(OPEN_RATE * 2.5)


def test_target_against_direction_ignored() -> None:
    tracker = motion.MotionTracker()
    tracker.command_sent(0, target=0)  # asked to close...
    tracker.update(1, 40, OPEN_RATE)  # ...but it's opening (e.g. obstruction reversal)
    assert tracker.position(30) == 100


VENTILATION = 7


def test_preset_learns_resting_position() -> None:
    learner = motion.PresetLearner()
    assert learner.target("door", VENTILATION) is None
    learner.sent("door", VENTILATION, 0)
    assert not learner.observe("door", 1, 0, moving=False)  # not started yet
    assert not learner.observe("door", 2, 0, moving=True)
    assert learner.observe("door", 13, 75, moving=False)
    assert learner.target("door", VENTILATION) == 75


def test_preset_relearns_when_changed_in_app() -> None:
    learner = motion.PresetLearner({"door:7": 75})
    learner.sent("door", VENTILATION, 0)
    learner.observe("door", 1, 0, moving=True)
    assert learner.observe("door", 10, 60, moving=False)
    assert learner.target("door", VENTILATION) == 60


def test_preset_cancelled_by_other_command() -> None:
    learner = motion.PresetLearner()
    learner.sent("door", VENTILATION, 0)
    learner.observe("door", 1, 0, moving=True)
    learner.cancel("door")  # user hit stop
    assert not learner.observe("door", 3, 20, moving=False)
    assert learner.target("door", VENTILATION) is None


def test_preset_learning_times_out() -> None:
    learner = motion.PresetLearner()
    learner.sent("door", VENTILATION, 0)
    learner.observe("door", 1, 0, moving=True)
    assert not learner.observe("door", 500, 75, moving=False)


def test_learned_preset_target_stops_estimate() -> None:
    tracker = motion.MotionTracker()
    tracker.command_sent(0, target=75)
    tracker.update(1, 0, OPEN_RATE)
    assert tracker.position(30) == 75


def test_hub_log_start_time_wins() -> None:
    tracker = motion.MotionTracker()
    tracker.update(0, 0, 0)
    # Remote press seen 4s after the last poll, hub log says it began 1.2s ago.
    tracker.update(4, 0, OPEN_RATE, started_ago=1.2)
    assert tracker.position(4) == pytest.approx(OPEN_RATE * 1.2)


def test_hub_log_start_time_keeps_command_target() -> None:
    tracker = motion.MotionTracker()
    tracker.command_sent(0, target=50)
    tracker.update(1, 0, OPEN_RATE, started_ago=0.6)
    assert tracker.position(30) == 50
