"""
dronevalkit — Validate drone-assisted delivery routes from OR solvers in
PX4 SITL simulation.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import logging
import math
import os
import queue
import sys
from typing import Optional

try:
    from tqdm.auto import tqdm
except ImportError:  # pragma: no cover - tqdm is an optional runtime nicety
    tqdm = None

from . import geo
from .models import Problem, Solution, Sortie, PlannedMetrics, TruckTimingSegment
from .config import (
    DroneModel,
    SimpleBattery,
    InfiniteBattery,
    CustomBattery,
    DEFAULT_MULTI_DRONE_TARGET_OFFSET_RADIUS_M,
    WindCondition,
    ExperimentConfig,
)
from .qgc import render_qgc_overlay_qml, save_qgc_overlay
from .experiment_suite import (
    default_scenarios,
    execute_experiment_suite,
    execute_factorial,
    expand_run_plan,
    load_manifest,
    write_run_plan,
)
from .robustness import AlgorithmRobustnessReport, generate_algorithm_robustness_artifacts
from .test_bed import coverage_counts, inventory_cases, select_balanced_cases, write_manifest

logger = logging.getLogger(__name__)
_DRONE_WAYPOINT_OFFSET_M = DEFAULT_MULTI_DRONE_TARGET_OFFSET_RADIUS_M


def _planned_delivery_time_s(solution: Solution, sortie_index: int, default: float = 60.0) -> float:
    sortie_leg_times = solution.planned_metrics.sortie_leg_times
    if sortie_leg_times is None or sortie_index >= len(sortie_leg_times):
        return float(default)
    for leg_timing in sortie_leg_times[sortie_index]:
        if str(leg_timing.name).lower() == "delivery":
            return max(0.0, float(leg_timing.end_time) - float(leg_timing.start_time))
    return float(default)


def _planned_sortie_leg_total(solution: Solution) -> int:
    sortie_leg_times = solution.planned_metrics.sortie_leg_times
    if sortie_leg_times is not None:
        total = sum(_expected_progress_legs(leg_timings) for leg_timings in sortie_leg_times)
        if len(sortie_leg_times) < len(solution.sorties):
            total += (len(solution.sorties) - len(sortie_leg_times)) * 9
        if total > 0:
            return total
    return len(solution.sorties) * 9


def _expected_progress_legs(leg_timings: list) -> int:
    """Estimate simulator leg events from planned leg timings for progress bars.

    The simulator always emits at least these nine sortie leg events:
    launch_prep, launch_takeoff, outbound, delivery_land, delivery,
    delivery_takeoff, return, recovery_land, recovery.

    Some imported planned schedules are coarser than that execution model.
    For example, Agatz cases commonly omit zero-duration `launch_prep` and
    `recovery` legs, and other inputs may collapse multiple execution legs into
    one planned leg. The progress bar should still use the simulator's leg
    granularity, otherwise its total ratchets upward mid-run as actual leg
    callbacks arrive.
    """

    normalized_names = set()
    for leg in leg_timings:
        if isinstance(leg, dict):
            name = leg.get("name", "")
        else:
            name = getattr(leg, "name", "")
        normalized_names.add(str(name).strip().lower())
    estimated = len(leg_timings)
    if "launch_prep" not in normalized_names:
        estimated += 1
    if "recovery" not in normalized_names:
        estimated += 1
    return max(9, estimated)


def run(
    config: ExperimentConfig,
    parallel: int = 1,
    output_dir: str = "./dvk_results",
    retry_count: int = 0,
) -> list:
    """Execute all simulation runs defined by *config*."""
    runs = [
        (condition, rep)
        for condition in config.conditions
        for rep in range(config.replications)
    ]

    drones_per_run = config.solution.num_drones

    if parallel == 1:
        return asyncio.run(_run_all_serial(config, runs, output_dir, retry_count=retry_count))

    instance_pool: queue.Queue = queue.Queue()
    for i in range(parallel * drones_per_run):
        instance_pool.put(i * drones_per_run)

    def _thread_entry(condition: WindCondition, rep: int) -> Optional[object]:
        base_instance = instance_pool.get()
        try:
            return asyncio.run(
                _run_single_with_retry(
                    config,
                    condition,
                    rep,
                    output_dir,
                    base_instance,
                    max_retries=retry_count,
                )
            )
        finally:
            instance_pool.put(base_instance)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = {
            executor.submit(_thread_entry, condition, rep): (condition, rep)
            for condition, rep in runs
        }
        for future in concurrent.futures.as_completed(futures):
            condition, rep = futures[future]
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Run (condition=%s, rep=%d) failed permanently: %s",
                    condition.label, rep, exc,
                )
    return results


def compare(solution: Solution, results: list):
    """Create a :class:`~dronevalkit.analysis.ComparisonReport`."""
    from .analysis import ComparisonReport  # type: ignore[import]
    return ComparisonReport(solution, results)


def load(path: str) -> Solution:
    """Load a :class:`Solution` from a JSON file."""
    from .io.json_io import load_solution
    return load_solution(path)


def from_veroviz(assignments_df, nodes_df, **kwargs) -> Solution:
    """Import a :class:`Solution` from VeRoViz dataframes."""
    from .io.veroviz_adapter import from_veroviz as _fv  # type: ignore[import]
    return _fv(assignments_df, nodes_df, **kwargs)


def from_mfstsp(path: str, **kwargs) -> Solution:
    """Import a :class:`Solution` from an mFSTSP benchmark CSV solution."""
    from .io.mfstsp_adapter import from_mfstsp as _fm  # type: ignore[import]
    return _fm(path, **kwargs)


def from_agatz(path: str, **kwargs) -> Solution:
    """Import a :class:`Solution` from an Agatz TSP-D benchmark solution."""
    from .io.agatz_adapter import from_agatz as _fa  # type: ignore[import]
    return _fa(path, **kwargs)


def list_mfstsp_cases(root: str) -> list:
    """List mFSTSP cases available below *root*."""
    from .io.mfstsp_adapter import list_mfstsp_cases as _list_cases  # type: ignore[import]
    return _list_cases(root)


def list_agatz_cases(root: str) -> list:
    """List Agatz TSP-D cases available below *root*."""
    from .io.agatz_adapter import list_agatz_cases as _list_cases  # type: ignore[import]
    return _list_cases(root)


def save_experiment_route(solution: Solution, path: str, **kwargs) -> None:
    """Save a planned truck/drone route image for a solution."""
    from .visualization import save_experiment_route as _save_experiment_route

    _save_experiment_route(solution, path, **kwargs)


__all__ = [
    "Problem",
    "Solution",
    "Sortie",
    "PlannedMetrics",
    "TruckTimingSegment",
    "DroneModel",
    "SimpleBattery",
    "InfiniteBattery",
    "CustomBattery",
    "WindCondition",
    "ExperimentConfig",
    "run",
    "compare",
    "load",
    "from_veroviz",
    "from_agatz",
    "from_mfstsp",
    "list_agatz_cases",
    "list_mfstsp_cases",
    "load_manifest",
    "expand_run_plan",
    "write_run_plan",
    "default_scenarios",
    "execute_experiment_suite",
    "execute_factorial",
    "inventory_cases",
    "select_balanced_cases",
    "coverage_counts",
    "write_manifest",
    "AlgorithmRobustnessReport",
    "generate_algorithm_robustness_artifacts",
    "render_qgc_overlay_qml",
    "save_qgc_overlay",
    "save_experiment_route",
]


async def _run_all_serial(
    config: ExperimentConfig,
    runs: list,
    output_dir: str,
    *,
    retry_count: int = 0,
) -> list:
    results = []
    for condition, rep in runs:
        result = await _run_single_with_retry(
            config,
            condition,
            rep,
            output_dir,
            base_instance=0,
            max_retries=retry_count,
        )
        if result is not None:
            results.append(result)
    return results


async def _run_single_with_retry(
    config: ExperimentConfig,
    condition: WindCondition,
    rep: int,
    output_dir: str,
    base_instance: int,
    max_retries: int = 0,
) -> Optional[object]:
    for attempt in range(max_retries + 1):
        try:
            return await _run_single(config, condition, rep, output_dir, base_instance)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Run (condition=%s, rep=%d) attempt %d/%d failed: %s",
                condition.label, rep, attempt + 1, max_retries + 1, exc,
            )
            if attempt < max_retries:
                logger.info("Retrying run (condition=%s, rep=%d)…", condition.label, rep)
    logger.error(
        "Run (condition=%s, rep=%d) abandoned after %d attempts.",
        condition.label, rep, max_retries + 1,
    )
    return None


async def _run_single(
    config: ExperimentConfig,
    condition: WindCondition,
    rep: int,
    output_dir: str,
    base_instance: int,
) -> object:
    from . import flight
    from .runner import PX4SimRunner
    from .logs import DroneRunResult, RunResult, extract_mission_results, parse_ulog

    safe_label = condition.label.replace(" ", "_").replace("/", "-") or "cond"
    run_dir = os.path.join(output_dir, f"{safe_label}_rep{rep}")

    runner = PX4SimRunner(
        config,
        log_dir=run_dir,
        base_instance=base_instance,
        wind_condition=condition,
    )
    mission_logs: dict[int, object] = {}
    drones = []
    progress_bar = None
    progress_position = base_instance // max(1, config.solution.num_drones)
    progress_total = _planned_sortie_leg_total(config.solution)

    if tqdm is not None and progress_total > 0:
        progress_bar = tqdm(
            total=progress_total,
            desc=f"{condition.label} rep {rep}",
            position=progress_position,
            leave=True,
            dynamic_ncols=True,
            unit="leg",
            file=sys.stdout,
        )

    def _update_progress(event: dict[str, object]) -> None:
        if progress_bar is None or str(event.get("segment_type", "")) != "sortie":
            return
        if progress_bar.total is not None and progress_bar.n + 1 > progress_bar.total:
            progress_bar.total = progress_bar.n + 1
            progress_bar.refresh()
        leg_name = str(event.get("leg_name", "")).strip()
        if leg_name:
            progress_bar.set_postfix_str(leg_name, refresh=False)
        progress_bar.update(1)

    runner.start()
    try:
        depot_lat, depot_lon = config.solution.problem.depot
        vehicle_profile = config.solution.planned_metrics.vehicle_speeds
        drones = await runner.wait_for_ready(timeout=60.0)
        await flight.configure_for_experiment(
            drones,
            config.battery,
            condition,
            speed_factor=config.speed_factor,
            vehicle_speeds=vehicle_profile,
        )
        mission_altitude = (
            float(config.altitude)
            if config.altitude is not None
            else (
                float(vehicle_profile.cruise_altitude)
                if vehicle_profile is not None and vehicle_profile.cruise_altitude is not None
                else 20.0
            )
        )
        planned_schedule = config.solution.planned_schedule()

        sortie_waypoints = [
            {
                "drone_id": sortie.drone_id,
                "launch_node": int(config.solution.sorties[i].launch),
                "rendezvous_node": int(sortie.rendezvous),
                "launch_visit": int(planned_schedule["launch_occurrences"][i]),
                "rendezvous_visit": int(planned_schedule["rendezvous_occurrences"][i]),
                "launch": _offset_gps_for_drone(
                    _node_to_gps(config.solution, config.solution.launch_node(i)),
                    sortie.drone_id,
                    config.solution.num_drones,
                    radius_m=config.target_offset_radius_m,
                ),
                "delivery": _offset_gps_for_drone(
                    _node_to_gps(config.solution, sortie.delivery),
                    sortie.drone_id,
                    config.solution.num_drones,
                    radius_m=config.target_offset_radius_m,
                ),
                "delivery_time_s": _planned_delivery_time_s(config.solution, i),
                "rendezvous": _offset_gps_for_drone(
                    _node_to_gps(config.solution, sortie.rendezvous),
                    sortie.drone_id,
                    config.solution.num_drones,
                    radius_m=config.target_offset_radius_m,
                ),
            }
            for i, sortie in enumerate(config.solution.sorties)
        ]

        per_drone_timeouts = [1.0] * config.solution.num_drones
        for sortie, planned_time in zip(
            config.solution.sorties,
            config.solution.planned_metrics.sortie_times,
        ):
            per_drone_timeouts[sortie.drone_id] += planned_time * 10.0
        mission_timeout = max(per_drone_timeouts, default=60.0)

        logger.info(
            "Flying mission: %d drones, %d total sorties, timeout=%.0fs, condition=%s, rep=%d",
            config.solution.num_drones,
            len(config.solution.sorties),
            mission_timeout,
            condition.label,
            rep,
        )
        mission_logs = await asyncio.wait_for(
            flight.fly_mission(
                {drone_id: drone for drone_id, drone in enumerate(drones)},
                sortie_waypoints,
                altitude=mission_altitude,
                tolerance=config.waypoint_tolerance,
                reference_gps=(depot_lat, depot_lon),
                cruise_speed_m_s=(
                    (
                        vehicle_profile.cruise
                        if vehicle_profile is not None
                        else config.solution.planned_metrics.drone_speed
                    )
                    * config.speed_factor
                ),
                truck_route_gps=[
                    _node_to_gps(config.solution, node_id)
                    for node_id in config.solution.truck_route
                ],
                truck_speed_m_s=config.solution.truck_speed,
                truck_leg_travel_times=config.solution.truck_leg_travel_times,
                planned_truck_timeline=config.solution.planned_truck_timeline,
                altitude_deconfliction_m=config.altitude_deconfliction_m,
                launch_time_s=(
                    float(vehicle_profile.launch_time)
                    if vehicle_profile is not None and vehicle_profile.launch_time is not None
                    else 0.0
                ),
                recovery_time_s=(
                    float(vehicle_profile.recovery_time)
                    if vehicle_profile is not None and vehicle_profile.recovery_time is not None
                    else 0.0
                ),
                progress_callback=_update_progress,
            ),
            timeout=mission_timeout,
        )
    finally:
        if drones:
            await flight.shutdown_mavsdk_systems(drones)
        runner.stop()
        if progress_bar is not None:
            progress_bar.close()
        await asyncio.sleep(1.0)

    drone_results = []
    ulog_paths = runner.get_latest_ulogs()
    for drone_id in range(config.solution.num_drones):
        mission_log = mission_logs.get(drone_id)
        ulog_path = ulog_paths.get(drone_id)
        if mission_log is not None:
            mission_log.ulog_path = ulog_path

        ulog_data: dict = {}
        if ulog_path:
            try:
                ulog_data = parse_ulog(ulog_path)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not parse ULog %s: %s", ulog_path, exc)
        else:
            logger.warning("No ULog file found for drone %d in %s", drone_id, run_dir)

        segments = (
            [dataclasses.asdict(seg) for seg in mission_log.segments]
            if mission_log
            else []
        )
        sortie_results, reposition_results = extract_mission_results(
            ulog_data,
            segments,
            drone_id=drone_id,
            expected_drain_rate=config.battery.drain_rate,
            time_scale_factor=config.speed_factor,
        )

        drone_sortie_indices = [
            i for i, sortie in enumerate(config.solution.sorties) if sortie.drone_id == drone_id
        ]
        for local_idx, sortie_result in enumerate(sortie_results):
            if local_idx < len(drone_sortie_indices):
                sortie_result.sortie_index = drone_sortie_indices[local_idx]
        for repo_idx, repo_result in enumerate(reposition_results):
            if repo_idx < len(drone_sortie_indices) - 1:
                from_idx = drone_sortie_indices[repo_idx]
                to_idx = drone_sortie_indices[repo_idx + 1]
                repo_result.from_rendezvous = config.solution.sorties[from_idx].rendezvous
                repo_result.to_launch = int(config.solution.sorties[to_idx].launch)

        if mission_log:
            sortie_segs = [s for s in mission_log.segments if s.segment_type == "sortie"]
            actual_makespan = sum(
                (s.end_time - s.start_time) * config.speed_factor for s in sortie_segs
            )
            raw_makespan = mission_log.total_time * config.speed_factor
        else:
            actual_makespan = 0.0
            raw_makespan = 0.0

        drone_results.append(
            DroneRunResult(
                drone_id=drone_id,
                sortie_results=sortie_results,
                reposition_results=reposition_results,
                actual_makespan=actual_makespan,
                raw_makespan=raw_makespan,
                ulog_path=ulog_path or "",
            )
        )

    return RunResult(
        condition=condition,
        replication=rep,
        drone_results=drone_results,
        actual_makespan=max((result.actual_makespan for result in drone_results), default=0.0),
        raw_makespan=max((result.raw_makespan for result in drone_results), default=0.0),
    )


def _node_to_gps(solution: Solution, node_id: int) -> tuple:
    if node_id == 0:
        return solution.problem.depot
    return solution.problem.customers[node_id]


def _offset_gps_for_drone(
    gps: tuple[float, float],
    drone_id: int,
    num_drones: int,
    radius_m: float = _DRONE_WAYPOINT_OFFSET_M,
) -> tuple[float, float]:
    """Apply a deterministic per-drone XY offset around a target GPS point."""
    if num_drones <= 1 or radius_m <= 0.0:
        return gps
    if drone_id <= 0:
        return gps
    offset_drones = num_drones - 1
    angle = 2.0 * math.pi * float(drone_id - 1) / float(offset_drones)
    north = radius_m * math.cos(angle)
    east = radius_m * math.sin(angle)
    return geo.ned_to_gps(north, east, gps[0], gps[1])
