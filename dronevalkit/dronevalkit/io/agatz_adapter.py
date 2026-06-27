"""Import dronevalkit solutions from Agatz TSP-D geometric benchmark files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import re

from ..exceptions import InvalidSolutionError
from ..geo import ned_to_gps
from ..models import LegTiming, PlannedMetrics, Problem, Solution, Sortie, TruckTimingSegment


_SUPPORTED_SOLUTION_SUFFIXES = ("-DP", "-tsp")
_COMMENT_RE = re.compile(r"/\*.*?\*/", flags=re.DOTALL)
_TOTAL_COST_RE = re.compile(r"Total cost\s*:\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)")
# Assumed local GPS anchor near the center of the University of Missouri campus.
_REFERENCE_LAT = 38.9457
_REFERENCE_LON = -92.3299
# Interpret Agatz Euclidean coordinates as local metric offsets after applying
# a fixed scale factor. This keeps the synthetic instances in a more realistic
# physical size/speed regime for PX4 than a literal 1 unit = 1 meter mapping.
_METERS_PER_UNIT = 10.0
_DEFAULT_AGATZ_CRUISE_ALTITUDE_M = 20.0
_DEFAULT_AGATZ_DELIVERY_ALTITUDE_M = 3.0
_DEFAULT_AGATZ_TAKEOFF_SPEED_M_S = 3.0
_DEFAULT_AGATZ_LANDING_SPEED_M_S = 1.5
_DEFAULT_AGATZ_DELIVERY_TIME_S = 60.0
_DEFAULT_AGATZ_LAUNCH_TIME_S = 0.0
_DEFAULT_AGATZ_RECOVERY_TIME_S = 0.0


@dataclass(frozen=True)
class AgatzCase:
    """A single Agatz problem/solution pair on disk."""

    instance_name: str
    instance_path: Path
    solution_path: Path
    solution_type: str


def list_agatz_cases(root: str | Path) -> list[AgatzCase]:
    """Return all supported Agatz problem/solution pairs found below *root*."""

    root_path = Path(root)
    if not root_path.exists():
        raise InvalidSolutionError(f"Agatz root does not exist: {root_path}")
    if not root_path.is_dir():
        raise InvalidSolutionError(f"Agatz root is not a directory: {root_path}")

    solutions_dir = root_path / "solutions"
    if not solutions_dir.exists() or not solutions_dir.is_dir():
        raise InvalidSolutionError(f"Agatz solutions directory does not exist: {solutions_dir}")

    instance_paths = {
        instance_path.stem: instance_path
        for instance_path in sorted(root_path.glob("uniform*.txt"))
        if instance_path.parent == root_path
    }

    cases: list[AgatzCase] = []
    for solution_path in sorted(solutions_dir.glob("*.txt")):
        try:
            instance_name, solution_type = _parse_solution_filename(solution_path.name)
        except InvalidSolutionError:
            continue
        instance_path = instance_paths.get(instance_name)
        if instance_path is None:
            continue
        cases.append(
            AgatzCase(
                instance_name=instance_name,
                instance_path=instance_path,
                solution_path=solution_path,
                solution_type=solution_type,
            )
        )
    return cases


def from_agatz(
    solution_path: str | Path,
    *,
    instance_path: str | Path | None = None,
    cruise_altitude_m: float = _DEFAULT_AGATZ_CRUISE_ALTITUDE_M,
    delivery_altitude_m: float = _DEFAULT_AGATZ_DELIVERY_ALTITUDE_M,
    takeoff_speed_m_s: float = _DEFAULT_AGATZ_TAKEOFF_SPEED_M_S,
    landing_speed_m_s: float = _DEFAULT_AGATZ_LANDING_SPEED_M_S,
    delivery_time_s: float = _DEFAULT_AGATZ_DELIVERY_TIME_S,
    launch_time_s: float = _DEFAULT_AGATZ_LAUNCH_TIME_S,
    recovery_time_s: float = _DEFAULT_AGATZ_RECOVERY_TIME_S,
) -> Solution:
    """Load one Agatz TSP-D solution file as a :class:`~dronevalkit.models.Solution`."""

    solution_file = Path(solution_path)
    if not solution_file.exists():
        raise InvalidSolutionError(f"Agatz solution file does not exist: {solution_file}")

    instance_name, _solution_type = _parse_solution_filename(solution_file.name)
    inferred_instance_path = solution_file.parent.parent / f"{instance_name}.txt"
    instance_file = Path(instance_path) if instance_path is not None else inferred_instance_path
    if not instance_file.exists():
        raise InvalidSolutionError(f"Agatz instance file does not exist: {instance_file}")

    cruise_altitude_m = float(cruise_altitude_m)
    delivery_altitude_m = float(delivery_altitude_m)
    takeoff_speed_m_s = float(takeoff_speed_m_s)
    landing_speed_m_s = float(landing_speed_m_s)
    delivery_time_s = float(delivery_time_s)
    launch_time_s = float(launch_time_s)
    recovery_time_s = float(recovery_time_s)
    if cruise_altitude_m <= 0.0:
        raise InvalidSolutionError("Agatz cruise_altitude_m must be positive")
    if delivery_altitude_m < 0.0:
        raise InvalidSolutionError("Agatz delivery_altitude_m must be non-negative")
    if takeoff_speed_m_s <= 0.0 or landing_speed_m_s <= 0.0:
        raise InvalidSolutionError("Agatz takeoff/landing speeds must be positive")
    if delivery_time_s < 0.0 or launch_time_s < 0.0 or recovery_time_s < 0.0:
        raise InvalidSolutionError("Agatz launch/delivery/recovery times must be non-negative")

    instance = _read_instance_file(instance_file)
    operations, total_cost = _read_solution_file(solution_file)

    truck_route: list[int] = []
    truck_leg_travel_times: list[float] = []
    planned_truck_timeline: list[TruckTimingSegment] = []
    truck_arrival_times: dict[int, float] = {0: 0.0}
    sorties: list[Sortie] = []
    sortie_times: list[float] = []
    sortie_leg_times: list[list[LegTiming]] = []
    operation_costs: list[float] = []
    elapsed_time = 0.0

    for operation in operations:
        start = int(operation["start"])
        end = int(operation["end"])
        fly = int(operation["fly"])
        internal_nodes = list(operation["internal_nodes"])
        truck_path = [start, *internal_nodes, end]

        if not truck_route:
            truck_route.extend(truck_path[:1])
        for from_node, to_node in zip(truck_path, truck_path[1:]):
            if from_node == to_node:
                continue
            if not truck_route or truck_route[-1] != from_node:
                truck_route.append(from_node)
            truck_route.append(to_node)
            truck_leg_travel_times.append(
                _distance(instance["plane_points"][from_node], instance["plane_points"][to_node])
                * float(instance["truck_factor"])
            )

        truck_cost = 0.0
        segment_time = elapsed_time
        for from_node, to_node in zip(truck_path, truck_path[1:]):
            travel_time = (
                _distance(instance["plane_points"][from_node], instance["plane_points"][to_node])
                * float(instance["truck_factor"])
            )
            truck_cost += travel_time
            if travel_time <= 0.0:
                continue
            planned_truck_timeline.append(
                TruckTimingSegment(
                    kind="move",
                    start_time=segment_time,
                    end_time=segment_time + travel_time,
                    start_node=from_node,
                    end_node=to_node,
                    label=f"Travel from node {from_node} to node {to_node}",
                )
            )
            segment_time += travel_time
            if to_node != 0:
                truck_arrival_times.setdefault(to_node, segment_time)

        drone_cost = 0.0
        if fly >= 0:
            leg_timings = _build_agatz_sortie_leg_timings(
                start_point=instance["plane_points"][start],
                delivery_point=instance["plane_points"][fly],
                rendezvous_point=instance["plane_points"][end],
                truck_travel_time_s=truck_cost,
                drone_factor=float(instance["drone_factor"]),
                cruise_altitude_m=cruise_altitude_m,
                delivery_altitude_m=delivery_altitude_m,
                takeoff_speed_m_s=takeoff_speed_m_s,
                landing_speed_m_s=landing_speed_m_s,
                delivery_time_s=delivery_time_s,
                launch_time_s=launch_time_s,
                recovery_time_s=recovery_time_s,
            )
            drone_cost = leg_timings[-1].end_time if leg_timings else 0.0
            sorties.append(
                Sortie(
                    launch=start,
                    delivery=fly,
                    rendezvous=end,
                )
            )
            sortie_times.append(drone_cost)
            sortie_leg_times.append(leg_timings)

        operation_duration = max(truck_cost, drone_cost)
        if operation_duration > truck_cost:
            planned_truck_timeline.append(
                TruckTimingSegment(
                    kind="dwell",
                    start_time=elapsed_time + truck_cost,
                    end_time=elapsed_time + operation_duration,
                    start_node=end,
                    end_node=end,
                    label=(
                        f"Wait for drone at node {end}"
                        if fly >= 0
                        else f"Dwell at node {end}"
                    ),
                )
            )

        elapsed_time += operation_duration
        operation_costs.append(operation_duration)

    if not truck_route:
        truck_route = [0]

    truck_speed = _factor_to_speed(float(instance["truck_factor"]), "truck")
    drone_speed = _factor_to_speed(float(instance["drone_factor"]), "drone")
    planned_metrics = PlannedMetrics(
        drone_speed=drone_speed,
        makespan=float(sum(operation_costs)),
        sortie_times=sortie_times,
        sortie_leg_times=sortie_leg_times or None,
    )

    return Solution(
        problem=Problem(
            depot=instance["gps_points"][0],
            customers={node_id: gps for node_id, gps in instance["gps_points"].items() if node_id != 0},
            drone_eligible=sorted(node_id for node_id in instance["gps_points"] if node_id != 0),
        ),
        truck_route=truck_route,
        sorties=sorties,
        planned_metrics=planned_metrics,
        num_drones=1,
        truck_speed=truck_speed,
        truck_service_time=0.0,
        truck_leg_travel_times=truck_leg_travel_times,
        planned_truck_timeline=planned_truck_timeline or None,
        truck_arrival_times=truck_arrival_times or None,
    )


def _parse_solution_filename(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    for suffix in _SUPPORTED_SOLUTION_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)], suffix[1:]
    raise InvalidSolutionError(f"Unsupported Agatz solution filename: {filename}")


def _read_instance_file(path: Path) -> dict[str, object]:
    tokens = _tokenize_without_comments(path.read_text())
    if len(tokens) < 6:
        raise InvalidSolutionError(f"Agatz instance file is too short: {path}")

    truck_factor = float(tokens[0])
    drone_factor = float(tokens[1])
    node_count = int(tokens[2])
    if truck_factor <= 0.0 or drone_factor <= 0.0:
        raise InvalidSolutionError(f"Agatz instance factors must be positive: {path}")
    if node_count < 1:
        raise InvalidSolutionError(f"Agatz node count must be positive: {path}")

    expected_token_count = 3 + node_count * 3
    if len(tokens) != expected_token_count:
        raise InvalidSolutionError(
            f"Agatz instance file has {len(tokens)} tokens, expected {expected_token_count}: {path}"
        )

    plane_points: dict[int, tuple[float, float]] = {}
    index = 3
    for node_id in range(node_count):
        x = float(tokens[index])
        y = float(tokens[index + 1])
        _node_name = tokens[index + 2]
        plane_points[node_id] = (x, y)
        index += 3

    depot_x, depot_y = plane_points[0]
    gps_points = {
        node_id: ned_to_gps(
            north=float(y - depot_y) * _METERS_PER_UNIT,
            east=float(x - depot_x) * _METERS_PER_UNIT,
            ref_lat=_REFERENCE_LAT,
            ref_lon=_REFERENCE_LON,
        )
        for node_id, (x, y) in plane_points.items()
    }

    return {
        "truck_factor": truck_factor,
        "drone_factor": drone_factor,
        "plane_points": plane_points,
        "gps_points": gps_points,
    }


def _read_solution_file(path: Path) -> tuple[list[dict[str, object]], float | None]:
    raw_text = path.read_text()
    total_cost_match = _TOTAL_COST_RE.search(raw_text)
    total_cost = float(total_cost_match.group(1)) if total_cost_match is not None else None

    cleaned_lines = [_strip_inline_comments(line).strip() for line in raw_text.splitlines()]
    data_lines = [line for line in cleaned_lines if line]
    if not data_lines:
        raise InvalidSolutionError(f"Agatz solution file is empty: {path}")

    try:
        operation_count = int(data_lines[0])
    except ValueError as exc:
        raise InvalidSolutionError(f"Agatz solution file is missing operation count: {path}") from exc

    operation_lines = data_lines[1:]
    if len(operation_lines) != operation_count:
        raise InvalidSolutionError(
            f"Agatz solution file declares {operation_count} operations but contains {len(operation_lines)}: {path}"
        )

    operations: list[dict[str, object]] = []
    for line in operation_lines:
        fields = line.split()
        if len(fields) < 4:
            raise InvalidSolutionError(f"Malformed Agatz operation row in {path}: {line}")
        start = int(fields[0])
        end = int(fields[1])
        fly = int(fields[2])
        internal_count = int(fields[3])
        expected_length = 4 + internal_count
        if len(fields) != expected_length:
            raise InvalidSolutionError(f"Malformed Agatz operation row in {path}: {line}")
        operations.append(
            {
                "start": start,
                "end": end,
                "fly": fly,
                "internal_nodes": [int(value) for value in fields[4:]],
            }
        )
    return operations, total_cost


def _tokenize_without_comments(text: str) -> list[str]:
    return _COMMENT_RE.sub(" ", text).split()


def _strip_inline_comments(line: str) -> str:
    return re.sub(r"/\*.*?\*/", "", line).strip()


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _factor_to_speed(factor: float, vehicle_name: str) -> float:
    if factor <= 0.0:
        raise InvalidSolutionError(f"Agatz {vehicle_name} factor must be positive")
    return _METERS_PER_UNIT / factor


def _build_agatz_sortie_leg_timings(
    *,
    start_point: tuple[float, float],
    delivery_point: tuple[float, float],
    rendezvous_point: tuple[float, float],
    truck_travel_time_s: float,
    drone_factor: float,
    cruise_altitude_m: float,
    delivery_altitude_m: float,
    takeoff_speed_m_s: float,
    landing_speed_m_s: float,
    delivery_time_s: float,
    launch_time_s: float,
    recovery_time_s: float,
) -> list[LegTiming]:
    timings: list[LegTiming] = []
    elapsed_time_s = 0.0

    def add_leg(name: str, duration_s: float) -> None:
        nonlocal elapsed_time_s
        duration_s = max(0.0, float(duration_s))
        if math.isclose(duration_s, 0.0):
            return
        timings.append(
            LegTiming(
                name=name,
                start_time=elapsed_time_s,
                end_time=elapsed_time_s + duration_s,
            )
        )
        elapsed_time_s += duration_s

    delivery_vertical_m = max(0.0, float(cruise_altitude_m) - float(delivery_altitude_m))
    add_leg("launch_prep", launch_time_s)
    add_leg("launch_takeoff", float(cruise_altitude_m) / float(takeoff_speed_m_s))
    add_leg("outbound", _distance(start_point, delivery_point) * float(drone_factor))
    add_leg("delivery_land", delivery_vertical_m / float(landing_speed_m_s))
    add_leg("delivery", delivery_time_s)
    add_leg("delivery_takeoff", delivery_vertical_m / float(takeoff_speed_m_s))
    add_leg("return", _distance(delivery_point, rendezvous_point) * float(drone_factor))
    add_leg("waiting", float(truck_travel_time_s) - elapsed_time_s)
    add_leg("recovery_land", float(cruise_altitude_m) / float(landing_speed_m_s))
    add_leg("recovery", recovery_time_s)
    return timings
