"""End-to-end multi-drone experiment example for dronevalkit.

This example configures:
- 2 drones
- Multiple sorties per drone
- A moving truck route with multiple intermediate nodes
- Multiple wind conditions and replications

Run:
    python3 examples/experiment.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import dronevalkit as dvk


def build_solution() -> dvk.Solution:
    """Create a 2-drone plan with a truck that moves across rendezvous nodes."""
    problem = dvk.Problem(
        depot=(38.8980, -77.0360),
        customers={
            1: (38.8988, -77.0368),
            2: (38.8993, -77.0363),
            3: (38.8992, -77.0353),
            4: (38.8999, -77.0348),
            5: (38.9003, -77.0354),
            6: (38.8978, -77.0350),
            7: (38.8997, -77.0366),
        },
        drone_eligible=[1, 2, 6, 7],
    )

    sorties = [
        dvk.Sortie(delivery=1, rendezvous=3, drone_id=0),
        dvk.Sortie(delivery=6, rendezvous=3, drone_id=1),
        dvk.Sortie(delivery=2, rendezvous=4, drone_id=0),
        dvk.Sortie(delivery=7, rendezvous=5, drone_id=1),
    ]

    planned_metrics = dvk.PlannedMetrics(
        drone_speed=10.0,
        makespan=360.0,
        sortie_times=[60.0, 55.0, 65.0, 70.0],
        sortie_energies=[4.5, 4.0, 5.0, 5.5],
    )

    return dvk.Solution(
        problem=problem,
        truck_route=[0, 3, 4, 5, 0],
        sorties=sorties,
        planned_metrics=planned_metrics,
        num_drones=2,
        truck_speed=8.33,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    solution = build_solution()

    config = dvk.ExperimentConfig(
        solution=solution,
        conditions=[
            dvk.WindCondition.calm(),
            # dvk.WindCondition.moderate(speed=5.0, direction=90.0),
            # dvk.WindCondition.strong(speed=10.0, direction=180.0),
        ],
        replications=1,
        speed_factor=2.0,
        altitude=20.0,
        battery=dvk.SimpleBattery(longevity=2.0)
    )

    out_root = Path("results") / "example_experiment"
    figures_dir = out_root / "figures"
    tables_dir = out_root / "tables"
    data_dir = out_root / "data"

    figures_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    overlay_path = dvk.save_qgc_overlay(
        solution,
        path=str(out_root / "dronevalkit_overlay.qml"),
        overlay_name="example experiment",
    )
    print(f"Wrote QGroundControl overlay to: {overlay_path}")

    results = dvk.run(config, parallel=1, output_dir=str(out_root / "runs"))
    if not results:
        raise RuntimeError("No run results were produced.")

    report = dvk.compare(solution, results)

    report.summary()
    report.feasibility()

    corrections = report.correction_factors()
    print("Correction factors:")
    print(corrections)

    report.to_latex(str(tables_dir / "summary.tex"))
    report.to_leg_latex(str(tables_dir / "leg_time_inflation.tex"))
    report.to_paper_leg_latex(str(tables_dir / "paper_leg_time_inflation.tex"))
    report.to_csv(str(data_dir / "raw_results.csv"))
    report.to_leg_csv(str(data_dir / "leg_time_inflation.csv"))
    report.to_paper_leg_csv(str(data_dir / "paper_leg_time_inflation.csv"))

    report.plot_scatter(str(figures_dir / "time_scatter.pdf"), metric="time")
    report.plot_scatter(str(figures_dir / "energy_scatter.pdf"), metric="energy")
    report.plot_feasibility(str(figures_dir / "feasibility.pdf"), threshold=20.0)
    report.plot_paths(str(figures_dir / "paths_sortie0.pdf"), sortie_index=0)
    report.plot_gantt(str(figures_dir / "gantt.pdf"))
    print(f"Wrote outputs to: {out_root}")


if __name__ == "__main__":
    main()
