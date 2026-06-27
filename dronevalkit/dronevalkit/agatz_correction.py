"""Helpers for calibrated Agatz time-correction experiments."""

from __future__ import annotations

import copy
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any

from .analysis import ComparisonReport
from .logs import RunResult
from .models import Solution


@dataclass(frozen=True)
class SortieTimeCorrection:
    """Calibrated correction applied to Agatz planned sortie times."""

    model: str
    calibration_condition: str
    sample_count: int
    fixed_overhead: float
    cruise_scale: float
    mission_scale: float
    mean_group_durations: dict[str, float]

    def corrected_sortie_time(self, planned_sortie_time: float) -> float:
        planned = max(0.0, float(planned_sortie_time))
        if self.model == "fixed":
            return planned + self.fixed_overhead
        if self.model == "multiplicative":
            return planned * self.mission_scale
        if self.model == "affine":
            return self.fixed_overhead + (self.cruise_scale * planned)
        raise ValueError(f"Unsupported correction model: {self.model}")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TimingErrorSummary:
    """Compact evaluation summary for planned vs actual sortie timing."""

    sample_count: int
    mean_planned_time: float
    mean_actual_time: float
    mean_bias: float
    mean_absolute_error: float
    mean_absolute_percentage_error: float
    mean_time_inflation: float

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)


def split_results(
    results: list[RunResult],
    *,
    calibration_replications: int,
    calibration_condition: str,
) -> tuple[list[RunResult], list[RunResult]]:
    """Split results into calibration and holdout evaluation sets."""

    if calibration_replications < 1:
        raise ValueError("calibration_replications must be at least 1")

    calibration_condition_normalized = calibration_condition.strip()
    calibration_results: list[RunResult] = []
    evaluation_results: list[RunResult] = []

    for run in results:
        condition_label = _condition_label(getattr(run, "condition", None))
        replication = int(getattr(run, "replication", 0))
        if (
            condition_label == calibration_condition_normalized
            and replication < calibration_replications
        ):
            calibration_results.append(run)
        else:
            evaluation_results.append(run)

    if not calibration_results:
        raise ValueError(
            "No calibration runs matched "
            f"condition={calibration_condition_normalized!r} "
            f"with replication < {calibration_replications}"
        )
    if not evaluation_results:
        raise ValueError("No holdout evaluation runs remain after calibration split")

    return calibration_results, evaluation_results


def fit_sortie_time_correction(
    solution: Solution,
    results: list[RunResult],
    *,
    calibration_condition: str,
    model: str = "affine",
) -> SortieTimeCorrection:
    """Estimate a timing correction from actual sortie leg timings."""

    if model not in {"fixed", "multiplicative", "affine"}:
        raise ValueError(f"Unsupported correction model: {model}")

    fixed_components: list[float] = []
    cruise_scales: list[float] = []
    mission_scales: list[float] = []
    group_durations: dict[str, list[float]] = defaultdict(list)

    for run in results:
        for drone_result in getattr(run, "drone_results", []):
            for sortie_result in getattr(drone_result, "sortie_results", []):
                sortie_index = int(sortie_result.sortie_index)
                if sortie_index < 0 or sortie_index >= len(solution.planned_metrics.sortie_times):
                    continue
                planned_time = float(solution.planned_metrics.sortie_times[sortie_index])
                if planned_time < 0.0:
                    continue

                grouped_actual = _group_sortie_leg_durations(sortie_result.leg_timings or [])
                if not grouped_actual:
                    continue

                fixed_time = sum(
                    duration
                    for group_name, duration in grouped_actual.items()
                    if group_name not in {"cruise_outbound", "cruise_return"}
                )
                cruise_time = (
                    grouped_actual.get("cruise_outbound", 0.0)
                    + grouped_actual.get("cruise_return", 0.0)
                )

                fixed_components.append(fixed_time)
                if planned_time > 0.0:
                    cruise_scales.append(cruise_time / planned_time)
                    mission_scales.append(float(sortie_result.actual_time) / planned_time)
                elif not math.isclose(float(sortie_result.actual_time), 0.0):
                    mission_scales.append(0.0)

                for group_name, duration in grouped_actual.items():
                    group_durations[group_name].append(duration)

    if not fixed_components:
        raise ValueError("No sortie leg timing data available for calibration")

    mean_group_durations = {
        group_name: mean(values)
        for group_name, values in sorted(group_durations.items())
        if values
    }
    fixed_overhead = mean(fixed_components)
    cruise_scale = mean(cruise_scales) if cruise_scales else 1.0
    mission_scale = mean(mission_scales) if mission_scales else 1.0

    return SortieTimeCorrection(
        model=model,
        calibration_condition=calibration_condition,
        sample_count=len(fixed_components),
        fixed_overhead=fixed_overhead,
        cruise_scale=cruise_scale,
        mission_scale=mission_scale,
        mean_group_durations=mean_group_durations,
    )


def apply_sortie_time_correction(
    solution: Solution,
    correction: SortieTimeCorrection,
) -> Solution:
    """Clone a solution with corrected planned sortie times and makespan."""

    corrected = copy.deepcopy(solution)
    corrected_sortie_times = [
        correction.corrected_sortie_time(planned_time)
        for planned_time in solution.planned_metrics.sortie_times
    ]
    corrected.planned_metrics.sortie_times = corrected_sortie_times
    # The correction is currently calibrated at the whole-sortie level, not as
    # a per-leg resynthesis. Retaining the original planned leg timings would
    # make corrected Gantts inconsistent with the corrected sortie durations.
    corrected.planned_metrics.sortie_leg_times = None
    # Imported Agatz solutions carry an explicit truck timeline/arrival schedule
    # derived from the published model. Once sortie times are corrected, that
    # explicit schedule is no longer valid; force downstream views like the Gantt
    # chart to recompute truck timing from the updated sortie durations.
    corrected.planned_truck_timeline = None
    corrected.truck_arrival_times = None
    corrected.planned_metrics.makespan = _recomputed_makespan(corrected)
    return corrected


def summarize_timing_errors(report: ComparisonReport) -> TimingErrorSummary:
    """Compute compact timing-error metrics from a comparison report."""

    rows = report.raw_rows()
    if not rows:
        return TimingErrorSummary(
            sample_count=0,
            mean_planned_time=0.0,
            mean_actual_time=0.0,
            mean_bias=0.0,
            mean_absolute_error=0.0,
            mean_absolute_percentage_error=0.0,
            mean_time_inflation=0.0,
        )

    planned = [float(row["planned_time"]) for row in rows]
    actual = [float(row["actual_time"]) for row in rows]
    errors = [act - plan for plan, act in zip(planned, actual)]
    abs_pct_errors = [
        abs(act - plan) / plan
        for plan, act in zip(planned, actual)
        if not math.isclose(plan, 0.0)
    ]
    inflations = [
        float(row["time_inflation"])
        for row in rows
    ]

    return TimingErrorSummary(
        sample_count=len(rows),
        mean_planned_time=mean(planned),
        mean_actual_time=mean(actual),
        mean_bias=mean(errors),
        mean_absolute_error=mean(abs(error) for error in errors),
        mean_absolute_percentage_error=mean(abs_pct_errors) if abs_pct_errors else 0.0,
        mean_time_inflation=mean(inflations),
    )


def _recomputed_makespan(solution: Solution) -> float:
    schedule = solution.planned_schedule()
    truck_departures = [float(value) for value in schedule.get("truck_departures", [])]
    sortie_end_times = [float(value) for value in schedule.get("sortie_end_times", {}).values()]
    return max(
        max(truck_departures, default=0.0),
        max(sortie_end_times, default=0.0),
    )


def _group_sortie_leg_durations(leg_timings: list[Any]) -> dict[str, float]:
    grouped: dict[str, float] = defaultdict(float)
    for leg_timing in leg_timings:
        group_name = _paper_leg_group(getattr(leg_timing, "name", ""))
        if group_name is None:
            continue
        grouped[group_name] += float(leg_timing.duration)
    return dict(grouped)


def _paper_leg_group(leg_name: str) -> str | None:
    mapping = {
        "launch": "launch_fixed",
        "launch_prep": "launch_fixed",
        "launch_takeoff": "vertical_takeoff",
        "outbound": "cruise_outbound",
        "delivery_land": "vertical_landing",
        "delivery": "service",
        "delivery_takeoff": "vertical_takeoff",
        "return": "cruise_return",
        "waiting": None,
        "collection": "recovery_fixed",
        "recovery_land": "vertical_landing",
        "recovery": "recovery_fixed",
    }
    return mapping.get(str(leg_name), str(leg_name))


def _condition_label(condition: Any) -> str:
    if condition is None:
        return "Unknown"
    label = getattr(condition, "label", "")
    if isinstance(label, str) and label.strip():
        return label.strip()
    return str(condition)
