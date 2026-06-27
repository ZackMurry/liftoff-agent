"""Import dronevalkit solutions from VeRoViz-style dataframes."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from ..exceptions import InvalidSolutionError
from ..geo import haversine_distance
from ..models import PlannedMetrics, Problem, Solution, Sortie


def from_veroviz(
    assignments_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    *,
    depot_id: int = 0,
    truck_object_id: object | None = None,
    drone_object_ids: Sequence[object] | None = None,
    drone_speed: float | None = 10.0,
    truck_speed: float | None = 8.33,
    num_drones: int | None = None,
    drone_eligible: Sequence[int] | None = None,
    makespan: float | None = None,
    sortie_energies: Sequence[float] | None = None,
    coordinate_tolerance_m: float = 5.0,
) -> Solution:
    """Convert VeRoViz-style assignments and nodes dataframes into a Solution.

    Defaults are intentionally pragmatic:
    - truck/drone vehicles are inferred from ``objectID``/``modelFile`` when not provided
    - ``drone_speed`` and ``truck_speed`` can be overridden directly; when set to
      ``None`` they are inferred from the assignments
    - ``depot_id`` defaults to ``0``
    - ``drone_eligible`` defaults to all non-depot customers
    """

    if assignments_df is None or nodes_df is None:
        raise InvalidSolutionError("assignments_df and nodes_df are required")
    if len(assignments_df) == 0:
        raise InvalidSolutionError("assignments_df is empty")
    if len(nodes_df) == 0:
        raise InvalidSolutionError("nodes_df is empty")
    if coordinate_tolerance_m <= 0.0:
        raise InvalidSolutionError("coordinate_tolerance_m must be positive")

    node_id_col = _find_column(nodes_df, ["id", "nodeID", "nodeId"])
    node_lat_col = _find_column(nodes_df, ["lat", "latitude", "latDeg"])
    node_lon_col = _find_column(nodes_df, ["lon", "lng", "longitude", "lonDeg"])

    obj_col = _find_column(assignments_df, ["objectID", "objectId", "object_id"])
    model_col = _find_optional_column(assignments_df, ["modelFile", "model", "vehicleType"])
    start_lat_col = _find_column(assignments_df, ["startLat", "startLatitude"])
    start_lon_col = _find_column(assignments_df, ["startLon", "startLng", "startLongitude"])
    end_lat_col = _find_column(assignments_df, ["endLat", "endLatitude"])
    end_lon_col = _find_column(assignments_df, ["endLon", "endLng", "endLongitude"])
    start_time_col = _find_column(assignments_df, ["startTimeSec", "startTime"])
    end_time_col = _find_column(assignments_df, ["endTimeSec", "endTime"])

    nodes = nodes_df.copy()
    nodes[node_id_col] = nodes[node_id_col].astype(int)
    node_positions = {
        int(row[node_id_col]): (float(row[node_lat_col]), float(row[node_lon_col]))
        for _, row in nodes.iterrows()
    }
    if depot_id not in node_positions:
        raise InvalidSolutionError(f"depot_id {depot_id} not found in nodes_df")

    assignments = assignments_df.copy()
    assignments[start_time_col] = assignments[start_time_col].astype(float)
    assignments[end_time_col] = assignments[end_time_col].astype(float)
    assignments = assignments.sort_values([start_time_col, end_time_col]).reset_index(drop=True)

    inferred_truck_object_id, inferred_drone_object_ids = _infer_vehicle_ids(
        assignments,
        obj_col=obj_col,
        model_col=model_col,
    )
    truck_object_id = inferred_truck_object_id if truck_object_id is None else truck_object_id
    if truck_object_id is None:
        raise InvalidSolutionError("Could not infer truck vehicle from assignments_df")
    if drone_object_ids is None:
        drone_object_ids = inferred_drone_object_ids
    drone_object_ids = list(drone_object_ids)
    if not drone_object_ids:
        raise InvalidSolutionError("Could not infer any drone vehicles from assignments_df")

    truck_rows = assignments[assignments[obj_col] == truck_object_id].copy()
    if truck_rows.empty:
        raise InvalidSolutionError(f"No truck assignments found for objectID={truck_object_id!r}")

    truck_route, inferred_arrivals, truck_leg_travel_times = _build_truck_route(
        truck_rows,
        node_positions=node_positions,
        depot_id=depot_id,
        start_lat_col=start_lat_col,
        start_lon_col=start_lon_col,
        end_lat_col=end_lat_col,
        end_lon_col=end_lon_col,
        start_time_col=start_time_col,
        end_time_col=end_time_col,
        coordinate_tolerance_m=coordinate_tolerance_m,
    )

    customers = {
        node_id: gps
        for node_id, gps in node_positions.items()
        if node_id != depot_id
    }
    if drone_eligible is None:
        drone_eligible = sorted(customers)
    else:
        drone_eligible = [int(node_id) for node_id in drone_eligible]

    sorties: list[Sortie] = []
    sortie_times: list[float] = []

    drone_speed_segments: list[float] = []
    truck_speed_segments: list[float] = []
    drone_id_map = {
        object_id: drone_index
        for drone_index, object_id in enumerate(drone_object_ids)
    }

    truck_route_nodes = set(truck_route)
    for object_id in drone_object_ids:
        drone_rows = assignments[assignments[obj_col] == object_id].copy()
        if drone_rows.empty:
            continue

        rows_as_segments: list[dict[str, object]] = []
        for _, row in drone_rows.iterrows():
            start_node = _nearest_node_id(
                float(row[start_lat_col]),
                float(row[start_lon_col]),
                node_positions,
                coordinate_tolerance_m=coordinate_tolerance_m,
            )
            end_node = _nearest_node_id(
                float(row[end_lat_col]),
                float(row[end_lon_col]),
                node_positions,
                coordinate_tolerance_m=coordinate_tolerance_m,
            )
            start_time = float(row[start_time_col])
            end_time = float(row[end_time_col])
            rows_as_segments.append(
                {
                    "start_node": start_node,
                    "end_node": end_node,
                    "start_time": start_time,
                    "end_time": end_time,
                }
            )
            drone_speed_segments.append(
                _segment_speed_m_s(
                    node_positions[start_node],
                    node_positions[end_node],
                    start_time,
                    end_time,
                )
            )

        sortie_chains = _group_drone_segments_into_sorties(
            rows_as_segments,
            truck_route_nodes=truck_route_nodes,
            depot_id=depot_id,
        )
        for chain in sortie_chains:
            sorties.append(
                Sortie(
                    launch=int(chain["launch"]),
                    delivery=int(chain["delivery"]),
                    rendezvous=int(chain["rendezvous"]),
                    drone_id=drone_id_map[object_id],
                )
            )
            sortie_times.append(float(chain["end_time"] - chain["start_time"]))

    if not sorties:
        raise InvalidSolutionError("No drone sorties could be reconstructed from assignments_df")

    for _, row in truck_rows.iterrows():
        start_node = _nearest_node_id(
            float(row[start_lat_col]),
            float(row[start_lon_col]),
            node_positions,
            coordinate_tolerance_m=coordinate_tolerance_m,
        )
        end_node = _nearest_node_id(
            float(row[end_lat_col]),
            float(row[end_lon_col]),
            node_positions,
            coordinate_tolerance_m=coordinate_tolerance_m,
        )
        truck_speed_segments.append(
            _segment_speed_m_s(
                node_positions[start_node],
                node_positions[end_node],
                float(row[start_time_col]),
                float(row[end_time_col]),
            )
        )

    if drone_speed is None:
        drone_speed = _mean_positive(drone_speed_segments, default=10.0)
    if truck_speed is None:
        truck_speed = _mean_positive(truck_speed_segments, default=8.33)
    if makespan is None:
        makespan = float(assignments[end_time_col].max())
    if num_drones is None:
        num_drones = max(len(drone_object_ids), max(sortie.drone_id for sortie in sorties) + 1)

    planned_metrics = PlannedMetrics(
        drone_speed=float(drone_speed),
        makespan=float(makespan),
        sortie_times=sortie_times,
        sortie_energies=([float(v) for v in sortie_energies] if sortie_energies is not None else None),
    )

    return Solution(
        problem=Problem(
            depot=node_positions[depot_id],
            customers=customers,
            drone_eligible=list(drone_eligible),
        ),
        truck_route=truck_route,
        sorties=sorties,
        planned_metrics=planned_metrics,
        num_drones=int(num_drones),
        truck_speed=float(truck_speed),
        truck_leg_travel_times=truck_leg_travel_times,
        truck_arrival_times=inferred_arrivals,
    )


def _find_column(df: pd.DataFrame, candidates: Sequence[str]) -> str:
    for name in candidates:
        if name in df.columns:
            return name
    raise InvalidSolutionError(
        "Missing required dataframe column; tried: " + ", ".join(candidates)
    )


def _find_optional_column(df: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    for name in candidates:
        if name in df.columns:
            return name
    return None


def _infer_vehicle_ids(
    assignments: pd.DataFrame,
    *,
    obj_col: str,
    model_col: str | None,
) -> tuple[object | None, list[object]]:
    labels: dict[object, str] = {}
    for object_id, group in assignments.groupby(obj_col):
        text_parts = [str(object_id).lower()]
        if model_col is not None:
            text_parts.extend(str(v).lower() for v in group[model_col].dropna().unique())
        labels[object_id] = " ".join(text_parts)

    truck_candidates = [
        object_id for object_id, label in labels.items()
        if "truck" in label or "car" in label or "ground" in label
    ]
    drone_candidates = [
        object_id for object_id, label in labels.items()
        if "drone" in label or "uav" in label or "quad" in label or "copter" in label
    ]

    if not truck_candidates:
        sorted_ids = sorted(labels, key=lambda object_id: str(object_id))
        truck_candidates = sorted_ids[:1]
    if not drone_candidates:
        drone_candidates = [
            object_id
            for object_id in sorted(labels, key=lambda value: str(value))
            if object_id not in truck_candidates
        ]

    truck_object_id = truck_candidates[0] if truck_candidates else None
    drone_object_ids = [object_id for object_id in drone_candidates if object_id != truck_object_id]
    return truck_object_id, drone_object_ids


def _build_truck_route(
    truck_rows: pd.DataFrame,
    *,
    node_positions: dict[int, tuple[float, float]],
    depot_id: int,
    start_lat_col: str,
    start_lon_col: str,
    end_lat_col: str,
    end_lon_col: str,
    start_time_col: str,
    end_time_col: str,
    coordinate_tolerance_m: float,
) -> tuple[list[int], dict[int, float], list[float]]:
    route: list[int] = []
    arrivals: dict[int, float] = {}
    leg_travel_times: list[float] = []

    for row_index, (_, row) in enumerate(truck_rows.iterrows()):
        start_node = _nearest_node_id(
            float(row[start_lat_col]),
            float(row[start_lon_col]),
            node_positions,
            coordinate_tolerance_m=coordinate_tolerance_m,
        )
        end_node = _nearest_node_id(
            float(row[end_lat_col]),
            float(row[end_lon_col]),
            node_positions,
            coordinate_tolerance_m=coordinate_tolerance_m,
        )
        leg_time = max(0.0, float(row[end_time_col]) - float(row[start_time_col]))
        if row_index == 0:
            route.append(start_node)
            arrivals.setdefault(start_node, 0.0)
        if route[-1] != end_node:
            route.append(end_node)
            leg_travel_times.append(leg_time)
        arrivals.setdefault(end_node, float(row[end_time_col]))

    if not route:
        raise InvalidSolutionError("Could not reconstruct truck route from assignments_df")
    if route[0] != depot_id:
        route.insert(0, depot_id)
        leg_travel_times.insert(0, 0.0)
        arrivals.setdefault(depot_id, 0.0)
    return route, arrivals, leg_travel_times


def _group_drone_segments_into_sorties(
    segments: list[dict[str, object]],
    *,
    truck_route_nodes: set[int],
    depot_id: int,
) -> list[dict[str, object]]:
    chains: list[dict[str, object]] = []
    current_chain: list[dict[str, object]] = []

    for segment in segments:
        start_node = int(segment["start_node"])
        end_node = int(segment["end_node"])

        if not current_chain:
            current_chain.append(segment)
        else:
            previous_end = int(current_chain[-1]["end_node"])
            if start_node != previous_end:
                raise InvalidSolutionError(
                    "Drone assignments are not contiguous enough to reconstruct sorties"
                )
            current_chain.append(segment)

        if end_node in truck_route_nodes and len(current_chain) >= 2:
            launch_node = int(current_chain[0]["start_node"])
            rendezvous_node = end_node
            nontruck_nodes = [
                int(chain_segment["end_node"])
                for chain_segment in current_chain
                if int(chain_segment["end_node"]) not in truck_route_nodes
            ]
            if not nontruck_nodes:
                raise InvalidSolutionError(
                    "Could not identify a delivery node in a drone sortie chain"
                )
            chains.append(
                {
                    "launch": launch_node,
                    "delivery": nontruck_nodes[0],
                    "rendezvous": rendezvous_node,
                    "start_time": float(current_chain[0]["start_time"]),
                    "end_time": float(current_chain[-1]["end_time"]),
                }
            )
            current_chain = []

    if current_chain:
        raise InvalidSolutionError(
            "Drone assignments ended before returning to a truck rendezvous node"
        )
    if not chains and segments:
        raise InvalidSolutionError("Could not reconstruct any drone sortie chains")
    return chains


def _nearest_node_id(
    lat: float,
    lon: float,
    node_positions: dict[int, tuple[float, float]],
    *,
    coordinate_tolerance_m: float,
) -> int:
    best_node_id: int | None = None
    best_distance = float("inf")
    for node_id, (node_lat, node_lon) in node_positions.items():
        distance = haversine_distance(lat, lon, node_lat, node_lon)
        if distance < best_distance:
            best_distance = distance
            best_node_id = node_id
    if best_node_id is None or best_distance > coordinate_tolerance_m:
        raise InvalidSolutionError(
            f"Could not match coordinate ({lat}, {lon}) to a node within {coordinate_tolerance_m}m"
        )
    return best_node_id


def _segment_speed_m_s(
    start: tuple[float, float],
    end: tuple[float, float],
    start_time_s: float,
    end_time_s: float,
) -> float:
    dt = float(end_time_s) - float(start_time_s)
    if dt <= 0.0:
        return 0.0
    return haversine_distance(start[0], start[1], end[0], end[1]) / dt


def _mean_positive(values: Sequence[float], default: float) -> float:
    positives = [float(value) for value in values if float(value) > 0.0]
    if not positives:
        return float(default)
    return sum(positives) / len(positives)
