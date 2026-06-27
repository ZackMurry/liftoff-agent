"""
Level 3 smoke test: full dvk.run() pipeline including Docker lifecycle.

Starts its own container, runs a 1-sortie mission under calm conditions,
then prints the RunResult.

Usage:
    python3 scripts/test_run.py
"""

import logging
import sys
sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

import dronevalkit as dvk

problem = dvk.Problem(
    depot=(38.9404, -92.3277),
    customers={1: (38.9414, -92.3277)},   # ~111 m north of depot
    drone_eligible=[1],
)

solution = dvk.Solution(
    problem=problem,
    truck_route=[0, 1, 0],
    sorties=[dvk.Sortie(delivery=1, rendezvous=0, drone_id=0)],
    planned_metrics=dvk.PlannedMetrics(
        drone_speed=10.0,
        makespan=120,
        sortie_times=[60],
    ),
    num_drones=1,
)

config = dvk.ExperimentConfig(
    solution=solution,
    conditions=[dvk.WindCondition.calm()],
    replications=1,
    # battery=dvk.SimpleBattery(longevity=1.0),
    battery=dvk.InfiniteBattery(),
    battery_print_interval_s=1.0,
    altitude=10.0,
    # takeoff_only=True,
    waypoint_tolerance=2.0,
    speed_factor=2.0,
)

print("Running experiment (parallel=1, 1 sortie, 1 replication, calm)...")
results = dvk.run(config, parallel=1, output_dir="./results/dvk_test_results")

if not results:
    print("FAIL — no results returned")
    sys.exit(1)

r = results[0]
print(f"\n=== RunResult ===")
print(f"  condition      : {r.condition.label}")
print(f"  replication    : {r.replication}")
print(f"  raw_makespan   : {r.raw_makespan:.1f} s")
print(f"  actual_makespan: {r.actual_makespan:.1f} s")
print(f"  drone_results  : {len(r.drone_results)}")
for dr in r.drone_results:
    print(f"  drone {dr.drone_id}: raw={dr.raw_makespan:.1f}s actual={dr.actual_makespan:.1f}s ulog={dr.ulog_path}")
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
