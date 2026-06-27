"""MAVSDK-based flight controller for dronevalkit.

Executes an entire FSTSP/TSP-D mission (all sorties plus repositioning legs
between them) in one continuous flight session within a single PX4 SITL run.

The mission is flown using GPS waypoints via ``action.goto_location()``.
Telemetry is still recorded and converted into depot-relative NED-like samples
for downstream analysis.
"""

import asyncio
import logging
import math
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Optional

import grpc
from mavsdk import System

from . import geo
from .drone import Drone
from .exceptions import ConnectionError, MissionAbortedError, WaypointTimeoutError
from .models import LegTiming, TruckTimingSegment, VehicleSpeeds

_EPISODE_STALL_TIMEOUT_S = 15.0 * 60.0
_EPISODE_MOVEMENT_THRESHOLD_M = 2.0


# ---------------------------------------------------------------------------
# Telemetry data structures
# ---------------------------------------------------------------------------


@dataclass
class SegmentLog:
    """Telemetry for one flight segment (sortie or repositioning)."""

    segment_type: str           # "sortie" or "reposition"
    sortie_index: Optional[int] # which sortie (None for reposition)
    start_time: float           # seconds since mission start
    end_time: float             # seconds since mission start
    positions: list             # [(north_m, east_m, down_m, timestamp_s), ...]
    battery_at_start: float     # PX4's reported battery %
    battery_at_end: float       # PX4's reported battery %
    battery_samples: Optional[list] = None  # [(timestamp_s, battery_pct), ...]
    leg_timings: Optional[list[LegTiming]] = None
    leg_energy_samples: Optional[list] = None


@dataclass
class MissionLog:
    """Full telemetry for an entire mission run (all sorties + repositioning)."""

    segments: list              # list[SegmentLog]
    total_time: float           # wall-clock seconds for the whole mission
    ulog_path: Optional[str]    # set by runner after container stops


# ---------------------------------------------------------------------------
# Background telemetry collection
# ---------------------------------------------------------------------------


class _TelemetryCollector:
    """Continuously samples drone NED position into a caller-supplied list.

    Usage::

        collector = _TelemetryCollector(drone, mission_start)
        positions = []
        collector.start(positions)
        # ... fly stuff ...
        collector.stop()
        # positions is now populated
    """

    def __init__(
        self,
        drone: System,
        mission_start: float,
        ref_lat: float,
        ref_lon: float,
    ) -> None:
        self._drone = drone
        self._mission_start = mission_start
        self._ref_lat = ref_lat
        self._ref_lon = ref_lon
        self._task: Optional[asyncio.Task] = None
        self._battery_task: Optional[asyncio.Task] = None
        self._positions: Optional[list] = None
        self._battery_samples: Optional[list] = None
        self._active = False

    def start(self, positions: list, battery_samples: Optional[list] = None) -> None:
        """Begin appending (north, east, down, timestamp) tuples to *positions*."""
        self._positions = positions
        self._battery_samples = battery_samples
        self._active = True
        self._task = asyncio.create_task(self._run())
        if self._battery_samples is not None:
            self._battery_task = asyncio.create_task(self._run_battery())

    def stop(self) -> None:
        """Stop collection and cancel the background task."""
        self._active = False
        if self._task and not self._task.done():
            self._task.cancel()
        if self._battery_task and not self._battery_task.done():
            self._battery_task.cancel()
        self._task = None
        self._battery_task = None

    async def _run(self) -> None:
        try:
            async for pos in self._drone.telemetry.position():
                if not self._active:
                    return
                ts = time.time() - self._mission_start
                north_m, east_m = geo.gps_to_ned(
                    pos.latitude_deg,
                    pos.longitude_deg,
                    self._ref_lat,
                    self._ref_lon,
                )
                self._positions.append((
                    north_m,
                    east_m,
                    -pos.relative_altitude_m,
                    ts,
                ))
        except asyncio.CancelledError:
            pass
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE and not self._active:
                return
            raise

    async def _run_battery(self) -> None:
        try:
            async for battery in self._drone.telemetry.battery():
                if not self._active:
                    return
                remaining = float(getattr(battery, "remaining_percent", float("nan")))
                if not math.isfinite(remaining):
                    continue
                ts = time.time() - self._mission_start
                self._battery_samples.append((ts, max(0.0, min(100.0, remaining))))
        except asyncio.CancelledError:
            pass
        except grpc.aio.AioRpcError as exc:
            if exc.code() == grpc.StatusCode.UNAVAILABLE and not self._active:
                return
            raise


class _BatteryStatusLogger:
    """Periodically log battery percentage for one drone during a mission."""

    def __init__(
        self,
        vehicle: Drone,
        interval_s: float = 5.0,
        movement_watchdog: Optional["_MovementWatchdog"] = None,
    ) -> None:
        self._vehicle = vehicle
        self._interval_s = float(interval_s)
        self._movement_watchdog = movement_watchdog
        self._task: Optional[asyncio.Task] = None
        self._active = False
        self._abort_task: Optional[asyncio.Task] = None
        self._depletion_message: Optional[str] = None

    def start(self, *, mission_task: Optional[asyncio.Task] = None) -> None:
        self._active = True
        self._abort_task = mission_task
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._active = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._abort_task = None

    @property
    def depletion_message(self) -> Optional[str]:
        return self._depletion_message

    async def _run(self) -> None:
        log = logging.getLogger(__name__)
        try:
            while self._active:
                try:
                    battery_pct = await self._vehicle.get_battery_pct()
                    try:
                        lat, lon, abs_alt, rel_alt = await self._vehicle.get_position_gps()
                    except Exception as pos_exc:  # noqa: BLE001
                        log.info(
                            "%s Battery status: %.1f%% (position unavailable: %s)",
                            self._log_prefix(),
                            battery_pct,
                            pos_exc,
                        )
                    else:
                        log.info(
                            "%s Battery status: %.1f%% lat=%.6f lon=%.6f abs_alt=%.2fm rel_alt=%.2fm",
                            self._log_prefix(),
                            battery_pct,
                            lat,
                            lon,
                            abs_alt,
                            rel_alt,
                        )
                        if self._movement_watchdog is not None:
                            self._movement_watchdog.observe_position(
                                drone_id=getattr(self._vehicle, "drone_id", None),
                                latitude_deg=lat,
                                longitude_deg=lon,
                                absolute_altitude_m=abs_alt,
                            )
                    if battery_pct <= 0.0:
                        self._depletion_message = (
                            f"{self._log_prefix()} Battery reached 0.0%; aborting mission"
                        )
                        log.error("%s", self._depletion_message)
                        self._active = False
                        if self._abort_task is not None and not self._abort_task.done():
                            self._abort_task.cancel()
                        return
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "%s Battery status sample failed: %s",
                        self._log_prefix(),
                        exc,
                    )
                await asyncio.sleep(self._interval_s)
        except asyncio.CancelledError:
            pass

    def _log_prefix(self) -> str:
        if hasattr(self._vehicle, "_log_prefix"):
            return self._vehicle._log_prefix()
        drone_id = getattr(self._vehicle, "drone_id", None)
        if drone_id is None:
            return "[drone=unknown]"
        return f"[drone={drone_id}]"


class _MovementWatchdog:
    """Abort a multi-drone mission if all drones stay stationary too long."""

    def __init__(
        self,
        timeout_s: float = _EPISODE_STALL_TIMEOUT_S,
        movement_threshold_m: float = _EPISODE_MOVEMENT_THRESHOLD_M,
        poll_interval_s: float = 5.0,
    ) -> None:
        self._timeout_s = float(timeout_s)
        self._movement_threshold_m = float(movement_threshold_m)
        self._poll_interval_s = float(poll_interval_s)
        self._task: Optional[asyncio.Task] = None
        self._active = False
        self._abort_task: Optional[asyncio.Task] = None
        self._stall_message: Optional[str] = None
        self._position_anchors: dict[int, tuple[float, float, float]] = {}
        self._last_movement_monotonic = time.monotonic()

    def start(self, *, mission_task: Optional[asyncio.Task] = None) -> None:
        self._active = True
        self._abort_task = mission_task
        self._stall_message = None
        self._last_movement_monotonic = time.monotonic()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._active = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._abort_task = None

    @property
    def stall_message(self) -> Optional[str]:
        return self._stall_message

    def observe_position(
        self,
        *,
        drone_id: Optional[int],
        latitude_deg: float,
        longitude_deg: float,
        absolute_altitude_m: float,
    ) -> None:
        if drone_id is None:
            return
        current = (float(latitude_deg), float(longitude_deg), float(absolute_altitude_m))
        previous = self._position_anchors.get(int(drone_id))
        if previous is None:
            self._position_anchors[int(drone_id)] = current
            return

        horizontal_m = geo.haversine_distance(
            previous[0],
            previous[1],
            current[0],
            current[1],
        )
        vertical_m = current[2] - previous[2]
        displacement_m = math.sqrt(horizontal_m ** 2 + vertical_m ** 2)
        if displacement_m >= self._movement_threshold_m:
            self._position_anchors[int(drone_id)] = current
            self._last_movement_monotonic = time.monotonic()

    async def _run(self) -> None:
        log = logging.getLogger(__name__)
        try:
            while self._active:
                stalled_for_s = time.monotonic() - self._last_movement_monotonic
                if stalled_for_s >= self._timeout_s:
                    self._stall_message = (
                        "No drone movement detected for "
                        f"{self._timeout_s / 60.0:.0f} minutes; aborting mission"
                    )
                    log.error("%s", self._stall_message)
                    self._active = False
                    if self._abort_task is not None and not self._abort_task.done():
                        self._abort_task.cancel()
                    return
                await asyncio.sleep(self._poll_interval_s)
        except asyncio.CancelledError:
            pass


class _TruckCoordinator:
    """Coordinate truck progress across route visits using actual drone events."""

    def __init__(
        self,
        truck_route_gps: list[tuple[float, float]],
        truck_speed_m_s: float,
        sorties: list[dict],
        truck_leg_travel_times: Optional[list[float]] = None,
        planned_truck_timeline: Optional[list[TruckTimingSegment]] = None,
    ) -> None:
        if truck_speed_m_s <= 0.0:
            raise MissionAbortedError("truck_speed_m_s must be positive")
        self._truck_route_gps = list(truck_route_gps)
        self._condition = asyncio.Condition()
        self._mission_start_monotonic = time.monotonic()
        self._launch_required: dict[int, int] = {}
        self._rendezvous_required: dict[int, int] = {}
        self._launch_done: dict[int, int] = {}
        self._rendezvous_done: dict[int, int] = {}
        self._launch_sequence_by_visit: dict[int, list[int]] = {}
        self._recovery_sequence_by_visit: dict[int, list[tuple[int, float]]] = {}
        self._arrival_times: dict[int, float] = {0: 0.0}
        self._departure_times: dict[int, float] = {}
        self._travel_times: list[float] = []

        if planned_truck_timeline is not None:
            (
                nominal_arrivals,
                nominal_departures,
                self._travel_times,
            ) = _planned_truck_visit_schedule_from_timeline(
                num_visits=len(self._truck_route_gps),
                sorties=sorties,
                timeline=planned_truck_timeline,
                fallback_leg_times=truck_leg_travel_times,
                fallback_route_gps=self._truck_route_gps,
                fallback_speed_m_s=truck_speed_m_s,
            )
            self._arrival_times = {0: float(nominal_arrivals[0])}
            self._launch_sequence_by_visit = _launch_sequence_from_timeline(
                num_visits=len(self._truck_route_gps),
                timeline=planned_truck_timeline,
            )
            self._recovery_sequence_by_visit = _recovery_sequence_from_timeline(
                num_visits=len(self._truck_route_gps),
                timeline=planned_truck_timeline,
            )
        elif truck_leg_travel_times is not None:
            self._travel_times = [float(value) for value in truck_leg_travel_times]
            if len(self._travel_times) != max(0, len(self._truck_route_gps) - 1):
                raise MissionAbortedError("truck_leg_travel_times must align with truck_route_gps")
        else:
            for visit_index in range(max(0, len(self._truck_route_gps) - 1)):
                start = self._truck_route_gps[visit_index]
                end = self._truck_route_gps[visit_index + 1]
                self._travel_times.append(
                    geo.haversine_distance(start[0], start[1], end[0], end[1]) / truck_speed_m_s
                )

        for sortie in sorties:
            launch_visit = sortie.get("launch_visit")
            rendezvous_visit = sortie.get("rendezvous_visit")
            if launch_visit is None or rendezvous_visit is None:
                continue
            self._launch_required[int(launch_visit)] = self._launch_required.get(int(launch_visit), 0) + 1
            self._rendezvous_required[int(rendezvous_visit)] = (
                self._rendezvous_required.get(int(rendezvous_visit), 0) + 1
            )

        self._maybe_advance_locked(now_s=0.0)

    def _elapsed_s(self) -> float:
        return time.monotonic() - self._mission_start_monotonic

    def _maybe_advance_locked(self, now_s: float) -> None:
        while True:
            next_visit = len(self._departure_times)
            if next_visit >= len(self._truck_route_gps):
                return
            arrival_s = self._arrival_times.get(next_visit)
            if arrival_s is None:
                return
            required_launches = self._launch_required.get(next_visit, 0)
            required_rendezvous = self._rendezvous_required.get(next_visit, 0)
            if self._launch_done.get(next_visit, 0) < required_launches:
                return
            if self._rendezvous_done.get(next_visit, 0) < required_rendezvous:
                return

            departure_s = max(arrival_s, now_s)
            self._departure_times[next_visit] = departure_s
            if next_visit + 1 < len(self._truck_route_gps):
                self._arrival_times[next_visit + 1] = departure_s + self._travel_times[next_visit]

    async def wait_for_truck_arrival(self, visit_index: Optional[int]) -> None:
        if visit_index is None:
            return
        visit_index = int(visit_index)
        while True:
            async with self._condition:
                arrival_s = self._arrival_times.get(visit_index)
                if arrival_s is None:
                    await self._condition.wait()
                    continue
            remaining_s = arrival_s - self._elapsed_s()
            if remaining_s <= 0.0:
                return
            await asyncio.sleep(remaining_s)

    async def wait_for_launch_clearance(self, visit_index: Optional[int], drone_id: Optional[int]) -> None:
        if visit_index is None or drone_id is None:
            return
        visit_index = int(visit_index)
        drone_id = int(drone_id)
        sequence = self._launch_sequence_by_visit.get(visit_index)
        if not sequence:
            return

        while True:
            async with self._condition:
                launched_count = self._launch_done.get(visit_index, 0)
                if launched_count >= len(sequence):
                    return
                if sequence[launched_count] == drone_id:
                    return
                await self._condition.wait()

    async def wait_for_recovery_clearance(self, visit_index: Optional[int], drone_id: Optional[int]) -> None:
        if visit_index is None or drone_id is None:
            return
        visit_index = int(visit_index)
        drone_id = int(drone_id)

        await self.wait_for_truck_arrival(visit_index)

        sequence = self._recovery_sequence_by_visit.get(visit_index)
        if not sequence:
            return

        slot_index = next(
            (index for index, (slot_drone_id, _slot_start) in enumerate(sequence) if slot_drone_id == drone_id),
            None,
        )
        if slot_index is None:
            return

        while True:
            async with self._condition:
                completed = self._rendezvous_done.get(visit_index, 0)
                now_s = self._elapsed_s()
                _slot_drone_id, slot_start_s = sequence[slot_index]
                if completed == slot_index and now_s >= slot_start_s:
                    return

                wait_timeout_s: Optional[float] = None
                if completed == slot_index and now_s < slot_start_s:
                    wait_timeout_s = slot_start_s - now_s

                if wait_timeout_s is None:
                    await self._condition.wait()
                    continue

                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=wait_timeout_s)
                except asyncio.TimeoutError:
                    pass

    async def notify_launch(self, visit_index: Optional[int]) -> None:
        if visit_index is None:
            return
        async with self._condition:
            visit_index = int(visit_index)
            self._launch_done[visit_index] = self._launch_done.get(visit_index, 0) + 1
            self._maybe_advance_locked(now_s=self._elapsed_s())
            self._condition.notify_all()

    async def notify_rendezvous(self, visit_index: Optional[int]) -> None:
        if visit_index is None:
            return
        async with self._condition:
            visit_index = int(visit_index)
            self._rendezvous_done[visit_index] = self._rendezvous_done.get(visit_index, 0) + 1
            self._maybe_advance_locked(now_s=self._elapsed_s())
            self._condition.notify_all()


def _planned_truck_visit_schedule_from_timeline(
    *,
    num_visits: int,
    sorties: list[dict],
    timeline: list[TruckTimingSegment],
    fallback_leg_times: Optional[list[float]],
    fallback_route_gps: list[tuple[float, float]],
    fallback_speed_m_s: float,
) -> tuple[list[float], list[float], list[float]]:
    if num_visits <= 0:
        return [], [], []

    normalized_timeline = [
        segment if isinstance(segment, TruckTimingSegment) else TruckTimingSegment(**segment)
        for segment in timeline
    ]
    ordered = sorted(
        normalized_timeline,
        key=lambda segment: (float(segment.start_time), float(segment.end_time)),
    )

    arrivals = [0.0] * num_visits
    departures = [0.0] * num_visits
    travel_times = _fallback_travel_times(
        num_visits=num_visits,
        leg_times=fallback_leg_times,
        route_gps=fallback_route_gps,
        speed_m_s=fallback_speed_m_s,
    )

    visit_pointer = 0
    segment_pointer = 0
    while visit_pointer < num_visits - 1:
        current_arrival = arrivals[visit_pointer]
        current_departure = current_arrival

        while segment_pointer < len(ordered):
            segment = ordered[segment_pointer]
            if segment.kind == "dwell":
                current_departure = max(current_departure, float(segment.end_time))
                segment_pointer += 1
                continue
            if segment.kind == "move":
                current_departure = max(current_departure, float(segment.start_time))
                departures[visit_pointer] = current_departure
                travel_times[visit_pointer] = max(
                    0.0,
                    float(segment.end_time) - float(segment.start_time),
                )
                arrivals[visit_pointer + 1] = current_departure + travel_times[visit_pointer]
                segment_pointer += 1
                break
            segment_pointer += 1
        else:
            departures[visit_pointer] = current_departure
            arrivals[visit_pointer + 1] = current_departure + travel_times[visit_pointer]

        visit_pointer += 1

    final_departure = arrivals[-1]
    while segment_pointer < len(ordered):
        segment = ordered[segment_pointer]
        if segment.kind == "dwell":
            final_departure = max(final_departure, float(segment.end_time))
        segment_pointer += 1
    departures[-1] = final_departure

    return arrivals, departures, travel_times


def _fallback_travel_times(
    *,
    num_visits: int,
    leg_times: Optional[list[float]],
    route_gps: list[tuple[float, float]],
    speed_m_s: float,
) -> list[float]:
    if leg_times is not None:
        values = [float(value) for value in leg_times]
        if len(values) != max(0, num_visits - 1):
            raise MissionAbortedError("truck_leg_travel_times must align with truck route visits")
        return values

    values: list[float] = []
    for visit_index in range(max(0, num_visits - 1)):
        start = route_gps[visit_index]
        end = route_gps[visit_index + 1]
        values.append(geo.haversine_distance(start[0], start[1], end[0], end[1]) / speed_m_s)
    return values


def _launch_sequence_from_timeline(
    *,
    num_visits: int,
    timeline: list[TruckTimingSegment],
) -> dict[int, list[int]]:
    if num_visits <= 0:
        return {}

    normalized_timeline = [
        segment if isinstance(segment, TruckTimingSegment) else TruckTimingSegment(**segment)
        for segment in timeline
    ]
    ordered = sorted(
        normalized_timeline,
        key=lambda segment: (float(segment.start_time), float(segment.end_time)),
    )

    by_visit: dict[int, list[int]] = {}
    visit_index = 0
    for segment in ordered:
        if segment.kind == "move":
            if visit_index + 1 < num_visits:
                visit_index += 1
            continue
        if segment.drone_id is None:
            continue
        label = str(segment.label).lower()
        if "launch" not in label:
            continue
        by_visit.setdefault(visit_index, []).append(int(segment.drone_id))
    return by_visit


def _recovery_sequence_from_timeline(
    *,
    num_visits: int,
    timeline: list[TruckTimingSegment],
) -> dict[int, list[tuple[int, float]]]:
    if num_visits <= 0:
        return {}

    normalized_timeline = [
        segment if isinstance(segment, TruckTimingSegment) else TruckTimingSegment(**segment)
        for segment in timeline
    ]
    ordered = sorted(
        normalized_timeline,
        key=lambda segment: (float(segment.start_time), float(segment.end_time)),
    )

    by_visit: dict[int, list[tuple[int, float]]] = {}
    visit_index = 0
    for segment in ordered:
        if segment.kind == "move":
            if visit_index + 1 < num_visits:
                visit_index += 1
            continue
        if segment.drone_id is None:
            continue
        label = str(segment.label).lower()
        if "retriev" not in label and "recover" not in label:
            continue
        by_visit.setdefault(visit_index, []).append(
            (int(segment.drone_id), float(segment.start_time))
        )
    return by_visit


# ---------------------------------------------------------------------------
# Connection and configuration
# ---------------------------------------------------------------------------


async def connect(
    address: str = "udpin://0.0.0.0:14540",
    timeout: float = 60.0,
    mavsdk_server_port: Optional[int] = None,
    drone_id: Optional[int] = None,
) -> System:
    """Connect to PX4 and wait for GPS fix.

    Args:
        address: MAVSDK system address, e.g. ``"udpin://0.0.0.0:14540"``.
        timeout: Seconds to wait for connection and position estimate.

    Returns:
        Connected :class:`mavsdk.System` instance.

    Raises:
        ConnectionError: If PX4 does not connect or obtain a position
            estimate within *timeout* seconds.
    """
    log = logging.getLogger(__name__)
    drone_prefix = f"[drone={drone_id}]" if drone_id is not None else "[drone=unknown]"
    # Use a dedicated MAVSDK server port per drone client in multi-drone runs.
    # Reusing the default port can collapse multiple clients onto one server/system.
    if mavsdk_server_port is None:
        drone = System()
    else:
        drone = System(port=mavsdk_server_port)
    try:
        await drone.connect(system_address=address)

        async def _wait_connected() -> None:
            async for state in drone.core.connection_state():
                if state.is_connected:
                    return

        try:
            await asyncio.wait_for(_wait_connected(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ConnectionError(
                f"Timed out waiting for PX4 at {address} after {timeout}s"
            ) from exc

        async def _wait_position_sample() -> None:
            async for pos in drone.telemetry.position():
                if (
                    math.isfinite(pos.latitude_deg)
                    and math.isfinite(pos.longitude_deg)
                    and math.isfinite(pos.absolute_altitude_m)
                ):
                    return

        try:
            await asyncio.wait_for(_wait_position_sample(), timeout=5.0)
        except asyncio.TimeoutError:
            log.warning(
                "%s No position telemetry yet on %s; continuing after connection discovery",
                drone_prefix,
                address,
            )

        return drone
    except Exception:
        await shutdown_mavsdk_system(drone)
        raise


async def connect_multi(
    num_drones: int,
    base_instance: int = 0,
    timeout: float = 60.0,
) -> list[System]:
    """Connect to multiple PX4 instances in one Gazebo experiment."""
    return await asyncio.gather(
        *(
            connect(
                address=f"udpin://0.0.0.0:{14540 + base_instance + drone_id}",
                timeout=timeout,
                mavsdk_server_port=50051 + base_instance + drone_id,
                drone_id=drone_id,
            )
            for drone_id in range(num_drones)
        )
    )


async def shutdown_mavsdk_system(drone: System) -> None:
    """Stop and reap the embedded mavsdk_server for one System instance."""
    if drone is None:
        return

    server_process = getattr(drone, "_server_process", None)
    if (
        server_process is None
        or not hasattr(server_process, "poll")
        or not hasattr(server_process, "kill")
        or not hasattr(server_process, "wait")
    ):
        return

    if server_process.poll() is None:
        server_process.kill()

    try:
        server_process.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        server_process.kill()
        server_process.wait()
    finally:
        stdout = getattr(server_process, "stdout", None)
        if stdout is not None and not stdout.closed:
            stdout.close()
        setattr(drone, "_server_process", None)
        if hasattr(drone, "_plugins"):
            try:
                drone._plugins = {}
            except Exception:
                pass


async def shutdown_mavsdk_systems(drones: list[System]) -> None:
    """Stop and reap embedded mavsdk_server children for all Systems."""
    await asyncio.gather(*(shutdown_mavsdk_system(drone) for drone in drones), return_exceptions=True)


async def configure_for_experiment(
    drone: System | list[System],
    battery_config,
    wind_condition,
    speed_factor: float = 1.0,
    vehicle_speeds: Optional[VehicleSpeeds] = None,
    drone_id: Optional[int] = None,
) -> None:
    """Configure PX4 parameters before the mission begins.

    **CRITICAL:** Disables all battery failsafes.  Because the drone flies
    repositioning legs between sorties, the simulated battery drains more
    than a real truck-mounted drone would experience.  Failsafes would
    trigger RTL or auto-land mid-experiment.  Energy accounting is done in
    post-processing from ULog ``actuator_outputs`` data.

    Args:
        drone: Connected MAVSDK System, or a list of Systems for a multi-drone run.
        battery_config: :class:`~dronevalkit.config.CustomBattery` instance.
        wind_condition: :class:`~dronevalkit.config.WindCondition` instance.
    """
    if isinstance(drone, list):
        await asyncio.gather(
            *(
                configure_for_experiment(
                    d,
                    battery_config,
                    wind_condition,
                    speed_factor=speed_factor,
                    vehicle_speeds=vehicle_speeds,
                    drone_id=index,
                )
                for index, d in enumerate(drone)
            )
        )
        return

    import logging
    log = logging.getLogger(__name__)
    drone_prefix = f"[drone={drone_id}]" if drone_id is not None else "[drone=unknown]"

    async def _set_int(name: str, value: int) -> bool:
        try:
            await drone.param.set_param_int(name, value)
            log.debug("%s param set: %s = %d", drone_prefix, name, value)
            return True
        except Exception as exc:
            log.warning("%s param FAILED: %s = %d (%s)", drone_prefix, name, value, exc)
            return False

    async def _set_float(name: str, value: float) -> bool:
        try:
            await drone.param.set_param_float(name, value)
            log.debug("%s param set: %s = %f", drone_prefix, name, value)
            return True
        except Exception as exc:
            log.warning("%s param FAILED: %s = %f (%s)", drone_prefix, name, value, exc)
            return False

    async def _set_float_verified(name: str, value: float, attempts: int = 3) -> bool:
        """Set a float parameter and confirm via read-back."""
        for attempt in range(1, attempts + 1):
            if not await _set_float(name, value):
                await asyncio.sleep(0.2)
                continue
            try:
                actual = await drone.param.get_param_float(name)
            except Exception as exc:
                log.warning(
                    "%s param verify FAILED: %s (attempt %d/%d, %s)",
                    drone_prefix,
                    name, attempt, attempts, exc,
                )
                await asyncio.sleep(0.2)
                continue
            if abs(actual - value) <= 1e-6:
                log.debug("%s param verified: %s = %f", drone_prefix, name, actual)
                return True
            log.warning(
                "%s param verify mismatch: %s expected=%f actual=%f (attempt %d/%d)",
                drone_prefix,
                name, value, actual, attempt, attempts,
            )
            await asyncio.sleep(0.2)
        return False

    log.info("%s Configuring PX4 experiment parameters...", drone_prefix)

    # Disable battery-triggered actions
    await _set_int("COM_LOW_BAT_ACT", 0)
    await _set_float("COM_ARM_BAT_MIN", 0.0)
    await _set_int("COM_ARM_WO_GPS", 2)
    await _set_int("CBRK_SUPPLY_CHK", 894281)

    # SITL battery simulation
    # If drain is disabled, pin minimum to 100% to avoid PX4 estimator
    # collapse-to-zero artifacts in SITL/QGC.
    if battery_config.drain_rate <= 0.0:
        sim_bat_min_pct = 100.0
    else:
        sim_bat_min_pct = 0.0 if battery_config.full_drain else 0.2
    await _set_float("SIM_BAT_MIN_PCT", sim_bat_min_pct)
    if not await _set_float_verified("SIM_BAT_DRAIN", battery_config.drain_rate):
        raise MissionAbortedError(
            "Could not set/verify SIM_BAT_DRAIN; aborting run to avoid invalid battery metrics."
        )

    batt = await get_battery_pct(drone)
    log.debug("%s Battery after configuration: %.1f%%  (drain_rate=%.3f %%/s)",
             drone_prefix,
             batt, battery_config.drain_rate)

    # Wind is configured in Gazebo world startup (runner.py). SIH_WIND_* params
    # apply to PX4's SIH backend and have no effect in Gazebo Harmonic.
    if wind_condition.speed > 0:
        log.info(
            "%s Wind condition requested: speed=%.3f m/s direction=%.1f° "
            "(applied at Gazebo startup)",
            drone_prefix,
            float(wind_condition.speed),
            float(wind_condition.direction),
        )
    if vehicle_speeds is not None and vehicle_speeds.yaw_rate_deg is not None:
        await _set_float("MPC_YAWRAUTO_MAX", float(vehicle_speeds.yaw_rate_deg))

    # Speed profile for goto_location() missions.
    # `speed_factor=2.0` means "fly roughly 2x faster" by scaling either the
    # benchmark-provided speeds or the default PX4 profile.
    if speed_factor <= 0.0:
        raise MissionAbortedError("speed_factor must be positive")
    if vehicle_speeds is None:
        base_xy_cruise = 5.0
        base_xy_max = 12.0
        base_z_up = 3.0
        base_z_down = 1.5
    else:
        base_xy_cruise = float(vehicle_speeds.cruise)
        base_xy_max = float(vehicle_speeds.cruise)
        base_z_up = float(vehicle_speeds.takeoff)
        base_z_down = float(vehicle_speeds.landing)
    target_xy_cruise = base_xy_cruise * speed_factor
    target_xy_max = max(target_xy_cruise, base_xy_max * speed_factor)
    # OR models specify vertical speeds as *average* (distance / time), but PX4
    # interprets them as *maximum* target velocities.  Due to the trapezoidal
    # velocity profile (accelerate → cruise → decelerate), the actual average is
    # roughly half the max.  Doubling the target compensates for this so the
    # simulated climb/descent duration matches the OR planner's assumption.
    _MAX_Z_UP = 8.0  # PX4 safe limit for MPC_Z_VEL_MAX_UP (m/s)
    _MAX_Z_DOWN = 4.0  # PX4 safe limit for MPC_Z_VEL_MAX_DN / MPC_LAND_SPEED
    _MAX_TKO = 5.0  # PX4 safe limit for MPC_TKO_SPEED
    target_z_up = min(base_z_up * speed_factor * 2.0, _MAX_Z_UP)
    target_z_down = min(base_z_down * speed_factor * 2.0, _MAX_Z_DOWN)
    target_tko = min(base_z_up * speed_factor * 2.0, _MAX_TKO)
    await _set_float("MPC_XY_CRUISE", target_xy_cruise)
    await _set_float("MPC_XY_VEL_MAX", target_xy_max)
    await _set_float("MPC_Z_VEL_MAX_UP", target_z_up)
    await _set_float("MPC_Z_VEL_MAX_DN", target_z_down)
    await _set_float("MPC_LAND_SPEED", target_z_down)
    await _set_float("MPC_TKO_SPEED", target_tko)
    log.info(
        "%s Speed profile: factor=%.2f, MPC_XY_CRUISE=%.2f m/s, MPC_XY_VEL_MAX=%.2f m/s, "
        "MPC_Z_VEL_MAX_UP=%.2f m/s, MPC_Z_VEL_MAX_DN=%.2f m/s, MPC_LAND_SPEED=%.2f m/s, "
        "MPC_TKO_SPEED=%.2f m/s",
        drone_prefix,
        speed_factor,
        target_xy_cruise,
        target_xy_max,
        target_z_up,
        target_z_down,
        target_z_down,
        target_tko,
    )


async def _ensure_min_duration(start_monotonic: float, min_duration_s: float) -> None:
    """Wait long enough so the elapsed time since *start_monotonic* reaches *min_duration_s*."""
    if min_duration_s <= 0.0:
        return
    remaining_s = float(min_duration_s) - (time.monotonic() - float(start_monotonic))
    if remaining_s > 0.0:
        await asyncio.sleep(remaining_s)


async def _fly_single_mission(
    drone: System,
    sortie_waypoints: list,
    altitude: float = 20.0,
    tolerance: float = 1.0,
    reference_gps: Optional[tuple[float, float]] = None,
    cruise_speed_m_s: Optional[float] = None,
    drone_id: Optional[int] = None,
    truck_coordinator: Optional[_TruckCoordinator] = None,
    altitude_deconfliction_m: float = 0.0,
    launch_time_s: float = 0.0,
    recovery_time_s: float = 0.0,
    default_delivery_time_s: float = 60.0,
    progress_callback: Optional[Callable[[dict[str, object]], None]] = None,
    movement_watchdog: Optional[_MovementWatchdog] = None,
) -> MissionLog:
    """Fly an entire FSTSP mission in one continuous session.

    Executes all sorties with repositioning legs between them.  Each
    segment is timestamped and tagged so the analysis layer can separate
    sortie energy from repositioning energy.

    A background :class:`_TelemetryCollector` task runs during every
    *sortie* segment and populates ``SegmentLog.positions`` with
    ``(north_m, east_m, down_m, mission_time_s)`` samples.
    Repositioning segments are logged but have ``positions=[]`` because
    their paths are not used in the analysis.

    Full flight sequence for *N* sorties::

        SORTIE i:
          arm + take off at sortie[i].launch to altitude
          fly to sortie[i].delivery at altitude
          land at customer
          wait on the ground for the delivery service duration
          take off from customer back to altitude
          fly to sortie[i].rendezvous at altitude
          hover above the rendezvous point until the truck arrives
          land and disarm

        REPOSITION i -> i+1 (if i < N-1):
          arm + take off at sortie[i].rendezvous to altitude
          fly to sortie[i+1].launch at altitude
          land and disarm

    Args:
        drone: Connected and configured MAVSDK System.
        sortie_waypoints: List of dicts, one per sortie::

            [{"launch": (lat, lon), "delivery": (lat, lon), "rendezvous": (lat, lon)}, ...]

        altitude: Cruise altitude above home in metres.
        tolerance: Arrival radius in metres.

    Returns:
        :class:`MissionLog` with a :class:`SegmentLog` per segment.

    Raises:
        WaypointTimeoutError: If the drone does not reach a waypoint within
            120 s (per-waypoint default timeout).
        MissionAbortedError: If offboard mode cannot be started.
    """
    log = logging.getLogger(__name__)
    drone_tag = f"drone={drone_id}" if drone_id is not None else "drone=unknown"
    drone_prefix = f"[{drone_tag}]"
    drone_altitude = altitude + float(drone_id or 0) * float(altitude_deconfliction_m)
    if drone_id is not None and altitude_deconfliction_m > 0.0:
        log.info(
            "%s Altitude deconfliction: requested=%.1fm actual=%.1fm",
            drone_prefix,
            altitude,
            drone_altitude,
        )
    segments: list = []
    mission_start = time.time()
    if reference_gps is None:
        reference_gps = sortie_waypoints[0]["launch"]
    collector = _TelemetryCollector(drone, mission_start, reference_gps[0], reference_gps[1])
    vehicle = Drone(drone, drone_id=drone_id)
    battery_logger = _BatteryStatusLogger(
        vehicle,
        interval_s=5.0,
        movement_watchdog=movement_watchdog,
    )
    last_battery_reading: Optional[float] = None
    last_battery_ts: Optional[float] = None

    async def _battery_safe(sample_label: str) -> float:
        """Read battery %, filtering obvious telemetry glitches."""
        nonlocal last_battery_reading, last_battery_ts
        current = await vehicle.get_battery_pct()
        now = time.monotonic()

        if last_battery_reading is not None and last_battery_ts is not None:
            dt = max(1e-3, now - last_battery_ts)
            drop = last_battery_reading - current
            # Reject implausible instantaneous drops (e.g., transient 0% samples).
            if drop > 20.0 and dt < 2.0:
                log.warning(
                    "%s Ignoring suspicious battery sample %.1f%% at %s "
                    "(prev=%.1f%%, dt=%.2fs)",
                    drone_prefix,
                    current, sample_label, last_battery_reading, dt,
                )
                current = last_battery_reading

        last_battery_reading = current
        last_battery_ts = now
        return current

    def _gps_distance_m(start_gps: tuple[float, float], end_gps: tuple[float, float]) -> float:
        return geo.haversine_distance(start_gps[0], start_gps[1], end_gps[0], end_gps[1])

    def _start_collector(positions_buffer: list, battery_buffer: list) -> None:
        try:
            collector.start(positions_buffer, battery_buffer)
        except TypeError:
            collector.start(positions_buffer)
            if hasattr(collector, "_battery_samples"):
                collector._battery_samples = battery_buffer

    class _LegTimer:
        def __init__(self, segment_type: str, sortie_index: Optional[int]) -> None:
            self._timings: list[LegTiming] = []
            self._energy_samples: list[dict[str, float | str]] = []
            self._active_name: str | None = None
            self._active_start: float | None = None
            self._active_battery_start: float | None = None
            self._segment_type = str(segment_type)
            self._sortie_index = sortie_index

        async def start(self, name: str) -> None:
            await self.stop()
            self._active_name = str(name)
            self._active_start = time.time() - mission_start
            self._active_battery_start = await _battery_safe(f"{name}_start")

        async def stop(self) -> None:
            if (
                self._active_name is None
                or self._active_start is None
                or self._active_battery_start is None
            ):
                return
            active_end = time.time() - mission_start
            battery_end = await _battery_safe(f"{self._active_name}_end")
            self._timings.append(
                LegTiming(
                    name=self._active_name,
                    start_time=self._active_start,
                    end_time=active_end,
                )
            )
            self._energy_samples.append(
                {
                    "name": self._active_name,
                    "start_time": self._active_start,
                    "end_time": active_end,
                    "raw_battery_at_start": self._active_battery_start,
                    "raw_battery_at_end": battery_end,
                    "energy_pct": max(0.0, self._active_battery_start - battery_end),
                }
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "segment_type": self._segment_type,
                        "sortie_index": self._sortie_index,
                        "drone_id": drone_id,
                        "leg_name": self._active_name,
                    }
                )
            self._active_name = None
            self._active_start = None
            self._active_battery_start = None

        @property
        def timings(self) -> list[LegTiming]:
            return list(self._timings)

        @property
        def energy_samples(self) -> list[dict[str, float | str]]:
            return list(self._energy_samples)

        def add_completed(self, name: str, start_time: float, end_time: float) -> None:
            if end_time <= start_time:
                return
            self._timings.append(
                LegTiming(
                    name=name,
                    start_time=start_time,
                    end_time=end_time,
                )
            )
            if progress_callback is not None:
                progress_callback(
                    {
                        "segment_type": self._segment_type,
                        "sortie_index": self._sortie_index,
                        "drone_id": drone_id,
                        "leg_name": str(name),
                    }
                )

    try:
        battery_logger.start(mission_task=asyncio.current_task())
        for i, sortie_wp in enumerate(sortie_waypoints):
            launch_gps = sortie_wp["launch"]
            delivery_gps = sortie_wp["delivery"]
            rendezvous_gps = sortie_wp["rendezvous"]
            delivery_time_s = max(
                0.0,
                float(sortie_wp.get("delivery_time_s", default_delivery_time_s)),
            )
            log.info(
                "%s Starting sortie %d/%d: launch=%s delivery=%s rendezvous=%s delivery_time=%.1fs",
                drone_prefix,
                i + 1,
                len(sortie_waypoints),
                launch_gps,
                delivery_gps,
                rendezvous_gps,
                delivery_time_s,
            )

            # ------------------------------------------------------------------ #
            # SORTIE SEGMENT
            # ------------------------------------------------------------------ #
            positions: list = []
            battery_samples: list = []
            leg_timer = _LegTimer(segment_type="sortie", sortie_index=i)

            try:
                if truck_coordinator is not None:
                    await truck_coordinator.wait_for_truck_arrival(sortie_wp.get("launch_visit"))
                    if i == 0:
                        log.info("%s Initial launch-ready at experiment start", drone_prefix)
                    else:
                        log.info(
                            "%s Ready to launch at visit %s",
                            drone_prefix,
                            sortie_wp.get("launch_visit"),
                        )
                    await truck_coordinator.wait_for_launch_clearance(
                        sortie_wp.get("launch_visit"),
                        drone_id,
                    )
                    if i == 0:
                        log.info("%s Initial launch clearance granted", drone_prefix)
                    else:
                        log.info(
                            "%s Launch clearance granted at visit %s",
                            drone_prefix,
                            sortie_wp.get("launch_visit"),
                        )
                sortie_start = time.time() - mission_start
                battery_start = await _battery_safe(f"sortie_{i}_start")
                # 1. Arm and take off at launch node
                log.info(
                    "%s Sortie %d leg 1/7: arm + takeoff at launch (distance=%.2fm vertical)",
                    drone_prefix,
                    i + 1,
                    drone_altitude,
                )
                launch_leg_start = time.monotonic()
                await leg_timer.start("launch_prep")
                await _ensure_min_duration(launch_leg_start, launch_time_s)
                await leg_timer.stop()
                if truck_coordinator is not None:
                    await truck_coordinator.notify_launch(sortie_wp.get("launch_visit"))
                await leg_timer.start("launch_takeoff")
                cruise_abs_alt = await vehicle.arm_and_takeoff(drone_altitude, tolerance)
                await leg_timer.stop()
                _start_collector(positions, battery_samples)

                # 2. Fly to delivery node
                log.info(
                    "%s Sortie %d leg 2/7: transit to delivery (distance=%.2fm)",
                    drone_prefix,
                    i + 1,
                    _gps_distance_m(launch_gps, delivery_gps),
                )
                await leg_timer.start("outbound")
                if not await vehicle.goto_waypoint(
                    delivery_gps[0], delivery_gps[1], cruise_abs_alt,
                    tolerance,
                    speed_m_s=cruise_speed_m_s,
                ):
                    raise WaypointTimeoutError(
                        f"Sortie {i}: timed out reaching delivery node"
                )
                await leg_timer.stop()

                home_abs_alt = cruise_abs_alt - drone_altitude
                delivery_abs_alt = home_abs_alt + 3.0

                # 3. Descend to customer handoff altitude
                log.info(
                    "%s Sortie %d leg 3/7: descend to customer handoff altitude (distance=%.2fm vertical)",
                    drone_prefix,
                    i + 1,
                    abs(cruise_abs_alt - delivery_abs_alt),
                )
                await leg_timer.start("delivery_land")
                await vehicle.goto_waypoint(
                    delivery_gps[0], delivery_gps[1], delivery_abs_alt,
                    tolerance,
                    speed_m_s=cruise_speed_m_s,
                )
                await leg_timer.stop()

                # 4. Hold at customer handoff altitude to simulate delivery
                log.info(
                    "%s Sortie %d leg 4/7: make delivery at customer handoff altitude (duration=%.2fs)",
                    drone_prefix,
                    i + 1,
                    delivery_time_s,
                )
                await leg_timer.start("delivery")
                await vehicle.hold_position(
                    delivery_gps[0], delivery_gps[1], delivery_abs_alt,
                    duration_s=delivery_time_s,
                )
                await leg_timer.stop()

                # 5. Climb back to cruise altitude
                log.info(
                    "%s Sortie %d leg 5/7: climb back to cruise altitude (distance=%.2fm vertical)",
                    drone_prefix,
                    i + 1,
                    abs(cruise_abs_alt - delivery_abs_alt),
                )
                await leg_timer.start("delivery_takeoff")
                await vehicle.goto_waypoint(
                    delivery_gps[0], delivery_gps[1], cruise_abs_alt,
                    tolerance,
                    speed_m_s=cruise_speed_m_s,
                )
                await leg_timer.stop()

                # 6. Fly to rendezvous node
                log.info(
                    "%s Sortie %d leg 6/7: transit to rendezvous (distance=%.2fm)",
                    drone_prefix,
                    i + 1,
                    _gps_distance_m(delivery_gps, rendezvous_gps),
                )
                await leg_timer.start("return")
                if not await vehicle.goto_waypoint(
                    rendezvous_gps[0], rendezvous_gps[1], cruise_abs_alt,
                    tolerance,
                    speed_m_s=cruise_speed_m_s,
                ):
                    raise WaypointTimeoutError(
                        f"Sortie {i}: timed out reaching rendezvous node"
                )
                await leg_timer.stop()

                rendezvous_visit = sortie_wp.get("rendezvous_visit")
                if truck_coordinator is not None and rendezvous_visit is not None:
                    wait_start = time.time() - mission_start
                    await truck_coordinator.wait_for_recovery_clearance(rendezvous_visit, drone_id)
                    wait_end = time.time() - mission_start
                    leg_timer.add_completed("waiting", wait_start, wait_end)

                # 7. Land and disarm at rendezvous for collection
                log.info(
                    "%s Sortie %d leg 7/7: collection at rendezvous (distance=%.2fm vertical)",
                    drone_prefix,
                    i + 1,
                    drone_altitude,
                )
                await leg_timer.start("recovery_land")
                await vehicle.land_and_disarm()
                await leg_timer.stop()
                recovery_leg_start = time.monotonic()
                await leg_timer.start("recovery")
                await _ensure_min_duration(recovery_leg_start, recovery_time_s)
                await leg_timer.stop()
                if truck_coordinator is not None:
                    await truck_coordinator.notify_rendezvous(rendezvous_visit)

            finally:
                # Always stop the collector so the task is not orphaned
                await leg_timer.stop()
                collector.stop()

            battery_end = await _battery_safe(f"sortie_{i}_end")
            sortie_end = time.time() - mission_start

            segments.append(SegmentLog(
                segment_type="sortie",
                sortie_index=i,
                start_time=sortie_start,
                end_time=sortie_end,
                positions=positions,
                battery_samples=battery_samples,
                battery_at_start=battery_start,
                battery_at_end=battery_end,
                leg_timings=leg_timer.timings,
                leg_energy_samples=leg_timer.energy_samples,
            ))
            log.info("%s Completed sortie %d/%d", drone_prefix, i + 1, len(sortie_waypoints))

            # ------------------------------------------------------------------ #
            # REPOSITIONING SEGMENT (between sorties)
            # ------------------------------------------------------------------ #
            if i < len(sortie_waypoints) - 1:
                next_launch_gps = sortie_waypoints[i + 1]["launch"]
                rendezvous_node = sortie_wp.get("rendezvous_node")
                next_launch_node = sortie_waypoints[i + 1].get("launch_node")
                if rendezvous_node is not None and next_launch_node is not None:
                    if int(rendezvous_node) == int(next_launch_node):
                        log.info(
                            "%s Skipping reposition %d/%d: route plan has same node (%s)",
                            drone_prefix,
                            i + 1,
                            len(sortie_waypoints) - 1,
                            rendezvous_node,
                        )
                        continue
                else:
                    # Backward compatibility for direct fly_mission() waypoint calls.
                    reposition_distance_m = geo.haversine_distance(
                        rendezvous_gps[0], rendezvous_gps[1], next_launch_gps[0], next_launch_gps[1]
                    )
                    if reposition_distance_m <= max(0.5, tolerance):
                        log.info(
                            "%s Skipping reposition %d/%d: already at next launch (distance=%.2fm)",
                            drone_prefix,
                            i + 1,
                            len(sortie_waypoints) - 1,
                            reposition_distance_m,
                        )
                        continue
                reposition_start = time.time() - mission_start
                battery_repo_start = await _battery_safe(f"reposition_{i}_start")
                repo_battery_samples: list = []
                repo_leg_timer = _LegTimer(segment_type="reposition", sortie_index=None)
                log.info(
                    "%s Starting reposition %d/%d: from rendezvous to next launch %s",
                    drone_prefix,
                    i + 1,
                    len(sortie_waypoints) - 1,
                    next_launch_gps,
                )

                _start_collector([], repo_battery_samples)
                try:
                    # 1. Arm and take off from rendezvous
                    log.info(
                        "%s Reposition %d leg 1/3: arm + takeoff (distance=%.2fm vertical)",
                        drone_prefix,
                        i + 1,
                        drone_altitude,
                    )
                    launch_leg_start = time.monotonic()
                    await repo_leg_timer.start("reposition_launch_takeoff")
                    cruise_abs_alt = await vehicle.arm_and_takeoff(drone_altitude, tolerance)
                    await _ensure_min_duration(launch_leg_start, launch_time_s)
                    await repo_leg_timer.stop()

                    # 2. Fly to next launch node
                    log.info(
                        "%s Reposition %d leg 2/3: transit to next launch (distance=%.2fm)",
                        drone_prefix,
                        i + 1,
                        _gps_distance_m(rendezvous_gps, next_launch_gps),
                    )
                    await repo_leg_timer.start("reposition_transit")
                    if not await vehicle.goto_waypoint(
                        next_launch_gps[0], next_launch_gps[1], cruise_abs_alt,
                        tolerance,
                        speed_m_s=cruise_speed_m_s,
                    ):
                        raise WaypointTimeoutError(
                            f"Reposition after sortie {i}: timed out reaching next launch node"
                        )
                    await repo_leg_timer.stop()

                    # 3. Land at next launch node
                    log.info(
                        "%s Reposition %d leg 3/3: land at next launch (distance=%.2fm vertical)",
                        drone_prefix,
                        i + 1,
                        drone_altitude,
                    )
                    recovery_leg_start = time.monotonic()
                    await repo_leg_timer.start("reposition_recovery_land")
                    await vehicle.land_and_disarm()
                    await _ensure_min_duration(recovery_leg_start, recovery_time_s)
                    await repo_leg_timer.stop()
                finally:
                    await repo_leg_timer.stop()
                    collector.stop()

                battery_repo_end = await _battery_safe(f"reposition_{i}_end")
                reposition_end = time.time() - mission_start

                segments.append(SegmentLog(
                    segment_type="reposition",
                    sortie_index=None,
                    start_time=reposition_start,
                    end_time=reposition_end,
                    positions=[],  # not used in analysis
                    battery_samples=repo_battery_samples,
                    battery_at_start=battery_repo_start,
                    battery_at_end=battery_repo_end,
                    leg_timings=repo_leg_timer.timings,
                    leg_energy_samples=repo_leg_timer.energy_samples,
                ))
                log.info("%s Completed reposition %d/%d", drone_prefix, i + 1, len(sortie_waypoints) - 1)

        return MissionLog(
            segments=segments,
            total_time=time.time() - mission_start,
            ulog_path=None,  # set by runner after container stops
        )
    except asyncio.CancelledError as exc:
        depletion_message = battery_logger.depletion_message
        if depletion_message is not None:
            raise MissionAbortedError(depletion_message) from exc
        raise
    finally:
        await battery_logger.stop()
        current_task = asyncio.current_task()
        if (
            current_task is not None
            and battery_logger.depletion_message is not None
            and current_task.cancelling()
        ):
            current_task.uncancel()
            raise MissionAbortedError(battery_logger.depletion_message)


async def fly_mission(
    drones: System | dict[int, System],
    sorties: list[dict],
    altitude: float = 20.0,
    tolerance: float = 1.0,
    reference_gps: Optional[tuple[float, float]] = None,
    cruise_speed_m_s: Optional[float] = None,
    truck_route_gps: Optional[list[tuple[float, float]]] = None,
    truck_speed_m_s: Optional[float] = None,
    truck_leg_travel_times: Optional[list[float]] = None,
    planned_truck_timeline: Optional[list[TruckTimingSegment]] = None,
    altitude_deconfliction_m: float = 0.0,
    launch_time_s: float = 0.0,
    recovery_time_s: float = 0.0,
    default_delivery_time_s: float = 60.0,
    progress_callback: Optional[Callable[[dict[str, object]], None]] = None,
) -> MissionLog | dict[int, MissionLog]:
    """Fly one or more drone mission plans.

    Backwards-compatible single-drone usage:
        ``fly_mission(drone, [{"launch": ..., "delivery": ..., "rendezvous": ...}])``

    Multi-drone usage:
        ``fly_mission({0: drone0, 1: drone1}, [{"drone_id": 0, ...}, {"drone_id": 1, ...}])``
    """
    if isinstance(drones, System):
        return await _fly_single_mission(
            drones,
            sorties,
            altitude=altitude,
            tolerance=tolerance,
            reference_gps=reference_gps,
            cruise_speed_m_s=cruise_speed_m_s,
            drone_id=0,
            altitude_deconfliction_m=altitude_deconfliction_m,
            launch_time_s=launch_time_s,
            recovery_time_s=recovery_time_s,
            default_delivery_time_s=default_delivery_time_s,
            progress_callback=progress_callback,
        )

    sortie_waypoints_by_drone: dict[int, list[dict]] = {drone_id: [] for drone_id in drones}
    for sortie in sorties:
        drone_id = int(sortie["drone_id"])
        if drone_id not in drones:
            raise MissionAbortedError(f"Sortie assigned to unknown drone_id={drone_id}")
        sortie_waypoints_by_drone[drone_id].append(sortie)

    truck_coordinator = None
    if truck_route_gps is not None and truck_speed_m_s is not None:
        truck_coordinator = _TruckCoordinator(
            truck_route_gps=truck_route_gps,
            truck_speed_m_s=truck_speed_m_s,
            sorties=sorties,
            truck_leg_travel_times=truck_leg_travel_times,
            planned_truck_timeline=planned_truck_timeline,
        )
    movement_watchdog = (
        _MovementWatchdog()
        if len(drones) >= 2
        else None
    )

    try:
        if movement_watchdog is not None:
            movement_watchdog.start(mission_task=asyncio.current_task())
        mission_logs = await asyncio.gather(
            *(
                _fly_single_mission(
                    drones[drone_id],
                    sortie_waypoints_by_drone[drone_id],
                    altitude=altitude,
                    tolerance=tolerance,
                    reference_gps=reference_gps,
                    cruise_speed_m_s=cruise_speed_m_s,
                    drone_id=drone_id,
                    truck_coordinator=truck_coordinator,
                    altitude_deconfliction_m=altitude_deconfliction_m,
                    launch_time_s=launch_time_s,
                    recovery_time_s=recovery_time_s,
                    default_delivery_time_s=default_delivery_time_s,
                    progress_callback=progress_callback,
                    movement_watchdog=movement_watchdog,
                )
                for drone_id in sorted(drones)
            )
        )
        return {
            drone_id: mission_log
            for drone_id, mission_log in zip(sorted(drones), mission_logs)
        }
    except asyncio.CancelledError as exc:
        if movement_watchdog is not None and movement_watchdog.stall_message is not None:
            raise MissionAbortedError(movement_watchdog.stall_message) from exc
        raise
    finally:
        if movement_watchdog is not None:
            await movement_watchdog.stop()
            current_task = asyncio.current_task()
            if (
                current_task is not None
                and movement_watchdog.stall_message is not None
                and current_task.cancelling()
            ):
                current_task.uncancel()
                raise MissionAbortedError(movement_watchdog.stall_message)


# ---------------------------------------------------------------------------
# Low-level flight primitives
# ---------------------------------------------------------------------------


async def goto_waypoint(
    drone: System,
    latitude_deg: float,
    longitude_deg: float,
    absolute_altitude_m: float,
    tolerance: float = 1.0,
    yaw: float = 0.0,
    timeout: Optional[float] = None,
    speed_m_s: Optional[float] = None,
) -> bool:
    """Compatibility wrapper; prefer :class:`dronevalkit.drone.Drone`."""
    return await _drone_adapter(drone).goto_waypoint(
        latitude_deg,
        longitude_deg,
        absolute_altitude_m,
        tolerance=tolerance,
        yaw=yaw,
        timeout=timeout,
        speed_m_s=speed_m_s,
    )


async def hold_position(
    drone: System,
    latitude_deg: float,
    longitude_deg: float,
    absolute_altitude_m: float,
    duration_s: float,
    yaw: float = 0.0,
) -> None:
    """Compatibility wrapper; prefer :class:`dronevalkit.drone.Drone`."""
    await _drone_adapter(drone).hold_position(
        latitude_deg,
        longitude_deg,
        absolute_altitude_m,
        duration_s,
        yaw=yaw,
    )


async def get_position_gps(drone: System) -> tuple[float, float, float, float]:
    """Compatibility wrapper; prefer :class:`dronevalkit.drone.Drone`."""
    return await _drone_adapter(drone).get_position_gps()


async def arm_and_takeoff(
    drone: System,
    relative_altitude_m: float,
    _tolerance: float,
    armable_wait_timeout: float = 180.0,
) -> float:
    """Compatibility wrapper; prefer :class:`dronevalkit.drone.Drone`."""
    return await _drone_adapter(drone).arm_and_takeoff(
        relative_altitude_m,
        _tolerance,
        armable_wait_timeout=armable_wait_timeout,
    )


async def land_and_disarm(drone: System) -> None:
    """Compatibility wrapper; prefer :class:`dronevalkit.drone.Drone`."""
    await _drone_adapter(drone).land_and_disarm()


async def get_battery_pct(drone: System) -> float:
    """Compatibility wrapper; prefer :class:`dronevalkit.drone.Drone`."""
    return await _drone_adapter(drone).get_battery_pct()


async def log_prearm_diagnostics(drone: System, target_altitude_m: float) -> None:
    """Compatibility wrapper; prefer :class:`dronevalkit.drone.Drone`."""
    await _drone_adapter(drone).log_prearm_diagnostics(target_altitude_m)


def _drone_adapter(drone: System) -> Drone:
    adapter = getattr(drone, "_dronevalkit_adapter", None)
    if isinstance(adapter, Drone):
        return adapter
    adapter = Drone(drone)
    try:
        setattr(drone, "_dronevalkit_adapter", adapter)
    except Exception:
        pass
    return adapter
