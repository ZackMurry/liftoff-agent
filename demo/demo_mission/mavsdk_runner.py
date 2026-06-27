from __future__ import annotations

import asyncio
import math
import time

from .flight_plan import FlightPlan, Waypoint, distance_m


async def fly_plan(plan: FlightPlan, system_address: str, timeout_s: float = 240.0) -> dict[str, float | bool]:
    """Fly the plan using MAVSDK.

    This is intentionally simple demo control code. It exercises the integration
    point Liftoff cares about: user-owned MAVSDK logic managing a PX4 vehicle.
    """
    try:
        from mavsdk import System
    except ImportError as exc:
        raise RuntimeError("mavsdk is not installed; run pip install -r requirements.txt") from exc

    drone = System()
    await drone.connect(system_address=system_address)
    await _wait_connected(drone, timeout_s=30.0)

    await drone.action.set_takeoff_altitude(plan.altitude_m)
    await drone.action.set_maximum_speed(plan.speed_m_s)

    start = time.monotonic()
    await drone.action.arm()
    await drone.action.takeoff()
    await asyncio.sleep(5.0)

    max_error = 0.0
    current = plan.home
    for waypoint in plan.waypoints:
        await drone.action.goto_location(
            waypoint.lat,
            waypoint.lon,
            plan.altitude_m,
            0.0,
        )
        leg_error = await _wait_near_waypoint(
            drone,
            waypoint,
            plan.acceptance_radius_m,
            timeout_s=timeout_s,
        )
        max_error = max(max_error, leg_error)
        current = waypoint

    await drone.action.return_to_launch()
    await _wait_landed(drone, timeout_s=timeout_s)

    actual_time = time.monotonic() - start
    return {
        "actual_time_s": actual_time,
        "max_position_error_m": max_error,
        "feasible": True,
        "actual_distance_m": _route_distance(plan.home, [*plan.waypoints, current]),
    }


async def _wait_connected(drone, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    async for state in drone.core.connection_state():
        if state.is_connected:
            return
        if time.monotonic() > deadline:
            raise TimeoutError("Timed out waiting for MAVSDK connection")


async def _wait_near_waypoint(
    drone,
    waypoint: Waypoint,
    acceptance_radius_m: float,
    timeout_s: float,
) -> float:
    deadline = time.monotonic() + timeout_s
    best_error = math.inf
    async for position in drone.telemetry.position():
        error = distance_m(Waypoint(position.latitude_deg, position.longitude_deg), waypoint)
        best_error = min(best_error, error)
        if error <= acceptance_radius_m:
            return float(error)
        if time.monotonic() > deadline:
            raise TimeoutError(f"Timed out reaching waypoint within {acceptance_radius_m}m")


async def _wait_landed(drone, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    async for in_air in drone.telemetry.in_air():
        if not in_air:
            return
        if time.monotonic() > deadline:
            raise TimeoutError("Timed out waiting for landing")


def _route_distance(home: Waypoint, waypoints: list[Waypoint]) -> float:
    points = [home, *waypoints, home]
    return sum(distance_m(a, b) for a, b in zip(points, points[1:]))
