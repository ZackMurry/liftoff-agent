# dronevalkit

Validate drone-assisted delivery routes from operations research (OR) solvers in PX4 SITL simulation. Measure the difference between optimized plans and realistic flight performance.

OR literature often assumes constant drone speed, linear energy models, and instantaneous takeoff/landing.
dronevalkit simulates those optimized plans in PX4 to detect time inflation, energy underestimation, and outright infeasibility under realistic flight dynamics and wind.

## Quick Start

```bash
pip install dronevalkit
```

```python
import dronevalkit as dvk

problem = dvk.Problem(
    depot=(38.898, -77.036),
    customers={1: (38.906, -77.043), 2: (38.912, -77.030), 3: (38.904, -77.022)},
    drone_eligible=[1, 2],
)

solution = dvk.Solution(
    problem=problem,
    truck_route=[0, 3, 0],
    sorties=[
        dvk.Sortie(delivery=1, rendezvous=3),
        dvk.Sortie(delivery=2, rendezvous=0),
    ],
    planned_metrics=dvk.PlannedMetrics(
        drone_speed=10.0, makespan=600, sortie_times=[180, 210],
    ),
)

config = dvk.ExperimentConfig(
    solution=solution,
    conditions=[dvk.WindCondition.calm(), dvk.WindCondition.moderate(speed=5.0)],
    replications=3,
)

results = dvk.run(config, output_dir="./results")
report = dvk.compare(solution, results)
report.summary()
report.feasibility()
report.plot_scatter("figures/time_scatter.pdf")
dvk.save_qgc_overlay(solution)
```

## Prerequisites

- **Python 3.9+**
- **Docker**

## How It Works

1. **Define** your problem and solution using OR conventions (depot, customers, truck route, drone sorties)
2. **Configure** experimental conditions (wind, battery, replications)
3. **Run**: dronevalkit launches a Docker container with PX4 and Gazebo, executes your experiment, and collects flight logs
4. **Analyze**: compare planned vs. actual time, energy, and feasibility

## Multi-Drone

dronevalkit supports scenarios with multiple drones in a single Gazebo world:

```python
solution = dvk.Solution(
    ...
    sorties=[
        dvk.Sortie(delivery=1, rendezvous=0, drone_id=0),
        dvk.Sortie(delivery=2, rendezvous=0, drone_id=1),
    ],
    num_drones=2,
)
```

## Collected Metrics

- **Time inflation factor**: actual sortie flight time / planned flight time
- **Energy multiplier**: actual energy consumed / planned energy
- **Corrected battery curve**: cumulative energy from sorties only (repositioning excluded), used to determine feasibility
- **Feasibility rate**: percentage of sorties that complete without violating battery constraints
- **Correction factors**: per-condition multipliers to feed back into OR models, plus per-leg timing inflation by condition

## Outputs

- `report.summary()` — formatted degradation table
- `report.feasibility()` — infeasibility analysis
- `report.correction_factors()` — multipliers for OR model feedback
- `report.to_latex(path)` — publication-ready LaTeX table
- `report.to_csv(path)` — raw results for further analysis
- `report.to_leg_latex(path)` — publication-ready per-leg timing inflation table
- `report.to_leg_csv(path)` — per-leg timing inflation dataset keyed by condition and leg
- `report.to_paper_leg_latex(path)` — grouped paper-facing leg timing inflation table
- `report.to_paper_leg_csv(path)` — grouped paper-facing leg timing dataset
- `report.plot_scatter(path)` — planned vs. actual (the headline figure)
- `report.plot_feasibility(path)` — battery margin erosion
- `report.plot_paths(path)` — straight-line planned vs. actual trajectory
- `report.plot_gantt(path)` — planned vs. actual timeline
- `dvk.render_qgc_overlay_qml(solution)` — render a QGroundControl map overlay as QML
- `dvk.save_qgc_overlay(solution, path=None, overlay_name="...")` — save a QGroundControl map overlay `.qml` file

## Experiment Monitor

dronevalkit includes a small built-in web portal for watching experiment-suite outputs on a server:

```bash
dronevalkit-monitor --root results/experiments --host 0.0.0.0 --port 8000
```

You can also run it as a module:

```bash
python3 -m dronevalkit.monitor --root results/experiments
```

The monitor reads `run_plan.csv`, per-run `raw_runs/*/status.json`, and finished `run_results.csv` files to show live suite progress, failures, and artifact links such as `planned_route.png` and `gantt.png`.

## QGroundControl Overlay Export

Generate a `.qml` overlay that visualizes the truck route, drone sorties, depot, and customer waypoints:

```python
import dronevalkit as dvk

qml_text = dvk.render_qgc_overlay_qml(solution, overlay_name="My Mission")
saved_path = dvk.save_qgc_overlay(solution, overlay_name="My Mission")
custom_path = dvk.save_qgc_overlay(solution, path="qgc/MyMissionOverlay.qml")
```

By default, `dvk.save_qgc_overlay(solution)` writes `./dronevalkit_overlay.qml`. If `path` is provided, that exact output path is used instead. The exported QML uses a `MapItemGroup` root with `MapPolyline` and `MapQuickItem` children so it can be embedded into a custom QGroundControl map layer.

## VeRoViz Integration

Import solutions directly from [VeRoViz](https://veroviz.org/) dataframes:

```python
solution = dvk.from_veroviz(
    assignments_df,
    nodes_df,
    drone_speed=12.0,
    truck_speed=9.0,
    num_drones=2,
)
```

## mFSTSP Benchmark Integration

Import Murray mFSTSP benchmark solutions directly from the benchmark CSV files:

```python
solution = dvk.from_mfstsp(
    "problems/mfstsp/20170608T131251001523/tbl_solutions_101_1_Heuristic.csv"
)

for case in dvk.list_mfstsp_cases("problems/mfstsp"):
    solution = dvk.from_mfstsp(case.solution_path)
    # run validation for each problem/solution pair
```

## Docker Image

The simulation environment is packaged as `zackmurry/dronevalkit-sim` on [DockerHub](https://hub.docker.com/repository/docker/zackmurry/dronevalkit-sim). To run manually:

```bash
docker run -it --rm --network host \
    -e PX4_HOME_LAT=38.898 \
    -e PX4_HOME_LON=-77.036 \
    -e PX4_HOME_ALT=0 \
    zackmurry/dronevalkit-sim:latest \
    bash -c "HEADLESS=1 make px4_sitl gz_x500"
```

## License

MIT
