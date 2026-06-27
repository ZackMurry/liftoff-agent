"""Visualization helpers for ComparisonReport outputs."""

from __future__ import annotations

import io
import os
import re
import logging
import textwrap
from collections import defaultdict
from statistics import mean, stdev
from urllib.request import Request, urlopen

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch
from matplotlib.ticker import ScalarFormatter

from .analysis import ComparisonReport
from .geo import gps_to_ned
from .models import Solution

logger = logging.getLogger(__name__)

_COLOR_CYCLE = {
    "calm": "#2E8B57",
    "moderate": "#DDAA33",
    "strong": "#C44E52",
}
_ROUTE_COLORS = [
    "#D1495B",
    "#00798C",
    "#EDAe49",
    "#30638E",
    "#6A994E",
    "#9C6644",
]
_BASEMAP_TILE_URL = "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png"
_BASEMAP_SUBDOMAINS = "abcd"


def plot_scatter(report: ComparisonReport, path: str, metric: str = "time") -> None:
    """Scatter: planned vs actual with y=x and per-condition regression lines."""
    if metric not in {"time", "energy"}:
        raise ValueError("metric must be 'time' or 'energy'")

    rows = report._rows
    if not rows:
        raise ValueError("No run data available for plotting")

    fig, ax = plt.subplots(figsize=(7.0, 5.0))
    _apply_plot_style()

    by_condition: dict[str, list] = {}
    for row in rows:
        by_condition.setdefault(row.condition, []).append(row)

    all_x: list[float] = []
    all_y: list[float] = []
    xlabel = ""
    ylabel = ""

    for condition in sorted(by_condition):
        group = by_condition[condition]
        color = _condition_color(condition)
        if metric == "time":
            x = np.array([r.planned_time for r in group], dtype=float)
            y = np.array([r.actual_time for r in group], dtype=float)
            xlabel = "Planned Time (s)"
            ylabel = "Actual Time (s)"
        else:
            valid_group = [r for r in group if r.planned_energy is not None]
            x = np.array([r.planned_energy for r in valid_group], dtype=float)
            y = np.array([r.actual_energy for r in valid_group], dtype=float)
            xlabel = "Planned Energy (% battery)"
            ylabel = "Actual Energy (% battery)"

        if len(x) == 0:
            continue

        finite_mask = np.isfinite(x) & np.isfinite(y)
        x = x[finite_mask]
        y = y[finite_mask]
        if len(x) == 0:
            continue

        all_x.extend(x.tolist())
        all_y.extend(y.tolist())

        ax.scatter(x, y, s=36, color=color, alpha=0.85, edgecolors="none", label=condition)

        if len(x) >= 2 and not np.allclose(x, x[0]):
            slope, intercept = np.polyfit(x, y, 1)
            x_line = np.linspace(float(np.min(x)), float(np.max(x)), 100)
            y_line = slope * x_line + intercept
            ax.plot(x_line, y_line, color=color, linewidth=1.6)

    if not all_x or not all_y:
        ax.text(
            0.5,
            0.5,
            (
                "Planned energy data unavailable"
                if metric == "energy"
                else "No plottable data available"
            ),
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontfamily="serif",
        )
        ax.set_axis_off()
        ax.set_title(f"Planned vs Actual {metric.capitalize()}", fontfamily="serif")
        _save_pdf(fig, path)
        return

    min_v = min(min(all_x), min(all_y))
    max_v = max(max(all_x), max(all_y))
    pad = max((max_v - min_v) * 0.05, 1e-6)
    ax.plot([min_v - pad, max_v + pad], [min_v - pad, max_v + pad], linestyle="--", color="0.4", linewidth=1.2, label="y=x")

    ax.set_xlabel(xlabel, fontfamily="serif")
    ax.set_ylabel(ylabel, fontfamily="serif")
    ax.set_title(f"Planned vs Actual {metric.capitalize()}", fontfamily="serif")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)

    _save_pdf(fig, path)


def plot_feasibility(report: ComparisonReport, path: str, threshold: float = 20.0) -> None:
    """Scatter: planned battery-at-end vs corrected battery-at-end."""
    rows = report._rows
    if not rows:
        raise ValueError("No run data available for plotting")

    planned_energies = report.solution.planned_metrics.sortie_energies
    if planned_energies is None:
        raise ValueError("Planned sortie energies are required for feasibility plot")

    cumulative = 0.0
    planned_remaining: dict[int, float] = {}
    for i, e in enumerate(planned_energies):
        cumulative += float(e)
        planned_remaining[i] = 100.0 - cumulative

    fig, ax = plt.subplots(figsize=(7.0, 5.0))
    _apply_plot_style()

    by_condition: dict[str, list] = {}
    for row in rows:
        by_condition.setdefault(row.condition, []).append(row)

    xs: list[float] = []
    ys: list[float] = []

    for condition in sorted(by_condition):
        group = by_condition[condition]
        color = _condition_color(condition)
        x = [planned_remaining[r.sortie_index] for r in group]
        y = [r.corrected_battery_at_end for r in group]
        xs.extend(x)
        ys.extend(y)

        ax.scatter(x, y, s=36, color=color, alpha=0.85, edgecolors="none", label=condition)

    min_v = min(min(xs), min(ys), threshold)
    max_v = max(max(xs), max(ys), threshold)
    pad = max((max_v - min_v) * 0.05, 1e-6)
    ax.plot([min_v - pad, max_v + pad], [min_v - pad, max_v + pad], linestyle="--", color="0.4", linewidth=1.2, label="y=x")
    ax.axhline(threshold, color="black", linewidth=1.2, linestyle=":", label=f"threshold={threshold:.1f}%")

    ax.set_xlabel("Planned Battery Remaining at Sortie End (%)", fontfamily="serif")
    ax.set_ylabel("Corrected Battery at Sortie End (%)", fontfamily="serif")
    ax.set_title("Feasibility Margin Erosion", fontfamily="serif")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False)

    _save_pdf(fig, path)


def plot_paths(report: ComparisonReport, path: str, sortie_index: int = 0) -> None:
    """Planned straight-line path vs actual path, one panel per condition."""
    if sortie_index < 0 or sortie_index >= len(report.solution.sorties):
        raise IndexError("sortie_index out of range")

    rows = [r for r in report._rows if r.sortie_index == sortie_index]
    if not rows:
        raise ValueError("No matching sortie data available for plotting")

    condition_labels = sorted({r.condition for r in rows})
    fig, axes = plt.subplots(1, len(condition_labels), figsize=(6.0 * len(condition_labels), 5.0), squeeze=False)
    _apply_plot_style()

    sortie = report.solution.sorties[sortie_index]
    ref_lat, ref_lon = report.solution.problem.depot

    launch_latlon = report._node_to_gps(report.solution.launch_node(sortie_index))
    delivery_latlon = report._node_to_gps(sortie.delivery)
    rendezvous_latlon = report._node_to_gps(sortie.rendezvous)

    launch_ne = gps_to_ned(launch_latlon[0], launch_latlon[1], ref_lat, ref_lon)
    delivery_ne = gps_to_ned(delivery_latlon[0], delivery_latlon[1], ref_lat, ref_lon)
    rendezvous_ne = gps_to_ned(rendezvous_latlon[0], rendezvous_latlon[1], ref_lat, ref_lon)

    for i, condition in enumerate(condition_labels):
        ax = axes[0][i]
        color = _condition_color(condition)

        ax.plot(
            [launch_ne[1], delivery_ne[1], rendezvous_ne[1]],
            [launch_ne[0], delivery_ne[0], rendezvous_ne[0]],
            color="black",
            linestyle="--",
            linewidth=1.6,
            label="Planned",
        )

        cond_rows = [r for r in rows if r.condition == condition]
        for j, row in enumerate(cond_rows):
            actual = _get_actual_path(report, row)
            if not actual:
                continue
            east = [p[1] for p in actual]
            north = [p[0] for p in actual]
            ax.plot(east, north, color=color, alpha=0.35, linewidth=1.0)
            if j == 0:
                ax.plot([], [], color=color, linewidth=1.5, label="Actual")

        ax.scatter(
            [launch_ne[1], delivery_ne[1], rendezvous_ne[1]],
            [launch_ne[0], delivery_ne[0], rendezvous_ne[0]],
            color="black",
            s=24,
        )

        ax.set_title(condition, fontfamily="serif")
        ax.set_xlabel("East (m)", fontfamily="serif")
        if i == 0:
            ax.set_ylabel("North (m)", fontfamily="serif")
        ax.grid(alpha=0.25)
        ax.legend(frameon=False)
        ax.set_aspect("equal", adjustable="datalim")

    fig.suptitle(f"Sortie {sortie_index}: Planned vs Actual Paths", fontfamily="serif")
    _save_pdf(fig, path)


def plot_gantt(report: ComparisonReport, path: str) -> None:
    """Planned vs actual truck/drone timeline with per-drone and wait detail."""
    if not report.results:
        raise ValueError("No run data available for plotting")

    actual_run = _select_gantt_run(report)
    planned_event_log = getattr(report, "planned_event_log", None)
    actual_event_logs = getattr(report, "actual_event_logs", None)
    actual_event_log = None
    if actual_event_logs is not None:
        actual_event_log = actual_event_logs.get(_event_log_run_key(actual_run))

    if planned_event_log is not None and actual_event_log is not None:
        planned_panel = _build_event_log_gantt_panel(planned_event_log, title="Planned")
        actual_panel = _build_event_log_gantt_panel(
            actual_event_log,
            title=_actual_gantt_title(actual_run),
        )
        legend_handles = _event_log_legend_handles(planned_panel, actual_panel)
    else:
        planned_panel = _build_planned_gantt_panel(report.solution)
        actual_panel = _build_actual_gantt_panel(report.solution, actual_run)
        legend_handles = [
            Patch(facecolor="#888888", edgecolor="#555555", label="Truck Move"),
            Patch(facecolor="#D9D9D9", edgecolor="#888888", label="Truck Dwell"),
            Patch(facecolor="#D1495B", edgecolor="#222222", label="Launch"),
            Patch(facecolor="#00798C", edgecolor="#222222", label="Outbound"),
            Patch(facecolor="#C97C1A", edgecolor="#222222", label="Customer Land"),
            Patch(facecolor="#EDAe49", edgecolor="#222222", label="Delivery"),
            Patch(facecolor="#9C6644", edgecolor="#222222", label="Customer Takeoff"),
            Patch(facecolor="#6A994E", edgecolor="#222222", label="Return"),
            Patch(facecolor="#E76F51", edgecolor="#222222", label="Waiting"),
            Patch(facecolor="#7B6D8D", edgecolor="#222222", label="Collection"),
            Patch(facecolor="#F6BD60", edgecolor="#9C6644", hatch="////", label="Wait For Truck"),
            Patch(facecolor="#A8DADC", edgecolor="#457B9D", hatch="..", label="Reposition"),
        ]
    xmax = max(planned_panel["end_time"], actual_panel["end_time"], 1.0)

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(24.0, max(5.5, 2.2 + 1.2 * (report.solution.num_drones + 1) * 2)),
        sharex=True,
    )
    _apply_plot_style()

    _render_gantt_panel(axes[0], planned_panel, xmax)
    _render_gantt_panel(axes[1], actual_panel, xmax)

    axes[1].set_xlabel("Time (s)", fontfamily="serif")

    fig.legend(
        handles=legend_handles,
        loc="upper center",
        ncol=4,
        frameon=False,
        bbox_to_anchor=(0.5, 0.995),
    )
    fig.suptitle("Truck-Drone Scenario Timeline", fontfamily="serif", y=0.995)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.90))

    _save_pdf(fig, path)


def plot_leg_energy(report: ComparisonReport, path: str) -> None:
    """Plot mean per-leg energy usage with replication error bars."""
    _plot_leg_metric(
        report,
        path,
        value_key="energy_pct",
        ylabel="Energy (% battery)",
        title="Per-Leg Energy Usage",
    )


def _select_gantt_run(report: ComparisonReport):
    return sorted(
        report.results,
        key=lambda run: (
            str(getattr(getattr(run, "condition", None), "label", "")),
            int(getattr(run, "replication", 0)),
        ),
    )[0]


def _collect_leg_energy_rows(report: ComparisonReport) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run in sorted(
        report.results,
        key=lambda result: (
            report._condition_label(getattr(result, "condition", None)),
            int(getattr(result, "replication", 0)),
        ),
    ):
        condition = report._condition_label(getattr(run, "condition", None))
        replication = int(getattr(run, "replication", 0))
        for drone_result in sorted(getattr(run, "drone_results", []), key=lambda item: int(item.drone_id)):
            drone_id = int(drone_result.drone_id)
            sortie_results = sorted(
                getattr(drone_result, "sortie_results", []),
                key=lambda result: (float(getattr(result, "start_time", 0.0)), int(getattr(result, "sortie_index", -1))),
            )
            reposition_results = sorted(
                getattr(drone_result, "reposition_results", []),
                key=lambda result: (float(getattr(result, "start_time", 0.0)), float(getattr(result, "end_time", 0.0))),
            )

            for sortie_order, sortie_result in enumerate(sortie_results):
                for leg_order, sample in enumerate(getattr(sortie_result, "leg_energy_samples", []) or []):
                    rows.append(
                        {
                            "drone_id": drone_id,
                            "condition": condition,
                            "replication": replication,
                            "mission_order": sortie_order * 2,
                            "segment_kind": "sortie",
                            "segment_index": int(getattr(sortie_result, "sortie_index", sortie_order)),
                            "leg_order": leg_order,
                            "leg_name": _normalized_leg_name(getattr(sample, "name", "")),
                            "energy_pct": float(getattr(sample, "energy_pct", 0.0)),
                        }
                    )

            for reposition_order, reposition_result in enumerate(reposition_results):
                for leg_order, sample in enumerate(getattr(reposition_result, "leg_energy_samples", []) or []):
                    rows.append(
                        {
                            "drone_id": drone_id,
                            "condition": condition,
                            "replication": replication,
                            "mission_order": reposition_order * 2 + 1,
                            "segment_kind": "reposition",
                            "segment_index": reposition_order,
                            "leg_order": leg_order,
                            "leg_name": _normalized_leg_name(getattr(sample, "name", "")),
                            "energy_pct": float(getattr(sample, "energy_pct", 0.0)),
                        }
                    )
    return rows


def _group_leg_metric_rows(rows: list[dict[str, object]], value_key: str) -> dict[str, object]:
    samples: dict[tuple[tuple[int, str, int, int, str], str], list[float]] = defaultdict(list)
    ordered_key_rank: dict[tuple[int, str, int, int, str], tuple[int, str, int, int, str]] = {}
    conditions_by_drone: dict[int, set[str]] = defaultdict(set)

    for row in rows:
        key = (
            int(row["drone_id"]),
            str(row["segment_kind"]),
            int(row["segment_index"]),
            int(row["leg_order"]),
            str(row["leg_name"]),
        )
        condition = str(row["condition"])
        samples[(key, condition)].append(float(row[value_key]))
        ordered_key_rank[key] = (
            int(row["mission_order"]),
            str(row["segment_kind"]),
            int(row["segment_index"]),
            int(row["leg_order"]),
            str(row["leg_name"]),
        )
        conditions_by_drone[int(row["drone_id"])].add(condition)

    ordered_keys = sorted(ordered_key_rank, key=lambda key: (key[0], ordered_key_rank[key]))
    return {
        "samples": samples,
        "ordered_keys": ordered_keys,
        "conditions_by_drone": {
            drone_id: sorted(values)
            for drone_id, values in conditions_by_drone.items()
        },
    }


def _plot_leg_metric(
    report: ComparisonReport,
    path: str,
    *,
    value_key: str,
    ylabel: str,
    title: str,
) -> None:
    rows = _collect_leg_energy_rows(report)
    if not rows:
        raise ValueError("No per-leg energy samples available for plotting")

    grouped = _group_leg_metric_rows(rows, value_key=value_key)
    drone_ids = sorted({row["drone_id"] for row in rows})
    fig, axes = plt.subplots(
        len(drone_ids),
        1,
        figsize=(max(9.0, 0.45 * max(len(grouped["ordered_keys"]), 1) + 4.0), max(4.5, 3.8 * len(drone_ids))),
        squeeze=False,
        sharex=False,
    )
    _apply_plot_style()

    for axis_index, drone_id in enumerate(drone_ids):
        ax = axes[axis_index][0]
        drone_keys = [key for key in grouped["ordered_keys"] if key[0] == drone_id]
        conditions = grouped["conditions_by_drone"][drone_id]
        x = np.arange(len(drone_keys), dtype=float)
        offsets = _condition_offsets(len(conditions))

        for offset, condition in zip(offsets, conditions):
            means = []
            errors = []
            for key in drone_keys:
                samples = grouped["samples"].get((key, condition), [])
                if samples:
                    means.append(mean(samples))
                    errors.append(stdev(samples) if len(samples) > 1 else 0.0)
                else:
                    means.append(np.nan)
                    errors.append(np.nan)
            ax.errorbar(
                x + offset,
                means,
                yerr=errors,
                fmt="o-",
                linewidth=1.4,
                markersize=4.5,
                capsize=3.0,
                color=_condition_color(condition),
                label=condition,
                alpha=0.9,
            )

        ax.set_ylabel(ylabel, fontfamily="serif")
        ax.set_title(f"Drone {drone_id}", fontfamily="serif")
        ax.grid(axis="y", alpha=0.25)
        ax.set_axisbelow(True)
        ax.set_xticks(x)
        ax.set_xticklabels([_format_leg_energy_label(key) for key in drone_keys], rotation=45, ha="right")
        ax.legend(frameon=False)

    axes[-1][0].set_xlabel("Mission Leg", fontfamily="serif")
    fig.suptitle(title, fontfamily="serif", y=0.99)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.96))

    _save_pdf(fig, path)


def _condition_offsets(count: int) -> np.ndarray:
    if count <= 1:
        return np.array([0.0])
    return np.linspace(-0.18, 0.18, count)


def _format_leg_energy_label(key: tuple[int, str, int, int, str]) -> str:
    _drone_id, segment_kind, segment_index, _leg_order, leg_name = key
    prefix = f"S{segment_index}" if segment_kind == "sortie" else f"R{segment_index}"
    return f"{prefix} {leg_name}"


def _normalized_leg_name(name: str) -> str:
    text = str(name)
    if text == "rendezvous":
        return "collection"
    if text.startswith("reposition_"):
        return text.replace("reposition_", "repo:")
    return text


def _build_planned_gantt_panel(solution: Solution) -> dict[str, object]:
    schedule = solution.planned_schedule()
    sorties_by_drone = _sortie_indices_by_drone(solution)

    if solution.planned_truck_timeline is not None:
        truck_segments = _truck_segments_from_timeline(solution.planned_truck_timeline)
    else:
        truck_segments = _truck_segments_from_schedule(solution, schedule)
    lanes = [{"label": "Truck", "segments": truck_segments}]
    for drone_id, sortie_indices in enumerate(sorties_by_drone):
        segments: list[dict[str, object]] = []
        ready_time = 0.0
        for sortie_index in sortie_indices:
            start_time, end_time = _planned_sortie_window(solution, schedule, sortie_index)
            launch_node = solution.launch_node(sortie_index)
            if start_time > ready_time + 1e-9:
                segments.append(
                    {
                        "kind": "wait_truck",
                        "start": ready_time,
                        "end": start_time,
                        "label": f"Wait @ {launch_node}",
                    }
                )
            segments.extend(
                _build_sortie_leg_segments(
                    sortie_index=sortie_index,
                    drone_id=drone_id,
                    sortie_start=start_time,
                    sortie_end=end_time,
                    leg_timings=(
                        solution.planned_metrics.sortie_leg_times[sortie_index]
                        if solution.planned_metrics.sortie_leg_times is not None
                        and sortie_index < len(solution.planned_metrics.sortie_leg_times)
                        else None
                    ),
                )
            )
            ready_time = end_time
        lanes.append({"label": f"Drone {drone_id}", "segments": segments})

    end_time = max(
        float(solution.planned_metrics.makespan),
        max((float(seg["end"]) for lane in lanes for seg in lane["segments"]), default=0.0),
    )
    return {"title": "Planned", "lanes": lanes, "end_time": end_time}


def _planned_sortie_window(
    solution: Solution,
    schedule: dict[str, object],
    sortie_index: int,
) -> tuple[float, float]:
    planned_leg_timings = solution.planned_metrics.sortie_leg_times
    if planned_leg_timings is not None and sortie_index < len(planned_leg_timings):
        normalized_legs = _normalize_leg_timings(
            sortie_start=float(schedule["sortie_launch_times"][sortie_index]),
            sortie_end=float(schedule["sortie_end_times"][sortie_index]),
            leg_timings=planned_leg_timings[sortie_index],
        )
        if normalized_legs:
            return (
                float(normalized_legs[0]["start_time"]),
                float(normalized_legs[-1]["end_time"]),
            )

    return (
        float(schedule["sortie_launch_times"][sortie_index]),
        float(schedule["sortie_end_times"][sortie_index]),
    )


def _build_actual_gantt_panel(solution: Solution, run) -> dict[str, object]:
    schedule = solution.planned_schedule()
    sorties_by_visit: dict[int, list[float]] = {}
    rendezvous_by_visit: dict[int, list[float]] = {}
    drone_results = sorted(getattr(run, "drone_results", []), key=lambda r: int(r.drone_id))
    run_end_time = float(getattr(run, "raw_makespan", 0.0))

    for drone_result in drone_results:
        for sortie_result in sorted(
            getattr(drone_result, "sortie_results", []),
            key=lambda s: (float(getattr(s, "start_time", 0.0)), int(getattr(s, "sortie_index", -1))),
        ):
            sortie_index = int(sortie_result.sortie_index)
            launch_visit = int(schedule["launch_occurrences"][sortie_index])
            rendezvous_visit = int(schedule["rendezvous_occurrences"][sortie_index])
            sorties_by_visit.setdefault(launch_visit, []).append(_actual_launch_completion_time(sortie_result))
            rendezvous_by_visit.setdefault(rendezvous_visit, []).append(float(sortie_result.end_time))

    truck_arrivals = [0.0] * len(solution.truck_route)
    truck_departures = [0.0] * len(solution.truck_route)
    for visit_index in range(len(solution.truck_route)):
        arrival = truck_arrivals[visit_index]
        launches = sorties_by_visit.get(visit_index, [])
        rendezvous = rendezvous_by_visit.get(visit_index, [])
        truck_departures[visit_index] = max(
            arrival,
            max(launches, default=arrival),
            max(rendezvous, default=arrival),
        )
        if visit_index + 1 < len(solution.truck_route):
            travel_time = solution._truck_travel_time_s(
                solution.truck_route[visit_index],
                solution.truck_route[visit_index + 1],
                leg_index=visit_index,
            )
            truck_arrivals[visit_index + 1] = truck_departures[visit_index] + travel_time

    actual_schedule = {
        "truck_arrivals": truck_arrivals,
        "truck_departures": truck_departures,
    }
    lanes = [{"label": "Truck", "segments": _actual_truck_segments(solution, actual_schedule)}]

    for drone_result in drone_results:
        drone_id = int(drone_result.drone_id)
        sortie_results = sorted(
            getattr(drone_result, "sortie_results", []),
            key=lambda s: (float(getattr(s, "start_time", 0.0)), int(getattr(s, "sortie_index", -1))),
        )
        reposition_results = sorted(
            getattr(drone_result, "reposition_results", []),
            key=lambda r: (float(getattr(r, "start_time", 0.0)), float(getattr(r, "end_time", 0.0))),
        )

        segments: list[dict[str, object]] = []
        ready_time = 0.0
        current_node = 0
        for local_index, sortie_result in enumerate(sortie_results):
            if local_index > 0:
                repo_index = local_index - 1
                if repo_index < len(reposition_results):
                    reposition_result = reposition_results[repo_index]
                    if reposition_result.end_time > reposition_result.start_time + 1e-9:
                        segments.append(
                            {
                                "kind": "reposition",
                                "start": float(reposition_result.start_time),
                                "end": float(reposition_result.end_time),
                                "label": f"R{repo_index}",
                            }
                        )
                    ready_time = float(reposition_result.end_time)
                    current_node = int(reposition_result.to_launch)
                else:
                    ready_time = float(sortie_results[local_index - 1].end_time)
                    previous_sortie_index = int(sortie_results[local_index - 1].sortie_index)
                    current_node = int(solution.sorties[previous_sortie_index].rendezvous)

            segment_start_time = float(sortie_result.start_time)
            segment_end_time = float(sortie_result.end_time)
            sortie_index = int(sortie_result.sortie_index)
            launch_node = solution.launch_node(sortie_index)
            if segment_start_time > ready_time + 1e-9:
                segments.append(
                    {
                        "kind": "wait_truck",
                        "start": ready_time,
                        "end": segment_start_time,
                        "label": f"Wait @ {launch_node}",
                    }
                )

            leg_start_time, _leg_end_time = _sortie_leg_window(
                sortie_start=segment_start_time,
                sortie_end=segment_end_time,
                leg_timings=getattr(sortie_result, "leg_timings", None),
            )
            wait_start_time = max(ready_time, segment_start_time)
            if leg_start_time > wait_start_time + 1e-9:
                segments.append(
                    {
                        "kind": "wait_truck",
                        "start": wait_start_time,
                        "end": leg_start_time,
                        "label": f"Wait @ {launch_node}",
                    }
                )
            current_node = launch_node
            segments.extend(
                _build_sortie_leg_segments(
                    sortie_index=sortie_index,
                    drone_id=drone_id,
                    sortie_start=segment_start_time,
                    sortie_end=segment_end_time,
                    leg_timings=getattr(sortie_result, "leg_timings", None),
                )
            )
            ready_time = segment_end_time
            current_node = int(solution.sorties[sortie_index].rendezvous)

        lanes.append({"label": f"Drone {drone_id}", "segments": segments})

    end_time = max(
        run_end_time,
        max((float(seg["end"]) for lane in lanes for seg in lane["segments"]), default=0.0),
    )
    condition_label = str(getattr(getattr(run, "condition", None), "label", ""))
    replication = int(getattr(run, "replication", 0))
    suffix = f" ({condition_label}, rep {replication})" if condition_label else f" (rep {replication})"
    return {"title": f"Actual{suffix}", "lanes": lanes, "end_time": end_time}


def _actual_gantt_title(run) -> str:
    condition_label = str(getattr(getattr(run, "condition", None), "label", ""))
    replication = int(getattr(run, "replication", 0))
    suffix = f" ({condition_label}, rep {replication})" if condition_label else f" (rep {replication})"
    return f"Actual{suffix}"


def _event_log_run_key(run) -> tuple[str, int]:
    return (
        str(getattr(getattr(run, "condition", None), "label", "")),
        int(getattr(run, "replication", 0)),
    )


def _build_event_log_gantt_panel(event_log, *, title: str) -> dict[str, object]:
    grouped_rows: dict[int, list[object]] = {}
    vehicle_meta: dict[int, tuple[str, str]] = {}
    for row in sorted(
        event_log.rows,
        key=lambda row: (
            int(row.vehicle_id),
            float(row.start_time),
            float(row.end_time),
            str(row.description),
        ),
    ):
        if float(row.end_time) < 0.0 or float(row.end_time) <= float(row.start_time) + 1e-9:
            continue
        vehicle_id = int(row.vehicle_id)
        grouped_rows.setdefault(vehicle_id, []).append(row)
        vehicle_type = str(row.vehicle_type)
        if vehicle_type.lower() == "truck":
            label = "Truck"
        else:
            label = f"{vehicle_type} {vehicle_id}"
        vehicle_meta[vehicle_id] = (vehicle_type, label)

    lanes: list[dict[str, object]] = []
    activity_types: list[str] = []
    for vehicle_id in sorted(grouped_rows):
        segments: list[dict[str, object]] = []
        for row in grouped_rows[vehicle_id]:
            activity_type = str(row.activity_type).strip()
            if activity_type and activity_type not in activity_types:
                activity_types.append(activity_type)
            segments.append(
                {
                    "kind": "event_log",
                    "start": float(row.start_time),
                    "end": float(row.end_time),
                    "label": str(row.description).strip(),
                    "activity_type": activity_type,
                    "status": str(row.status).strip(),
                    "vehicle_type": str(row.vehicle_type).strip(),
                    "vehicle_id": int(row.vehicle_id),
                }
            )
        lanes.append({"label": vehicle_meta[vehicle_id][1], "segments": segments})

    end_time = max(
        float(getattr(event_log, "objective_value", 0.0)),
        max((float(segment["end"]) for lane in lanes for segment in lane["segments"]), default=0.0),
    )
    return {
        "title": title,
        "lanes": lanes,
        "end_time": end_time,
        "activity_types": activity_types,
    }


def _event_log_legend_handles(*panels: dict[str, object]) -> list[Patch]:
    activity_types: list[str] = []
    for panel in panels:
        for activity_type in panel.get("activity_types", []):
            if activity_type not in activity_types:
                activity_types.append(activity_type)
    return [
        Patch(
            facecolor=_event_log_activity_style(activity_type)["facecolor"],
            edgecolor=_event_log_activity_style(activity_type)["edgecolor"],
            label=activity_type,
        )
        for activity_type in activity_types
    ]


def _sortie_indices_by_drone(solution: Solution) -> list[list[int]]:
    sorties_by_drone: list[list[int]] = [[] for _ in range(solution.num_drones)]
    for sortie_index, sortie in enumerate(solution.sorties):
        sorties_by_drone[int(sortie.drone_id)].append(sortie_index)
    return sorties_by_drone


def _truck_segments_from_schedule(solution: Solution, schedule: dict[str, object]) -> list[dict[str, object]]:
    arrivals = [float(t) for t in schedule["truck_arrivals"]]
    departures = [float(t) for t in schedule["truck_departures"]]
    segments: list[dict[str, object]] = []

    for visit_index, node_id in enumerate(solution.truck_route):
        arrival = arrivals[visit_index]
        departure = departures[visit_index]
        if departure > arrival + 1e-9:
            segments.append(
                {
                    "kind": "truck_wait",
                    "start": arrival,
                    "end": departure,
                    "label": _abbreviate_truck_wait_label(f"Node {node_id}"),
                }
            )

        if visit_index + 1 < len(solution.truck_route):
            next_arrival = arrivals[visit_index + 1]
            next_node = solution.truck_route[visit_index + 1]
            segments.append(
                {
                    "kind": "truck_move",
                    "start": departure,
                    "end": next_arrival,
                    "label": f"{node_id}->{next_node}",
                }
            )
    return segments


def _actual_truck_segments(solution: Solution, schedule: dict[str, object]) -> list[dict[str, object]]:
    arrivals = [float(t) for t in schedule["truck_arrivals"]]
    departures = [float(t) for t in schedule["truck_departures"]]
    if solution.planned_truck_timeline is None:
        return _truck_segments_from_schedule(solution, schedule)

    dwell_templates = _planned_truck_dwell_segments_by_visit(solution)
    segments: list[dict[str, object]] = []

    for visit_index, node_id in enumerate(solution.truck_route):
        arrival = arrivals[visit_index]
        departure = departures[visit_index]
        dwell_duration = max(0.0, departure - arrival)
        if dwell_duration > 1e-9:
            visit_templates = dwell_templates.get(visit_index, [])
            if visit_templates:
                template_total = sum(
                    max(0.0, float(template["end"]) - float(template["start"]))
                    for template in visit_templates
                )
                cursor = arrival
                if template_total > 1e-9:
                    for template_index, template in enumerate(visit_templates):
                        template_duration = max(0.0, float(template["end"]) - float(template["start"]))
                        if template_duration <= 1e-9:
                            continue
                        if template_index == len(visit_templates) - 1:
                            next_cursor = departure
                        else:
                            next_cursor = cursor + dwell_duration * (template_duration / template_total)
                        segments.append(
                            {
                                "kind": "truck_wait",
                                "start": cursor,
                                "end": min(next_cursor, departure),
                                "label": _abbreviate_truck_wait_label(
                                    str(template["label"]),
                                    template.get("drone_id"),
                                    fallback_label=f"Node {solution.truck_route[visit_index]}",
                                ),
                            }
                        )
                        cursor = next_cursor
                else:
                    segments.append(
                        {
                            "kind": "truck_wait",
                            "start": arrival,
                            "end": departure,
                            "label": _abbreviate_truck_wait_label(f"Node {node_id}"),
                        }
                    )
            else:
                segments.append(
                    {
                        "kind": "truck_wait",
                        "start": arrival,
                        "end": departure,
                        "label": _abbreviate_truck_wait_label(f"Node {node_id}"),
                    }
                )

        if visit_index + 1 < len(solution.truck_route):
            next_arrival = arrivals[visit_index + 1]
            next_node = solution.truck_route[visit_index + 1]
            segments.append(
                {
                    "kind": "truck_move",
                    "start": departure,
                    "end": next_arrival,
                    "label": f"{node_id}->{next_node}",
                }
            )
    return segments


def _planned_truck_dwell_segments_by_visit(solution: Solution) -> dict[int, list[dict[str, object]]]:
    if solution.planned_truck_timeline is None:
        return {}

    dwell_segments: dict[int, list[dict[str, object]]] = {}
    visit_index = 0
    ordered = sorted(
        [
            segment if hasattr(segment, "kind") else segment
            for segment in solution.planned_truck_timeline
        ],
        key=lambda segment: (
            float(segment.start_time if hasattr(segment, "start_time") else segment["start_time"]),
            float(segment.end_time if hasattr(segment, "end_time") else segment["end_time"]),
        ),
    )
    for segment in ordered:
        kind = str(segment.kind if hasattr(segment, "kind") else segment["kind"])
        if kind == "dwell":
            dwell_segments.setdefault(visit_index, []).append(
                {
                    "start": float(segment.start_time if hasattr(segment, "start_time") else segment["start_time"]),
                    "end": float(segment.end_time if hasattr(segment, "end_time") else segment["end_time"]),
                    "label": _abbreviate_truck_wait_label(
                        str(segment.label if hasattr(segment, "label") else segment["label"]),
                        segment.drone_id if hasattr(segment, "drone_id") else segment.get("drone_id"),
                        fallback_label=f"Node {solution.truck_route[visit_index]}",
                    ),
                    "drone_id": (
                        segment.drone_id
                        if hasattr(segment, "drone_id")
                        else segment.get("drone_id")
                    ),
                }
            )
        elif kind == "move" and visit_index + 1 < len(solution.truck_route):
            visit_index += 1
    return dwell_segments


def _abbreviate_truck_wait_label(
    label: str,
    drone_id: object = None,
    fallback_label: str | None = None,
) -> str:
    text = label.strip()
    if not text:
        return fallback_label.strip() if fallback_label is not None else ""
    if drone_id is not None:
        normalized_id = int(drone_id)
        if re.fullmatch(r"Launching UAV \d+", text, flags=re.IGNORECASE):
            return f"L{normalized_id}"
        if re.fullmatch(r"Retrieving UAV \d+", text, flags=re.IGNORECASE):
            return f"R{normalized_id}"
    launch_match = re.fullmatch(r"Launching UAV (\d+)", text, flags=re.IGNORECASE)
    if launch_match is not None:
        return f"L{int(launch_match.group(1))}"
    recovery_match = re.fullmatch(r"Retrieving UAV (\d+)", text, flags=re.IGNORECASE)
    if recovery_match is not None:
        return f"R{int(recovery_match.group(1))}"
    return text


def _actual_launch_completion_time(sortie_result) -> float:
    start_time = float(getattr(sortie_result, "start_time", 0.0))
    normalized_legs = _normalize_leg_timings(
        sortie_start=start_time,
        sortie_end=float(getattr(sortie_result, "end_time", start_time)),
        leg_timings=getattr(sortie_result, "leg_timings", None),
    )
    for leg in normalized_legs:
        if str(leg["name"]).lower() == "launch_prep":
            return float(leg["end_time"])
    for leg in normalized_legs:
        if str(leg["name"]).lower() == "launch":
            return float(leg["end_time"])
    return start_time


def _truck_segments_from_timeline(timeline) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    for segment in timeline:
        raw_kind = segment.kind if hasattr(segment, "kind") else segment["kind"]
        kind = "truck_move" if str(raw_kind) == "move" else "truck_wait"
        start_node = int(segment.start_node if hasattr(segment, "start_node") else segment["start_node"])
        end_node = int(segment.end_node if hasattr(segment, "end_node") else segment["end_node"])
        if kind == "truck_move":
            label = f"{start_node}->{end_node}"
        else:
            raw_label = segment.label if hasattr(segment, "label") else segment["label"]
            label = _abbreviate_truck_wait_label(
                str(raw_label),
                segment.drone_id if hasattr(segment, "drone_id") else segment.get("drone_id"),
                fallback_label=f"Node {end_node}",
            )
        segments.append(
            {
                "kind": kind,
                "start": float(segment.start_time if hasattr(segment, "start_time") else segment["start_time"]),
                "end": float(segment.end_time if hasattr(segment, "end_time") else segment["end_time"]),
                "label": label,
            }
        )
    return segments


def _planned_truck_dwell_times(solution: Solution) -> list[float]:
    if solution.planned_truck_timeline is None:
        return [0.0] * len(solution.truck_route)

    arrivals = [0.0] * len(solution.truck_route)
    departures = [0.0] * len(solution.truck_route)
    ordered = sorted(
        [
            segment if hasattr(segment, "kind") else segment
            for segment in solution.planned_truck_timeline
        ],
        key=lambda segment: (
            float(segment.start_time if hasattr(segment, "start_time") else segment["start_time"]),
            float(segment.end_time if hasattr(segment, "end_time") else segment["end_time"]),
        ),
    )

    visit_index = 0
    for segment in ordered:
        kind = str(segment.kind if hasattr(segment, "kind") else segment["kind"])
        start_time = float(segment.start_time if hasattr(segment, "start_time") else segment["start_time"])
        end_time = float(segment.end_time if hasattr(segment, "end_time") else segment["end_time"])
        if kind == "dwell":
            departures[visit_index] = max(departures[visit_index], end_time)
        elif kind == "move" and visit_index + 1 < len(solution.truck_route):
            departures[visit_index] = max(departures[visit_index], start_time)
            arrivals[visit_index + 1] = end_time
            visit_index += 1

    if departures:
        departures[-1] = max(departures[-1], arrivals[-1])
    return [max(0.0, departure - arrival) for arrival, departure in zip(arrivals, departures)]


def _render_gantt_panel(ax, panel: dict[str, object], xmax: float) -> None:
    lanes = panel["lanes"]
    lane_positions = list(range(len(lanes) - 1, -1, -1))

    for lane_position, lane in zip(lane_positions, lanes):
        for segment in lane["segments"]:
            start = float(segment["start"])
            end = float(segment["end"])
            duration = end - start
            if duration <= 0.0:
                continue

            style = _gantt_segment_style(segment)
            bar_container = ax.barh(
                lane_position,
                duration,
                left=start,
                height=0.68,
                color=style["facecolor"],
                edgecolor=style["edgecolor"],
                linewidth=1.0,
                hatch=style.get("hatch"),
                alpha=style.get("alpha", 0.95),
            )
            label_text = _gantt_segment_text(segment, duration, xmax)
            if label_text:
                text = ax.text(
                    start + duration / 2.0,
                    lane_position,
                    label_text,
                    ha="center",
                    va="center",
                    fontsize=8,
                )
                text.set_clip_path(bar_container.patches[0])

    ax.set_yticks(lane_positions)
    ax.set_yticklabels([lane["label"] for lane in lanes], fontfamily="serif")
    ax.set_xlim(0.0, xmax * 1.02)
    ax.set_title(str(panel["title"]), fontfamily="serif")
    ax.grid(axis="x", alpha=0.25)


def _gantt_segment_style(segment: dict[str, object]) -> dict[str, object]:
    kind = str(segment["kind"])
    if kind == "event_log":
        return _event_log_activity_style(str(segment.get("activity_type", "")))
    if kind == "truck_move":
        return {"facecolor": "#888888", "edgecolor": "#555555"}
    if kind == "truck_wait":
        return {"facecolor": "#D9D9D9", "edgecolor": "#888888"}
    if kind == "wait_truck":
        return {"facecolor": "#F6BD60", "edgecolor": "#9C6644", "hatch": "////"}
    if kind == "reposition":
        return {"facecolor": "#A8DADC", "edgecolor": "#457B9D", "hatch": ".."}
    if kind == "sortie_leg":
        return _sortie_leg_style(str(segment.get("leg_name", "")))
    drone_id = int(segment.get("drone_id", 0))
    return {"facecolor": _ROUTE_COLORS[drone_id % len(_ROUTE_COLORS)], "edgecolor": "#222222"}


def _build_sortie_leg_segments(
    sortie_index: int,
    drone_id: int,
    sortie_start: float,
    sortie_end: float,
    leg_timings,
) -> list[dict[str, object]]:
    normalized_legs = _normalize_leg_timings(
        sortie_start=sortie_start,
        sortie_end=sortie_end,
        leg_timings=leg_timings,
    )
    if not normalized_legs:
        return [
            {
                "kind": "sortie_leg",
                "leg_name": "sortie",
                "start": sortie_start,
                "end": sortie_end,
                "label": f"S{sortie_index}",
                "drone_id": drone_id,
                "sortie_index": sortie_index,
            }
        ]

    segments: list[dict[str, object]] = []
    for leg in normalized_legs:
        if leg["end_time"] <= leg["start_time"] + 1e-9:
            continue
        leg_name = str(leg["name"])
        if leg_name == "rendezvous":
            leg_name = "collection"
        segments.append(
            {
                "kind": "sortie_leg",
                "leg_name": leg_name,
                "start": leg["start_time"],
                "end": leg["end_time"],
                "label": f"S{sortie_index} {leg_name}",
                "drone_id": drone_id,
                "sortie_index": sortie_index,
            }
        )
    return segments


def _sortie_leg_window(
    sortie_start: float,
    sortie_end: float,
    leg_timings,
) -> tuple[float, float]:
    normalized_legs = _normalize_leg_timings(
        sortie_start=sortie_start,
        sortie_end=sortie_end,
        leg_timings=leg_timings,
    )
    if not normalized_legs:
        return float(sortie_start), float(sortie_end)
    return float(normalized_legs[0]["start_time"]), float(normalized_legs[-1]["end_time"])


def _normalize_leg_timings(
    sortie_start: float,
    sortie_end: float,
    leg_timings,
) -> list[dict[str, float | str]]:
    if not leg_timings:
        return []

    legs = [
        {
            "name": str(leg_timing.name if hasattr(leg_timing, "name") else leg_timing["name"]),
            "start_time": float(
                leg_timing.start_time
                if hasattr(leg_timing, "start_time")
                else leg_timing["start_time"]
            ),
            "end_time": float(
                leg_timing.end_time
                if hasattr(leg_timing, "end_time")
                else leg_timing["end_time"]
            ),
        }
        for leg_timing in leg_timings
    ]
    if not legs:
        return []

    min_start = min(float(leg["start_time"]) for leg in legs)
    max_end = max(float(leg["end_time"]) for leg in legs)
    sortie_duration = max(0.0, float(sortie_end) - float(sortie_start))
    looks_relative = (
        min_start >= -1e-6
        and max_end <= sortie_duration + 1e-6
        and min_start < max(1.0, sortie_start - 1e-6)
    )
    if looks_relative:
        for leg in legs:
            leg["start_time"] = float(sortie_start) + float(leg["start_time"])
            leg["end_time"] = float(sortie_start) + float(leg["end_time"])
    return legs


def _sortie_leg_style(leg_name: str) -> dict[str, object]:
    normalized = str(leg_name).lower()
    styles = {
        "launch": {"facecolor": "#D1495B", "edgecolor": "#222222"},
        "launch_prep": {"facecolor": "#D1495B", "edgecolor": "#222222"},
        "launch_takeoff": {"facecolor": "#B23A48", "edgecolor": "#222222"},
        "outbound": {"facecolor": "#00798C", "edgecolor": "#222222"},
        "delivery_land": {"facecolor": "#C97C1A", "edgecolor": "#222222"},
        "delivery": {"facecolor": "#EDAe49", "edgecolor": "#222222"},
        "delivery_takeoff": {"facecolor": "#9C6644", "edgecolor": "#222222"},
        "return_takeoff": {"facecolor": "#9C6644", "edgecolor": "#222222"},
        "return": {"facecolor": "#6A994E", "edgecolor": "#222222"},
        "waiting": {"facecolor": "#E76F51", "edgecolor": "#222222"},
        "recovery_land": {"facecolor": "#7B6D8D", "edgecolor": "#222222"},
        "recovery": {"facecolor": "#5A4E6B", "edgecolor": "#222222"},
        "collection": {"facecolor": "#7B6D8D", "edgecolor": "#222222"},
        "rendezvous": {"facecolor": "#7B6D8D", "edgecolor": "#222222"},
        "sortie": {"facecolor": _ROUTE_COLORS[0], "edgecolor": "#222222"},
    }
    return styles.get(normalized, {"facecolor": "#5C677D", "edgecolor": "#222222"})


def _event_log_activity_style(activity_type: str) -> dict[str, object]:
    normalized = str(activity_type).strip().lower()
    styles = {
        "truck is stationary with uav(s) on board": {"facecolor": "#D9D9D9", "edgecolor": "#888888"},
        "truck is stationary with no uavs on board": {"facecolor": "#EFEFEF", "edgecolor": "#888888"},
        "truck travels with uav(s) on board": {"facecolor": "#888888", "edgecolor": "#555555"},
        "truck travels with no uavs on board": {"facecolor": "#A9A9A9", "edgecolor": "#555555"},
        "uav is stationary with a parcel": {"facecolor": "#EDAe49", "edgecolor": "#222222"},
        "uav taking off or landing with a parcel": {"facecolor": "#D1495B", "edgecolor": "#222222"},
        "uav travels with parcel": {"facecolor": "#00798C", "edgecolor": "#222222"},
        "uav is stationary without a parcel": {"facecolor": "#7B6D8D", "edgecolor": "#222222"},
        "uav taking off or landing with no parcels": {"facecolor": "#9C6644", "edgecolor": "#222222"},
        "uav travels empty": {"facecolor": "#6A994E", "edgecolor": "#222222"},
    }
    return styles.get(normalized, {"facecolor": "#5C677D", "edgecolor": "#222222"})


def _gantt_segment_text(segment: dict[str, object], duration: float, xmax: float) -> str:
    label = str(segment["label"])
    if str(segment["kind"]) == "event_log":
        return _wrap_gantt_label(label, duration, xmax)
    if str(segment["kind"]) != "sortie_leg":
        return label
    if duration >= max(12.0, xmax * 0.06):
        return label
    return str(segment.get("leg_name", label))


def _wrap_gantt_label(label: str, duration: float, xmax: float) -> str:
    text = str(label).strip()
    if not text:
        return ""

    min_duration_for_text = max(6.0, xmax * 0.02)
    if duration < min_duration_for_text:
        return ""

    chars_per_line = max(8, int(round(duration / max(xmax, 1.0) * 80.0)))
    return textwrap.fill(
        text,
        width=chars_per_line,
        break_long_words=False,
        break_on_hyphens=False,
    )


def save_experiment_route(
    solution: Solution,
    path: str,
    zoom: int | None = None,
    satellite_tiles: bool = True,
) -> None:
    """Save a planned-route image with satellite background when available."""
    fig, ax, _lat_bounds, _lon_bounds = _route_figure(
        _planned_route_points(solution),
        zoom=zoom,
        satellite_tiles=satellite_tiles,
    )

    truck_points = [_node_to_gps(solution, node_id) for node_id in solution.truck_route]
    ax.plot(
        [point[1] for point in truck_points],
        [point[0] for point in truck_points],
        color="#111111",
        linewidth=3.0,
        alpha=0.85,
        label="Truck route",
        zorder=3,
    )

    for sortie_index, sortie in enumerate(solution.sorties):
        color = _ROUTE_COLORS[sortie.drone_id % len(_ROUTE_COLORS)]
        points = [
            _node_to_gps(solution, solution.launch_node(sortie_index)),
            _node_to_gps(solution, sortie.delivery),
            _node_to_gps(solution, sortie.rendezvous),
        ]
        ax.plot(
            [point[1] for point in points],
            [point[0] for point in points],
            color=color,
            linewidth=2.2,
            alpha=0.9,
            linestyle="-",
            label=f"Drone {sortie.drone_id}" if sortie_index == _first_sortie_for_drone(solution, sortie.drone_id) else None,
            zorder=4,
        )

    _draw_route_landmarks(ax, solution)
    ax.set_title("Experiment Route", fontfamily="serif")
    _finalize_route_axes(ax)
    _save_figure(fig, path)


def _condition_color(condition_label: str) -> str:
    text = condition_label.lower()
    if "calm" in text:
        return _COLOR_CYCLE["calm"]
    if "moderate" in text:
        return _COLOR_CYCLE["moderate"]
    if "strong" in text:
        return _COLOR_CYCLE["strong"]
    match = re.search(r"([-+]?[0-9]*\\.?[0-9]+)", text)
    if match:
        speed = float(match.group(1))
        if speed <= 0.1:
            return _COLOR_CYCLE["calm"]
        if speed >= 8.0:
            return _COLOR_CYCLE["strong"]
    return _COLOR_CYCLE["moderate"]


def _apply_plot_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "axes.titlesize": 12,
            "axes.labelsize": 11,
            "legend.fontsize": 9,
        }
    )


def _get_actual_path(report: ComparisonReport, row) -> list:
    for run in report.results:
        if report._condition_label(getattr(run, "condition", None)) != row.condition:
            continue
        if int(getattr(run, "replication", 0)) != row.replication:
            continue
        for drone_result in getattr(run, "drone_results", []):
            if int(getattr(drone_result, "drone_id", -1)) != row.drone_id:
                continue
            for sortie_result in getattr(drone_result, "sortie_results", []):
                if int(sortie_result.sortie_index) == row.sortie_index:
                    return list(sortie_result.actual_path)
    return []


def _save_pdf(fig, path: str) -> None:
    _save_figure(fig, path, default_format="pdf")


def _save_figure(fig, path: str, default_format: str | None = None) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fig.tight_layout()
    save_kwargs = {"dpi": 300, "bbox_inches": "tight"}
    if default_format is not None and "." not in os.path.basename(path):
        save_kwargs["format"] = default_format
    fig.savefig(path, **save_kwargs)
    plt.close(fig)


def _node_to_gps(solution: Solution, node_id: int) -> tuple[float, float]:
    if node_id == 0:
        return solution.problem.depot
    return solution.problem.customers[node_id]


def _first_sortie_for_drone(solution: Solution, drone_id: int) -> int:
    for sortie_index, sortie in enumerate(solution.sorties):
        if sortie.drone_id == drone_id:
            return sortie_index
    return -1


def _route_figure(
    points: list[tuple[float, float]],
    *,
    zoom: int | None,
    satellite_tiles: bool,
):
    lat_min, lat_max, lon_min, lon_max = _route_bounds(points)
    fig, ax = plt.subplots(figsize=(8.5, 8.5))
    _apply_plot_style()

    if satellite_tiles:
        try:
            tile_zoom = _auto_zoom(lat_min, lat_max, lon_min, lon_max) if zoom is None else int(zoom)
            tile_img, tile_extent = _fetch_satellite_basemap(
                lat_min,
                lat_max,
                lon_min,
                lon_max,
                zoom=tile_zoom,
            )
            ax.imshow(tile_img, extent=tile_extent, origin="upper", zorder=0)
        except Exception as exc:
            logger.warning("Falling back to plain background for route map: %s", exc)
            ax.set_facecolor("#E8ECEF")
    else:
        ax.set_facecolor("#E8ECEF")

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    return fig, ax, (lat_min, lat_max), (lon_min, lon_max)


def _route_bounds(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    lats = [point[0] for point in points]
    lons = [point[1] for point in points]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    lat_pad = max((lat_max - lat_min) * 0.2, 5e-4)
    lon_pad = max((lon_max - lon_min) * 0.2, 5e-4)
    return lat_min - lat_pad, lat_max + lat_pad, lon_min - lon_pad, lon_max + lon_pad


def _planned_route_points(solution: Solution) -> list[tuple[float, float]]:
    points = [solution.problem.depot, *solution.problem.customers.values()]
    for sortie_index, sortie in enumerate(solution.sorties):
        points.append(_node_to_gps(solution, solution.launch_node(sortie_index)))
        points.append(_node_to_gps(solution, sortie.delivery))
        points.append(_node_to_gps(solution, sortie.rendezvous))
    return points


def _draw_route_landmarks(ax, solution: Solution) -> None:
    depot = solution.problem.depot
    ax.scatter(
        [depot[1]],
        [depot[0]],
        s=110,
        marker="*",
        color="#FFFFFF",
        edgecolors="#111111",
        linewidths=1.5,
        label="Depot",
        zorder=6,
    )
    ax.text(depot[1], depot[0], " Depot", color="#111111", fontsize=10, zorder=7)

    eligible_points = []
    ineligible_points = []
    for node_id, point in solution.problem.customers.items():
        if node_id in solution.problem.drone_eligible:
            eligible_points.append((node_id, point))
        else:
            ineligible_points.append((node_id, point))

    if eligible_points:
        ax.scatter(
            [point[1][1] for point in eligible_points],
            [point[1][0] for point in eligible_points],
            s=42,
            color="#7CF29C",
            edgecolors="#0B3D20",
            linewidths=0.9,
            label="Drone-eligible customer",
            zorder=5,
        )
    if ineligible_points:
        ax.scatter(
            [point[1][1] for point in ineligible_points],
            [point[1][0] for point in ineligible_points],
            s=42,
            color="#FFD166",
            edgecolors="#6B4F00",
            linewidths=0.9,
            label="Customer",
            zorder=5,
        )

    for node_id, point in sorted(solution.problem.customers.items()):
        ax.text(point[1], point[0], f" C{node_id}", color="#111111", fontsize=9, zorder=7)


def _finalize_route_axes(ax) -> None:
    ax.set_xlabel("Longitude", fontfamily="serif")
    ax.set_ylabel("Latitude", fontfamily="serif")
    handles, labels = ax.get_legend_handles_labels()
    ordered_handles, ordered_labels = _order_route_legend(handles, labels)
    ax.legend(ordered_handles, ordered_labels, frameon=False, loc="upper right")
    ax.xaxis.set_major_formatter(ScalarFormatter(useOffset=False))
    ax.yaxis.set_major_formatter(ScalarFormatter(useOffset=False))
    ax.ticklabel_format(style="plain", axis="both", useOffset=False)


def _order_route_legend(handles: list, labels: list[str]) -> tuple[list, list[str]]:
    paired = list(zip(handles, labels))
    seen: set[str] = set()
    unique_pairs = []
    for handle, label in paired:
        if label in seen:
            continue
        seen.add(label)
        unique_pairs.append((handle, label))

    def _sort_key(item: tuple[object, str]) -> tuple[int, int, str]:
        label = item[1]
        if label == "Truck route":
            return (0, -1, label)
        if label == "Depot":
            return (1, -1, label)
        if label == "Drone-eligible customer":
            return (2, -1, label)
        if label == "Customer":
            return (3, -1, label)
        match = re.fullmatch(r"Drone (\d+)", label)
        if match:
            return (4, int(match.group(1)), label)
        return (5, -1, label)

    ordered = sorted(unique_pairs, key=_sort_key)
    return [handle for handle, _ in ordered], [label for _, label in ordered]


def _fetch_satellite_basemap(
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    zoom: int,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    x_min, y_max = _latlon_to_tile_xy(lat_min, lon_min, zoom)
    x_max, y_min = _latlon_to_tile_xy(lat_max, lon_max, zoom)
    x0, x1 = sorted((int(np.floor(x_min)), int(np.floor(x_max))))
    y0, y1 = sorted((int(np.floor(y_min)), int(np.floor(y_max))))

    max_tiles_per_side = 4
    if (x1 - x0 + 1) > max_tiles_per_side or (y1 - y0 + 1) > max_tiles_per_side:
        raise ValueError("Requested route extent is too large for the default tile fetch window")

    rows = []
    for tile_y in range(y0, y1 + 1):
        row_tiles = []
        for tile_x in range(x0, x1 + 1):
            row_tiles.append(_fetch_satellite_tile(tile_x, tile_y, zoom))
        rows.append(np.concatenate(row_tiles, axis=1))
    image = np.concatenate(rows, axis=0)

    lon_left, lat_top = _tile_xy_to_latlon(x0, y0, zoom)
    lon_right, lat_bottom = _tile_xy_to_latlon(x1 + 1, y1 + 1, zoom)
    extent = (lon_left, lon_right, lat_bottom, lat_top)
    return image, extent


def _fetch_satellite_tile(tile_x: int, tile_y: int, zoom: int) -> np.ndarray:
    url = _tile_url(tile_x, tile_y, zoom)
    request = Request(url, headers={"User-Agent": "dronevalkit/0.1"})
    with urlopen(request, timeout=10.0) as response:
        data = response.read()
    image = _decode_tile_image(data)
    if image.ndim == 2:
        image = np.stack([image, image, image], axis=-1)
    return image


def _decode_tile_image(data: bytes) -> np.ndarray:
    try:
        from PIL import Image
    except ImportError:
        image = plt.imread(io.BytesIO(data))
        if image.dtype.kind in {"u", "i"}:
            image = image.astype(np.float32) / 255.0
        return image

    with Image.open(io.BytesIO(data)) as img:
        image = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    return image


def _latlon_to_tile_xy(lat_deg: float, lon_deg: float, zoom: int) -> tuple[float, float]:
    lat_rad = np.radians(np.clip(lat_deg, -85.05112878, 85.05112878))
    n = 2.0 ** zoom
    x = (lon_deg + 180.0) / 360.0 * n
    y = (1.0 - np.log(np.tan(lat_rad) + 1.0 / np.cos(lat_rad)) / np.pi) / 2.0 * n
    return x, y


def _tile_xy_to_latlon(tile_x: float, tile_y: float, zoom: int) -> tuple[float, float]:
    n = 2.0 ** zoom
    lon_deg = tile_x / n * 360.0 - 180.0
    lat_rad = np.arctan(np.sinh(np.pi * (1.0 - 2.0 * tile_y / n)))
    lat_deg = np.degrees(lat_rad)
    return lon_deg, lat_deg


def _auto_zoom(lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> int:
    for zoom in range(19, 11, -1):
        x_min, y_max = _latlon_to_tile_xy(lat_min, lon_min, zoom)
        x_max, y_min = _latlon_to_tile_xy(lat_max, lon_max, zoom)
        x_tiles = abs(int(np.floor(x_max)) - int(np.floor(x_min))) + 1
        y_tiles = abs(int(np.floor(y_max)) - int(np.floor(y_min))) + 1
        if x_tiles <= 4 and y_tiles <= 4:
            return zoom
    return 12


def _tile_url(tile_x: int, tile_y: int, zoom: int) -> str:
    subdomain = _BASEMAP_SUBDOMAINS[(tile_x + tile_y) % len(_BASEMAP_SUBDOMAINS)]
    return _BASEMAP_TILE_URL.format(
        s=subdomain,
        z=zoom,
        x=tile_x,
        y=tile_y,
        r="",
    )
