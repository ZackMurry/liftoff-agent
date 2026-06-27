"""Data models for dronevalkit: Problem, Solution, Sortie, PlannedMetrics."""

from dataclasses import dataclass
from typing import Optional

from .geo import haversine_distance


@dataclass
class Problem:
    """An FSTSP/TSP-D problem instance."""
    depot: tuple[float, float]                    # (lat, lon)
    customers: dict[int, tuple[float, float]]     # id -> (lat, lon)
    drone_eligible: list[int]                     # customer IDs the drone can serve


@dataclass
class Sortie:
    """A single drone sortie: launch -> deliver -> rendezvous."""
    delivery: int     # node ID where drone delivers
    rendezvous: int   # node ID where drone returns to truck
    launch: Optional[int] = None  # node ID where drone launches from truck
    drone_id: int = 0 # which physical drone flies this sortie

    def __post_init__(self) -> None:
        if self.launch is not None:
            self.launch = int(self.launch)
            if self.launch < 0:
                raise ValueError("sortie.launch must be non-negative when provided")
        self.delivery = int(self.delivery)
        self.rendezvous = int(self.rendezvous)
        self.drone_id = int(self.drone_id)
        if self.drone_id < 0:
            raise ValueError("sortie.drone_id must be non-negative")


@dataclass
class LegTiming:
    """Timing for one named leg/phase within a sortie."""

    name: str
    start_time: float
    end_time: float

    def __post_init__(self) -> None:
        self.name = str(self.name)
        self.start_time = float(self.start_time)
        self.end_time = float(self.end_time)
        if not self.name:
            raise ValueError("leg_timing.name must not be empty")
        if self.end_time < self.start_time:
            raise ValueError("leg_timing.end_time must be >= start_time")

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


@dataclass
class TruckTimingSegment:
    """Timing for one planned truck phase within the route timeline."""

    kind: str
    start_time: float
    end_time: float
    start_node: int
    end_node: int
    label: str
    drone_id: Optional[int] = None

    def __post_init__(self) -> None:
        self.kind = str(self.kind)
        self.start_time = float(self.start_time)
        self.end_time = float(self.end_time)
        self.start_node = int(self.start_node)
        self.end_node = int(self.end_node)
        self.label = str(self.label)
        if self.drone_id is not None:
            self.drone_id = int(self.drone_id)
        if self.kind not in {"move", "dwell"}:
            raise ValueError("truck_timing_segment.kind must be 'move' or 'dwell'")
        if self.end_time < self.start_time:
            raise ValueError("truck_timing_segment.end_time must be >= start_time")


@dataclass
class VehicleSpeeds:
    """Vehicle profile used by the OR model / benchmark instance."""

    takeoff: float
    cruise: float
    landing: float
    yaw_rate_deg: Optional[float] = None
    launch_time: Optional[float] = None
    recovery_time: Optional[float] = None
    cruise_altitude: Optional[float] = None

    def __post_init__(self) -> None:
        self.takeoff = float(self.takeoff)
        self.cruise = float(self.cruise)
        self.landing = float(self.landing)
        if self.takeoff <= 0.0 or self.cruise <= 0.0 or self.landing <= 0.0:
            raise ValueError("vehicle speeds must be positive")
        if self.yaw_rate_deg is not None:
            self.yaw_rate_deg = float(self.yaw_rate_deg)
            if self.yaw_rate_deg <= 0.0:
                raise ValueError("vehicle_speeds.yaw_rate_deg must be positive when provided")
        if self.launch_time is not None:
            self.launch_time = float(self.launch_time)
            if self.launch_time < 0.0:
                raise ValueError("vehicle_speeds.launch_time must be non-negative when provided")
        if self.recovery_time is not None:
            self.recovery_time = float(self.recovery_time)
            if self.recovery_time < 0.0:
                raise ValueError("vehicle_speeds.recovery_time must be non-negative when provided")
        if self.cruise_altitude is not None:
            self.cruise_altitude = float(self.cruise_altitude)
            if self.cruise_altitude <= 0.0:
                raise ValueError("vehicle_speeds.cruise_altitude must be positive when provided")


@dataclass
class PlannedMetrics:
    """What the OR model predicted. Used for comparison."""
    drone_speed: float                              # m/s assumed constant
    makespan: float                                 # total mission time (seconds)
    sortie_times: list[float]                       # planned flight time per sortie (seconds)
    sortie_energies: Optional[list[float]] = None   # planned energy per sortie (% battery)
    sortie_leg_times: Optional[list[list[LegTiming]]] = None  # planned per-leg timing per sortie
    vehicle_speeds: Optional[VehicleSpeeds] = None  # optional takeoff/cruise/landing profile

    def __post_init__(self) -> None:
        self.sortie_times = [float(t) for t in self.sortie_times]
        if self.sortie_energies is not None:
            self.sortie_energies = [float(e) for e in self.sortie_energies]
            if len(self.sortie_energies) != len(self.sortie_times):
                raise ValueError("sortie_times and sortie_energies must align")
        if self.sortie_leg_times is not None:
            coerced_sortie_leg_times: list[list[LegTiming]] = []
            for sortie_leg_times in self.sortie_leg_times:
                coerced_sortie_leg_times.append(
                    [
                        leg_timing
                        if isinstance(leg_timing, LegTiming)
                        else LegTiming(**leg_timing)
                        for leg_timing in sortie_leg_times
                    ]
                )
            self.sortie_leg_times = coerced_sortie_leg_times
            if len(self.sortie_leg_times) != len(self.sortie_times):
                raise ValueError("sortie_leg_times must align with sortie_times")
        if self.vehicle_speeds is not None and not isinstance(self.vehicle_speeds, VehicleSpeeds):
            self.vehicle_speeds = VehicleSpeeds(**self.vehicle_speeds)


@dataclass
class Solution:
    """A complete FSTSP/TSP-D solution to validate."""
    problem: Problem
    truck_route: list[int]                          # ordered node IDs
    sorties: list[Sortie]
    planned_metrics: PlannedMetrics
    num_drones: int = 1
    truck_speed: float = 8.33                       # m/s (~30 km/h), for analytical truck model
    truck_service_time: float = 0.0                # per-customer truck delivery dwell time (s)
    truck_leg_travel_times: Optional[list[float]] = None  # per-route-leg nominal truck travel times (s)
    planned_truck_timeline: Optional[list[TruckTimingSegment]] = None  # explicit planned truck phases
    truck_arrival_times: Optional[dict[int, float]] = None  # override: node_id -> arrival time (s)

    def __post_init__(self) -> None:
        self.num_drones = int(self.num_drones)
        if self.num_drones < 1:
            raise ValueError("solution.num_drones must be at least 1")
        self.sorties = list(self.sorties)
        if len(self.planned_metrics.sortie_times) != len(self.sorties):
            raise ValueError("planned_metrics.sortie_times must align with sorties")
        if self.planned_metrics.sortie_energies is not None:
            if len(self.planned_metrics.sortie_energies) != len(self.sorties):
                raise ValueError("planned_metrics.sortie_energies must align with sorties")
        self.truck_route = [int(node_id) for node_id in self.truck_route]
        self.truck_speed = float(self.truck_speed)
        if self.truck_speed <= 0.0:
            raise ValueError("solution.truck_speed must be positive")
        self.truck_service_time = float(self.truck_service_time)
        if self.truck_service_time < 0.0:
            raise ValueError("solution.truck_service_time must be non-negative")
        if self.truck_leg_travel_times is not None:
            self.truck_leg_travel_times = [float(t) for t in self.truck_leg_travel_times]
            if len(self.truck_leg_travel_times) != max(0, len(self.truck_route) - 1):
                raise ValueError("truck_leg_travel_times must align with truck_route legs")
            if any(t < 0.0 for t in self.truck_leg_travel_times):
                raise ValueError("truck_leg_travel_times must be non-negative")
        if self.planned_truck_timeline is not None:
            self.planned_truck_timeline = [
                segment
                if isinstance(segment, TruckTimingSegment)
                else TruckTimingSegment(**segment)
                for segment in self.planned_truck_timeline
            ]
        if self.truck_arrival_times is not None:
            self.truck_arrival_times = {
                int(node_id): float(arrival_time)
                for node_id, arrival_time in self.truck_arrival_times.items()
            }
        # Infer omitted launches per drone from prior rendezvous (or depot for first sortie).
        last_rendezvous_by_drone = {drone_id: 0 for drone_id in range(self.num_drones)}
        for sortie in self.sorties:
            if sortie.drone_id >= self.num_drones:
                raise ValueError("sortie.drone_id exceeds solution.num_drones")
            if sortie.launch is None:
                sortie.launch = last_rendezvous_by_drone[sortie.drone_id]
            last_rendezvous_by_drone[sortie.drone_id] = sortie.rendezvous
        self._compute_planned_schedule()

    def launch_node(self, sortie_index: int) -> int:
        """Return the inferred launch node for a sortie.

        First sortie for a drone launches at depot (node 0).
        Subsequent sorties launch from that drone's previous rendezvous.
        """
        if sortie_index < 0 or sortie_index >= len(self.sorties):
            raise IndexError("sortie_index out of range")
        return int(self.sorties[sortie_index].launch)

    def planned_schedule(self) -> dict[str, object]:
        """Return the analytically derived truck/drone schedule for this plan."""
        return self._compute_planned_schedule()

    def _compute_planned_schedule(self) -> dict[str, object]:
        if not self.truck_route:
            raise ValueError("solution.truck_route must not be empty")

        occurrences_by_node: dict[int, list[int]] = {}
        for visit_index, node_id in enumerate(self.truck_route):
            occurrences_by_node.setdefault(node_id, []).append(visit_index)

        duplicate_customers = sorted(
            node_id
            for node_id, visits in occurrences_by_node.items()
            if node_id != 0 and len(visits) > 1
        )
        if duplicate_customers:
            raise ValueError(
                "truck_route must not visit the same customer more than once: "
                + ", ".join(str(node_id) for node_id in duplicate_customers)
            )

        sorties_by_drone: dict[int, list[int]] = {drone_id: [] for drone_id in range(self.num_drones)}
        for sortie_index, sortie in enumerate(self.sorties):
            sorties_by_drone[sortie.drone_id].append(sortie_index)

        launch_occurrence_by_sortie: dict[int, int] = {}
        rendezvous_occurrence_by_sortie: dict[int, int] = {}
        drone_local_order: dict[int, int] = {}

        def _first_occurrence(node_id: int, min_visit_index: int) -> int:
            visits = occurrences_by_node.get(node_id, [])
            for visit_index in visits:
                if visit_index >= min_visit_index:
                    return visit_index
            raise ValueError(
                f"Deadlock or invalid route: truck never reaches node {node_id} "
                f"after visit index {min_visit_index}"
            )

        for drone_id, sortie_indices in sorties_by_drone.items():
            previous_rendezvous_occurrence = 0
            for local_order, sortie_index in enumerate(sortie_indices):
                sortie = self.sorties[sortie_index]
                drone_local_order[sortie_index] = local_order

                launch_node = self.launch_node(sortie_index)
                launch_occurrence = _first_occurrence(
                    launch_node,
                    previous_rendezvous_occurrence,
                )
                rendezvous_occurrence = _first_occurrence(
                    sortie.rendezvous,
                    launch_occurrence,
                )

                launch_occurrence_by_sortie[sortie_index] = launch_occurrence
                rendezvous_occurrence_by_sortie[sortie_index] = rendezvous_occurrence
                previous_rendezvous_occurrence = rendezvous_occurrence

        sorties_launching_at_visit: dict[int, list[int]] = {}
        sorties_rendezvousing_at_visit: dict[int, list[int]] = {}
        for sortie_index in range(len(self.sorties)):
            launch_visit = launch_occurrence_by_sortie[sortie_index]
            rendezvous_visit = rendezvous_occurrence_by_sortie[sortie_index]
            sorties_launching_at_visit.setdefault(launch_visit, []).append(sortie_index)
            sorties_rendezvousing_at_visit.setdefault(rendezvous_visit, []).append(sortie_index)

        truck_arrivals: list[float] = [0.0] * len(self.truck_route)
        truck_departures: list[float] = [0.0] * len(self.truck_route)
        sortie_launch_times: dict[int, float] = {}
        sortie_end_times: dict[int, float] = {}
        drone_available_time: dict[int, float] = {drone_id: 0.0 for drone_id in range(self.num_drones)}

        for visit_index, node_id in enumerate(self.truck_route):
            if visit_index == 0:
                truck_arrivals[visit_index] = (
                    self.truck_arrival_times.get(node_id, 0.0)
                    if self.truck_arrival_times is not None
                    else 0.0
                )
            else:
                previous_node = self.truck_route[visit_index - 1]
                previous_departure = truck_departures[visit_index - 1]
                travel_time = self._truck_travel_time_s(
                    previous_node,
                    node_id,
                    leg_index=visit_index - 1,
                )
                nominal_arrival = previous_departure + travel_time
                if self.truck_arrival_times is not None and node_id in self.truck_arrival_times:
                    nominal_arrival = max(nominal_arrival, self.truck_arrival_times[node_id])
                truck_arrivals[visit_index] = nominal_arrival

            service_complete_time = truck_arrivals[visit_index] + self._truck_service_time_s(node_id)
            latest_launch_time = service_complete_time
            launches_here = sorted(
                sorties_launching_at_visit.get(visit_index, []),
                key=lambda sortie_index: (
                    self.sorties[sortie_index].drone_id,
                    drone_local_order[sortie_index],
                    sortie_index,
                ),
            )
            for sortie_index in launches_here:
                sortie = self.sorties[sortie_index]
                launch_time = max(
                    service_complete_time,
                    drone_available_time[sortie.drone_id],
                )
                end_time = launch_time + float(self.planned_metrics.sortie_times[sortie_index])
                sortie_launch_times[sortie_index] = launch_time
                sortie_end_times[sortie_index] = end_time
                drone_available_time[sortie.drone_id] = end_time
                latest_launch_time = max(latest_launch_time, launch_time)

            latest_rendezvous_time = max(
                (
                    sortie_end_times[sortie_index]
                    for sortie_index in sorties_rendezvousing_at_visit.get(visit_index, [])
                ),
                default=truck_arrivals[visit_index],
            )
            truck_departures[visit_index] = max(
                service_complete_time,
                latest_launch_time,
                latest_rendezvous_time,
            )

        return {
            "truck_arrivals": truck_arrivals,
            "truck_departures": truck_departures,
            "sortie_launch_times": sortie_launch_times,
            "sortie_end_times": sortie_end_times,
            "launch_occurrences": launch_occurrence_by_sortie,
            "rendezvous_occurrences": rendezvous_occurrence_by_sortie,
        }

    def _truck_travel_time_s(self, from_node: int, to_node: int, leg_index: Optional[int] = None) -> float:
        if self.truck_leg_travel_times is not None:
            if leg_index is None:
                raise ValueError("leg_index is required when truck_leg_travel_times are provided")
            return float(self.truck_leg_travel_times[leg_index])
        from_lat, from_lon = self._node_to_gps(from_node)
        to_lat, to_lon = self._node_to_gps(to_node)
        return haversine_distance(from_lat, from_lon, to_lat, to_lon) / self.truck_speed

    def _node_to_gps(self, node_id: int) -> tuple[float, float]:
        if node_id == 0:
            return self.problem.depot
        if node_id not in self.problem.customers:
            raise ValueError(f"node {node_id} not found in solution.problem.customers")
        return self.problem.customers[node_id]

    def _truck_service_time_s(self, node_id: int) -> float:
        if int(node_id) == 0:
            return 0.0
        return float(self.truck_service_time)
