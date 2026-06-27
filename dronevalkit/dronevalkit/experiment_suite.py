"""Manifest-driven experiment-suite execution helpers."""

from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass
from pathlib import Path
import csv
import json
import logging
import math
import shutil
from statistics import mean
import sys
import time
from typing import Iterable

from .agatz_correction import apply_sortie_time_correction, fit_sortie_time_correction
from .analysis import ComparisonReport
from .config import ExperimentConfig, SimpleBattery, WindCondition
from .logs import RunResult
from .models import Solution
from .robustness import generate_algorithm_robustness_artifacts

logger = logging.getLogger(__name__)
_WIND_DIRECTION_MODES = {"fixed", "sweep"}


class _TeeStream:
    """Write text to the original console stream and a per-run log file."""

    def __init__(self, console_stream, log_handle) -> None:
        self._console_stream = console_stream
        self._log_handle = log_handle
        self.encoding = getattr(console_stream, "encoding", "utf-8")

    def write(self, data: str) -> int:
        written = self._console_stream.write(data)
        self._log_handle.write(data)
        return written

    def flush(self) -> None:
        self._console_stream.flush()
        self._log_handle.flush()

    def isatty(self) -> bool:
        return bool(getattr(self._console_stream, "isatty", lambda: False)())


@contextlib.contextmanager
def _capture_run_log(path: Path):
    """Mirror console output and log records into a per-run experiment log."""

    path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setLevel(logging.NOTSET)
    formatter = next(
        (handler.formatter for handler in root_logger.handlers if handler.formatter is not None),
        None,
    )
    if formatter is None:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler.setFormatter(formatter)

    previous_level = root_logger.level
    if previous_level > logging.INFO:
        root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    with path.open("a", encoding="utf-8") as log_handle:
        stdout_tee = _TeeStream(sys.stdout, log_handle)
        stderr_tee = _TeeStream(sys.stderr, log_handle)
        try:
            with contextlib.redirect_stdout(stdout_tee), contextlib.redirect_stderr(stderr_tee):
                yield
        finally:
            root_logger.removeHandler(file_handler)
            file_handler.close()
            root_logger.setLevel(previous_level)


@dataclass(frozen=True)
class ScenarioSpec:
    """One paper-facing experimental scenario."""

    scenario_id: str
    wind_speed: float = 0.0
    wind_direction: float = 0.0
    battery_longevity: float = 1.0
    altitude: float | None = None
    speed_factor: float = 1.0
    label: str = ""

    def wind_condition(self) -> WindCondition:
        if self.wind_speed <= 0.0:
            return WindCondition.calm()
        return WindCondition(
            speed=float(self.wind_speed),
            direction=float(self.wind_direction),
            label=self.label or f"Wind {self.wind_speed}m/s",
        )


@dataclass(frozen=True)
class PlannedRun:
    """One concrete run-plan row in the experiment suite."""

    run_id: str
    case_id: str
    scenario_id: str
    replication: int
    benchmark_family: str
    algorithm_label: str
    source_path: str
    output_dir: str
    wind_speed: float
    wind_direction: float
    battery_longevity: float
    altitude: float | None
    speed_factor: float
    scenario_label: str


def default_scenarios() -> list[ScenarioSpec]:
    """Return the default paper-facing scenario set."""
    return [
        ScenarioSpec(
            scenario_id="baseline",
            wind_speed=0.0,
            battery_longevity=1.0,
            label="Calm",
        ),
        ScenarioSpec(
            scenario_id="wind_moderate",
            wind_speed=5.0,
            wind_direction=0.0,
            battery_longevity=1.0,
            label="Wind 5.0m/s",
        ),
        ScenarioSpec(
            scenario_id="wind_strong",
            wind_speed=10.0,
            wind_direction=0.0,
            battery_longevity=1.0,
            label="Wind 10.0m/s",
        ),
    ]


def load_manifest(path: str | Path) -> list[dict[str, str]]:
    """Load a curated manifest CSV."""
    manifest_path = Path(path)
    with manifest_path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def expand_run_plan(
    manifest_rows: Iterable[dict[str, str]],
    *,
    scenarios: Iterable[ScenarioSpec],
    replications: int,
    output_root: str | Path,
    case_ids: set[str] | None = None,
    algorithm_labels: set[str] | None = None,
    scenario_ids: set[str] | None = None,
    battery_longevity: float | None = None,
    wind_direction_mode: str = "fixed",
) -> list[PlannedRun]:
    """Expand curated cases across scenarios and replications."""

    if replications < 1:
        raise ValueError("replications must be at least 1")
    if battery_longevity is not None and battery_longevity <= 0.0:
        raise ValueError("battery_longevity must be positive when provided")
    if wind_direction_mode not in _WIND_DIRECTION_MODES:
        raise ValueError(
            f"wind_direction_mode must be one of {sorted(_WIND_DIRECTION_MODES)}"
        )

    planned_runs: list[PlannedRun] = []
    output_root_path = Path(output_root)

    # Filter manifest rows and scenarios up front.
    filtered_rows: list[dict[str, str]] = []
    for manifest_row in manifest_rows:
        case_id = manifest_row["case_id"]
        algorithm_label = manifest_row["algorithm_label"]
        if case_ids is not None and case_id not in case_ids:
            continue
        if algorithm_labels is not None and algorithm_label not in algorithm_labels:
            continue
        filtered_rows.append(manifest_row)

    filtered_scenarios = [
        s for s in scenarios
        if scenario_ids is None or s.scenario_id in scenario_ids
    ]

    # Outer loop order: replication → scenario → case.
    # This ensures a complete pass over all cases under the baseline scenario
    # in replication 0 before starting further replications, so failures are
    # surfaced early.
    for replication in range(replications):
        for scenario in filtered_scenarios:
            for manifest_row in filtered_rows:
                case_id = manifest_row["case_id"]
                run_id = f"{case_id}__{scenario.scenario_id}__rep{replication}"
                planned_runs.append(
                    PlannedRun(
                        run_id=run_id,
                        case_id=case_id,
                        scenario_id=scenario.scenario_id,
                        replication=replication,
                        benchmark_family=manifest_row["benchmark_family"],
                        algorithm_label=manifest_row["algorithm_label"],
                        source_path=manifest_row["source_path"],
                        output_dir=str(output_root_path / "raw_runs" / run_id),
                        wind_speed=float(scenario.wind_speed),
                        wind_direction=_resolve_wind_direction(
                            scenario=scenario,
                            replication=replication,
                            replications=replications,
                            mode=wind_direction_mode,
                        ),
                        battery_longevity=(
                            float(battery_longevity)
                            if battery_longevity is not None
                            else float(scenario.battery_longevity)
                        ),
                        altitude=(
                            float(scenario.altitude)
                            if scenario.altitude is not None
                            else _optional_float(manifest_row.get("vehicle_profile_cruise_altitude_m"))
                        ),
                        speed_factor=float(scenario.speed_factor),
                        scenario_label=scenario.label or scenario.scenario_id,
                    )
                )
    return planned_runs


def write_run_plan(planned_runs: Iterable[PlannedRun], path: str | Path) -> Path:
    """Write expanded run-plan rows to CSV."""
    rows = [asdict(run_row) for run_row in planned_runs]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def execute_experiment_suite(
    manifest_path: str | Path,
    *,
    scenarios: Iterable[ScenarioSpec] | None = None,
    replications: int = 1,
    parallel: int = 1,
    output_dir: str | Path = "results/experiments/default",
    case_ids: set[str] | None = None,
    algorithm_labels: set[str] | None = None,
    scenario_ids: set[str] | None = None,
    battery_longevity: float | None = None,
    wind_direction_mode: str = "fixed",
    correction_model: str | None = "affine",
    correction_scenario_id: str = "baseline",
    agatz_correction_model: str | None = None,
    agatz_correction_scenario_id: str | None = None,
    retry_count: int = 0,
    resume: bool = False,
    cleanup_successful_simulation_logs: bool = True,
) -> dict[str, object]:
    """Execute a manifest-driven experiment suite and write aggregated outputs."""

    if agatz_correction_model is not None:
        correction_model = agatz_correction_model
    if agatz_correction_scenario_id is not None:
        correction_scenario_id = agatz_correction_scenario_id

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    scenario_list = list(scenarios) if scenarios is not None else default_scenarios()
    manifest_rows = load_manifest(manifest_path)
    manifest_by_case_id = {row["case_id"]: row for row in manifest_rows}
    run_plan = expand_run_plan(
        manifest_rows,
        scenarios=scenario_list,
        replications=replications,
        output_root=output_path,
        case_ids=case_ids,
        algorithm_labels=algorithm_labels,
        scenario_ids=scenario_ids,
        battery_longevity=battery_longevity,
        wind_direction_mode=wind_direction_mode,
    )
    write_run_plan(run_plan, output_path / "run_plan.csv")

    aggregated_sortie_rows: list[dict[str, object]] = []
    aggregated_run_rows: list[dict[str, object]] = []
    completed = 0
    failed = 0
    skipped = 0
    correction_artifacts: dict[str, dict[str, object]] = {}

    for planned_run in run_plan:
        if resume:
            completed_marker = _completed_resume_marker(Path(planned_run.output_dir))
            if completed_marker is not None:
                logger.info(
                    "Resume enabled: skipping completed run %s via %s",
                    planned_run.run_id,
                    completed_marker.name,
                )
                skipped += 1
                continue

        manifest_row = manifest_by_case_id[planned_run.case_id]
        result = execute_planned_run(
            planned_run,
            manifest_row=manifest_row,
            parallel=parallel,
            retry_count=retry_count,
            cleanup_successful_simulation_logs=cleanup_successful_simulation_logs,
        )
        aggregated_run_rows.append(result["run_summary"])
        aggregated_sortie_rows.extend(result["sortie_rows"])
        if "correction_artifact" in result:
            artifact = result["correction_artifact"]
            case_id = str(result["run_summary"]["case_id"])
            bucket = correction_artifacts.setdefault(
                case_id,
                {
                    "solution": artifact["solution"],
                    "runs_by_scenario": {},
                    "per_run": [],
                },
            )
            runs_by_scenario = bucket["runs_by_scenario"]
            runs_by_scenario.setdefault(result["run_summary"]["scenario_id"], []).extend(artifact["results"])
            bucket["per_run"].append(
                {
                    "run_id": result["run_summary"]["run_id"],
                    "output_dir": result["run_summary"]["output_dir"],
                    "results": artifact["results"],
                }
            )
        if result["run_summary"]["status"] == "completed":
            completed += 1
        else:
            failed += 1

    correction_summaries: list[dict[str, object]] = []
    if correction_model is not None:
        correction_summaries = _apply_benchmark_corrections(
            aggregated_sortie_rows=aggregated_sortie_rows,
            aggregated_run_rows=aggregated_run_rows,
            correction_artifacts=correction_artifacts,
            correction_model=correction_model,
            correction_scenario_id=correction_scenario_id,
        )

    _write_dict_rows(output_path / "aggregated_results.csv", aggregated_sortie_rows)
    (output_path / "aggregated_results.json").write_text(
        json.dumps(aggregated_sortie_rows, indent=2),
        encoding="utf-8",
    )
    _write_dict_rows(output_path / "run_results.csv", aggregated_run_rows)
    _write_dict_rows(output_path / "corrections.csv", correction_summaries)
    (output_path / "corrections.json").write_text(
        json.dumps(correction_summaries, indent=2),
        encoding="utf-8",
    )
    _write_dict_rows(output_path / "agatz_corrections.csv", correction_summaries)
    (output_path / "agatz_corrections.json").write_text(
        json.dumps(correction_summaries, indent=2),
        encoding="utf-8",
    )
    robustness_summary = generate_algorithm_robustness_artifacts(
        aggregated_run_rows,
        output_path / "robustness",
    )
    summary = {
        "manifest_path": str(manifest_path),
        "output_dir": str(output_path),
        "planned_runs": len(run_plan),
        "completed_runs": completed,
        "failed_runs": failed,
        "skipped_runs": skipped,
        "correction_model": correction_model,
        "correction_scenario_id": correction_scenario_id,
        "correction_case_count": len(correction_summaries),
        "agatz_correction_model": correction_model,
        "agatz_correction_scenario_id": correction_scenario_id,
        "agatz_correction_case_count": len(correction_summaries),
        "retry_count": int(retry_count),
        "wind_direction_mode": wind_direction_mode,
        "cleanup_successful_simulation_logs": bool(cleanup_successful_simulation_logs),
        "robustness_output_dir": robustness_summary["output_dir"],
        "robustness_algorithm_count": robustness_summary["algorithm_count"],
        "robustness_analysis_scope": robustness_summary["analysis_scope"],
        "scenarios": [asdict(scenario) for scenario in scenario_list],
    }
    (output_path / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def execute_factorial(*args, **kwargs) -> dict[str, object]:
    """Backward-compatible alias for :func:`execute_experiment_suite`."""
    return execute_experiment_suite(*args, **kwargs)


def execute_planned_run(
    planned_run: PlannedRun,
    *,
    manifest_row: dict[str, str],
    parallel: int,
    retry_count: int,
    cleanup_successful_simulation_logs: bool,
) -> dict[str, object]:
    """Execute one planned run and return normalized aggregated rows."""

    started_at = time.time()
    run_dir = Path(planned_run.output_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "experiment.log"
    status_path = run_dir / "status.json"
    status_path.write_text(
        json.dumps(
            _running_run_summary(
                planned_run=planned_run,
                manifest_row=manifest_row,
                started_at=started_at,
            ),
            indent=2,
        ),
        encoding="utf-8",
    )

    with _capture_run_log(log_path):
        result: dict[str, object]
        logger.info(
            "Starting planned run %s (case=%s, scenario=%s, replication=%d)",
            planned_run.run_id,
            planned_run.case_id,
            planned_run.scenario_id,
            planned_run.replication,
        )
        try:
            try:
                solution = _load_solution(planned_run.benchmark_family, planned_run.source_path)
                _write_planned_route_image(solution, run_dir / "planned_route.png")
            except Exception as exc:
                logger.exception("Import failed for planned run %s", planned_run.run_id)
                result = _write_failure_status(
                    status_path,
                    planned_run=planned_run,
                    manifest_row=manifest_row,
                    started_at=started_at,
                    status="failed_import",
                    error=str(exc),
                )
                return result

            try:
                config = ExperimentConfig(
                    solution=solution,
                    conditions=[_planned_run_condition(planned_run)],
                    replications=1,
                    speed_factor=float(planned_run.speed_factor),
                    altitude=planned_run.altitude,
                    battery=SimpleBattery(longevity=float(planned_run.battery_longevity)),
                )
                from . import run

                results = run(
                    config,
                    parallel=parallel,
                    output_dir=str(run_dir / "simulation"),
                    retry_count=int(retry_count),
                )
                if not results:
                    logger.error("Simulation produced no run results for %s", planned_run.run_id)
                    result = _write_failure_status(
                        status_path,
                        planned_run=planned_run,
                        manifest_row=manifest_row,
                        started_at=started_at,
                        status="failed_simulation",
                        error="No run results were produced.",
                    )
                    return result
            except Exception as exc:
                logger.exception("Simulation failed for planned run %s", planned_run.run_id)
                result = _write_failure_status(
                    status_path,
                    planned_run=planned_run,
                    manifest_row=manifest_row,
                    started_at=started_at,
                    status="failed_simulation",
                    error=str(exc),
                )
                return result

            try:
                from . import compare

                report = compare(solution, results)
                sortie_rows = _normalized_sortie_rows(
                    planned_run=planned_run,
                    manifest_row=manifest_row,
                    report=report,
                    results=results,
                )
                run_summary = _completed_run_summary(
                    planned_run=planned_run,
                    manifest_row=manifest_row,
                    report=report,
                    results=results,
                    started_at=started_at,
                )
                _write_gantt_image(report, run_dir / "gantt.png")
                _write_dict_rows(run_dir / "sortie_rows.csv", sortie_rows)
                (run_dir / "run_summary.json").write_text(json.dumps(run_summary, indent=2), encoding="utf-8")
                status_path.write_text(json.dumps(run_summary, indent=2), encoding="utf-8")
                logger.info(
                    "Completed planned run %s with status=%s duration=%.1fs",
                    planned_run.run_id,
                    run_summary["status"],
                    float(run_summary["duration_s"]),
                )
                result = {"run_summary": run_summary, "sortie_rows": sortie_rows}
                if planned_run.benchmark_family in {"agatz", "mfstsp"} and getattr(solution, "sorties", None):
                    result["correction_artifact"] = {
                        "solution": solution,
                        "results": results,
                    }
                return result
            except Exception as exc:
                logger.exception("Analysis failed for planned run %s", planned_run.run_id)
                result = _write_failure_status(
                    status_path,
                    planned_run=planned_run,
                    manifest_row=manifest_row,
                    started_at=started_at,
                    status="failed_analysis",
                    error=str(exc),
                )
                return result
        finally:
            if cleanup_successful_simulation_logs:
                _cleanup_simulation_artifacts(run_dir / "simulation")


def _normalized_sortie_rows(
    *,
    planned_run: PlannedRun,
    manifest_row: dict[str, str],
    report: ComparisonReport,
    results: list[object],
) -> list[dict[str, object]]:
    raw_rows = report.raw_rows()
    run_result = results[0] if results else None
    rows: list[dict[str, object]] = []
    for raw_row in raw_rows:
        rows.append(
            {
                "run_id": planned_run.run_id,
                "case_id": planned_run.case_id,
                "scenario_id": planned_run.scenario_id,
                "scenario_label": planned_run.scenario_label,
                "replication": planned_run.replication,
                "benchmark_family": manifest_row["benchmark_family"],
                "algorithm_label": manifest_row["algorithm_label"],
                "source_path": manifest_row["source_path"],
                "num_customers": _optional_int(manifest_row.get("num_customers")),
                "size_tier": manifest_row.get("size_tier"),
                "num_drones": _optional_int(manifest_row.get("num_drones")),
                "sortie_count": _optional_int(manifest_row.get("sortie_count")),
                "drone_count_tier": manifest_row.get("drone_count_tier"),
                "spatial_pattern": manifest_row.get("spatial_pattern"),
                "sortie_distance_profile": manifest_row.get("sortie_distance_profile"),
                "wind_speed": planned_run.wind_speed,
                "wind_direction": planned_run.wind_direction,
                "battery_longevity": planned_run.battery_longevity,
                "planned_makespan_s": _optional_float(manifest_row.get("planned_makespan_s")),
                "actual_makespan_s": float(getattr(run_result, "actual_makespan", 0.0)) if run_result else None,
                "raw_makespan_s": float(getattr(run_result, "raw_makespan", 0.0)) if run_result else None,
                "makespan_inflation": (
                    float(getattr(run_result, "actual_makespan", 0.0)) / _optional_float(manifest_row.get("planned_makespan_s"))
                    if run_result and _optional_float(manifest_row.get("planned_makespan_s"))
                    else None
                ),
                **raw_row,
            }
        )
    return rows


def _completed_run_summary(
    *,
    planned_run: PlannedRun,
    manifest_row: dict[str, str],
    report: ComparisonReport,
    results: list[object],
    started_at: float,
) -> dict[str, object]:
    run_result = results[0] if results else None
    raw_rows = report.raw_rows()
    finished_at = time.time()
    return {
        "run_id": planned_run.run_id,
        "case_id": planned_run.case_id,
        "scenario_id": planned_run.scenario_id,
        "scenario_label": planned_run.scenario_label,
        "replication": planned_run.replication,
        "output_dir": planned_run.output_dir,
        "benchmark_family": manifest_row["benchmark_family"],
        "algorithm_label": manifest_row["algorithm_label"],
        "source_path": manifest_row["source_path"],
        "sortie_count": _optional_int(manifest_row.get("sortie_count")),
        "status": "completed",
        "error": "",
        "sortie_row_count": len(raw_rows),
        "planned_makespan_s": _optional_float(manifest_row.get("planned_makespan_s")),
        "actual_makespan_s": float(getattr(run_result, "actual_makespan", 0.0)) if run_result else None,
        "raw_makespan_s": float(getattr(run_result, "raw_makespan", 0.0)) if run_result else None,
        "mean_time_inflation": _mean_optional([row["time_inflation"] for row in raw_rows]),
        "feasible_sortie_rate": _mean_optional([1.0 if row["feasible"] else 0.0 for row in raw_rows]),
        "started_at": _timestamp_iso(started_at),
        "finished_at": _timestamp_iso(finished_at),
        "updated_at": _timestamp_iso(finished_at),
        "updated_at_ts": finished_at,
        "duration_s": finished_at - started_at,
    }


def _write_failure_status(
    status_path: Path,
    *,
    planned_run: PlannedRun,
    manifest_row: dict[str, str],
    started_at: float,
    status: str,
    error: str,
) -> dict[str, object]:
    finished_at = time.time()
    run_summary = {
        "run_id": planned_run.run_id,
        "case_id": planned_run.case_id,
        "scenario_id": planned_run.scenario_id,
        "scenario_label": planned_run.scenario_label,
        "replication": planned_run.replication,
        "output_dir": planned_run.output_dir,
        "benchmark_family": manifest_row["benchmark_family"],
        "algorithm_label": manifest_row["algorithm_label"],
        "source_path": manifest_row["source_path"],
        "sortie_count": _optional_int(manifest_row.get("sortie_count")),
        "status": status,
        "error": error,
        "sortie_row_count": 0,
        "planned_makespan_s": _optional_float(manifest_row.get("planned_makespan_s")),
        "actual_makespan_s": None,
        "raw_makespan_s": None,
        "mean_time_inflation": None,
        "feasible_sortie_rate": None,
        "started_at": _timestamp_iso(started_at),
        "finished_at": _timestamp_iso(finished_at),
        "updated_at": _timestamp_iso(finished_at),
        "updated_at_ts": finished_at,
        "duration_s": finished_at - started_at,
    }
    status_path.write_text(json.dumps(run_summary, indent=2), encoding="utf-8")
    return {"run_summary": run_summary, "sortie_rows": []}


def _completed_resume_marker(run_dir: Path) -> Path | None:
    """Return the per-run file that proves this run already completed."""

    for marker_name in ("status.json", "run_summary.json"):
        marker_path = run_dir / marker_name
        payload = _read_json_file(marker_path)
        if payload is None:
            continue
        if str(payload.get("status", "")).strip().lower() == "completed":
            return marker_path
    return None


def _read_json_file(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Could not parse JSON file %s: %s", path, exc)
        return None


def _running_run_summary(
    *,
    planned_run: PlannedRun,
    manifest_row: dict[str, str],
    started_at: float,
) -> dict[str, object]:
    return {
        "run_id": planned_run.run_id,
        "case_id": planned_run.case_id,
        "scenario_id": planned_run.scenario_id,
        "scenario_label": planned_run.scenario_label,
        "replication": planned_run.replication,
        "output_dir": planned_run.output_dir,
        "benchmark_family": manifest_row["benchmark_family"],
        "algorithm_label": manifest_row["algorithm_label"],
        "source_path": manifest_row["source_path"],
        "sortie_count": _optional_int(manifest_row.get("sortie_count")),
        "status": "running",
        "error": "",
        "sortie_row_count": 0,
        "planned_makespan_s": _optional_float(manifest_row.get("planned_makespan_s")),
        "actual_makespan_s": None,
        "raw_makespan_s": None,
        "mean_time_inflation": None,
        "feasible_sortie_rate": None,
        "started_at": _timestamp_iso(started_at),
        "finished_at": None,
        "updated_at": _timestamp_iso(started_at),
        "updated_at_ts": started_at,
        "duration_s": 0.0,
    }


def _planned_run_condition(planned_run: PlannedRun) -> WindCondition:
    if planned_run.wind_speed <= 0.0:
        return WindCondition(speed=0.0, direction=float(planned_run.wind_direction), label=planned_run.scenario_label)
    return WindCondition(
        speed=float(planned_run.wind_speed),
        direction=float(planned_run.wind_direction),
        label=planned_run.scenario_label,
    )


def _resolve_wind_direction(
    *,
    scenario: ScenarioSpec,
    replication: int,
    replications: int,
    mode: str,
) -> float:
    base_direction = float(scenario.wind_direction)
    if mode != "sweep":
        return base_direction
    if float(scenario.wind_speed) <= 0.0:
        return base_direction
    # Deterministic uniform angular coverage across replications.
    return (base_direction + (360.0 * float(replication) / float(replications))) % 360.0


def _load_solution(benchmark_family: str, source_path: str) -> object:
    if benchmark_family == "agatz":
        from . import from_agatz
        from .models import VehicleSpeeds

        solution = from_agatz(source_path)
        if solution.planned_metrics.vehicle_speeds is None:
            solution.planned_metrics.vehicle_speeds = VehicleSpeeds(
                takeoff=3.0,
                cruise=solution.planned_metrics.drone_speed,
                landing=1.5,
            )
        return solution
    if benchmark_family == "mfstsp":
        from . import from_mfstsp

        return from_mfstsp(source_path)
    raise ValueError(f"Unsupported benchmark family: {benchmark_family}")


def _write_planned_route_image(solution: object, path: str | Path) -> None:
    try:
        from . import save_experiment_route

        save_experiment_route(solution, str(path))
    except Exception as exc:
        logger.warning("Could not write planned route image to %s: %s", path, exc)


def _write_gantt_image(report: ComparisonReport, path: str | Path) -> None:
    try:
        report.plot_gantt(str(path))
    except Exception as exc:
        logger.warning("Could not write gantt image to %s: %s", path, exc)


def _write_corrected_gantt_image(
    *,
    corrected_solution: Solution,
    per_run_entries: list[dict[str, object]],
    target_run_id: str,
) -> None:
    matching_entry = next(
        (entry for entry in per_run_entries if str(entry.get("run_id")) == target_run_id),
        None,
    )
    if matching_entry is None:
        return

    results = matching_entry.get("results", [])
    output_dir = matching_entry.get("output_dir")
    if not results or not output_dir:
        return

    try:
        from . import compare

        corrected_report = compare(corrected_solution, list(results))
        corrected_report.plot_gantt(str(Path(str(output_dir)) / "gantt_corrected.png"))
    except Exception as exc:
        logger.warning(
            "Could not write corrected gantt image for run %s: %s",
            target_run_id,
            exc,
        )


def _cleanup_simulation_artifacts(simulation_dir: Path) -> None:
    if not simulation_dir.exists():
        return
    try:
        shutil.rmtree(simulation_dir)
        logger.info("Deleted simulation logs in %s", simulation_dir)
    except Exception as exc:
        logger.warning(
            "Could not delete simulation logs in %s: %s",
            simulation_dir,
            exc,
        )


def _write_dict_rows(path: str | Path, rows: list[dict[str, object]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _mean_optional(values: Iterable[float | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _timestamp_iso(epoch_seconds: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


def _apply_benchmark_corrections(
    *,
    aggregated_sortie_rows: list[dict[str, object]],
    aggregated_run_rows: list[dict[str, object]],
    correction_artifacts: dict[str, dict[str, object]],
    correction_model: str,
    correction_scenario_id: str,
) -> list[dict[str, object]]:
    if correction_model not in {"fixed", "multiplicative", "affine"}:
        raise ValueError(f"Unsupported correction_model: {correction_model}")

    correction_summaries: list[dict[str, object]] = []
    corrections_by_case: dict[str, dict[str, object]] = {}
    for case_id, artifact in sorted(correction_artifacts.items()):
        solution = artifact.get("solution")
        runs_by_scenario = artifact.get("runs_by_scenario", {})
        calibration_runs = list(runs_by_scenario.get(correction_scenario_id, []))
        if not isinstance(solution, Solution) or not calibration_runs:
            continue
        correction = fit_sortie_time_correction(
            solution,
            calibration_runs,
            calibration_condition=correction_scenario_id,
            model=correction_model,
        )
        corrected_solution = apply_sortie_time_correction(solution, correction)
        correction_payload = correction.as_dict()
        correction_payload["case_id"] = case_id
        correction_payload["benchmark_family"] = str(
            next(
                (
                    row.get("benchmark_family")
                    for row in aggregated_run_rows
                    if str(row.get("case_id")) == case_id
                ),
                "",
            )
        )
        correction_payload["calibration_scenario_id"] = correction_scenario_id
        correction_payload["corrected_makespan"] = corrected_solution.planned_metrics.makespan
        corrections_by_case[case_id] = {
            "correction": correction,
            "corrected_solution": corrected_solution,
            "per_run": list(artifact.get("per_run", [])),
        }
        correction_summaries.append(correction_payload)

    for row in aggregated_sortie_rows:
        case_id = str(row.get("case_id"))
        case_artifact = corrections_by_case.get(case_id)
        if case_artifact is None:
            row["corrected_planned_time"] = None
            row["corrected_time_inflation"] = None
            row["correction_model"] = None
            row["agatz_correction_model"] = None
            continue
        corrected_solution = case_artifact["corrected_solution"]
        sortie_index = _coerce_int(row.get("sortie_index"))
        if sortie_index is None or sortie_index < 0 or sortie_index >= len(corrected_solution.planned_metrics.sortie_times):
            row["corrected_planned_time"] = None
            row["corrected_time_inflation"] = None
            row["correction_model"] = case_artifact["correction"].model
            row["agatz_correction_model"] = case_artifact["correction"].model
            continue
        corrected_planned_time = float(corrected_solution.planned_metrics.sortie_times[sortie_index])
        actual_time = _coerce_float(row.get("actual_time"))
        row["corrected_planned_time"] = corrected_planned_time
        row["corrected_time_inflation"] = (
            (actual_time / corrected_planned_time)
            if actual_time is not None and not math.isclose(corrected_planned_time, 0.0)
            else None
        )
        row["correction_model"] = case_artifact["correction"].model
        row["agatz_correction_model"] = case_artifact["correction"].model

    for run_row in aggregated_run_rows:
        case_id = str(run_row.get("case_id"))
        case_artifact = corrections_by_case.get(case_id)
        if case_artifact is None:
            run_row["corrected_planned_makespan_s"] = None
            run_row["corrected_makespan_inflation"] = None
            run_row["corrected_mean_time_inflation"] = None
            run_row["correction_model"] = None
            run_row["agatz_correction_model"] = None
            continue
        actual_makespan = _coerce_float(run_row.get("actual_makespan_s"))
        corrected_planned_makespan = float(
            case_artifact["corrected_solution"].planned_metrics.makespan
        )
        corrected_mean_time_inflation = _mean_optional(
            [
                _coerce_float(row.get("corrected_time_inflation"))
                for row in aggregated_sortie_rows
                if row.get("run_id") == run_row.get("run_id")
            ]
        )
        run_row["corrected_planned_makespan_s"] = corrected_planned_makespan
        run_row["corrected_makespan_inflation"] = (
            (actual_makespan / corrected_planned_makespan)
            if actual_makespan is not None
            and corrected_planned_makespan is not None
            and not math.isclose(corrected_planned_makespan, 0.0)
            else None
        )
        run_row["corrected_mean_time_inflation"] = corrected_mean_time_inflation
        run_row["correction_model"] = case_artifact["correction"].model
        run_row["agatz_correction_model"] = case_artifact["correction"].model
        _write_corrected_gantt_image(
            corrected_solution=case_artifact["corrected_solution"],
            per_run_entries=case_artifact["per_run"],
            target_run_id=str(run_row.get("run_id")),
        )

    return correction_summaries


def _coerce_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _coerce_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    return int(value)
