"""Tests for dronevalkit.geo coordinate conversion utilities."""

import math
import pytest
from dronevalkit.geo import gps_to_ned, ned_to_gps, haversine_distance


REF_LAT = 38.898
REF_LON = -77.036


# ---------------------------------------------------------------------------
# gps_to_ned
# ---------------------------------------------------------------------------

def test_gps_to_ned_origin():
    """Reference point maps to (0, 0)."""
    n, e = gps_to_ned(REF_LAT, REF_LON, REF_LAT, REF_LON)
    assert n == pytest.approx(0.0)
    assert e == pytest.approx(0.0)


def test_gps_to_ned_north():
    """Point due north should have positive N, zero E."""
    delta_lat = 0.001  # ~111 m north
    n, e = gps_to_ned(REF_LAT + delta_lat, REF_LON, REF_LAT, REF_LON)
    expected_n = math.radians(delta_lat) * 6371000
    assert n == pytest.approx(expected_n, rel=1e-4)
    assert abs(e) < 0.01  # essentially zero


def test_gps_to_ned_east():
    """Point due east should have zero N, positive E."""
    delta_lon = 0.001  # varies with latitude
    n, e = gps_to_ned(REF_LAT, REF_LON + delta_lon, REF_LAT, REF_LON)
    expected_e = math.radians(delta_lon) * 6371000 * math.cos(math.radians(REF_LAT))
    assert abs(n) < 0.01
    assert e == pytest.approx(expected_e, rel=1e-4)


def test_gps_to_ned_south_west():
    """Point south-west should have negative N and negative E."""
    n, e = gps_to_ned(REF_LAT - 0.01, REF_LON - 0.01, REF_LAT, REF_LON)
    assert n < 0
    assert e < 0


# ---------------------------------------------------------------------------
# ned_to_gps (round-trip)
# ---------------------------------------------------------------------------

def test_ned_to_gps_origin():
    lat, lon = ned_to_gps(0.0, 0.0, REF_LAT, REF_LON)
    assert lat == pytest.approx(REF_LAT)
    assert lon == pytest.approx(REF_LON)


def test_round_trip_north():
    orig_lat = REF_LAT + 0.005
    orig_lon = REF_LON
    n, e = gps_to_ned(orig_lat, orig_lon, REF_LAT, REF_LON)
    lat, lon = ned_to_gps(n, e, REF_LAT, REF_LON)
    assert lat == pytest.approx(orig_lat, rel=1e-6)
    assert lon == pytest.approx(orig_lon, abs=1e-8)


def test_round_trip_arbitrary():
    orig_lat = REF_LAT + 0.014
    orig_lon = REF_LON - 0.007
    n, e = gps_to_ned(orig_lat, orig_lon, REF_LAT, REF_LON)
    lat, lon = ned_to_gps(n, e, REF_LAT, REF_LON)
    assert lat == pytest.approx(orig_lat, rel=1e-6)
    assert lon == pytest.approx(orig_lon, rel=1e-6)


# ---------------------------------------------------------------------------
# haversine_distance
# ---------------------------------------------------------------------------

def test_haversine_same_point():
    d = haversine_distance(REF_LAT, REF_LON, REF_LAT, REF_LON)
    assert d == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_distance():
    # 1 degree of latitude ≈ 111,195 m
    d = haversine_distance(0.0, 0.0, 1.0, 0.0)
    assert d == pytest.approx(111195, rel=0.001)


def test_haversine_symmetry():
    d1 = haversine_distance(REF_LAT, REF_LON, REF_LAT + 0.01, REF_LON + 0.01)
    d2 = haversine_distance(REF_LAT + 0.01, REF_LON + 0.01, REF_LAT, REF_LON)
    assert d1 == pytest.approx(d2)


def test_haversine_small_distance():
    # ~100 m north
    d = haversine_distance(REF_LAT, REF_LON, REF_LAT + 0.0009, REF_LON)
    assert 95 < d < 105


def test_haversine_consistent_with_ned():
    """Haversine distance should roughly match NED Euclidean distance for short legs."""
    lat2, lon2 = REF_LAT + 0.008, REF_LON - 0.005
    hav = haversine_distance(REF_LAT, REF_LON, lat2, lon2)
    n, e = gps_to_ned(lat2, lon2, REF_LAT, REF_LON)
    ned_dist = math.sqrt(n**2 + e**2)
    assert hav == pytest.approx(ned_dist, rel=0.001)
