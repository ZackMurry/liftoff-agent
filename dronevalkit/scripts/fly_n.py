"""
Parametric multi-drone smoke test: N drones flying in evenly spaced directions.

Usage:
    python3 -m scripts.test_n
    python3 -m scripts.test_n --n 8
"""

import argparse
import logging
import math
import sys

sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

import dronevalkit as dvk


DEPOT = (38.9404, -92.3277)


def _offset_gps(lat_deg: float, lon_deg: float, north_m: float, east_m: float) -> tuple[float, float]:
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * math.cos(math.radians(lat_deg)))
    return lat_deg + dlat, lon_deg + dlon


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=5, help="Number of drones/customers (default: 5)")
    args = parser.parse_args()

    n = args.n
    if n <= 0:
        print("FAIL — --n must be >= 1")
        return 1

    customers: dict[int, tuple[float, float]] = {}
    sorties: list[dvk.Sortie] = []
    radius_m = 120.0

    for i in range(n):
        customer_id = i + 1
        angle = (2.0 * math.pi * i) / n
        north = radius_m * math.cos(angle)
        east = radius_m * math.sin(angle)
        customers[customer_id] = _offset_gps(DEPOT[0], DEPOT[1], north, east)
        sorties.append(dvk.Sortie(delivery=customer_id, rendezvous=0, drone_id=i))

    problem = dvk.Problem(
        depot=DEPOT,
        customers=customers,
        drone_eligible=list(customers.keys()),
    )

    solution = dvk.Solution(
        problem=problem,
        truck_route=[0] + list(customers.keys()) + [0],
        sorties=sorties,
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=10.0,
            makespan=300.0,
            sortie_times=[90.0] * n,
        ),
        num_drones=n,
    )

    config = dvk.ExperimentConfig(
        solution=solution,
        conditions=[dvk.WindCondition.calm()],
        replications=1,
        battery=dvk.CustomBattery(capacity_mah=5000, full_drain=True, drain_rate=0.0),
        battery_print_interval_s=1.0,
        altitude=10.0,
        waypoint_tolerance=2.0,
        speed_factor=1.0,
    )

    print(f"Running N-drone experiment (n={n}, parallel=1, calm)...")
    results = dvk.run(config, parallel=1, output_dir=f"./results/dvk_test_n{n}_results")

    if not results:
        print("FAIL — no results returned")
        return 1

    r = results[0]
    print("\n=== RunResult ===")
    print(f"  condition      : {r.condition.label}")
    print(f"  replication    : {r.replication}")
    print(f"  raw_makespan   : {r.raw_makespan:.1f} s")
    print(f"  actual_makespan: {r.actual_makespan:.1f} s")
    print(f"  drone_results  : {len(r.drone_results)}")
    print("\nPASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
