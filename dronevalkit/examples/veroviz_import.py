"""Run an experiment from VeRoViz-style dataframes.

This example builds small in-memory dataframes that match the VeRoViz import
shape expected by ``dvk.from_veroviz(...)``. It then:

- converts them into a ``dronevalkit.Solution``
- saves the imported solution as JSON
- exports a QGroundControl overlay
- runs a dronevalkit experiment
- writes report tables and figures

Run:
    python3 examples/veroviz_import.py
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

import dronevalkit as dvk

def build_nodes_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"id": 0, "lat": 38.9404, "lon": -92.3277},
            {"id": 1, "lat": 38.9410, "lon": -92.3285},
            {"id": 2, "lat": 38.9417, "lon": -92.3269},
            {"id": 3, "lat": 38.9401, "lon": -92.3266},
        ]
    )


def build_assignments_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "objectID": "truck",
                "modelFile": "truck.glb",
                "startLat": 38.9404,
                "startLon": -92.3277,
                "endLat": 38.9417,
                "endLon": -92.3269,
                "startTimeSec": 0.0,
                "endTimeSec": 100.0,
            },
            {
                "objectID": "drone0",
                "modelFile": "drone.glb",
                "startLat": 38.9404,
                "startLon": -92.3277,
                "endLat": 38.9410,
                "endLon": -92.3285,
                "startTimeSec": 0.0,
                "endTimeSec": 20.0,
            },
            {
                "objectID": "drone0",
                "modelFile": "drone.glb",
                "startLat": 38.9410,
                "startLon": -92.3285,
                "endLat": 38.9417,
                "endLon": -92.3269,
                "startTimeSec": 20.0,
                "endTimeSec": 40.0,
            },
            {
                "objectID": "drone1",
                "modelFile": "drone.glb",
                "startLat": 38.9404,
                "startLon": -92.3277,
                "endLat": 38.9401,
                "endLon": -92.3266,
                "startTimeSec": 0.0,
                "endTimeSec": 30.0,
            },
            {
                "objectID": "drone1",
                "modelFile": "drone.glb",
                "startLat": 38.9401,
                "startLon": -92.3266,
                "endLat": 38.9417,
                "endLon": -92.3269,
                "startTimeSec": 30.0,
                "endTimeSec": 60.0,
            },
            {
                "objectID": "truck",
                "modelFile": "truck.glb",
                "startLat": 38.9417,
                "startLon": -92.3269,
                "endLat": 38.9404,
                "endLon": -92.3277,
                "startTimeSec": 150.0,
                "endTimeSec": 250.0,
            },
        ]
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    nodes_df = build_nodes_df()
    assignments_df = build_assignments_df()

    solution = dvk.from_veroviz(
        assignments_df,
        nodes_df,
        drone_speed=12.0,
        truck_speed=9.0,
        num_drones=2,
        drone_eligible=[1, 3],
    )

    config = dvk.ExperimentConfig(
        solution=solution,
        conditions=[dvk.WindCondition.calm()],
        replications=1,
        speed_factor=2.0,
        altitude=20.0,
        battery=dvk.SimpleBattery(longevity=2.0),
    )

    out_dir = Path("results") / "example_veroviz_import"
    figures_dir = out_dir / "figures"
    tables_dir = out_dir / "tables"
    data_dir = out_dir / "data"

    figures_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "imported_solution.json"
    dvk.save_experiment_route(solution, str(out_dir / "route.png"))

    print("Imported VeRoViz solution:")
    print(f"  truck route: {solution.truck_route}")
    print(f"  num drones: {solution.num_drones}")
    print(f"  truck speed: {solution.truck_speed:.2f} m/s")
    print(f"  drone speed: {solution.planned_metrics.drone_speed:.2f} m/s")
    print(f"  planned sortie times: {solution.planned_metrics.sortie_times}")
    print(f"Wrote imported solution JSON to: {json_path}")

    results = dvk.run(config, parallel=1, output_dir=str(out_dir / "runs"))
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
    # report.plot_feasibility(str(figures_dir / "feasibility.pdf"), threshold=20.0)
    report.plot_paths(str(figures_dir / "paths_sortie0.pdf"), sortie_index=0)
    report.plot_gantt(str(figures_dir / "gantt.pdf"))
    print(f"Wrote outputs to: {out_dir}")


if __name__ == "__main__":
    main()
