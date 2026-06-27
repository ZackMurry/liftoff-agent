"""Parse and export Murray mFSTSP event logs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from ..models import LegTiming, Solution
from .mfstsp_adapter import _parse_solution_filename, _read_vehicle_spec


@dataclass(frozen=True)
class MfstspEventRow:
    vehicle_id: int
    vehicle_type: str
    activity_type: str
    start_time: float
    start_node: int
    end_time: float
    end_node: int
    description: str
    status: str
    raw_line: str | None = None

    def to_csv_line(self) -> str:
        if self.raw_line is not None:
            return self.raw_line
        return (
            ", ".join(
                [
                    str(int(self.vehicle_id)),
                    self.vehicle_type,
                    self.activity_type,
                    f"{float(self.start_time):.6f}",
                    str(int(self.start_node)),
                    f"{float(self.end_time):.6f}",
                    str(int(self.end_node)),
                    self.description,
                    self.status,
                ]
            )
            + " "
        )


@dataclass
class MfstspEventLog:
    metadata_header_line: str
    metadata_values_line: str
    objective_value: float
    assignment_header_line: str
    rows: list[MfstspEventRow]
    raw_bytes: bytes | None = None
    source_path: Path | None = None

    def to_csv(self, path: str | Path) -> None:
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if self.raw_bytes is not None:
            out_path.write_bytes(self.raw_bytes)
            return

        lines = [
            self.metadata_header_line,
            self.metadata_values_line,
            "",
            f"Objective Function Value: {float(self.objective_value):.6f} ",
            "",
            "Assignments: ",
            self.assignment_header_line,
            *(row.to_csv_line() for row in self.rows),
        ]
        out_path.write_bytes(("\r\n".join(lines) + "\r\n").encode("utf-8"))


def load_mfstsp_event_log(path: str | Path) -> MfstspEventLog:
    source_path = Path(path)
    raw_bytes = source_path.read_bytes()
    text = raw_bytes.decode("utf-8")
    lines = text.splitlines()
    if len(lines) < 7:
        raise ValueError(f"mFSTSP event log is too short: {source_path}")

    objective_line = next((line for line in lines if line.startswith("Objective Function Value:")), None)
    if objective_line is None:
        raise ValueError(f"Missing objective line in {source_path}")
    objective_value = float(objective_line.split(":", 1)[1].strip())

    try:
        assignments_index = next(index for index, line in enumerate(lines) if line.strip() == "Assignments:")
    except StopIteration as exc:
        raise ValueError(f"Missing Assignments section in {source_path}") from exc
    assignment_header_line = lines[assignments_index + 1]

    rows: list[MfstspEventRow] = []
    for raw_line in lines[assignments_index + 2 :]:
        if not raw_line.strip():
            continue
        values = next(csv.reader([raw_line], skipinitialspace=True))
        if len(values) != 9:
            raise ValueError(f"Malformed assignment row in {source_path}: {raw_line}")
        rows.append(
            MfstspEventRow(
                vehicle_id=int(float(values[0])),
                vehicle_type=values[1].strip(),
                activity_type=values[2].strip(),
                start_time=float(values[3]),
                start_node=int(float(values[4])),
                end_time=float(values[5]),
                end_node=int(float(values[6])),
                description=values[7].strip(),
                status=values[8].strip(),
                raw_line=raw_line,
            )
        )

    return MfstspEventLog(
        metadata_header_line=lines[0],
        metadata_values_line=lines[1],
        objective_value=objective_value,
        assignment_header_line=assignment_header_line,
        rows=rows,
        raw_bytes=raw_bytes,
        source_path=source_path,
    )


def build_actual_mfstsp_event_log(
    solution: Solution,
    run,
    planned_log: MfstspEventLog,
    *,
    source_solution_path: str | Path | None = None,
) -> MfstspEventLog:
    source_file = Path(source_solution_path) if source_solution_path is not None else planned_log.source_path
    if source_file is None:
        raise ValueError("source_solution_path is required when planned_log.source_path is unavailable")

    vehicle_file_id, _num_uavs, _solution_type = _parse_solution_filename(source_file.name)
    vehicles_file = source_file.parent.parent / f"tbl_vehicles_{vehicle_file_id}.csv"
    vehicle_spec = _read_vehicle_spec(vehicles_file)
    drone_vehicle_ids = sorted(int(vehicle_id) for vehicle_id in vehicle_spec["uav_ids"])
    drone_vehicle_id_by_index = {
        drone_index: drone_vehicle_ids[drone_index]
        for drone_index in range(min(len(drone_vehicle_ids), int(solution.num_drones)))
    }

    rows = _build_event_rows(
        solution=solution,
        run=run,
        planned_log=planned_log,
        drone_vehicle_id_by_index=drone_vehicle_id_by_index,
    )
    normalized_rows, _time_offset = _normalize_event_rows(rows)

    return MfstspEventLog(
        metadata_header_line=planned_log.metadata_header_line,
        metadata_values_line=planned_log.metadata_values_line,
        objective_value=_objective_value_from_rows(normalized_rows),
        assignment_header_line=planned_log.assignment_header_line,
        rows=normalized_rows,
        raw_bytes=None,
        source_path=source_file,
    )


def save_mfstsp_event_log(
    solution: Solution,
    run,
    source_solution_path: str | Path,
    path: str | Path,
) -> None:
    planned_log = load_mfstsp_event_log(source_solution_path)
    actual_log = build_actual_mfstsp_event_log(
        solution=solution,
        run=run,
        planned_log=planned_log,
        source_solution_path=source_solution_path,
    )
    actual_log.to_csv(path)


def _build_event_rows(
    *,
    solution: Solution,
    run,
    planned_log: MfstspEventLog,
    drone_vehicle_id_by_index: dict[int, int],
) -> list[MfstspEventRow]:
    schedule = solution.planned_schedule()
    drone_results = sorted(getattr(run, "drone_results", []), key=lambda result: int(result.drone_id))
    end_depot_node = max(solution.problem.customers, default=0) + 1
    final_visit_index = max(0, len(solution.truck_route) - 1)

    launch_events_by_visit: dict[int, list[dict[str, object]]] = {}
    recovery_events_by_visit: dict[int, list[dict[str, object]]] = {}
    uav_rows: list[MfstspEventRow] = []

    for drone_result in drone_results:
        drone_id = int(drone_result.drone_id)
        vehicle_id = drone_vehicle_id_by_index.get(drone_id, drone_id + 2)
        for sortie_result in sorted(
            getattr(drone_result, "sortie_results", []),
            key=lambda result: (float(result.start_time), int(result.sortie_index)),
        ):
            sortie_index = int(sortie_result.sortie_index)
            launch_visit = int(schedule["launch_occurrences"][sortie_index])
            rendezvous_visit = int(schedule["rendezvous_occurrences"][sortie_index])
            leg_map = _leg_map(getattr(sortie_result, "leg_timings", None))
            launch_prep = leg_map.get("launch_prep") or leg_map.get("launch")
            recovery = leg_map.get("recovery") or leg_map.get("collection")
            if launch_prep is not None:
                launch_events_by_visit.setdefault(launch_visit, []).append(
                    {
                        "vehicle_id": vehicle_id,
                        "start_time": float(launch_prep.start_time),
                        "end_time": float(launch_prep.end_time),
                    }
                )
            if recovery is not None:
                recovery_events_by_visit.setdefault(rendezvous_visit, []).append(
                    {
                        "vehicle_id": vehicle_id,
                        "start_time": float(recovery.start_time),
                        "end_time": float(recovery.end_time),
                    }
                )
            uav_rows.extend(
                _uav_rows_for_sortie(
                    solution=solution,
                    sortie_index=sortie_index,
                    sortie_result=sortie_result,
                    vehicle_id=vehicle_id,
                    final_visit_index=final_visit_index,
                    end_depot_node=end_depot_node,
                    launch_visit=launch_visit,
                    rendezvous_visit=rendezvous_visit,
                )
            )

    for events in launch_events_by_visit.values():
        events.sort(key=lambda event: (float(event["start_time"]), int(event["vehicle_id"])))
    for events in recovery_events_by_visit.values():
        events.sort(key=lambda event: (float(event["start_time"]), int(event["vehicle_id"])))

    truck_rows = _truck_rows(
        solution=solution,
        planned_log=planned_log,
        launch_events_by_visit=launch_events_by_visit,
        recovery_events_by_visit=recovery_events_by_visit,
        end_depot_node=end_depot_node,
    )

    return sorted(
        [*truck_rows, *uav_rows],
        key=lambda row: (
            int(row.vehicle_id),
            float(row.start_time),
            float(row.end_time),
            row.vehicle_type,
        ),
    )


def _normalize_event_rows(rows: list[MfstspEventRow]) -> tuple[list[MfstspEventRow], float]:
    non_terminal_start_times = [
        float(row.start_time)
        for row in rows
        if float(row.end_time) >= 0.0
    ]
    if not non_terminal_start_times:
        return rows, 0.0

    time_offset = min(non_terminal_start_times)
    if abs(time_offset) <= 1e-9:
        return rows, 0.0

    normalized_rows: list[MfstspEventRow] = []
    for row in rows:
        start_time = max(0.0, float(row.start_time) - time_offset)
        end_time = (
            -1.0
            if float(row.end_time) < 0.0
            else max(0.0, float(row.end_time) - time_offset)
        )
        description = row.description
        if row.status == "Vehicle Tasks Complete":
            description = f"At the Depot.  Total Time = {_format_hms(start_time)}"
        normalized_rows.append(
            MfstspEventRow(
                vehicle_id=row.vehicle_id,
                vehicle_type=row.vehicle_type,
                activity_type=row.activity_type,
                start_time=start_time,
                start_node=row.start_node,
                end_time=end_time,
                end_node=row.end_node,
                description=description,
                status=row.status,
            )
        )
    return normalized_rows, time_offset


def _truck_rows(
    *,
    solution: Solution,
    planned_log: MfstspEventLog,
    launch_events_by_visit: dict[int, list[dict[str, object]]],
    recovery_events_by_visit: dict[int, list[dict[str, object]]],
    end_depot_node: int,
) -> list[MfstspEventRow]:
    rows: list[MfstspEventRow] = []
    carrying_count = int(solution.num_drones)
    event_order_by_visit = _truck_event_order_by_visit(solution, planned_log)
    cursor = 0.0
    seen_truck_work = False

    for visit_index, node_id in enumerate(solution.truck_route):
        export_node = _export_node_id(node_id, visit_index=visit_index, end_depot_node=end_depot_node)
        launches = list(launch_events_by_visit.get(visit_index, []))
        recoveries = list(recovery_events_by_visit.get(visit_index, []))
        for event_kind in event_order_by_visit.get(visit_index, []):
            if event_kind == "delivery" and int(node_id) != 0:
                delivery_start = cursor
                delivery_end = delivery_start + float(solution.truck_service_time)
                rows.append(
                    MfstspEventRow(
                        vehicle_id=1,
                        vehicle_type="Truck",
                        activity_type=_truck_stationary_activity(carrying_count),
                        start_time=delivery_start,
                        start_node=export_node,
                        end_time=delivery_end,
                        end_node=export_node,
                        description=f"Dropping off package to Customer {int(node_id)}",
                        status="Making Delivery",
                    )
                )
                cursor = delivery_end
                seen_truck_work = True
                continue
            if event_kind == "launch" and launches:
                event = launches.pop(0)
                event_start = float(event["start_time"])
                event_end = float(event["end_time"])
                duration = max(0.0, event_end - event_start)
                if not seen_truck_work:
                    cursor = max(cursor, event_start)
                elif event_start > cursor + 1e-9:
                    rows.append(_truck_idle_row(cursor, event_start, export_node, carrying_count))
                    cursor = event_start
                actual_start = max(cursor, event_start)
                actual_end = actual_start + duration
                rows.append(
                    MfstspEventRow(
                        vehicle_id=1,
                        vehicle_type="Truck",
                        activity_type=_truck_stationary_activity(carrying_count),
                        start_time=actual_start,
                        start_node=export_node,
                        end_time=actual_end,
                        end_node=export_node,
                        description=f"Launching UAV {int(event['vehicle_id'])}",
                        status="UAV Launch",
                    )
                )
                cursor = actual_end
                carrying_count = max(0, carrying_count - 1)
                seen_truck_work = True
                continue
            if event_kind == "recovery" and recoveries:
                event = recoveries.pop(0)
                event_start = float(event["start_time"])
                event_end = float(event["end_time"])
                duration = max(0.0, event_end - event_start)
                if not seen_truck_work:
                    cursor = max(cursor, event_start)
                elif event_start > cursor + 1e-9:
                    rows.append(_truck_idle_row(cursor, event_start, export_node, carrying_count))
                    cursor = event_start
                actual_start = max(cursor, event_start)
                actual_end = actual_start + duration
                rows.append(
                    MfstspEventRow(
                        vehicle_id=1,
                        vehicle_type="Truck",
                        activity_type=_truck_stationary_activity(carrying_count),
                        start_time=actual_start,
                        start_node=export_node,
                        end_time=actual_end,
                        end_node=export_node,
                        description=f"Retrieving UAV {int(event['vehicle_id'])}",
                        status="UAV Recovery",
                    )
                )
                cursor = actual_end
                carrying_count += 1
                seen_truck_work = True

        if visit_index + 1 < len(solution.truck_route):
            next_node_id = solution.truck_route[visit_index + 1]
            travel_duration = solution._truck_travel_time_s(
                solution.truck_route[visit_index],
                next_node_id,
                leg_index=visit_index,
            )
            departure = cursor
            arrival = departure + travel_duration
            next_node = _export_node_id(
                next_node_id,
                visit_index=visit_index + 1,
                end_depot_node=end_depot_node,
            )
            rows.append(
                MfstspEventRow(
                    vehicle_id=1,
                    vehicle_type="Truck",
                    activity_type=_truck_travel_activity(carrying_count),
                    start_time=departure,
                    start_node=export_node,
                    end_time=arrival,
                    end_node=next_node,
                    description=f"Travel from node {export_node} to node {next_node}",
                    status="Traveling",
                )
            )
            cursor = arrival
            seen_truck_work = True
        else:
            rows.append(
                MfstspEventRow(
                    vehicle_id=1,
                    vehicle_type="Truck",
                    activity_type=_truck_stationary_activity(carrying_count),
                    start_time=cursor,
                    start_node=export_node,
                    end_time=-1.0,
                    end_node=export_node,
                    description=f"At the Depot.  Total Time = {_format_hms(cursor)}",
                    status="Vehicle Tasks Complete",
                )
            )
    return rows


def _objective_value_from_rows(rows: list[MfstspEventRow]) -> float:
    completion_times = [
        float(row.start_time if float(row.end_time) < 0.0 else row.end_time)
        for row in rows
    ]
    if not completion_times:
        return 0.0
    return max(completion_times)


def _truck_event_order_by_visit(solution: Solution, planned_log: MfstspEventLog) -> dict[int, list[str]]:
    order: dict[int, list[str]] = {}
    visit_index = 0
    for row in planned_log.rows:
        if row.vehicle_type != "Truck":
            continue
        if row.status == "Traveling":
            if visit_index + 1 < len(solution.truck_route):
                visit_index += 1
            continue
        normalized_status = row.status.lower()
        if normalized_status == "making delivery":
            order.setdefault(visit_index, []).append("delivery")
        elif normalized_status == "uav launch":
            order.setdefault(visit_index, []).append("launch")
        elif normalized_status == "uav recovery":
            order.setdefault(visit_index, []).append("recovery")
    return order


def _truck_idle_row(start_time: float, end_time: float, node_id: int, carrying_count: int) -> MfstspEventRow:
    seconds = int(round(max(0.0, float(end_time) - float(start_time))))
    return MfstspEventRow(
        vehicle_id=1,
        vehicle_type="Truck",
        activity_type=_truck_stationary_activity(carrying_count),
        start_time=float(start_time),
        start_node=int(node_id),
        end_time=float(end_time),
        end_node=int(node_id),
        description=f"Idle for {seconds:3d} seconds",
        status="Idle",
    )


def _uav_rows_for_sortie(
    *,
    solution: Solution,
    sortie_index: int,
    sortie_result,
    vehicle_id: int,
    final_visit_index: int,
    end_depot_node: int,
    launch_visit: int,
    rendezvous_visit: int,
) -> list[MfstspEventRow]:
    sortie = solution.sorties[sortie_index]
    leg_map = _leg_map(getattr(sortie_result, "leg_timings", None))
    launch_node = _export_node_id(solution.launch_node(sortie_index), visit_index=launch_visit, end_depot_node=end_depot_node)
    delivery_node = int(sortie.delivery)
    rendezvous_node = _export_node_id(sortie.rendezvous, visit_index=rendezvous_visit, end_depot_node=end_depot_node)
    at_depot_launch = int(solution.launch_node(sortie_index)) == 0 and int(launch_visit) == 0
    at_depot_recovery = int(sortie.rendezvous) == 0 and int(rendezvous_visit) == int(final_visit_index)

    rows: list[MfstspEventRow] = []
    launch_prep = leg_map.get("launch_prep") or leg_map.get("launch")
    if launch_prep is not None:
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV is stationary with a parcel",
                start_time=float(launch_prep.start_time),
                start_node=launch_node,
                end_time=float(launch_prep.end_time),
                end_node=launch_node,
                description="Prepare to launch from truck",
                status="UAV Launch",
            )
        )
    launch_takeoff = leg_map.get("launch_takeoff")
    if launch_takeoff is not None:
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV taking off or landing with a parcel",
                start_time=float(launch_takeoff.start_time),
                start_node=launch_node,
                end_time=float(launch_takeoff.end_time),
                end_node=launch_node,
                description=(
                    "Takeoff from Depot"
                    if at_depot_launch
                    else f"Takeoff from truck at Customer {int(solution.launch_node(sortie_index))}"
                ),
                status="Traveling",
            )
        )
    if "outbound" in leg_map:
        leg = leg_map["outbound"]
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV travels with parcel",
                start_time=float(leg.start_time),
                start_node=launch_node,
                end_time=float(leg.end_time),
                end_node=delivery_node,
                description=f"Fly to UAV customer {delivery_node}",
                status="Traveling",
            )
        )
    if "delivery_land" in leg_map:
        leg = leg_map["delivery_land"]
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV taking off or landing with a parcel",
                start_time=float(leg.start_time),
                start_node=delivery_node,
                end_time=float(leg.end_time),
                end_node=delivery_node,
                description=f"Land at UAV customer {delivery_node}",
                status="Traveling",
            )
        )
    if "delivery" in leg_map:
        leg = leg_map["delivery"]
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV is stationary with a parcel",
                start_time=float(leg.start_time),
                start_node=delivery_node,
                end_time=float(leg.end_time),
                end_node=delivery_node,
                description=f"Serving UAV customer {delivery_node}",
                status="Making Delivery",
            )
        )
    if "delivery_takeoff" in leg_map:
        leg = leg_map["delivery_takeoff"]
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV taking off or landing with no parcels",
                start_time=float(leg.start_time),
                start_node=delivery_node,
                end_time=float(leg.end_time),
                end_node=delivery_node,
                description=f"Takeoff from UAV customer {delivery_node}",
                status="Traveling",
            )
        )
    if "return" in leg_map:
        leg = leg_map["return"]
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV travels empty",
                start_time=float(leg.start_time),
                start_node=delivery_node,
                end_time=float(leg.end_time),
                end_node=rendezvous_node,
                description=(
                    "Fly to depot"
                    if at_depot_recovery
                    else f"Fly to truck at customer {int(sortie.rendezvous)}"
                ),
                status="Traveling",
            )
        )
    if "waiting" in leg_map:
        leg = leg_map["waiting"]
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV is stationary without a parcel",
                start_time=float(leg.start_time),
                start_node=rendezvous_node,
                end_time=float(leg.end_time),
                end_node=rendezvous_node,
                description=(
                    "Idle above depot location"
                    if at_depot_recovery
                    else f"Idle above rendezvous location (customer {int(sortie.rendezvous)})"
                ),
                status="Idle",
            )
        )
    recovery_land = leg_map.get("recovery_land")
    if recovery_land is not None:
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV taking off or landing with no parcels",
                start_time=float(recovery_land.start_time),
                start_node=rendezvous_node,
                end_time=float(recovery_land.end_time),
                end_node=rendezvous_node,
                description=(
                    "Land at depot"
                    if at_depot_recovery
                    else f"Land at truck rendezvous location (customer {int(sortie.rendezvous)})"
                ),
                status="Traveling",
            )
        )
    recovery = leg_map.get("recovery") or leg_map.get("collection")
    if recovery is not None:
        rows.append(
            MfstspEventRow(
                vehicle_id=vehicle_id,
                vehicle_type="UAV",
                activity_type="UAV is stationary without a parcel",
                start_time=float(recovery.start_time),
                start_node=rendezvous_node,
                end_time=float(recovery.end_time),
                end_node=rendezvous_node,
                description=(
                    "Recovered at depot"
                    if at_depot_recovery
                    else f"Recovered by truck at customer {int(sortie.rendezvous)}"
                ),
                status="UAV Recovery",
            )
        )
    return rows


def _leg_map(leg_timings) -> dict[str, LegTiming]:
    if not leg_timings:
        return {}
    return {
        str(leg.name if hasattr(leg, "name") else leg["name"]): (
            leg if isinstance(leg, LegTiming) else LegTiming(**leg)
        )
        for leg in leg_timings
    }


def _truck_stationary_activity(carrying_count: int) -> str:
    if carrying_count > 0:
        return "Truck is stationary with UAV(s) on board"
    return "Truck is stationary with no UAVs on board"


def _truck_travel_activity(carrying_count: int) -> str:
    if carrying_count > 0:
        return "Truck travels with UAV(s) on board"
    return "Truck travels with no UAVs on board"


def _export_node_id(node_id: int, *, visit_index: int, end_depot_node: int) -> int:
    if int(node_id) != 0:
        return int(node_id)
    if int(visit_index) == 0:
        return 0
    return int(end_depot_node)


def _format_hms(seconds: float) -> str:
    total_seconds = max(0, int(round(float(seconds))))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    remaining_seconds = total_seconds % 60
    return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
