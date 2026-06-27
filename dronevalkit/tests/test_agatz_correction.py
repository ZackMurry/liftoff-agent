"""Tests for calibrated Agatz timing corrections."""

from __future__ import annotations

import pytest

from dronevalkit.agatz_correction import (
    apply_sortie_time_correction,
    fit_sortie_time_correction,
    split_results,
    summarize_timing_errors,
)
from dronevalkit.config import WindCondition
from dronevalkit.logs import DroneRunResult, RunResult, SortieResult
from dronevalkit.models import LegTiming, PlannedMetrics, Problem, Solution, Sortie


def _make_solution() -> Solution:
    return Solution(
        problem=Problem(
            depot=(38.898, -77.036),
            customers={1: (38.899, -77.037), 2: (38.900, -77.038)},
            drone_eligible=[1, 2],
        ),
        truck_route=[0, 0],
        sorties=[
            Sortie(delivery=1, rendezvous=0, drone_id=0),
            Sortie(delivery=2, rendezvous=0, drone_id=0),
        ],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=250.0,
            sortie_times=[100.0, 150.0],
        ),
    )


def _make_run(replication: int, *, condition, sortie_times: tuple[float, float]) -> RunResult:
    s0_time, s1_time = sortie_times
    return RunResult(
        condition=condition,
        replication=replication,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=0,
                        actual_time=s0_time,
                        actual_energy=5.0,
                        actual_distance=1000.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=95.0,
                        corrected_battery_at_end=95.0,
                        feasible=True,
                        max_position_error=0.0,
                        leg_timings=[
                            LegTiming(name="launch_takeoff", start_time=0.0, end_time=10.0),
                            LegTiming(name="outbound", start_time=10.0, end_time=50.0),
                            LegTiming(name="delivery_land", start_time=50.0, end_time=60.0),
                            LegTiming(name="delivery", start_time=60.0, end_time=65.0),
                            LegTiming(name="delivery_takeoff", start_time=65.0, end_time=75.0),
                            LegTiming(name="return", start_time=75.0, end_time=115.0),
                            LegTiming(name="recovery_land", start_time=115.0, end_time=120.0),
                        ],
                    ),
                    SortieResult(
                        drone_id=0,
                        sortie_index=1,
                        actual_time=s1_time,
                        actual_energy=6.0,
                        actual_distance=1200.0,
                        actual_path=[],
                        raw_battery_at_start=95.0,
                        raw_battery_at_end=89.0,
                        corrected_battery_at_end=89.0,
                        feasible=True,
                        max_position_error=0.0,
                        leg_timings=[
                            LegTiming(name="launch_takeoff", start_time=0.0, end_time=10.0),
                            LegTiming(name="outbound", start_time=10.0, end_time=70.0),
                            LegTiming(name="delivery_land", start_time=70.0, end_time=80.0),
                            LegTiming(name="delivery", start_time=80.0, end_time=85.0),
                            LegTiming(name="delivery_takeoff", start_time=85.0, end_time=95.0),
                            LegTiming(name="return", start_time=95.0, end_time=155.0),
                            LegTiming(name="recovery_land", start_time=155.0, end_time=160.0),
                        ],
                    ),
                ],
                reposition_results=[],
                actual_makespan=s0_time + s1_time,
                raw_makespan=s0_time + s1_time,
                ulog_path="",
            ),
        ],
        actual_makespan=s0_time + s1_time,
        raw_makespan=s0_time + s1_time,
    )


def test_split_results_reserves_calibration_condition_and_replications():
    calm = WindCondition.calm()
    wind = WindCondition.moderate(speed=5.0)
    results = [
        _make_run(0, condition=calm, sortie_times=(120.0, 160.0)),
        _make_run(1, condition=calm, sortie_times=(120.0, 160.0)),
        _make_run(2, condition=calm, sortie_times=(120.0, 160.0)),
        _make_run(0, condition=wind, sortie_times=(125.0, 170.0)),
    ]

    calibration, evaluation = split_results(
        results,
        calibration_replications=2,
        calibration_condition="Calm",
    )

    assert len(calibration) == 2
    assert all(run.condition.label == "Calm" for run in calibration)
    assert {run.replication for run in calibration} == {0, 1}
    assert len(evaluation) == 2


def test_fit_affine_correction_uses_fixed_overhead_and_cruise_scale():
    solution = _make_solution()
    calibration_results = [
        _make_run(0, condition=WindCondition.calm(), sortie_times=(120.0, 160.0)),
    ]

    correction = fit_sortie_time_correction(
        solution,
        calibration_results,
        calibration_condition="Calm",
        model="affine",
    )

    assert correction.sample_count == 2
    assert correction.fixed_overhead == pytest.approx(40.0)
    assert correction.cruise_scale == pytest.approx(0.8)
    assert correction.mission_scale == pytest.approx((1.2 + (160.0 / 150.0)) / 2.0)


def test_apply_sortie_time_correction_scales_with_sortie_count():
    solution = _make_solution()
    solution.planned_truck_timeline = []
    solution.truck_arrival_times = {0: 0.0}
    correction = fit_sortie_time_correction(
        solution,
        [_make_run(0, condition=WindCondition.calm(), sortie_times=(120.0, 160.0))],
        calibration_condition="Calm",
        model="affine",
    )

    corrected = apply_sortie_time_correction(solution, correction)

    assert corrected.planned_metrics.sortie_times == pytest.approx([120.0, 160.0])
    assert corrected.planned_metrics.makespan == pytest.approx(280.0)
    assert corrected.planned_metrics.sortie_leg_times is None
    assert corrected.planned_truck_timeline is None
    assert corrected.truck_arrival_times is None


def test_summarize_timing_errors_reflects_corrected_predictions():
    solution = _make_solution()
    results = [_make_run(2, condition=WindCondition.calm(), sortie_times=(120.0, 160.0))]
    baseline_report = __import__("dronevalkit").compare(solution, results)

    correction = fit_sortie_time_correction(
        solution,
        [_make_run(0, condition=WindCondition.calm(), sortie_times=(120.0, 160.0))],
        calibration_condition="Calm",
        model="affine",
    )
    corrected_solution = apply_sortie_time_correction(solution, correction)
    corrected_report = __import__("dronevalkit").compare(corrected_solution, results)

    baseline_summary = summarize_timing_errors(baseline_report)
    corrected_summary = summarize_timing_errors(corrected_report)

    assert baseline_summary.mean_absolute_error == pytest.approx(15.0)
    assert corrected_summary.mean_absolute_error == pytest.approx(0.0)
