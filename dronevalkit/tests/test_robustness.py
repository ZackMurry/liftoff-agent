"""Tests for cross-algorithm robustness reporting."""

from __future__ import annotations

import csv

from dronevalkit.robustness import AlgorithmRobustnessReport


def _make_run_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for algorithm_label, baseline_time, wind_time, baseline_makespan, wind_makespan, wind_feasible in [
        ("DP", 1.05, 1.30, 1.10, 1.40, 0.75),
        ("Heuristic", 1.08, 1.15, 1.06, 1.12, 1.00),
    ]:
        for replication in range(2):
            rows.append(
                {
                    "run_id": f"{algorithm_label}-baseline-rep{replication}",
                    "case_id": f"{algorithm_label}-case",
                    "scenario_id": "baseline",
                    "scenario_label": "Calm",
                    "replication": replication,
                    "algorithm_label": algorithm_label,
                    "status": "completed",
                    "planned_makespan_s": "100.0",
                    "actual_makespan_s": f"{baseline_makespan * 100.0}",
                    "mean_time_inflation": f"{baseline_time}",
                    "feasible_sortie_rate": "1.0",
                }
            )
            rows.append(
                {
                    "run_id": f"{algorithm_label}-wind-rep{replication}",
                    "case_id": f"{algorithm_label}-case",
                    "scenario_id": "wind_moderate",
                    "scenario_label": "Wind 5.0m/s",
                    "replication": replication,
                    "algorithm_label": algorithm_label,
                    "status": "completed",
                    "planned_makespan_s": "100.0",
                    "actual_makespan_s": f"{wind_makespan * 100.0}",
                    "mean_time_inflation": f"{wind_time}",
                    "feasible_sortie_rate": f"{wind_feasible}",
                }
            )
    return rows


def test_algorithm_robustness_report_ranks_algorithms_and_writes_outputs(tmp_path):
    report = AlgorithmRobustnessReport(_make_run_rows())

    summary_rows = report.summary_rows()
    assert len(summary_rows) == 2
    assert summary_rows[0].algorithm_label == "Heuristic"
    assert summary_rows[0].robustness_rank == 1
    assert summary_rows[1].algorithm_label == "DP"
    assert summary_rows[1].robustness_rank == 2
    assert summary_rows[0].robustness_score < summary_rows[1].robustness_score
    assert summary_rows[0].stressed_feasible_sortie_rate == 1.0
    assert summary_rows[1].stressed_feasible_sortie_rate == 0.75

    delta_rows = report.delta_rows()
    assert len(delta_rows) == 6
    time_delta = next(
        row for row in delta_rows
        if row["metric"] == "mean_time_inflation" and row["algorithm_label"] == "DP"
    )
    assert time_delta["comparison_value"] == "wind_moderate"
    assert time_delta["pair_count"] == 2

    summary = report.write_artifacts(tmp_path / "robustness")
    assert summary["algorithm_count"] == 2
    assert summary["analysis_scope"] == "stressed_only"

    summary_rows_csv = list(csv.DictReader((tmp_path / "robustness" / "summary.csv").open(encoding="utf-8")))
    assert summary_rows_csv[0]["algorithm_label"] == "Heuristic"
    assert summary_rows_csv[0]["robustness_rank"] == "1"
    assert (tmp_path / "robustness" / "paired_deltas.csv").exists()
    assert (tmp_path / "robustness" / "summary.tex").exists()
    assert (tmp_path / "robustness" / "ranking.pdf").exists()
