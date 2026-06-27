"""Load/save dronevalkit Solution objects as JSON."""

import json

from ..models import (
    LegTiming,
    PlannedMetrics,
    Problem,
    Solution,
    Sortie,
    TruckTimingSegment,
    VehicleSpeeds,
)
from ..exceptions import InvalidSolutionError


def load_solution(path: str) -> Solution:
    """
    Load a Solution from a JSON file.

    Expected JSON structure::

        {
            "problem": {
                "depot": [lat, lon],
                "customers": {"1": [lat, lon], "2": [lat, lon], ...},
                "drone_eligible": [1, 2, 4]
            },
            "truck_route": [0, 3, 1, 0],
            "sorties": [
                {"launch": 0, "delivery": 2, "rendezvous": 3, "drone_id": 0},
                ...
            ],
            "num_drones": 2,
            "planned_metrics": {
                "drone_speed": 10.0,
                "makespan": 1680,
                "sortie_times": [180, 210],
                "sortie_energies": [12.5, 14.8]
            },
            "truck_speed": 8.33
        }
    """
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise InvalidSolutionError(f"Cannot read solution file '{path}': {e}") from e

    try:
        prob_data = data["problem"]
        depot = tuple(prob_data["depot"])
        customers = {int(k): tuple(v) for k, v in prob_data["customers"].items()}
        drone_eligible = [int(x) for x in prob_data["drone_eligible"]]
        problem = Problem(
            depot=depot,
            customers=customers,
            drone_eligible=drone_eligible,
        )
        num_drones = int(data.get("num_drones", 1))

        truck_route = [int(x) for x in data["truck_route"]]

        sorties = [
            Sortie(
                launch=(int(s["launch"]) if s.get("launch") is not None else None),
                delivery=int(s["delivery"]),
                rendezvous=int(s["rendezvous"]),
                drone_id=int(s.get("drone_id", 0)),
            )
            for s in data["sorties"]
        ]

        pm_data = data["planned_metrics"]
        sortie_times = [float(t) for t in pm_data["sortie_times"]]
        sortie_energies = None
        if pm_data.get("sortie_energies") is not None:
            sortie_energies = [float(e) for e in pm_data["sortie_energies"]]
        sortie_leg_times = None
        if pm_data.get("sortie_leg_times") is not None:
            sortie_leg_times = [
                [
                    LegTiming(
                        name=str(leg["name"]),
                        start_time=float(leg["start_time"]),
                        end_time=float(leg["end_time"]),
                    )
                    for leg in sortie_legs
                ]
                for sortie_legs in pm_data["sortie_leg_times"]
            ]
        vehicle_speeds = None
        if pm_data.get("vehicle_speeds") is not None:
            vehicle_speeds = VehicleSpeeds(
                takeoff=float(pm_data["vehicle_speeds"]["takeoff"]),
                cruise=float(pm_data["vehicle_speeds"]["cruise"]),
                landing=float(pm_data["vehicle_speeds"]["landing"]),
                yaw_rate_deg=pm_data["vehicle_speeds"].get("yaw_rate_deg"),
                launch_time=pm_data["vehicle_speeds"].get("launch_time"),
                recovery_time=pm_data["vehicle_speeds"].get("recovery_time"),
                cruise_altitude=pm_data["vehicle_speeds"].get("cruise_altitude"),
            )

        planned_metrics = PlannedMetrics(
            drone_speed=float(pm_data["drone_speed"]),
            makespan=float(pm_data["makespan"]),
            sortie_times=sortie_times,
            sortie_energies=sortie_energies,
            sortie_leg_times=sortie_leg_times,
            vehicle_speeds=vehicle_speeds,
        )

        truck_speed = float(data.get("truck_speed", 8.33))
        truck_service_time = float(data.get("truck_service_time", 0.0))
        truck_leg_travel_times = None
        if data.get("truck_leg_travel_times") is not None:
            truck_leg_travel_times = [float(value) for value in data["truck_leg_travel_times"]]
        planned_truck_timeline = None
        if data.get("planned_truck_timeline") is not None:
            planned_truck_timeline = [
                TruckTimingSegment(
                    kind=str(segment["kind"]),
                    start_time=float(segment["start_time"]),
                    end_time=float(segment["end_time"]),
                    start_node=int(segment["start_node"]),
                    end_node=int(segment["end_node"]),
                    label=str(segment["label"]),
                    drone_id=(
                        int(segment["drone_id"])
                        if segment.get("drone_id") is not None
                        else None
                    ),
                )
                for segment in data["planned_truck_timeline"]
            ]

        truck_arrival_times = None
        if data.get("truck_arrival_times") is not None:
            truck_arrival_times = {int(k): float(v) for k, v in data["truck_arrival_times"].items()}

        return Solution(
            problem=problem,
            truck_route=truck_route,
            sorties=sorties,
            planned_metrics=planned_metrics,
            num_drones=num_drones,
            truck_speed=truck_speed,
            truck_service_time=truck_service_time,
            truck_leg_travel_times=truck_leg_travel_times,
            planned_truck_timeline=planned_truck_timeline,
            truck_arrival_times=truck_arrival_times,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise InvalidSolutionError(f"Malformed solution file '{path}': {e}") from e


def save_solution(solution: Solution, path: str) -> None:
    """Save a Solution to a JSON file."""
    data = {
        "problem": {
            "depot": list(solution.problem.depot),
            "customers": {str(k): list(v) for k, v in solution.problem.customers.items()},
            "drone_eligible": solution.problem.drone_eligible,
        },
        "truck_route": solution.truck_route,
        "sorties": [
            {
                "launch": s.launch,
                "delivery": s.delivery,
                "rendezvous": s.rendezvous,
                "drone_id": s.drone_id,
            }
            for s in solution.sorties
        ],
        "num_drones": solution.num_drones,
        "planned_metrics": {
            "drone_speed": solution.planned_metrics.drone_speed,
            "makespan": solution.planned_metrics.makespan,
            "sortie_times": solution.planned_metrics.sortie_times,
            "sortie_energies": solution.planned_metrics.sortie_energies,
            "sortie_leg_times": (
                [
                    [
                        {
                            "name": leg.name,
                            "start_time": leg.start_time,
                            "end_time": leg.end_time,
                        }
                        for leg in sortie_legs
                    ]
                    for sortie_legs in solution.planned_metrics.sortie_leg_times
                ]
                if solution.planned_metrics.sortie_leg_times is not None
                else None
            ),
            "vehicle_speeds": (
                {
                    "takeoff": solution.planned_metrics.vehicle_speeds.takeoff,
                    "cruise": solution.planned_metrics.vehicle_speeds.cruise,
                    "landing": solution.planned_metrics.vehicle_speeds.landing,
                    "yaw_rate_deg": solution.planned_metrics.vehicle_speeds.yaw_rate_deg,
                    "launch_time": solution.planned_metrics.vehicle_speeds.launch_time,
                    "recovery_time": solution.planned_metrics.vehicle_speeds.recovery_time,
                    "cruise_altitude": solution.planned_metrics.vehicle_speeds.cruise_altitude,
                }
                if solution.planned_metrics.vehicle_speeds is not None
                else None
            ),
        },
        "truck_speed": solution.truck_speed,
        "truck_service_time": solution.truck_service_time,
        "truck_leg_travel_times": solution.truck_leg_travel_times,
        "planned_truck_timeline": (
            [
                {
                    "kind": (segment.kind if hasattr(segment, "kind") else segment["kind"]),
                    "start_time": (
                        segment.start_time if hasattr(segment, "start_time") else segment["start_time"]
                    ),
                    "end_time": (
                        segment.end_time if hasattr(segment, "end_time") else segment["end_time"]
                    ),
                    "start_node": (
                        segment.start_node if hasattr(segment, "start_node") else segment["start_node"]
                    ),
                    "end_node": (
                        segment.end_node if hasattr(segment, "end_node") else segment["end_node"]
                    ),
                    "label": (segment.label if hasattr(segment, "label") else segment["label"]),
                    "drone_id": (
                        segment.drone_id if hasattr(segment, "drone_id") else segment.get("drone_id")
                    ),
                }
                for segment in solution.planned_truck_timeline
            ]
            if solution.planned_truck_timeline is not None
            else None
        ),
        "truck_arrival_times": (
            {str(k): v for k, v in solution.truck_arrival_times.items()}
            if solution.truck_arrival_times is not None
            else None
        ),
    }

    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        raise InvalidSolutionError(f"Cannot write solution file '{path}': {e}") from e
