"""Tests for per-drone waypoint offset helpers."""

from __future__ import annotations

import math

import pytest

from dronevalkit import _offset_gps_for_drone
from dronevalkit import geo


def test_offset_gps_for_drone_keeps_drone_zero_on_actual_target():
    gps = (38.898, -77.036)

    offset_gps = _offset_gps_for_drone(gps, drone_id=0, num_drones=3)

    assert offset_gps == pytest.approx(gps)


def test_offset_gps_for_drone_keeps_single_drone_on_actual_target():
    gps = (38.898, -77.036)

    offset_gps = _offset_gps_for_drone(gps, drone_id=0, num_drones=1)

    assert offset_gps == pytest.approx(gps)


def test_offset_gps_for_drone_spaces_only_nonzero_drones_around_circle():
    gps = (38.898, -77.036)
    radius_m = 3.0

    drone_1 = _offset_gps_for_drone(gps, drone_id=1, num_drones=3, radius_m=radius_m)
    drone_2 = _offset_gps_for_drone(gps, drone_id=2, num_drones=3, radius_m=radius_m)

    north_1, east_1 = geo.gps_to_ned(drone_1[0], drone_1[1], gps[0], gps[1])
    north_2, east_2 = geo.gps_to_ned(drone_2[0], drone_2[1], gps[0], gps[1])

    assert north_1 == pytest.approx(radius_m, abs=1e-3)
    assert east_1 == pytest.approx(0.0, abs=1e-3)
    assert north_2 == pytest.approx(-radius_m, abs=1e-3)
    assert east_2 == pytest.approx(0.0, abs=1e-3)
    assert math.hypot(north_1, east_1) == pytest.approx(radius_m, abs=1e-3)
    assert math.hypot(north_2, east_2) == pytest.approx(radius_m, abs=1e-3)
