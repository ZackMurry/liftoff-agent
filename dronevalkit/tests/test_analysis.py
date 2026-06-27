"""Tests for dronevalkit.analysis."""

from __future__ import annotations

import csv

import pytest

from dronevalkit.analysis import (
    AggregateSummaryRow,
    ComparisonReport,
    CorrectionFactors,
    FeasibilityReport,
    PairedTestResult,
    StatisticalReport,
)
from dronevalkit.config import WindCondition
from dronevalkit.logs import DroneRunResult, RunResult, SortieResult
from dronevalkit.models import LegTiming, PlannedMetrics, Problem, Solution, Sortie


def _make_solution(num_drones: int = 1) -> Solution:
    problem = Problem(
        depot=(38.898, -77.036),
        customers={1: (38.906, -77.043), 2: (38.912, -77.030)},
        drone_eligible=[1, 2],
    )
    sorties = [
        Sortie(delivery=1, rendezvous=0, drone_id=0),
        Sortie(delivery=2, rendezvous=0, drone_id=0),
    ]
    return Solution(
        problem=problem,
        truck_route=[0, 0],
        sorties=sorties,
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=360.0,
            sortie_times=[100.0, 150.0],
            sortie_energies=[10.0, 15.0],
            sortie_leg_times=[
                [
                    LegTiming(name="launch", start_time=0.0, end_time=10.0),
                    LegTiming(name="outbound", start_time=10.0, end_time=60.0),
                ],
                [
                    LegTiming(name="launch", start_time=0.0, end_time=15.0),
                    LegTiming(name="outbound", start_time=15.0, end_time=90.0),
                ],
            ],
        ),
        num_drones=num_drones,
    )


def _make_run(condition: WindCondition, replication: int, s0: tuple[float, float, float, bool], s1: tuple[float, float, float, bool]) -> RunResult:
    s0_time, s0_energy, s0_batt, s0_feasible = s0
    s1_time, s1_energy, s1_batt, s1_feasible = s1

    drone_result = DroneRunResult(
        drone_id=0,
        sortie_results=[
            SortieResult(
                drone_id=0,
                sortie_index=0,
                actual_time=s0_time,
                actual_energy=s0_energy,
                actual_distance=1200.0,
                actual_path=[(0.0, 0.0, -20.0, 0.0), (10.0, 5.0, -20.0, 2.0)],
                raw_battery_at_start=100.0,
                raw_battery_at_end=s0_batt,
                corrected_battery_at_end=s0_batt,
                feasible=s0_feasible,
                max_position_error=1.0,
                leg_timings=[
                    LegTiming(name="launch", start_time=0.0, end_time=12.0),
                    LegTiming(name="outbound", start_time=12.0, end_time=62.0),
                ],
            ),
            SortieResult(
                drone_id=0,
                sortie_index=1,
                actual_time=s1_time,
                actual_energy=s1_energy,
                actual_distance=1400.0,
                actual_path=[(0.0, 0.0, -20.0, 0.0), (12.0, 7.0, -20.0, 2.0)],
                raw_battery_at_start=s0_batt,
                raw_battery_at_end=s1_batt,
                corrected_battery_at_end=s1_batt,
                feasible=s1_feasible,
                max_position_error=1.2,
                leg_timings=[
                    LegTiming(name="launch", start_time=0.0, end_time=18.0),
                    LegTiming(name="outbound", start_time=18.0, end_time=95.0),
                ],
            ),
        ],
        reposition_results=[],
        actual_makespan=s0_time + s1_time,
        raw_makespan=s0_time + s1_time + 20.0,
        ulog_path="",
    )

    return RunResult(
        condition=condition,
        replication=replication,
        drone_results=[drone_result],
        actual_makespan=drone_result.actual_makespan,
        raw_makespan=drone_result.raw_makespan,
    )


def _make_results() -> list[RunResult]:
    calm = WindCondition.calm()
    wind = WindCondition.moderate(speed=5.0)
    return [
        _make_run(calm, 0, (110.0, 11.0, 89.0, True), (165.0, 17.0, 72.0, True)),
        _make_run(calm, 1, (100.0, 10.0, 90.0, True), (150.0, 15.0, 75.0, True)),
        _make_run(wind, 0, (130.0, 13.5, 86.5, True), (180.0, 19.0, 18.0, False)),
        _make_run(wind, 1, (120.0, 12.0, 88.0, True), (170.0, 18.0, 17.0, False)),
    ]


def _make_suite_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    cases = [
        ("case-a", "DP", "small"),
        ("case-b", "DP", "small"),
        ("case-c", "Heuristic", "medium"),
        ("case-d", "Heuristic", "medium"),
    ]
    baseline_values = {
        ("case-a", 0): 1.02,
        ("case-a", 1): 1.05,
        ("case-b", 0): 1.01,
        ("case-b", 1): 1.04,
        ("case-c", 0): 0.98,
        ("case-c", 1): 1.01,
        ("case-d", 0): 0.99,
        ("case-d", 1): 1.00,
    }
    wind_deltas = {
        ("case-a", 0): 0.15,
        ("case-a", 1): 0.25,
        ("case-b", 0): 0.30,
        ("case-b", 1): 0.40,
        ("case-c", 0): 0.12,
        ("case-c", 1): 0.18,
        ("case-d", 0): 0.22,
        ("case-d", 1): 0.28,
    }
    for case_id, algorithm_label, size_tier in cases:
        for replication in (0, 1):
            baseline = baseline_values[(case_id, replication)]
            rows.append(
                {
                    "case_id": case_id,
                    "scenario_id": "baseline",
                    "scenario_label": "Calm",
                    "algorithm_label": algorithm_label,
                    "size_tier": size_tier,
                    "sortie_index": "0",
                    "replication": str(replication),
                    "time_inflation": f"{baseline}",
                    "feasible": "True",
                }
            )
            rows.append(
                {
                    "case_id": case_id,
                    "scenario_id": "wind_moderate",
                    "scenario_label": "Wind 5.0m/s",
                    "algorithm_label": algorithm_label,
                    "size_tier": size_tier,
                    "sortie_index": "0",
                    "replication": str(replication),
                    "time_inflation": f"{baseline + wind_deltas[(case_id, replication)]}",
                    "feasible": "True",
                }
            )
    return rows


def test_summary_prints_table_and_omits_drone_column_for_single_drone(capsys):
    report = ComparisonReport(_make_solution(num_drones=1), _make_results())

    report.summary()

    out = capsys.readouterr().out
    assert "Sortie" in out
    assert "Condition" in out
    assert "Actual Time" in out
    assert "Drone" not in out
    assert "ALL" in out


def test_metric_summary_rows_include_confidence_intervals():
    report = ComparisonReport(_make_solution(), _make_results())

    rows = report.metric_summary_rows(metric="time_inflation", group_by=("condition",))

    assert len(rows) == 2
    calm = next(row for row in rows if row.group_values["condition"] == "Calm")
    assert isinstance(calm, AggregateSummaryRow)
    assert calm.sample_count == 4
    assert calm.mean == pytest.approx(1.05)
    assert calm.std > 0.0
    assert calm.ci_low < calm.mean < calm.ci_high


def test_paired_condition_test_rows_report_effect_sizes():
    report = ComparisonReport(_make_solution(), _make_results())

    rows = report.paired_condition_test_rows(
        metric="time_inflation",
        baseline_condition="Calm",
        pair_by=("sortie_index", "replication"),
    )

    assert len(rows) == 1
    paired = rows[0]
    assert isinstance(paired, PairedTestResult)
    assert paired.comparison_value == "Wind 5.0m/s"
    assert paired.pair_count == 4
    assert paired.mean_delta == pytest.approx(0.1583333333)
    assert paired.ci_low < paired.mean_delta < paired.ci_high
    assert paired.p_value is not None and paired.p_value < 0.05
    assert paired.effect_size_dz is not None and paired.effect_size_dz > 1.0


def test_feasibility_report_counts_and_critical_pairs(capsys):
    report = ComparisonReport(_make_solution(), _make_results())

    feasibility = report.feasibility()

    assert isinstance(feasibility, FeasibilityReport)
    assert feasibility.total_sortie_runs == 8
    assert feasibility.infeasible_count == 2
    assert feasibility.infeasibility_by_condition["Calm"] == pytest.approx(0.0)
    assert feasibility.infeasibility_by_condition["Wind 5.0m/s"] == pytest.approx(0.5)
    assert feasibility.critical_sorties == [(1, "Wind 5.0m/s")]

    out = capsys.readouterr().out
    assert "Total sortie-runs: 8" in out
    assert "Infeasible: 2" in out


def test_statistical_report_groups_suite_rows_for_paper_tables(tmp_path):
    report = StatisticalReport(_make_suite_rows())

    paper_rows = report.paper_summary_rows(
        metric="time_inflation",
        group_by=("algorithm_label", "scenario_label", "size_tier"),
    )

    assert len(paper_rows) == 4
    baseline_dp = next(
        row
        for row in paper_rows
        if row.group_values["algorithm_label"] == "DP"
        and row.group_values["scenario_label"] == "Calm"
        and row.group_values["size_tier"] == "small"
    )
    assert baseline_dp.sample_count == 4
    assert baseline_dp.mean == pytest.approx(1.03)

    paired_rows = report.paired_test_rows(
        "time_inflation",
        compare_column="scenario_id",
        baseline_value="baseline",
        pair_by=("case_id", "sortie_index", "replication"),
        group_by=("algorithm_label", "size_tier"),
    )

    assert len(paired_rows) == 2
    heuristic = next(row for row in paired_rows if row.group_values["algorithm_label"] == "Heuristic")
    assert heuristic.comparison_value == "wind_moderate"
    assert heuristic.pair_count == 4
    assert heuristic.mean_delta == pytest.approx(0.2)
    assert heuristic.effect_size_dz is not None and heuristic.effect_size_dz > 1.0

    summary_csv = tmp_path / "paper_summary.csv"
    paired_csv = tmp_path / "paired.csv"
    latex_path = tmp_path / "paper_summary.tex"
    report.to_metric_summary_csv(
        str(summary_csv),
        "time_inflation",
        group_by=("algorithm_label", "scenario_label", "size_tier"),
    )
    report.to_paired_test_csv(
        str(paired_csv),
        "time_inflation",
        compare_column="scenario_id",
        baseline_value="baseline",
        pair_by=("case_id", "sortie_index", "replication"),
        group_by=("algorithm_label", "size_tier"),
    )
    report.to_metric_summary_latex(
        str(latex_path),
        "time_inflation",
        group_by=("algorithm_label", "scenario_label", "size_tier"),
    )

    summary_rows = list(csv.DictReader(summary_csv.open(encoding="utf-8")))
    paired_csv_rows = list(csv.DictReader(paired_csv.open(encoding="utf-8")))
    assert summary_rows[0]["metric"] == "time_inflation"
    assert paired_csv_rows[0]["compare_column"] == "scenario_id"
    assert "95\\% CI" in latex_path.read_text(encoding="utf-8")


def test_correction_factors_returns_expected_ratios():
    report = ComparisonReport(_make_solution(), _make_results())

    factors = report.correction_factors()

    assert isinstance(factors, CorrectionFactors)
    assert factors.time_inflation["Calm"] == pytest.approx(1.05)
    assert factors.time_inflation["Wind 5.0m/s"] == pytest.approx(1.2083333333)
    assert factors.leg_time_inflation["Calm"]["launch"] == pytest.approx(1.2)
    assert factors.leg_time_inflation["Calm"]["outbound"] == pytest.approx(1.016)
    assert factors.leg_time_inflation["Wind 5.0m/s"]["launch"] == pytest.approx(1.2)
    assert factors.leg_time_inflation["Wind 5.0m/s"]["outbound"] == pytest.approx(1.016)
    assert factors.energy_multiplier["Calm"] == pytest.approx(1.0583333333)
    assert factors.energy_multiplier["Wind 5.0m/s"] == pytest.approx(1.2541666667)
    assert factors.min_safe_margin == pytest.approx(72.0)
    assert "Calm" in factors.distance_inflation


def test_correction_factors_report_missing_energy_when_planned_energy_absent():
    solution = _make_solution()
    solution.planned_metrics.sortie_energies = None
    report = ComparisonReport(solution, _make_results())

    factors = report.correction_factors()

    assert factors.energy_multiplier["Calm"] is None
    assert factors.energy_multiplier["Wind 5.0m/s"] is None


def test_to_csv_writes_flat_raw_rows(tmp_path):
    report = ComparisonReport(_make_solution(), _make_results())
    out_path = tmp_path / "raw.csv"

    report.to_csv(str(out_path))

    assert out_path.exists()
    with out_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 8
    assert rows[0]["sortie_index"] == "0"
    assert rows[0]["condition"] in {"Calm", "Wind 5.0m/s"}
    assert "corrected_battery_at_end" in rows[0]
    assert "feasible" in rows[0]


def test_to_csv_leaves_planned_energy_blank_when_missing(tmp_path):
    solution = _make_solution()
    solution.planned_metrics.sortie_energies = None
    report = ComparisonReport(solution, _make_results())
    out_path = tmp_path / "raw.csv"

    report.to_csv(str(out_path))

    with out_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert rows[0]["planned_energy"] == ""


def test_to_latex_writes_tabular(tmp_path):
    report = ComparisonReport(_make_solution(), _make_results())
    out_path = tmp_path / "summary.tex"

    report.to_latex(str(out_path))

    text = out_path.read_text(encoding="utf-8")
    assert "\\begin{table}" in text
    assert "\\begin{tabular}" in text
    assert "Actual Time (mean$\\pm$std)" in text
    assert "TODO: comparison summary caption" in text


def test_to_latex_uses_na_for_missing_energy(tmp_path):
    solution = _make_solution()
    solution.planned_metrics.sortie_energies = None
    report = ComparisonReport(solution, _make_results())
    out_path = tmp_path / "summary.tex"

    report.to_latex(str(out_path))

    text = out_path.read_text(encoding="utf-8")
    assert "n/a" in text


def test_to_leg_csv_writes_condition_by_leg_rows(tmp_path):
    report = ComparisonReport(_make_solution(), _make_results())
    out_path = tmp_path / "leg.csv"

    report.to_leg_csv(str(out_path))

    with out_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 4
    calm_launch = next(
        row for row in rows if row["condition"] == "Calm" and row["leg_name"] == "launch"
    )
    assert calm_launch["sample_count"] == "4"
    assert float(calm_launch["time_inflation"]) == pytest.approx(1.2)


def test_to_leg_latex_writes_leg_timing_table(tmp_path):
    report = ComparisonReport(_make_solution(), _make_results())
    out_path = tmp_path / "leg_summary.tex"

    report.to_leg_latex(str(out_path))

    text = out_path.read_text(encoding="utf-8")
    assert "\\begin{table}" in text
    assert "Condition & Leg & N & Planned Time" in text
    assert "TODO: per-leg timing inflation caption" in text
    assert "launch" in text
    assert "Wind 5.0m/s" in text


def test_to_leg_latex_escapes_underscores_without_double_escaping(tmp_path):
    problem = Problem(
        depot=(38.898, -77.036),
        customers={1: (38.906, -77.043)},
        drone_eligible=[1],
    )
    solution = Solution(
        problem=problem,
        truck_route=[0, 0],
        sorties=[Sortie(delivery=1, rendezvous=0, drone_id=0)],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=20.0,
            sortie_times=[20.0],
            sortie_energies=[2.0],
            sortie_leg_times=[
                [LegTiming(name="delivery_land", start_time=0.0, end_time=4.0)],
            ],
        ),
    )
    run = RunResult(
        condition=WindCondition.calm(),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=0,
                        actual_time=5.0,
                        actual_energy=2.0,
                        actual_distance=10.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=98.0,
                        corrected_battery_at_end=98.0,
                        feasible=True,
                        max_position_error=0.0,
                        leg_timings=[LegTiming(name="delivery_land", start_time=0.0, end_time=5.0)],
                    ),
                ],
                reposition_results=[],
                actual_makespan=5.0,
                raw_makespan=5.0,
                ulog_path="",
            ),
        ],
        actual_makespan=5.0,
        raw_makespan=5.0,
    )
    report = ComparisonReport(solution, [run])
    out_path = tmp_path / "leg_summary.tex"

    report.to_leg_latex(str(out_path))

    text = out_path.read_text(encoding="utf-8")
    assert "delivery\\_land" in text
    assert "textbackslash" not in text


def test_paper_leg_summary_groups_mfstsp_style_legs_and_omits_waiting(capsys):
    problem = Problem(
        depot=(38.898, -77.036),
        customers={1: (38.906, -77.043)},
        drone_eligible=[1],
    )
    solution = Solution(
        problem=problem,
        truck_route=[0, 0],
        sorties=[Sortie(delivery=1, rendezvous=0, drone_id=0)],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=168.0,
            sortie_times=[168.0],
            sortie_energies=[5.0],
            sortie_leg_times=[
                [
                    LegTiming(name="launch_prep", start_time=0.0, end_time=60.0),
                    LegTiming(name="launch_takeoff", start_time=60.0, end_time=64.0),
                    LegTiming(name="outbound", start_time=64.0, end_time=72.0),
                    LegTiming(name="delivery_land", start_time=72.0, end_time=76.0),
                    LegTiming(name="delivery", start_time=76.0, end_time=96.0),
                    LegTiming(name="delivery_takeoff", start_time=96.0, end_time=100.0),
                    LegTiming(name="return", start_time=100.0, end_time=108.0),
                    LegTiming(name="waiting", start_time=108.0, end_time=146.0),
                    LegTiming(name="recovery_land", start_time=146.0, end_time=150.0),
                    LegTiming(name="recovery", start_time=150.0, end_time=180.0),
                ]
            ],
        ),
    )
    run = RunResult(
        condition=WindCondition.calm(),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=0,
                        actual_time=190.0,
                        actual_energy=5.0,
                        actual_distance=10.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=95.0,
                        corrected_battery_at_end=95.0,
                        feasible=True,
                        max_position_error=0.0,
                        leg_timings=[
                            LegTiming(name="launch_prep", start_time=0.0, end_time=60.0),
                            LegTiming(name="launch_takeoff", start_time=60.0, end_time=80.0),
                            LegTiming(name="outbound", start_time=80.0, end_time=94.0),
                            LegTiming(name="delivery_land", start_time=94.0, end_time=108.0),
                            LegTiming(name="delivery", start_time=108.0, end_time=130.0),
                            LegTiming(name="delivery_takeoff", start_time=130.0, end_time=141.0),
                            LegTiming(name="return", start_time=141.0, end_time=151.0),
                            LegTiming(name="waiting", start_time=151.0, end_time=151.0),
                            LegTiming(name="recovery_land", start_time=151.0, end_time=168.0),
                            LegTiming(name="recovery", start_time=168.0, end_time=198.0),
                        ],
                    )
                ],
                reposition_results=[],
                actual_makespan=190.0,
                raw_makespan=190.0,
                ulog_path="",
            )
        ],
        actual_makespan=190.0,
        raw_makespan=190.0,
    )
    report = ComparisonReport(solution, [run])

    report.paper_leg_summary()

    out = capsys.readouterr().out
    assert "launch_fixed" in out
    assert "vertical_takeoff" in out
    assert "vertical_landing" in out
    assert "cruise_outbound" in out
    assert "service" in out
    assert "cruise_return" in out
    assert "recovery_fixed" in out
    assert "waiting" not in out


def test_to_paper_leg_csv_writes_grouped_rows(tmp_path):
    report = ComparisonReport(_make_solution(), _make_results())
    out_path = tmp_path / "paper_leg.csv"

    report.to_paper_leg_csv(str(out_path))

    with out_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 4
    calm_launch = next(
        row for row in rows
        if row["condition"] == "Calm" and row["paper_leg_group"] == "launch_fixed"
    )
    assert calm_launch["source_legs"] == "launch"
    assert calm_launch["sample_count"] == "4"
    outbound = next(
        row for row in rows
        if row["condition"] == "Wind 5.0m/s" and row["paper_leg_group"] == "cruise_outbound"
    )
    assert float(outbound["time_inflation"]) == pytest.approx(1.016)
    assert all(row["paper_leg_group"] != "vertical_takeoff" for row in rows)


def test_to_paper_leg_latex_writes_grouped_table(tmp_path):
    report = ComparisonReport(_make_solution(), _make_results())
    out_path = tmp_path / "paper_leg.tex"

    report.to_paper_leg_latex(str(out_path))

    text = out_path.read_text(encoding="utf-8")
    assert "\\begin{table}" in text
    assert "Condition & Group & Legs & N & Planned Time" in text
    assert "TODO: grouped per-leg timing inflation caption" in text
    assert "launch\\_fixed" in text
    assert "cruise\\_outbound" in text


def test_leg_summary_prints_leg_level_table(capsys):
    report = ComparisonReport(_make_solution(), _make_results())

    report.leg_summary()

    out = capsys.readouterr().out
    assert "Leg" in out
    assert "launch" in out
    assert "outbound" in out
    assert "Condition" in out
    assert "Time Inflation" in out


def test_report_rows_preserve_global_sortie_indices_for_multi_drone_runs():
    problem = Problem(
        depot=(38.898, -77.036),
        customers={1: (38.906, -77.043), 2: (38.912, -77.030), 3: (38.917, -77.020)},
        drone_eligible=[1, 2, 3],
    )
    solution = Solution(
        problem=problem,
        truck_route=[0, 2, 0],
        sorties=[
            Sortie(delivery=1, rendezvous=2, drone_id=1),
            Sortie(delivery=2, rendezvous=2, drone_id=0),
            Sortie(delivery=3, rendezvous=0, launch=2, drone_id=1),
        ],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=300.0,
            sortie_times=[100.0, 120.0, 140.0],
            sortie_energies=[10.0, 12.0, 14.0],
        ),
        num_drones=2,
    )
    run = RunResult(
        condition=WindCondition.calm(),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=1,
                        actual_time=123.0,
                        actual_energy=11.0,
                        actual_distance=1300.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=89.0,
                        corrected_battery_at_end=89.0,
                        feasible=True,
                        max_position_error=1.0,
                    ),
                ],
                reposition_results=[],
                actual_makespan=123.0,
                raw_makespan=130.0,
                ulog_path="",
            ),
            DroneRunResult(
                drone_id=1,
                sortie_results=[
                    SortieResult(
                        drone_id=1,
                        sortie_index=0,
                        actual_time=98.0,
                        actual_energy=9.0,
                        actual_distance=1100.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=91.0,
                        corrected_battery_at_end=91.0,
                        feasible=True,
                        max_position_error=1.0,
                    ),
                    SortieResult(
                        drone_id=1,
                        sortie_index=2,
                        actual_time=145.0,
                        actual_energy=13.0,
                        actual_distance=1500.0,
                        actual_path=[],
                        raw_battery_at_start=91.0,
                        raw_battery_at_end=78.0,
                        corrected_battery_at_end=78.0,
                        feasible=True,
                        max_position_error=1.0,
                    ),
                ],
                reposition_results=[],
                actual_makespan=243.0,
                raw_makespan=260.0,
                ulog_path="",
            ),
        ],
        actual_makespan=243.0,
        raw_makespan=260.0,
    )

    report = ComparisonReport(solution, [run])

    assert [(row.sortie_index, row.drone_id) for row in report._rows] == [
        (1, 0),
        (0, 1),
        (2, 1),
    ]
