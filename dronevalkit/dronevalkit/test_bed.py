"""Helpers for curating a structured validation test bed from solved cases."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import csv
import json
from typing import Iterable

from .geo import haversine_distance
from .io import list_agatz_cases, list_mfstsp_cases
from .models import Solution


@dataclass(frozen=True)
class CuratedCase:
    """One solved benchmark case annotated for manifest-driven experiments."""

    case_id: str
    benchmark_family: str
    algorithm_family: str
    algorithm_label: str
    source_path: str
    instance_key: str
    solution_type: str
    num_customers: int
    size_tier: str
    num_drones: int
    drone_count_tier: str
    sortie_count: int
    planned_makespan_s: float
    planned_drone_speed_m_s: float
    mean_depot_distance_m: float
    max_depot_distance_m: float
    mean_nearest_neighbor_distance_m: float
    clustering_ratio: float
    spatial_pattern: str
    mean_sortie_distance_m: float
    max_sortie_distance_m: float
    sortie_distance_ratio: float
    sortie_distance_profile: str
    has_vehicle_profile: bool
    vehicle_profile_cruise_altitude_m: float | None


def inventory_cases(
    *,
    agatz_root: str | Path | None = None,
    mfstsp_root: str | Path | None = None,
    strict: bool = False,
) -> list[CuratedCase]:
    """Load solved benchmark cases and annotate them for curation."""

    records: list[CuratedCase] = []

    if agatz_root is not None:
        for case in list_agatz_cases(agatz_root):
            try:
                solution = _load_solution("agatz", case.solution_path)
            except Exception:
                if strict:
                    raise
                continue
            records.append(
                build_curated_case(
                    solution=solution,
                    benchmark_family="agatz",
                    algorithm_family="agatz",
                    algorithm_label=case.solution_type,
                    source_path=case.solution_path,
                    instance_key=case.instance_name,
                    solution_type=case.solution_type,
                )
            )

    if mfstsp_root is not None:
        for case in list_mfstsp_cases(mfstsp_root):
            try:
                solution = _load_solution("mfstsp", case.solution_path)
            except Exception:
                if strict:
                    raise
                continue
            records.append(
                build_curated_case(
                    solution=solution,
                    benchmark_family="mfstsp",
                    algorithm_family="mfstsp",
                    algorithm_label=case.solution_type,
                    source_path=case.solution_path,
                    instance_key=case.problem_name,
                    solution_type=case.solution_type,
                )
            )

    return sorted(records, key=lambda record: record.case_id)


def build_curated_case(
    *,
    solution: Solution,
    benchmark_family: str,
    algorithm_family: str,
    algorithm_label: str,
    source_path: str | Path,
    instance_key: str,
    solution_type: str,
) -> CuratedCase:
    """Compute curation metadata for one imported solution."""

    num_customers = len(solution.problem.customers)
    depot_lat, depot_lon = solution.problem.depot
    customer_points = [
        (int(node_id), float(lat), float(lon))
        for node_id, (lat, lon) in sorted(solution.problem.customers.items())
    ]

    depot_distances = [
        haversine_distance(depot_lat, depot_lon, lat, lon)
        for _, lat, lon in customer_points
    ]
    mean_depot_distance = _mean(depot_distances)
    max_depot_distance = max(depot_distances, default=0.0)
    mean_nearest_neighbor_distance = _mean(_nearest_neighbor_distances(customer_points))
    clustering_ratio = (
        mean_nearest_neighbor_distance / mean_depot_distance
        if mean_depot_distance > 0.0
        else 0.0
    )

    sortie_distances = [_sortie_distance_m(solution, sortie_index) for sortie_index, _ in enumerate(solution.sorties)]
    mean_sortie_distance = _mean(sortie_distances)
    max_sortie_distance = max(sortie_distances, default=0.0)
    sortie_distance_ratio = (
        mean_sortie_distance / max_depot_distance
        if max_depot_distance > 0.0 and solution.sorties
        else 0.0
    )

    vehicle_profile = solution.planned_metrics.vehicle_speeds
    case_id = _slug(
        benchmark_family,
        algorithm_label,
        instance_key,
        Path(source_path).stem,
    )
    return CuratedCase(
        case_id=case_id,
        benchmark_family=str(benchmark_family),
        algorithm_family=str(algorithm_family),
        algorithm_label=str(algorithm_label),
        source_path=str(Path(source_path)),
        instance_key=str(instance_key),
        solution_type=str(solution_type),
        num_customers=num_customers,
        size_tier=_size_tier(num_customers),
        num_drones=int(solution.num_drones),
        drone_count_tier=_drone_count_tier(int(solution.num_drones)),
        sortie_count=len(solution.sorties),
        planned_makespan_s=float(solution.planned_metrics.makespan),
        planned_drone_speed_m_s=float(solution.planned_metrics.drone_speed),
        mean_depot_distance_m=mean_depot_distance,
        max_depot_distance_m=max_depot_distance,
        mean_nearest_neighbor_distance_m=mean_nearest_neighbor_distance,
        clustering_ratio=clustering_ratio,
        spatial_pattern=_spatial_pattern(clustering_ratio),
        mean_sortie_distance_m=mean_sortie_distance,
        max_sortie_distance_m=max_sortie_distance,
        sortie_distance_ratio=sortie_distance_ratio,
        sortie_distance_profile=_sortie_distance_profile(sortie_distance_ratio, len(solution.sorties)),
        has_vehicle_profile=vehicle_profile is not None,
        vehicle_profile_cruise_altitude_m=(
            float(vehicle_profile.cruise_altitude)
            if vehicle_profile is not None and vehicle_profile.cruise_altitude is not None
            else None
        ),
    )


def select_balanced_cases(
    records: Iterable[CuratedCase],
    *,
    quota_per_cell: int = 1,
    include_truck_only: bool = False,
) -> list[CuratedCase]:
    """Select a deterministic balanced subset from curated records."""

    if quota_per_cell < 1:
        raise ValueError("quota_per_cell must be at least 1")

    buckets: dict[tuple[str, str, str, str, str], list[CuratedCase]] = {}
    for record in sorted(records, key=lambda item: item.case_id):
        if not include_truck_only and record.sortie_count == 0:
            continue
        key = (
            record.algorithm_label,
            record.size_tier,
            record.drone_count_tier,
            record.spatial_pattern,
            record.sortie_distance_profile,
        )
        buckets.setdefault(key, []).append(record)

    selected: list[CuratedCase] = []
    seen_case_ids: set[str] = set()
    for key in sorted(buckets):
        for record in buckets[key][:quota_per_cell]:
            if record.case_id in seen_case_ids:
                continue
            selected.append(record)
            seen_case_ids.add(record.case_id)
    return selected


def coverage_counts(records: Iterable[CuratedCase]) -> dict[str, dict[str, int]]:
    """Summarize coverage across paper-facing stratification fields."""

    summary: dict[str, dict[str, int]] = {
        "algorithm_label": {},
        "size_tier": {},
        "drone_count_tier": {},
        "spatial_pattern": {},
        "sortie_distance_profile": {},
    }
    for record in records:
        for field_name in summary:
            value = getattr(record, field_name)
            summary[field_name][value] = summary[field_name].get(value, 0) + 1
    return summary


def write_manifest(records: Iterable[CuratedCase], output_dir: str | Path) -> tuple[Path, Path]:
    """Write curated records to CSV and JSON manifests."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    manifest_records = [asdict(record) for record in sorted(records, key=lambda item: item.case_id)]

    csv_path = output_path / "manifest.csv"
    json_path = output_path / "manifest.json"
    if manifest_records:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(manifest_records[0].keys()))
            writer.writeheader()
            writer.writerows(manifest_records)
    else:
        csv_path.write_text("", encoding="utf-8")
    json_path.write_text(json.dumps(manifest_records, indent=2), encoding="utf-8")
    return csv_path, json_path


def _load_solution(benchmark_family: str, path: Path) -> Solution:
    if benchmark_family == "agatz":
        from . import from_agatz

        return from_agatz(path)
    if benchmark_family == "mfstsp":
        from . import from_mfstsp

        return from_mfstsp(path)
    raise ValueError(f"Unsupported benchmark family: {benchmark_family}")


def _mean(values: Iterable[float]) -> float:
    numbers = list(values)
    if not numbers:
        return 0.0
    return float(sum(numbers) / len(numbers))


def _nearest_neighbor_distances(customer_points: list[tuple[int, float, float]]) -> list[float]:
    if len(customer_points) < 2:
        return [0.0] if customer_points else []

    distances: list[float] = []
    for node_id, lat, lon in customer_points:
        nearest = min(
            haversine_distance(lat, lon, other_lat, other_lon)
            for other_id, other_lat, other_lon in customer_points
            if other_id != node_id
        )
        distances.append(nearest)
    return distances


def _sortie_distance_m(solution: Solution, sortie_index: int) -> float:
    sortie = solution.sorties[sortie_index]
    launch_lat, launch_lon = _node_to_gps(solution, int(solution.launch_node(sortie_index)))
    delivery_lat, delivery_lon = _node_to_gps(solution, int(sortie.delivery))
    rendezvous_lat, rendezvous_lon = _node_to_gps(solution, int(sortie.rendezvous))
    return (
        haversine_distance(launch_lat, launch_lon, delivery_lat, delivery_lon)
        + haversine_distance(delivery_lat, delivery_lon, rendezvous_lat, rendezvous_lon)
    )


def _node_to_gps(solution: Solution, node_id: int) -> tuple[float, float]:
    if int(node_id) == 0:
        return solution.problem.depot
    return solution.problem.customers[int(node_id)]


def _size_tier(num_customers: int) -> str:
    if num_customers <= 10:
        return "small"
    if num_customers <= 50:
        return "medium"
    return "large"


def _drone_count_tier(num_drones: int) -> str:
    if num_drones <= 1:
        return "single"
    if num_drones <= 3:
        return "multi_light"
    return "multi_heavy"


def _spatial_pattern(clustering_ratio: float) -> str:
    if clustering_ratio <= 0.25:
        return "clustered"
    if clustering_ratio >= 0.5:
        return "dispersed"
    return "mixed"


def _sortie_distance_profile(sortie_distance_ratio: float, sortie_count: int) -> str:
    if sortie_count == 0:
        return "truck_only"
    if sortie_distance_ratio < 1.0:
        return "short"
    if sortie_distance_ratio < 1.75:
        return "medium"
    return "long"


def _slug(*parts: object) -> str:
    text = "-".join(str(part).strip().lower() for part in parts if str(part).strip())
    return "".join(char if char.isalnum() else "-" for char in text).strip("-")
