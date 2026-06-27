"""
Level 3 smoke test: multi-drone dvk.run() pipeline including Docker lifecycle.

Starts three PX4 vehicles in one Gazebo world for the same experiment run, flies one sortie
per drone under calm conditions, then prints the RunResult.

Usage:
    python3 scripts/test_multi.py
"""

import logging
import sys
sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

import dronevalkit as dvk

DEPOT = (38.9404, -92.3277)

problem = dvk.Problem(
    depot=DEPOT,
    customers={
        1: (38.9414, -92.3277),   # ~111 m north
        2: (38.9404, -92.3265),   # ~103 m east
        3: (38.9394, -92.3277),   # ~111 m south
    },
    drone_eligible=[1, 2, 3],
)

solution = dvk.Solution(
    problem=problem,
    truck_route=[0, 1, 2, 3, 0],
    sorties=[
        dvk.Sortie(delivery=1, rendezvous=0, drone_id=0),
        dvk.Sortie(delivery=2, rendezvous=0, drone_id=1),
        dvk.Sortie(delivery=3, rendezvous=0, drone_id=2),
    ],
    planned_metrics=dvk.PlannedMetrics(
        drone_speed=10.0,
        makespan=120.0,
        sortie_times=[60.0, 60.0, 60.0],
    ),
    num_drones=3,
)

config = dvk.ExperimentConfig(
    solution=solution,
    conditions=[dvk.WindCondition.calm()],
    replications=1,
    battery=dvk.CustomBattery(capacity_mah=5000, full_drain=True, drain_rate=0.0),
    altitude=10.0,
    waypoint_tolerance=2.0,
    speed_factor=1.0,
)

print("Running multi-drone experiment (parallel=1, 3 drones, calm)...")
results = dvk.run(config, parallel=1, output_dir="./results/dvk_test_multi_results")

if not results:
    print("FAIL — no results returned")
    sys.exit(1)

r = results[0]
print("\n=== RunResult ===")
print(f"  condition      : {r.condition.label}")
print(f"  replication    : {r.replication}")
print(f"  raw_makespan   : {r.raw_makespan:.1f} s")
print(f"  actual_makespan: {r.actual_makespan:.1f} s")
print(f"  drone_results  : {len(r.drone_results)}")

for dr in r.drone_results:
    print(
        f"  drone {dr.drone_id}: raw={dr.raw_makespan:.1f}s "
        f"actual={dr.actual_makespan:.1f}s ulog={dr.ulog_path}"
    )
    print(f"    sortie_results : {len(dr.sortie_results)}")
    for sr in dr.sortie_results:
        print(
            f"      sortie {sr.sortie_index}: "
            f"time={sr.actual_time:.1f}s  "
            f"energy={sr.actual_energy:.1f}%  "
            f"distance={sr.actual_distance:.1f}m  "
            f"feasible={sr.feasible}  "
            f"corrected_batt={sr.corrected_battery_at_end:.1f}%"
        )
    print(f"    reposition_results: {len(dr.reposition_results)}")

print("\nPASS")
