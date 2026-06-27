"""
Level 1/2 smoke test: flight.py against a manually-started container.

Usage:
    # Terminal 1 — start a container:
    docker run -d --rm --network host \
        -e PX4_HOME_LAT=38.898 -e PX4_HOME_LON=-77.036 -e PX4_HOME_ALT=0 \
        zackmurry/dronevalkit-sim:latest \
        bash -c "HEADLESS=1 make px4_sitl gz_x500"

    # Terminal 2 — run this script (wait ~20s for PX4 to boot first):
    python3 scripts/test_flight.py
"""

import asyncio
import sys
sys.path.insert(0, ".")

from dronevalkit import CustomBattery, WindCondition
from dronevalkit.flight import (
    connect,
    configure_for_experiment,
    fly_mission,
    get_battery_pct,
)


# One small sortie in GPS coordinates.
SORTIE_WAYPOINTS = [
    {
        "launch":      (38.898000, -77.036000),
        "delivery":    (38.898899, -77.036000),
        "rendezvous":  (38.898450, -77.036000),
    },
]


async def main():
    print("Connecting to PX4...")
    drone = await connect("udp://:14540", timeout=60.0)
    print("Connected.")

    batt = await get_battery_pct(drone)
    print(f"Battery at start: {batt:.1f}%")

    print("Configuring experiment parameters...")
    await configure_for_experiment(
        drone,
        CustomBattery(capacity_mah=5000, full_drain=True, drain_rate=2.0),
        WindCondition.calm(),
    )
    print("Configured.")

    print("Flying 1-sortie mission (altitude=10m, tolerance=2m)...")
    log = await fly_mission(
        drone,
        SORTIE_WAYPOINTS,
        altitude=10.0,
        tolerance=2.0,
        reference_gps=SORTIE_WAYPOINTS[0]["launch"],
    )

    print("\n=== MissionLog ===")
    print(f"  Total time : {log.total_time:.1f} s")
    print(f"  ULog path  : {log.ulog_path}")
    for seg in log.segments:
        kind = seg.segment_type.upper()
        idx  = f"#{seg.sortie_index}" if seg.sortie_index is not None else "repo"
        print(
            f"  [{kind} {idx}]  "
            f"{seg.start_time:.1f}s – {seg.end_time:.1f}s  "
            f"battery {seg.battery_at_start:.1f}% → {seg.battery_at_end:.1f}%  "
            f"positions={len(seg.positions)}"
        )
    print("\nPASS")


asyncio.run(main())
