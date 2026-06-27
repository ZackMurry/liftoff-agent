from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


DEFAULT_HOME = (38.898, -77.036)
DEFAULT_WAYPOINTS = (
    (38.899, -77.035),
    (38.900, -77.034),
)

SCENARIO_DEFAULTS: dict[str, dict[str, Any]] = {
    "waypoint_mission": {},
    "crosswind": {"acceptance_radius_m": 3.0},
    "crosswind_mission": {"acceptance_radius_m": 3.0},
    "crosswind_stability": {"acceptance_radius_m": 3.0},
    "tight_turns": {
        "waypoints": (
            (38.8984, -77.0355),
            (38.8988, -77.0365),
            (38.8992, -77.0355),
        ),
        "acceptance_radius_m": 4.0,
    },
    "low_battery_rtl": {"acceptance_radius_m": 4.0},
    "emergency_stop": {"acceptance_radius_m": 4.0},
}


@dataclass(frozen=True)
class Waypoint:
    lat: float
    lon: float


@dataclass(frozen=True)
class FlightPlan:
    home: Waypoint
    waypoints: tuple[Waypoint, ...]
    altitude_m: float = 5.0
    speed_m_s: float = 8.0
    acceptance_radius_m: float = 3.0

    def __post_init__(self) -> None:
        if not self.waypoints:
            raise ValueError("flight plan must include at least one waypoint")
        if self.altitude_m <= 0:
            raise ValueError("altitude_m must be positive")
        if self.speed_m_s <= 0:
            raise ValueError("speed_m_s must be positive")
        if self.acceptance_radius_m <= 0:
            raise ValueError("acceptance_radius_m must be positive")

    @property
    def planned_distance_m(self) -> float:
        points = [self.home, *self.waypoints, self.home]
        return sum(distance_m(a, b) for a, b in zip(points, points[1:]))

    @property
    def planned_time_s(self) -> float:
        climb_land_time_s = (self.altitude_m / 3.0) * 2.0
        return min(20.0, self.planned_distance_m / self.speed_m_s + climb_land_time_s)


def parse_flight_plan(params: dict[str, Any]) -> FlightPlan:
    """Parse explicit or scenario-style Liftoff params into a flight plan."""
    scenario = str(params.get("scenario", "")).strip().lower()
    scenario_defaults = SCENARIO_DEFAULTS.get(scenario, {})
    raw_plan = params.get("flight_plan")
    if raw_plan is not None:
        if not isinstance(raw_plan, dict):
            raise ValueError("flight_plan must be an object")
        source = raw_plan
    else:
        source = params

    home = _waypoint(source.get("home", source.get("depot", scenario_defaults.get("home", DEFAULT_HOME))), "home")
    raw_waypoints = source.get("waypoints", scenario_defaults.get("waypoints", DEFAULT_WAYPOINTS))
    if not isinstance(raw_waypoints, (list, tuple)):
        raise ValueError("waypoints must be a list")
    waypoints = tuple(_waypoint(value, f"waypoint[{idx}]") for idx, value in enumerate(raw_waypoints))

    return FlightPlan(
        home=home,
        waypoints=waypoints,
        altitude_m=float(source.get("altitude_m", source.get("altitude", scenario_defaults.get("altitude_m", 5.0)))),
        speed_m_s=float(source.get("speed_m_s", source.get("drone_speed", scenario_defaults.get("speed_m_s", 8.0)))),
        acceptance_radius_m=float(source.get("acceptance_radius_m", scenario_defaults.get("acceptance_radius_m", 3.0))),
    )


def distance_m(a: Waypoint, b: Waypoint) -> float:
    earth_radius_m = 6_371_000.0
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    d_lat = lat2 - lat1
    d_lon = math.radians(b.lon - a.lon)
    h = (
        math.sin(d_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2.0) ** 2
    )
    return 2.0 * earth_radius_m * math.asin(math.sqrt(h))


def _waypoint(raw: Any, label: str) -> Waypoint:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"{label} must be [lat, lon]")
    lat = float(raw[0])
    lon = float(raw[1])
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"{label} latitude out of range")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"{label} longitude out of range")
    return Waypoint(lat=lat, lon=lon)
