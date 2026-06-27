"""Tests for the experiment monitor snapshot builder."""

from __future__ import annotations

import csv
import json

import dronevalkit as dvk


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def test_build_monitor_snapshot_tracks_live_run_statuses(tmp_path):
    root = tmp_path / "results" / "experiments"
    suite_dir = root / "demo"
    raw_runs = suite_dir / "raw_runs"
    raw_runs.mkdir(parents=True)

    _write_csv(
        suite_dir / "run_plan.csv",
        [
            {
                "run_id": "case-a__baseline__rep0",
                "case_id": "case-a",
                "scenario_id": "baseline",
                "replication": "0",
                "benchmark_family": "agatz",
                "algorithm_label": "DP",
                "source_path": "problems/agatz/example-a.txt",
                "output_dir": str(raw_runs / "case-a__baseline__rep0"),
                "wind_speed": "0.0",
                "wind_direction": "0.0",
                "battery_longevity": "1.0",
                "altitude": "",
                "speed_factor": "1.0",
                "scenario_label": "Calm",
            },
            {
                "run_id": "case-b__baseline__rep0",
                "case_id": "case-b",
                "scenario_id": "baseline",
                "replication": "0",
                "benchmark_family": "mfstsp",
                "algorithm_label": "IP",
                "source_path": "problems/mfstsp/example-b.csv",
                "output_dir": str(raw_runs / "case-b__baseline__rep0"),
                "wind_speed": "0.0",
                "wind_direction": "0.0",
                "battery_longevity": "1.0",
                "altitude": "50.0",
                "speed_factor": "1.0",
                "scenario_label": "Calm",
            },
            {
                "run_id": "case-c__wind__rep0",
                "case_id": "case-c",
                "scenario_id": "wind",
                "replication": "0",
                "benchmark_family": "agatz",
                "algorithm_label": "Heuristic",
                "source_path": "problems/agatz/example-c.txt",
                "output_dir": str(raw_runs / "case-c__wind__rep0"),
                "wind_speed": "5.0",
                "wind_direction": "0.0",
                "battery_longevity": "1.0",
                "altitude": "",
                "speed_factor": "1.0",
                "scenario_label": "Wind 5.0m/s",
            },
            {
                "run_id": "case-d__wind__rep0",
                "case_id": "case-d",
                "scenario_id": "wind",
                "replication": "0",
                "benchmark_family": "agatz",
                "algorithm_label": "DP",
                "source_path": "problems/agatz/example-d.txt",
                "output_dir": str(raw_runs / "case-d__wind__rep0"),
                "wind_speed": "5.0",
                "wind_direction": "0.0",
                "battery_longevity": "1.0",
                "altitude": "",
                "speed_factor": "1.0",
                "scenario_label": "Wind 5.0m/s",
            },
        ],
    )

    completed_dir = raw_runs / "case-a__baseline__rep0"
    completed_dir.mkdir()
    (completed_dir / "status.json").write_text(
        json.dumps(
            {
                "run_id": "case-a__baseline__rep0",
                "case_id": "case-a",
                "scenario_id": "baseline",
                "scenario_label": "Calm",
                "replication": 0,
                "benchmark_family": "agatz",
                "algorithm_label": "DP",
                "source_path": "problems/agatz/example-a.txt",
                "output_dir": str(completed_dir),
                "sortie_count": 2,
                "status": "completed",
                "error": "",
                "sortie_row_count": 2,
                "planned_makespan_s": 100.0,
                "actual_makespan_s": 120.0,
                "raw_makespan_s": 122.0,
                "mean_time_inflation": 1.2,
                "feasible_sortie_rate": 1.0,
                "started_at": "2026-04-20T00:00:00Z",
                "finished_at": "2026-04-20T00:02:00Z",
                "updated_at": "2026-04-20T00:02:00Z",
                "updated_at_ts": 10.0,
                "duration_s": 120.0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (completed_dir / "planned_route.png").write_text("route", encoding="utf-8")
    (completed_dir / "gantt.png").write_text("gantt", encoding="utf-8")
    (completed_dir / "experiment.log").write_text("completed log", encoding="utf-8")
    simulation_log = completed_dir / "simulation" / "Calm_rep0" / "instance_0"
    simulation_log.mkdir(parents=True)
    (simulation_log / "px4_stdout.log").write_text("px4 stdout", encoding="utf-8")

    running_dir = raw_runs / "case-b__baseline__rep0"
    running_dir.mkdir()
    (running_dir / "status.json").write_text(
        json.dumps(
            {
                "run_id": "case-b__baseline__rep0",
                "case_id": "case-b",
                "scenario_id": "baseline",
                "scenario_label": "Calm",
                "replication": 0,
                "benchmark_family": "mfstsp",
                "algorithm_label": "IP",
                "source_path": "problems/mfstsp/example-b.csv",
                "output_dir": str(running_dir),
                "sortie_count": 1,
                "status": "running",
                "error": "",
                "sortie_row_count": 0,
                "planned_makespan_s": 200.0,
                "actual_makespan_s": None,
                "raw_makespan_s": None,
                "mean_time_inflation": None,
                "feasible_sortie_rate": None,
                "started_at": "2026-04-20T00:03:00Z",
                "finished_at": None,
                "updated_at": "2026-04-20T00:03:00Z",
                "updated_at_ts": 20.0,
                "duration_s": 0.0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    failed_dir = raw_runs / "case-c__wind__rep0"
    failed_dir.mkdir()
    (failed_dir / "status.json").write_text(
        json.dumps(
            {
                "run_id": "case-c__wind__rep0",
                "case_id": "case-c",
                "scenario_id": "wind",
                "scenario_label": "Wind 5.0m/s",
                "replication": 0,
                "benchmark_family": "agatz",
                "algorithm_label": "Heuristic",
                "source_path": "problems/agatz/example-c.txt",
                "output_dir": str(failed_dir),
                "sortie_count": 3,
                "status": "failed_simulation",
                "error": "PX4 did not connect",
                "sortie_row_count": 0,
                "planned_makespan_s": 180.0,
                "actual_makespan_s": None,
                "raw_makespan_s": None,
                "mean_time_inflation": None,
                "feasible_sortie_rate": None,
                "started_at": "2026-04-20T00:04:00Z",
                "finished_at": "2026-04-20T00:05:30Z",
                "updated_at": "2026-04-20T00:05:30Z",
                "updated_at_ts": 30.0,
                "duration_s": 90.0,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    _write_csv(
        suite_dir / "run_results.csv",
        [
            {
                "run_id": "case-a__baseline__rep0",
                "case_id": "case-a",
                "scenario_id": "baseline",
                "scenario_label": "Calm",
                "replication": "0",
                "output_dir": str(completed_dir),
                "benchmark_family": "agatz",
                "algorithm_label": "DP",
                "source_path": "problems/agatz/example-a.txt",
                "sortie_count": "2",
                "status": "completed",
                "error": "",
                "sortie_row_count": "2",
                "planned_makespan_s": "100.0",
                "actual_makespan_s": "120.0",
                "raw_makespan_s": "122.0",
                "mean_time_inflation": "1.2",
                "feasible_sortie_rate": "1.0",
                "duration_s": "120.0",
                "corrected_planned_makespan_s": "95.0",
                "corrected_makespan_inflation": "1.26",
                "corrected_mean_time_inflation": "1.1",
                "correction_model": "affine",
                "agatz_correction_model": "affine",
            }
        ],
    )

    (suite_dir / "summary.json").write_text(
        json.dumps(
            {
                "manifest_path": "experiments/test_beds/demo/manifest.csv",
                "output_dir": str(suite_dir),
                "correction_model": "affine",
                "correction_scenario_id": "baseline",
                "scenarios": [{"scenario_id": "baseline"}, {"scenario_id": "wind"}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    snapshot = dvk.build_monitor_snapshot(root)

    assert snapshot["suite_count"] == 1
    assert snapshot["totals"] == {
        "planned_runs": 4,
        "completed_runs": 1,
        "failed_runs": 1,
        "running_runs": 1,
        "pending_runs": 1,
    }

    suite = snapshot["suites"][0]
    assert suite["name"] == "demo"
    assert suite["manifest_path"] == "experiments/test_beds/demo/manifest.csv"
    assert suite["correction_model"] == "affine"
    assert suite["planned_runs"] == 4
    assert suite["completed_runs"] == 1
    assert suite["failed_runs"] == 1
    assert suite["running_runs"] == 1
    assert suite["pending_runs"] == 1
    assert len(suite["recent_failures"]) == 1
    assert suite["recent_failures"][0]["error"] == "PX4 did not connect"

    runs_by_id = {run["run_id"]: run for run in suite["runs"]}
    assert runs_by_id["case-a__baseline__rep0"]["corrected_mean_time_inflation"] == 1.1
    assert runs_by_id["case-a__baseline__rep0"]["artifacts"]["planned_route"].endswith("planned_route.png")
    assert runs_by_id["case-a__baseline__rep0"]["artifacts"]["experiment_log"].endswith("experiment.log")
    assert runs_by_id["case-a__baseline__rep0"]["artifacts"]["px4_stdout_0"].endswith("px4_stdout.log")
    assert runs_by_id["case-b__baseline__rep0"]["status"] == "running"
    assert runs_by_id["case-c__wind__rep0"]["status"] == "failed_simulation"
    assert runs_by_id["case-d__wind__rep0"]["status"] == "pending"


def test_build_monitor_snapshot_ignores_non_suite_summary_json(tmp_path):
    root = tmp_path / "results" / "experiments"
    (root / "demo" / "robustness").mkdir(parents=True)
    (root / "demo" / "robustness" / "summary.json").write_text("{}", encoding="utf-8")

    snapshot = dvk.build_monitor_snapshot(root)

    assert snapshot["suite_count"] == 0


def test_build_monitor_snapshot_uses_status_json_for_resumed_suites(tmp_path):
    root = tmp_path / "results" / "experiments"
    suite_dir = root / "demo"
    raw_runs = suite_dir / "raw_runs"
    raw_runs.mkdir(parents=True)

    run_a_dir = raw_runs / "case-a__baseline__rep0"
    run_b_dir = raw_runs / "case-b__baseline__rep0"

    _write_csv(
        suite_dir / "run_plan.csv",
        [
            {
                "run_id": "case-a__baseline__rep0",
                "case_id": "case-a",
                "scenario_id": "baseline",
                "replication": "0",
                "benchmark_family": "agatz",
                "algorithm_label": "DP",
                "source_path": "problems/agatz/example-a.txt",
                "output_dir": str(run_a_dir),
                "wind_speed": "0.0",
                "wind_direction": "0.0",
                "battery_longevity": "1.0",
                "altitude": "",
                "speed_factor": "1.0",
                "scenario_label": "Calm",
            },
            {
                "run_id": "case-b__baseline__rep0",
                "case_id": "case-b",
                "scenario_id": "baseline",
                "replication": "0",
                "benchmark_family": "agatz",
                "algorithm_label": "DP",
                "source_path": "problems/agatz/example-b.txt",
                "output_dir": str(run_b_dir),
                "wind_speed": "0.0",
                "wind_direction": "0.0",
                "battery_longevity": "1.0",
                "altitude": "",
                "speed_factor": "1.0",
                "scenario_label": "Calm",
            },
        ],
    )

    for run_id, run_dir, updated_at_ts in [
        ("case-a__baseline__rep0", run_a_dir, 10.0),
        ("case-b__baseline__rep0", run_b_dir, 20.0),
    ]:
        run_dir.mkdir()
        (run_dir / "status.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "case_id": run_id.split("__", 1)[0],
                    "scenario_id": "baseline",
                    "scenario_label": "Calm",
                    "replication": 0,
                    "benchmark_family": "agatz",
                    "algorithm_label": "DP",
                    "source_path": f"problems/agatz/{run_id}.txt",
                    "output_dir": str(run_dir),
                    "sortie_count": 1,
                    "status": "completed",
                    "error": "",
                    "sortie_row_count": 1,
                    "planned_makespan_s": 100.0,
                    "actual_makespan_s": 120.0,
                    "raw_makespan_s": 122.0,
                    "mean_time_inflation": 1.2,
                    "feasible_sortie_rate": 1.0,
                    "started_at": "2026-04-20T00:00:00Z",
                    "finished_at": "2026-04-20T00:02:00Z",
                    "updated_at": "2026-04-20T00:02:00Z",
                    "updated_at_ts": updated_at_ts,
                    "duration_s": 120.0,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        (run_dir / "experiment.log").write_text("completed log", encoding="utf-8")

    # Simulate a resumed suite writing only the latest invocation's aggregate
    # files; the earlier completed run no longer appears in run_results.csv.
    _write_csv(
        suite_dir / "run_results.csv",
        [
            {
                "run_id": "case-b__baseline__rep0",
                "case_id": "case-b",
                "scenario_id": "baseline",
                "scenario_label": "Calm",
                "replication": "0",
                "output_dir": str(run_b_dir),
                "benchmark_family": "agatz",
                "algorithm_label": "DP",
                "source_path": "problems/agatz/example-b.txt",
                "sortie_count": "1",
                "status": "completed",
                "error": "",
                "sortie_row_count": "1",
                "planned_makespan_s": "100.0",
                "actual_makespan_s": "120.0",
                "raw_makespan_s": "122.0",
                "mean_time_inflation": "1.2",
                "feasible_sortie_rate": "1.0",
                "duration_s": "120.0",
                "corrected_planned_makespan_s": "95.0",
                "corrected_makespan_inflation": "1.26",
                "corrected_mean_time_inflation": "1.1",
                "correction_model": "affine",
                "agatz_correction_model": "affine",
            }
        ],
    )

    (suite_dir / "summary.json").write_text(
        json.dumps(
            {
                "manifest_path": "experiments/test_beds/demo/manifest.csv",
                "output_dir": str(suite_dir),
                "planned_runs": 1,
                "completed_runs": 1,
                "failed_runs": 0,
                "skipped_runs": 1,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    snapshot = dvk.build_monitor_snapshot(root)

    assert snapshot["suite_count"] == 1
    assert snapshot["totals"]["planned_runs"] == 2
    assert snapshot["totals"]["completed_runs"] == 2

    suite = snapshot["suites"][0]
    assert suite["planned_runs"] == 2
    assert suite["completed_runs"] == 2
    assert suite["failed_runs"] == 0
    assert suite["running_runs"] == 0
    assert suite["pending_runs"] == 0

    runs_by_id = {run["run_id"]: run for run in suite["runs"]}
    assert runs_by_id["case-a__baseline__rep0"]["status"] == "completed"
    assert runs_by_id["case-b__baseline__rep0"]["status"] == "completed"
    assert "px4_stdout_0" not in runs_by_id["case-a__baseline__rep0"]["artifacts"]
    assert runs_by_id["case-a__baseline__rep0"]["artifacts"]["status"].endswith("status.json")
