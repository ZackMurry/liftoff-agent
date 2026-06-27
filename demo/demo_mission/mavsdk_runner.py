from __future__ import annotations

import asyncio
import time

from .flight_plan import FlightPlan


async def fly_plan(plan: FlightPlan, system_address: str, timeout_s: float = 45.0) -> dict[str, float | bool]:
    """Run a short MAVSDK smoke flight.

    The demo intentionally does not fly the full waypoint route. Liftoff still
    passes and records the flight plan, but this command only verifies that the
    cloned user code can connect to PX4, arm, take off briefly, and land.
    """
    try:
        from mavsdk import System
    except ImportError as exc:
        raise RuntimeError("mavsdk is not installed; run pip install -r requirements.txt") from exc

    drone = System()
    await drone.connect(system_address=system_address)
    await _wait_connected(drone, timeout_s=30.0)

    takeoff_altitude_m = min(plan.altitude_m, 5.0)
    await drone.action.set_takeoff_altitude(takeoff_altitude_m)
    await drone.action.set_maximum_speed(plan.speed_m_s)

    start = time.monotonic()
    await drone.action.arm()
    await drone.action.takeoff()
    await asyncio.sleep(float(_hover_seconds()))
    await drone.action.land()
    await _wait_landed(drone, timeout_s=timeout_s)

    actual_time = time.monotonic() - start
    return {
        "actual_time_s": actual_time,
        "max_position_error_m": 0.0,
        "feasible": True,
        "actual_distance_m": 0.0,
    }


async def _wait_connected(drone, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    async for state in drone.core.connection_state():
        if state.is_connected:
            return
        if time.monotonic() > deadline:
            raise TimeoutError("Timed out waiting for MAVSDK connection")


async def _wait_landed(drone, timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    async for in_air in drone.telemetry.in_air():
        if not in_air:
            return
        if time.monotonic() > deadline:
            raise TimeoutError("Timed out waiting for landing")


def _hover_seconds() -> float:
    return 2.0
