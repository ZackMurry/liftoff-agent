"""Import dronevalkit solutions from Murray mFSTSP benchmark CSV files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import re
from typing import Iterable

from ..exceptions import InvalidSolutionError
from ..models import (
    LegTiming,
    PlannedMetrics,
    Problem,
    Solution,
    Sortie,
    TruckTimingSegment,
    VehicleSpeeds,
)


_SOLUTION_NAME_RE = re.compile(r"tbl_solutions_(\d+)_(\d+)_(.+)\.csv$")
_TRUCK_UAV_REF_RE = re.compile(r"UAV\s+(\d+)")


@dataclass(frozen=True)
class MfstspCase:
    """A single mFSTSP problem/solution pair on disk."""

    problem_name: str
    problem_dir: Path
    solution_path: Path
    vehicle_file_id: int
    num_uavs: int
    solution_type: str


def list_mfstsp_cases(root: str | Path) -> list[MfstspCase]:
    """Return all parseable mFSTSP cases found below *root*."""

    root_path = Path(root)
    if not root_path.exists():
        raise InvalidSolutionError(f"mFSTSP root does not exist: {root_path}")
    if not root_path.is_dir():
        raise InvalidSolutionError(f"mFSTSP root is not a directory: {root_path}")

    cases: list[MfstspCase] = []
    for problem_dir in sorted(path for path in root_path.iterdir() if path.is_dir()):
        for solution_path in sorted(problem_dir.glob("tbl_solutions_*.csv")):
            vehicle_file_id, num_uavs, solution_type = _parse_solution_filename(solution_path.name)
            cases.append(
                MfstspCase(
                    problem_name=problem_dir.name,
                    problem_dir=problem_dir,
                    solution_path=solution_path,
                    vehicle_file_id=vehicle_file_id,
                    num_uavs=num_uavs,
                    solution_type=solution_type,
                )
            )
    return cases


def from_mfstsp(
    solution_path: str | Path,
    *,
    locations_path: str | Path | None = None,
    vehicles_path: str | Path | None = None,
    truck_travel_path: str | Path | None = None,
) -> Solution:
    """Load one mFSTSP solution CSV as a :class:`~dronevalkit.models.Solution`."""

    solution_file = Path(solution_path)
    if not solution_file.exists():
        raise InvalidSolutionError(f"mFSTSP solution file does not exist: {solution_file}")

    vehicle_file_id, _, _ = _parse_solution_filename(solution_file.name)
    problem_dir = solution_file.parent
    dataset_root = problem_dir.parent

    locations_file = Path(locations_path) if locations_path is not None else problem_dir / "tbl_locations.csv"
    vehicles_file = (
        Path(vehicles_path)
        if vehicles_path is not None
        else dataset_root / f"tbl_vehicles_{vehicle_file_id}.csv"
    )
    truck_travel_file = (
        Path(truck_travel_path)
        if truck_travel_path is not None
        else problem_dir / "tbl_truck_travel_data_PG.csv"
    )

    metadata, assignment_rows, objective_value = _read_solution_file(solution_file)
    locations = _read_locations(locations_file)
    vehicle_spec = _read_vehicle_spec(vehicles_file)
    truck_travel = _read_truck_travel(truck_travel_file)

    end_depot_node = max(locations["customers"], default=0) + 1

    truck_rows = [row for row in assignment_rows if row["vehicleType"] == "Truck"]
    if not truck_rows:
        raise InvalidSolutionError(f"No truck assignments found in {solution_file}")

    truck_route, truck_arrival_times = _build_truck_route(
        truck_rows,
        end_depot_node=end_depot_node,
    )

    drone_vehicle_ids = sorted(vehicle_id for vehicle_id in vehicle_spec["uav_ids"])
    drone_id_map = {vehicle_id: index for index, vehicle_id in enumerate(drone_vehicle_ids)}
    sorties = _build_sorties(
        assignment_rows,
        drone_id_map=drone_id_map,
        end_depot_node=end_depot_node,
        solution_path=solution_file,
    )

    truck_speed = _infer_truck_speed(truck_route, truck_travel)
    truck_leg_travel_times = [
        float(truck_travel[(int(from_node), int(to_node))][0])
        for from_node, to_node in zip(truck_route, truck_route[1:])
    ]
    planned_truck_timeline = _build_truck_timeline(
        truck_rows,
        end_depot_node=end_depot_node,
        drone_id_map=drone_id_map,
    )

    planned_metrics = PlannedMetrics(
        drone_speed=float(vehicle_spec["drone_speed"]),
        makespan=objective_value,
        sortie_times=[float(sortie_data["duration_s"]) for sortie_data in sorties],
        sortie_energies=None,
        sortie_leg_times=[list(sortie_data["leg_timings"]) for sortie_data in sorties],
        vehicle_speeds=VehicleSpeeds(
            takeoff=float(vehicle_spec["takeoff_speed"]),
            cruise=float(vehicle_spec["drone_speed"]),
            landing=float(vehicle_spec["landing_speed"]),
            yaw_rate_deg=float(vehicle_spec["yaw_rate_deg"]),
            launch_time=float(vehicle_spec["launch_time"]),
            recovery_time=float(vehicle_spec["recovery_time"]),
            cruise_altitude=float(vehicle_spec["cruise_altitude"]),
        ),
    )

    return Solution(
        problem=Problem(
            depot=locations["depot"],
            customers=locations["customers"],
            drone_eligible=_eligible_customers(
                locations["customer_weights_lbs"],
                float(vehicle_spec["drone_capacity_lbs"]),
            ),
        ),
        truck_route=truck_route,
        sorties=[
            Sortie(
                launch=int(sortie_data["launch"]),
                delivery=int(sortie_data["delivery"]),
                rendezvous=int(sortie_data["rendezvous"]),
                drone_id=int(sortie_data["drone_id"]),
            )
            for sortie_data in sorties
        ],
        planned_metrics=planned_metrics,
        num_drones=int(metadata.get("numUAVs", len(drone_vehicle_ids))),
        truck_speed=truck_speed,
        truck_service_time=float(vehicle_spec["truck_service_time"]),
        truck_leg_travel_times=truck_leg_travel_times,
        planned_truck_timeline=planned_truck_timeline,
        truck_arrival_times=truck_arrival_times or None,
    )


def _parse_solution_filename(filename: str) -> tuple[int, int, str]:
    match = _SOLUTION_NAME_RE.match(filename)
    if match is None:
        raise InvalidSolutionError(f"Unrecognized mFSTSP solution filename: {filename}")
    return int(match.group(1)), int(match.group(2)), match.group(3)


def _read_solution_file(path: Path) -> tuple[dict[str, object], list[dict[str, object]], float]:
    lines = [line.strip() for line in path.read_text().splitlines() if line.strip()]
    if len(lines) < 5:
        raise InvalidSolutionError(f"mFSTSP solution file is too short: {path}")

    metadata_header = next(_parse_csv_rows([lines[0]]))
    metadata_values = next(_parse_csv_rows([lines[1]]))
    metadata = {
        key: _coerce_value(value)
        for key, value in zip(metadata_header, metadata_values)
    }

    objective_line = next((line for line in lines if line.startswith("Objective Function Value:")), None)
    if objective_line is None:
        raise InvalidSolutionError(f"Missing objective value in {path}")
    objective_value = float(objective_line.split(":", 1)[1].strip())

    try:
        assignments_index = lines.index("Assignments:")
    except ValueError as exc:
        raise InvalidSolutionError(f"Missing assignments section in {path}") from exc
    if assignments_index + 2 >= len(lines):
        raise InvalidSolutionError(f"Assignments section is incomplete in {path}")

    assignment_header = next(_parse_csv_rows([lines[assignments_index + 1]]))
    assignment_rows: list[dict[str, object]] = []
    for row in _parse_csv_rows(lines[assignments_index + 2:]):
        if len(row) != len(assignment_header):
            raise InvalidSolutionError(f"Malformed assignment row in {path}: {row}")
        assignment_rows.append(
            {
                key: _coerce_assignment_value(key, value)
                for key, value in zip(assignment_header, row)
            }
        )

    if not assignment_rows:
        raise InvalidSolutionError(f"No assignments found in {path}")
    return metadata, assignment_rows, objective_value


def _read_locations(path: Path) -> dict[str, object]:
    rows = list(_read_comment_csv(path))
    if not rows:
        raise InvalidSolutionError(f"No locations found in {path}")

    depot: tuple[float, float] | None = None
    customers: dict[int, tuple[float, float]] = {}
    customer_weights_lbs: dict[int, float] = {}

    for row in rows:
        node_id = int(row[0])
        node_type = int(row[1])
        lat = float(row[2])
        lon = float(row[3])
        parcel_weight_lbs = float(row[5])
        if node_type == 0:
            depot = (lat, lon)
            continue
        customers[node_id] = (lat, lon)
        customer_weights_lbs[node_id] = parcel_weight_lbs

    if depot is None:
        raise InvalidSolutionError(f"No depot node found in {path}")
    return {
        "depot": depot,
        "customers": customers,
        "customer_weights_lbs": customer_weights_lbs,
    }


def _read_vehicle_spec(path: Path) -> dict[str, object]:
    rows = list(_read_comment_csv(path))
    if not rows:
        raise InvalidSolutionError(f"No vehicle data found in {path}")

    uav_rows = [row for row in rows if int(row[1]) == 2]
    if not uav_rows:
        raise InvalidSolutionError(f"No UAV rows found in {path}")
    truck_rows = [row for row in rows if int(row[1]) == 1 and int(row[0]) == 1]
    if not truck_rows:
        raise InvalidSolutionError(f"No truck row (vehicleID=1, vehicleType=1) found in {path}")

    drone_speed = float(uav_rows[0][3])
    takeoff_speed = float(uav_rows[0][2])
    landing_speed = float(uav_rows[0][4])
    yaw_rate_deg = float(uav_rows[0][5])
    cruise_altitude = float(uav_rows[0][6])
    drone_capacity_lbs = float(uav_rows[0][7])
    launch_time = float(uav_rows[0][8])
    recovery_time = float(uav_rows[0][9])
    truck_service_time = float(truck_rows[0][10])
    uav_ids = [int(row[0]) for row in uav_rows]
    return {
        "takeoff_speed": takeoff_speed,
        "drone_speed": drone_speed,
        "landing_speed": landing_speed,
        "yaw_rate_deg": yaw_rate_deg,
        "cruise_altitude": cruise_altitude,
        "drone_capacity_lbs": drone_capacity_lbs,
        "launch_time": launch_time,
        "recovery_time": recovery_time,
        "truck_service_time": truck_service_time,
        "uav_ids": uav_ids,
    }


def _read_truck_travel(path: Path) -> dict[tuple[int, int], tuple[float, float]]:
    rows = list(_read_comment_csv(path))
    if not rows:
        raise InvalidSolutionError(f"No truck travel data found in {path}")

    return {
        (int(row[0]), int(row[1])): (float(row[2]), float(row[3]))
        for row in rows
    }


def _build_truck_route(
    truck_rows: list[dict[str, object]],
    *,
    end_depot_node: int,
) -> tuple[list[int], dict[int, float]]:
    ordered_rows = sorted(
        truck_rows,
        key=lambda row: (float(row["startTime"]), float(row["endTime"]), int(row["vehicleID"])),
    )

    route: list[int] = []
    arrivals: dict[int, float] = {}
    for row in ordered_rows:
        start_node = _normalize_node_id(int(row["startNode"]), end_depot_node)
        end_node = _normalize_node_id(int(row["endNode"]), end_depot_node)
        if not route:
            route.append(start_node)
        if route[-1] != end_node:
            route.append(end_node)
            if end_node != 0:
                arrivals.setdefault(end_node, float(row["endTime"]))

    if not route:
        raise InvalidSolutionError("Could not reconstruct truck route from truck assignments")
    return route, arrivals


def _build_truck_timeline(
    truck_rows: list[dict[str, object]],
    *,
    end_depot_node: int,
    drone_id_map: dict[int, int],
) -> list[TruckTimingSegment]:
    ordered_rows = sorted(
        truck_rows,
        key=lambda row: (float(row["startTime"]), float(row["endTime"]), int(row["vehicleID"])),
    )

    timeline: list[TruckTimingSegment] = []
    for row in ordered_rows:
        end_time = float(row["endTime"])
        if end_time < 0.0:
            continue
        start_node = _normalize_node_id(int(row["startNode"]), end_depot_node)
        end_node = _normalize_node_id(int(row["endNode"]), end_depot_node)
        status = str(row["Status"]).strip()
        kind = "move" if status == "Traveling" else "dwell"
        description = str(row["Description"]).strip()
        drone_id = None
        drone_match = _TRUCK_UAV_REF_RE.search(description)
        if drone_match is not None:
            drone_id = drone_id_map.get(int(drone_match.group(1)))
        timeline.append(
            TruckTimingSegment(
                kind=kind,
                start_time=float(row["startTime"]),
                end_time=end_time,
                start_node=start_node,
                end_node=end_node,
                label=description,
                drone_id=drone_id,
            )
        )
    return timeline


def _build_sorties(
    assignment_rows: list[dict[str, object]],
    *,
    drone_id_map: dict[int, int],
    end_depot_node: int,
    solution_path: Path,
) -> list[dict[str, object]]:
    sorties: list[dict[str, object]] = []
    uav_rows_by_vehicle: dict[int, list[dict[str, object]]] = {}
    for row in assignment_rows:
        if row["vehicleType"] != "UAV":
            continue
        vehicle_id = int(row["vehicleID"])
        uav_rows_by_vehicle.setdefault(vehicle_id, []).append(row)

    for vehicle_id, rows in sorted(uav_rows_by_vehicle.items()):
        current_sortie: dict[str, object] | None = None
        for row in sorted(rows, key=lambda item: (float(item["startTime"]), float(item["endTime"]))):
            status = str(row["Status"])
            activity_type = str(row["activityType"])
            description = str(row["Description"])

            if status == "UAV Launch":
                if current_sortie is not None:
                    raise InvalidSolutionError(
                        f"Encountered a new UAV launch before recovery in {solution_path}"
                    )
                current_sortie = {
                    "launch": _normalize_node_id(int(row["startNode"]), end_depot_node),
                    "start_time": float(row["startTime"]),
                    "delivery": None,
                    "rendezvous": None,
                    "drone_id": drone_id_map[vehicle_id],
                    "rows": [row],
                }
                continue

            if current_sortie is None:
                continue

            current_sortie["rows"].append(row)

            if status == "Making Delivery" and "UAV customer" in description:
                current_sortie["delivery"] = _normalize_node_id(int(row["startNode"]), end_depot_node)
                continue

            if status == "UAV Recovery" or activity_type == "UAV Recovery":
                current_sortie["rendezvous"] = _normalize_node_id(int(row["endNode"]), end_depot_node)
                current_sortie["duration_s"] = float(row["endTime"]) - float(current_sortie["start_time"])
                if current_sortie["delivery"] is None:
                    raise InvalidSolutionError(
                        f"Could not identify UAV delivery node for vehicle {vehicle_id} in {solution_path}"
                    )
                current_sortie["leg_timings"] = _build_sortie_leg_timings(
                    current_sortie["rows"],
                    solution_path=solution_path,
                )
                sorties.append(current_sortie)
                current_sortie = None

        if current_sortie is not None:
            raise InvalidSolutionError(
                f"UAV vehicle {vehicle_id} did not finish its final sortie in {solution_path}"
            )

    return sorted(
        sorties,
        key=lambda sortie: (float(sortie["start_time"]), int(sortie["drone_id"]), int(sortie["delivery"])),
    )


def _infer_truck_speed(
    truck_route: list[int],
    truck_travel: dict[tuple[int, int], tuple[float, float]],
) -> float:
    total_distance_m = 0.0
    total_time_s = 0.0
    for from_node, to_node in zip(truck_route, truck_route[1:]):
        try:
            travel_time_s, distance_m = truck_travel[(int(from_node), int(to_node))]
        except KeyError as exc:
            raise InvalidSolutionError(
                f"Missing truck travel entry for segment {from_node}->{to_node}"
            ) from exc
        total_distance_m += distance_m
        total_time_s += travel_time_s
    if total_time_s <= 0.0:
        raise InvalidSolutionError("Truck route has zero total travel time")
    return total_distance_m / total_time_s


def _build_sortie_leg_timings(rows: list[dict[str, object]], *, solution_path: Path) -> list[LegTiming]:
    launch_row = rows[0]
    launch_takeoff_row = _find_row(
        rows,
        lambda row: str(row["activityType"]) == "UAV taking off or landing with a parcel"
        and "takeoff" in str(row["Description"]).lower(),
        "outbound takeoff",
        solution_path,
    )
    outbound_row = _find_row(
        rows,
        lambda row: str(row["activityType"]) == "UAV travels with parcel",
        "outbound parcel flight",
        solution_path,
    )
    delivery_service_row = _find_row(
        rows,
        lambda row: str(row["Status"]) == "Making Delivery" and "uav customer" in str(row["Description"]).lower(),
        "delivery service",
        solution_path,
    )
    return_takeoff_row = _find_row(
        rows,
        lambda row: str(row["activityType"]) == "UAV taking off or landing with no parcels"
        and "takeoff" in str(row["Description"]).lower(),
        "delivery takeoff",
        solution_path,
    )
    return_row = _find_row(
        rows,
        lambda row: str(row["activityType"]) == "UAV travels empty",
        "return empty flight",
        solution_path,
    )
    recovery_row = _find_row(
        rows,
        lambda row: str(row["Status"]) == "UAV Recovery" or str(row["activityType"]) == "UAV Recovery",
        "recovery",
        solution_path,
    )

    landing_with_parcel_rows = [
        row for row in rows
        if str(row["activityType"]) == "UAV taking off or landing with a parcel"
        and "land" in str(row["Description"]).lower()
    ]
    delivery_landing_row = landing_with_parcel_rows[-1] if landing_with_parcel_rows else outbound_takeoff_row

    post_return_rows = [
        row
        for row in rows
        if float(row["startTime"]) >= float(return_row["endTime"])
    ]
    collection_start_row = next(
        (
            row
            for row in post_return_rows
            if (
                str(row["activityType"]) == "UAV taking off or landing with no parcels"
                and "land" in str(row["Description"]).lower()
            )
            or str(row["Status"]) == "UAV Recovery"
            or str(row["activityType"]) == "UAV Recovery"
        ),
        recovery_row,
    )
    waiting_start_time = float(return_row["endTime"])
    collection_start_time = float(collection_start_row["startTime"])
    recovery_start_time = float(recovery_row["startTime"])
    recovery_end_time = float(recovery_row["endTime"])

    leg_timings = [
        LegTiming(
            name="launch_prep",
            start_time=float(launch_row["startTime"]),
            end_time=float(launch_row["endTime"]),
        ),
        LegTiming(
            name="launch_takeoff",
            start_time=float(launch_takeoff_row["startTime"]),
            end_time=float(launch_takeoff_row["endTime"]),
        ),
        LegTiming(
            name="outbound",
            start_time=float(outbound_row["startTime"]),
            end_time=float(outbound_row["endTime"]),
        ),
        LegTiming(
            name="delivery_land",
            start_time=float(delivery_landing_row["startTime"]),
            end_time=float(delivery_landing_row["endTime"]),
        ),
        LegTiming(
            name="delivery",
            start_time=float(delivery_service_row["startTime"]),
            end_time=float(delivery_service_row["endTime"]),
        ),
        LegTiming(
            name="delivery_takeoff",
            start_time=float(return_takeoff_row["startTime"]),
            end_time=float(return_takeoff_row["endTime"]),
        ),
        LegTiming(
            name="return",
            start_time=float(return_row["startTime"]),
            end_time=float(return_row["endTime"]),
        ),
    ]
    if collection_start_time > waiting_start_time:
        leg_timings.append(
            LegTiming(
                name="waiting",
                start_time=waiting_start_time,
                end_time=collection_start_time,
            )
        )
    leg_timings.append(
        LegTiming(
            name="recovery_land",
            start_time=collection_start_time,
            end_time=recovery_start_time,
        )
    )
    leg_timings.append(
        LegTiming(
            name="recovery",
            start_time=recovery_start_time,
            end_time=recovery_end_time,
        )
    )
    return leg_timings


def _find_row(
    rows: list[dict[str, object]],
    predicate,
    label: str,
    solution_path: Path,
) -> dict[str, object]:
    for row in rows:
        if predicate(row):
            return row
    raise InvalidSolutionError(f"Could not identify {label} row in {solution_path}")


def _eligible_customers(customer_weights_lbs: dict[int, float], drone_capacity_lbs: float) -> list[int]:
    return sorted(
        customer_id
        for customer_id, parcel_weight_lbs in customer_weights_lbs.items()
        if parcel_weight_lbs <= drone_capacity_lbs
    )


def _normalize_node_id(node_id: int, end_depot_node: int) -> int:
    return 0 if node_id == end_depot_node else int(node_id)


def _read_comment_csv(path: Path) -> Iterable[list[str]]:
    if not path.exists():
        raise InvalidSolutionError(f"Required mFSTSP file does not exist: {path}")
    with path.open(newline="") as handle:
        for row in csv.reader(handle, skipinitialspace=True):
            if not row:
                continue
            first = row[0].strip()
            if not first or first.startswith("%"):
                continue
            yield [value.strip() for value in row]


def _parse_csv_rows(lines: Iterable[str]) -> Iterable[list[str]]:
    for row in csv.reader(lines, skipinitialspace=True):
        if row:
            yield [value.strip() for value in row]


def _coerce_value(value: str) -> object:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        number = float(value)
    except ValueError:
        return value
    if number.is_integer():
        return int(number)
    return number


def _coerce_assignment_value(key: str, value: str) -> object:
    if key in {"vehicleID", "startNode", "endNode"}:
        return int(float(value))
    if key in {"startTime", "endTime"}:
        return float(value)
    return value
