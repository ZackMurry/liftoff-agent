"""
Minimal smoke test for MAVSDK `action.goto_location()`.

Runs a simple square path at a fixed GPS altitude to validate how PX4 SITL
interprets `goto_location()` independently of dronevalkit's mission logic.

Usage:
    # Terminal 1 — start a container:
    docker run -d --rm --network host \
        -e PX4_HOME_LAT=38.898 -e PX4_HOME_LON=-77.036 -e PX4_HOME_ALT=0 \
        zackmurry/dronevalkit-sim:latest \
        bash -c "HEADLESS=1 make px4_sitl gz_x500"

    # Terminal 2 — run this script:
    python3 scripts/test_goto_location.py
"""

import asyncio
import logging
import sys

sys.path.insert(0, ".")

from dronevalkit import CustomBattery, WindCondition
from dronevalkit import geo
from dronevalkit.flight import connect, configure_for_experiment


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


async def get_position(drone):
    async for pos in drone.telemetry.position():
        return pos
    raise RuntimeError("No position telemetry sample received")


async def wait_until_close(
    drone,
    target_lat: float,
    target_lon: float,
    target_abs_alt: float,
    tolerance_m: float = 2.0,
    timeout_s: float = 120.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        pos = await get_position(drone)
        horizontal = geo.haversine_distance(
            pos.latitude_deg,
            pos.longitude_deg,
            target_lat,
            target_lon,
        )
        vertical = abs(pos.absolute_altitude_m - target_abs_alt)
        logging.info(
            "pos lat=%.6f lon=%.6f abs=%.1f rel=%.1f horiz_err=%.1f vert_err=%.1f",
            pos.latitude_deg,
            pos.longitude_deg,
            pos.absolute_altitude_m,
            pos.relative_altitude_m,
            horizontal,
            vertical,
        )
        if (horizontal ** 2 + vertical ** 2) ** 0.5 < tolerance_m:
            return
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError(
                f"Timed out reaching lat={target_lat:.6f} lon={target_lon:.6f} alt={target_abs_alt:.1f}"
            )
        await asyncio.sleep(1.0)


async def wait_until_altitude_stable(
    drone,
    target_rel_alt_m: float,
    tolerance_m: float = 1.0,
) -> float:
    stable = 0
    deadline = asyncio.get_running_loop().time() + 60.0
    while stable < 3:
        pos = await get_position(drone)
        logging.info(
            "takeoff pos lat=%.6f lon=%.6f abs=%.1f rel=%.1f",
            pos.latitude_deg,
            pos.longitude_deg,
            pos.absolute_altitude_m,
            pos.relative_altitude_m,
        )
        if abs(pos.relative_altitude_m - target_rel_alt_m) < tolerance_m:
            stable += 1
        else:
            stable = 0
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("Timed out stabilizing after takeoff")
        await asyncio.sleep(1.0)
    return pos.absolute_altitude_m


async def main():
    print("Connecting to PX4...")
    drone = await connect("udp://:14540", timeout=60.0)
    print("Connected.")

    print("Configuring experiment parameters...")
    await configure_for_experiment(
        drone,
        CustomBattery(capacity_mah=5000, full_drain=True, drain_rate=0.0),
        WindCondition.calm(),
    )
    print("Configured.")

    start = await get_position(drone)
    start_lat = start.latitude_deg
    start_lon = start.longitude_deg
    print(f"Start GPS: lat={start_lat:.6f} lon={start_lon:.6f}")

    print("Arming and taking off to 10m...")
    await drone.action.set_takeoff_altitude(10.0)
    await drone.action.arm()
    await drone.action.takeoff()

    target_abs_alt = await wait_until_altitude_stable(drone, 10.0)
    print(f"Takeoff stabilized at abs_alt={target_abs_alt:.1f}m")

    print("Switching to hold before square path...")
    await drone.action.hold()
    await asyncio.sleep(2.0)

    corners_ned = [
        (20.0, 0.0),
        (20.0, 20.0),
        (0.0, 20.0),
        (0.0, 0.0),
    ]
    corners_gps = [
        geo.ned_to_gps(north, east, start_lat, start_lon)
        for north, east in corners_ned
    ]

    for index, (lat, lon) in enumerate(corners_gps, start=1):
        print(
            f"Corner {index}: goto_location lat={lat:.6f} lon={lon:.6f} abs_alt={target_abs_alt:.1f}"
        )
        await drone.action.goto_location(lat, lon, target_abs_alt, 0.0)
        await wait_until_close(drone, lat, lon, target_abs_alt, tolerance_m=2.0)
        print(f"Reached corner {index}")

    print("Landing...")
    await drone.action.land()
    async for in_air in drone.telemetry.in_air():
        if not in_air:
            break
    print("Done.")


asyncio.run(main())
