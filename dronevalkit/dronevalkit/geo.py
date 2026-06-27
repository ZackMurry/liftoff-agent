"""GPS (lat/lon) <-> NED coordinate conversion utilities."""

import math


def gps_to_ned(lat: float, lon: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """
    Convert GPS (lat, lon) to NED (north, east) in meters, relative to a reference point.
    Uses equirectangular approximation (accurate for distances < 50km).
    Returns (north_m, east_m). Down is handled separately (altitude).
    """
    R = 6371000  # Earth radius in meters
    north = math.radians(lat - ref_lat) * R
    east = math.radians(lon - ref_lon) * R * math.cos(math.radians(ref_lat))
    return (north, east)


def ned_to_gps(north: float, east: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """Convert NED (north, east) in meters back to GPS (lat, lon)."""
    R = 6371000
    lat = ref_lat + math.degrees(north / R)
    lon = ref_lon + math.degrees(east / (R * math.cos(math.radians(ref_lat))))
    return (lat, lon)


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in meters between two GPS points."""
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))
