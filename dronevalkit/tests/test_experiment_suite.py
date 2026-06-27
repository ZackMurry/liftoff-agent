"""Tests for experiment-suite helpers."""

from __future__ import annotations

import csv
import json

import dronevalkit as dvk
import pytest
from dronevalkit.analysis import ComparisonReport
from dronevalkit.config import WindCondition
from dronevalkit.experiment_suite import ScenarioSpec, default_scenarios, expand_run_plan, execute_experiment_suite
from dronevalkit.logs import DroneRunResult, RunResult, SortieResult
from dronevalkit.models import LegTiming, PlannedMetrics, Problem, Solution, Sortie


def _make_manifest_rows() -> list[dict[str, str]]:
    return [
        {
            "case_id": "case-a",
            "benchmark_family": "agatz",
            "algorithm_label": "DP",
            "source_path": "problems/agatz/solutions/example-a.txt",
            "num_customers": "8",
            "size_tier": "small",
            "num_drones": "1",
            "sortie_count": "1",
            "drone_count_tier": "single",
            "spatial_pattern": "clustered",
            "sortie_distance_profile": "short",
            "planned_makespan_s": "100.0",
            "vehicle_profile_cruise_altitude_m": "",
        },
        {
            "case_id": "case-b",
            "benchmark_family": "mfstsp",
            "algorithm_label": "Heuristic",
            "source_path": "problems/mfstsp/example-b.csv",
            "num_customers": "12",
            "size_tier": "medium",
            "num_drones": "2",
            "sortie_count": "1",
            "drone_count_tier": "multi_light",
            "spatial_pattern": "mixed",
            "sortie_distance_profile": "medium",
            "planned_makespan_s": "200.0",
            "vehicle_profile_cruise_altitude_m": "50.0",
        },
    ]


def _make_solution() -> Solution:
    return Solution(
        problem=Problem(
            depot=(38.898, -77.036),
            customers={1: (38.906, -77.043)},
            drone_eligible=[1],
        ),
        truck_route=[0, 0],
        sorties=[Sortie(delivery=1, rendezvous=0, drone_id=0)],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=100.0,
            sortie_times=[50.0],
            sortie_energies=[10.0],
            sortie_leg_times=[
                [
                    LegTiming(name="launch", start_time=0.0, end_time=10.0),
                    LegTiming(name="outbound", start_time=10.0, end_time=50.0),
                ]
            ],
        ),
        num_drones=1,
    )


def _make_run_result(condition: WindCondition) -> RunResult:
    drone_result = DroneRunResult(
        drone_id=0,
        sortie_results=[
            SortieResult(
                drone_id=0,
                sortie_index=0,
                actual_time=60.0,
                actual_energy=12.0,
                actual_distance=1200.0,
                actual_path=[],
                raw_battery_at_start=100.0,
                raw_battery_at_end=88.0,
                corrected_battery_at_end=88.0,
                feasible=True,
                max_position_error=1.0,
                leg_timings=[
                    LegTiming(name="launch", start_time=0.0, end_time=12.0),
                    LegTiming(name="outbound", start_time=12.0, end_time=60.0),
                ],
            )
        ],
        reposition_results=[],
        actual_makespan=60.0,
        raw_makespan=70.0,
        ulog_path="",
    )
    return RunResult(
        condition=condition,
        replication=0,
        drone_results=[drone_result],
        actual_makespan=60.0,
        raw_makespan=70.0,
    )


def test_expand_run_plan_crosses_manifest_with_scenarios_and_replications(tmp_path):
    plan = expand_run_plan(
        _make_manifest_rows(),
        scenarios=[
            ScenarioSpec(scenario_id="baseline", label="Calm"),
            ScenarioSpec(scenario_id="wind_moderate", wind_speed=5.0, label="Wind 5.0m/s"),
        ],
        replications=2,
        output_root=tmp_path,
    )

    assert len(plan) == 8
    assert plan[0].run_id == "case-a__baseline__rep0"
    assert plan[-1].run_id == "case-b__wind_moderate__rep1"
    assert plan[-1].altitude == 50.0


def test_expand_run_plan_applies_battery_longevity_override(tmp_path):
    plan = expand_run_plan(
        _make_manifest_rows()[:1],
        scenarios=[ScenarioSpec(scenario_id="baseline", battery_longevity=0.7, label="Calm")],
        replications=1,
        output_root=tmp_path,
        battery_longevity=1.8,
    )

    assert len(plan) == 1
    assert plan[0].battery_longevity == 1.8


def test_expand_run_plan_wind_direction_mode_sweep_rotates_by_replication(tmp_path):
    plan = expand_run_plan(
        _make_manifest_rows()[:1],
        scenarios=[ScenarioSpec(scenario_id="wind_moderate", wind_speed=5.0, wind_direction=30.0, label="Wind 5.0m/s")],
        replications=4,
        output_root=tmp_path,
        wind_direction_mode="sweep",
    )

    assert len(plan) == 4
    assert [run.wind_direction for run in plan] == pytest.approx([30.0, 120.0, 210.0, 300.0])


def test_expand_run_plan_wind_direction_mode_sweep_keeps_calm_direction(tmp_path):
    plan = expand_run_plan(
        _make_manifest_rows()[:1],
        scenarios=[ScenarioSpec(scenario_id="baseline", wind_speed=0.0, wind_direction=47.0, label="Calm")],
        replications=3,
        output_root=tmp_path,
        wind_direction_mode="sweep",
    )

    assert len(plan) == 3
    assert [run.wind_direction for run in plan] == pytest.approx([47.0, 47.0, 47.0])


def test_expand_run_plan_rejects_invalid_wind_direction_mode(tmp_path):
    with pytest.raises(ValueError, match="wind_direction_mode"):
        expand_run_plan(
            _make_manifest_rows()[:1],
            scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
            replications=1,
            output_root=tmp_path,
            wind_direction_mode="randomized",
        )


def test_default_scenarios_only_include_baseline_and_wind_conditions():
    scenario_ids = [scenario.scenario_id for scenario in default_scenarios()]

    assert scenario_ids == ["baseline", "wind_moderate", "wind_strong"]


def test_execute_experiment_suite_writes_aggregated_outputs(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    monkeypatch.setattr("dronevalkit.experiment_suite._load_solution", lambda *args, **kwargs: _make_solution())
    monkeypatch.setattr("dronevalkit.run", lambda *args, **kwargs: [_make_run_result(WindCondition.calm())])
    monkeypatch.setattr("dronevalkit.compare", lambda solution, results: ComparisonReport(solution, results))

    def _fake_route(solution, path, **kwargs):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("planned route")

    def _fake_gantt(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("gantt")

    monkeypatch.setattr("dronevalkit.save_experiment_route", _fake_route)
    monkeypatch.setattr(ComparisonReport, "plot_gantt", _fake_gantt)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
    )

    assert summary["planned_runs"] == 1
    assert summary["completed_runs"] == 1
    assert summary["retry_count"] == 0
    assert summary["wind_direction_mode"] == "fixed"
    aggregated_rows = list(csv.DictReader((tmp_path / "out" / "aggregated_results.csv").open(encoding="utf-8")))
    assert len(aggregated_rows) == 1
    assert aggregated_rows[0]["run_id"] == "case-a__baseline__rep0"
    assert aggregated_rows[0]["time_inflation"] == "1.2"
    assert aggregated_rows[0]["corrected_planned_time"] == "60.0"
    run_rows = list(csv.DictReader((tmp_path / "out" / "run_results.csv").open(encoding="utf-8")))
    assert run_rows[0]["status"] == "completed"
    assert run_rows[0]["correction_model"] == "affine"
    assert summary["robustness_algorithm_count"] == 1
    assert (tmp_path / "out" / "robustness" / "summary.csv").exists()
    assert (tmp_path / "out" / "robustness" / "ranking.pdf").exists()
    status = json.loads((tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["started_at"].endswith("Z")
    assert status["finished_at"].endswith("Z")
    experiment_log = (tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "experiment.log")
    assert experiment_log.exists()
    assert "Starting planned run case-a__baseline__rep0" in experiment_log.read_text(encoding="utf-8")
    assert (tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "planned_route.png").exists()
    assert (tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "gantt.png").exists()
    assert (tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "gantt_corrected.png").exists()


def test_execute_experiment_suite_passes_retry_count_to_run(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    seen: dict[str, object] = {}

    monkeypatch.setattr("dronevalkit.experiment_suite._load_solution", lambda *args, **kwargs: _make_solution())

    def _fake_run(*args, **kwargs):
        seen["retry_count"] = kwargs.get("retry_count")
        return [_make_run_result(WindCondition.calm())]

    monkeypatch.setattr("dronevalkit.run", _fake_run)
    monkeypatch.setattr("dronevalkit.compare", lambda solution, results: ComparisonReport(solution, results))

    def _fake_route(solution, path, **kwargs):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("planned route")

    def _fake_gantt(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("gantt")

    monkeypatch.setattr("dronevalkit.save_experiment_route", _fake_route)
    monkeypatch.setattr(ComparisonReport, "plot_gantt", _fake_gantt)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
        retry_count=2,
    )

    assert summary["retry_count"] == 2
    assert seen["retry_count"] == 2


def test_execute_experiment_suite_applies_agatz_correction_columns(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    monkeypatch.setattr("dronevalkit.experiment_suite._load_solution", lambda *args, **kwargs: _make_solution())
    monkeypatch.setattr("dronevalkit.run", lambda *args, **kwargs: [_make_run_result(WindCondition.calm())])
    monkeypatch.setattr("dronevalkit.compare", lambda solution, results: ComparisonReport(solution, results))

    def _fake_route(solution, path, **kwargs):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("planned route")

    def _fake_gantt(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("gantt")

    monkeypatch.setattr("dronevalkit.save_experiment_route", _fake_route)
    monkeypatch.setattr(ComparisonReport, "plot_gantt", _fake_gantt)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
        agatz_correction_model="fixed",
        agatz_correction_scenario_id="baseline",
    )

    assert summary["agatz_correction_model"] == "fixed"
    aggregated_rows = list(csv.DictReader((tmp_path / "out" / "aggregated_results.csv").open(encoding="utf-8")))
    assert aggregated_rows[0]["corrected_planned_time"] == "62.0"
    assert float(aggregated_rows[0]["corrected_time_inflation"]) == pytest.approx(60.0 / 62.0)
    run_rows = list(csv.DictReader((tmp_path / "out" / "run_results.csv").open(encoding="utf-8")))
    assert run_rows[0]["corrected_planned_makespan_s"] == "62.0"
    assert float(run_rows[0]["corrected_makespan_inflation"]) == pytest.approx(60.0 / 62.0)
    correction_rows = list(csv.DictReader((tmp_path / "out" / "agatz_corrections.csv").open(encoding="utf-8")))
    assert correction_rows[0]["case_id"] == "case-a"
    assert correction_rows[0]["model"] == "fixed"
    assert correction_rows[0]["mean_group_durations"] != ""
    assert (tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "gantt_corrected.png").exists()


def test_execute_experiment_suite_applies_correction_to_mfstsp_cases(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[1].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[1])

    monkeypatch.setattr("dronevalkit.experiment_suite._load_solution", lambda *args, **kwargs: _make_solution())
    monkeypatch.setattr("dronevalkit.run", lambda *args, **kwargs: [_make_run_result(WindCondition.calm())])
    monkeypatch.setattr("dronevalkit.compare", lambda solution, results: ComparisonReport(solution, results))

    def _fake_route(solution, path, **kwargs):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("planned route")

    def _fake_gantt(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("gantt")

    monkeypatch.setattr("dronevalkit.save_experiment_route", _fake_route)
    monkeypatch.setattr(ComparisonReport, "plot_gantt", _fake_gantt)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
    )

    assert summary["correction_model"] == "affine"
    aggregated_rows = list(csv.DictReader((tmp_path / "out" / "aggregated_results.csv").open(encoding="utf-8")))
    assert aggregated_rows[0]["benchmark_family"] == "mfstsp"
    assert aggregated_rows[0]["corrected_planned_time"] != ""
    correction_rows = list(csv.DictReader((tmp_path / "out" / "corrections.csv").open(encoding="utf-8")))
    assert correction_rows[0]["benchmark_family"] == "mfstsp"


def test_execute_experiment_suite_resume_skips_completed_runs(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    run_dir = tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    called = {"value": False}

    def _unexpected_call(*args, **kwargs):
        called["value"] = True
        raise AssertionError("run should not be called when resume skips completed runs")

    monkeypatch.setattr("dronevalkit.run", _unexpected_call)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
        resume=True,
    )

    assert summary["skipped_runs"] == 1
    assert called["value"] is False


def test_execute_experiment_suite_resume_skips_completed_runs_via_run_summary(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    run_dir = tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_summary.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    called = {"value": False}

    def _unexpected_call(*args, **kwargs):
        called["value"] = True
        raise AssertionError("run should not be called when resume skips completed runs")

    monkeypatch.setattr("dronevalkit.run", _unexpected_call)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
        resume=True,
    )

    assert summary["skipped_runs"] == 1
    assert called["value"] is False


def test_execute_experiment_suite_resume_falls_back_to_run_summary_when_status_json_is_bad(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    run_dir = tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text("{not valid json", encoding="utf-8")
    (run_dir / "run_summary.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    called = {"value": False}

    def _unexpected_call(*args, **kwargs):
        called["value"] = True
        raise AssertionError("run should not be called when resume skips completed runs")

    monkeypatch.setattr("dronevalkit.run", _unexpected_call)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
        resume=True,
    )

    assert summary["skipped_runs"] == 1
    assert called["value"] is False


def test_execute_experiment_suite_resume_uses_run_summary_when_status_json_says_running(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    run_dir = tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
    (run_dir / "run_summary.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    (run_dir / "gantt.png").write_text("gantt", encoding="utf-8")

    called = {"value": False}

    def _unexpected_call(*args, **kwargs):
        called["value"] = True
        raise AssertionError("run should not be called when resume skips completed runs")

    monkeypatch.setattr("dronevalkit.run", _unexpected_call)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
        resume=True,
    )

    assert summary["skipped_runs"] == 1
    assert called["value"] is False


def test_execute_experiment_suite_marks_empty_run_results_as_failure(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    monkeypatch.setattr("dronevalkit.experiment_suite._load_solution", lambda *args, **kwargs: _make_solution())
    monkeypatch.setattr("dronevalkit.run", lambda *args, **kwargs: [])

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
    )

    assert summary["completed_runs"] == 0
    assert summary["failed_runs"] == 1
    run_rows = list(csv.DictReader((tmp_path / "out" / "run_results.csv").open(encoding="utf-8")))
    assert run_rows[0]["status"] == "failed_simulation"


def test_execute_experiment_suite_deletes_simulation_logs_after_success_by_default(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    monkeypatch.setattr("dronevalkit.experiment_suite._load_solution", lambda *args, **kwargs: _make_solution())

    def _fake_run(*args, **kwargs):
        simulation_root = tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "simulation"
        log_dir = simulation_root / "Calm_rep0" / "instance_0"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "flight.ulg").write_text("ulog", encoding="utf-8")
        (log_dir / "px4_stdout.log").write_text("stdout", encoding="utf-8")
        return [_make_run_result(WindCondition.calm())]

    monkeypatch.setattr("dronevalkit.run", _fake_run)
    monkeypatch.setattr("dronevalkit.compare", lambda solution, results: ComparisonReport(solution, results))

    def _fake_route(solution, path, **kwargs):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("planned route")

    def _fake_gantt(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("gantt")

    monkeypatch.setattr("dronevalkit.save_experiment_route", _fake_route)
    monkeypatch.setattr(ComparisonReport, "plot_gantt", _fake_gantt)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
    )

    assert summary["cleanup_successful_simulation_logs"] is True
    assert not (tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "simulation").exists()


def test_execute_experiment_suite_can_keep_simulation_logs(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    monkeypatch.setattr("dronevalkit.experiment_suite._load_solution", lambda *args, **kwargs: _make_solution())

    def _fake_run(*args, **kwargs):
        simulation_root = tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "simulation"
        log_dir = simulation_root / "Calm_rep0" / "instance_0"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "flight.ulg").write_text("ulog", encoding="utf-8")
        return [_make_run_result(WindCondition.calm())]

    monkeypatch.setattr("dronevalkit.run", _fake_run)
    monkeypatch.setattr("dronevalkit.compare", lambda solution, results: ComparisonReport(solution, results))

    def _fake_route(solution, path, **kwargs):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("planned route")

    def _fake_gantt(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("gantt")

    monkeypatch.setattr("dronevalkit.save_experiment_route", _fake_route)
    monkeypatch.setattr(ComparisonReport, "plot_gantt", _fake_gantt)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
        cleanup_successful_simulation_logs=False,
    )

    assert summary["cleanup_successful_simulation_logs"] is False
    assert (tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "simulation").exists()


def test_execute_experiment_suite_deletes_simulation_logs_after_failed_run_by_default(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    monkeypatch.setattr("dronevalkit.experiment_suite._load_solution", lambda *args, **kwargs: _make_solution())

    def _fake_run(*args, **kwargs):
        simulation_root = tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "simulation"
        log_dir = simulation_root / "Calm_rep0" / "instance_0"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "flight.ulg").write_text("ulog", encoding="utf-8")
        return []

    monkeypatch.setattr("dronevalkit.run", _fake_run)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
    )

    assert summary["failed_runs"] == 1
    assert not (tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "simulation").exists()


def test_execute_experiment_suite_can_keep_simulation_logs_after_failed_run(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_make_manifest_rows()[0].keys()))
        writer.writeheader()
        writer.writerow(_make_manifest_rows()[0])

    monkeypatch.setattr("dronevalkit.experiment_suite._load_solution", lambda *args, **kwargs: _make_solution())

    def _fake_run(*args, **kwargs):
        simulation_root = tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "simulation"
        log_dir = simulation_root / "Calm_rep0" / "instance_0"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "flight.ulg").write_text("ulog", encoding="utf-8")
        return []

    monkeypatch.setattr("dronevalkit.run", _fake_run)

    summary = execute_experiment_suite(
        manifest_path,
        scenarios=[ScenarioSpec(scenario_id="baseline", label="Calm")],
        replications=1,
        output_dir=tmp_path / "out",
        cleanup_successful_simulation_logs=False,
    )

    assert summary["failed_runs"] == 1
    assert (tmp_path / "out" / "raw_runs" / "case-a__baseline__rep0" / "simulation").exists()
