"""Tests for dronevalkit.flight."""

import asyncio
import sys
import types

import grpc
import pytest

sys.modules.setdefault("mavsdk", types.SimpleNamespace(System=object))

from dronevalkit.config import CustomBattery, WindCondition
from dronevalkit.drone import Drone
from dronevalkit.exceptions import MissionAbortedError, WaypointTimeoutError
from dronevalkit.models import TruckTimingSegment, VehicleSpeeds

from dronevalkit.flight import (
    _TelemetryCollector,
    _MovementWatchdog,
    _TruckCoordinator,
    _ensure_min_duration,
    _fly_single_mission,
    configure_for_experiment,
    fly_mission,
    get_battery_pct,
)


class _FakeParam:
    def __init__(self) -> None:
        self.int_params = {}
        self.float_params = {}

    async def set_param_int(self, name: str, value: int) -> None:
        self.int_params[name] = value

    async def set_param_float(self, name: str, value: float) -> None:
        self.float_params[name] = value

    async def get_param_float(self, name: str) -> float:
        return self.float_params[name]


class _FakeDrone:
    def __init__(self) -> None:
        self.param = _FakeParam()


def test_configure_for_experiment_scales_vertical_speed_with_speed_factor(monkeypatch):
    async def _fake_battery_pct(_drone):
        return 100.0

    monkeypatch.setattr("dronevalkit.flight.get_battery_pct", _fake_battery_pct)

    drone = _FakeDrone()
    battery = CustomBattery(drain_rate=1.25)
    wind = WindCondition.calm()

    asyncio.run(
        configure_for_experiment(
            drone,
            battery,
            wind,
            speed_factor=2.0,
        )
    )

    assert drone.param.float_params["MPC_XY_CRUISE"] == pytest.approx(10.0)
    assert drone.param.float_params["MPC_XY_VEL_MAX"] == pytest.approx(24.0)
    # 2x average-to-max correction: base 3.0 * factor 2.0 * 2.0 = 12.0, capped at 8.0
    assert drone.param.float_params["MPC_Z_VEL_MAX_UP"] == pytest.approx(8.0)
    # base 1.5 * factor 2.0 * 2.0 = 6.0, capped at 4.0
    assert drone.param.float_params["MPC_Z_VEL_MAX_DN"] == pytest.approx(4.0)
    assert drone.param.float_params["MPC_LAND_SPEED"] == pytest.approx(4.0)


def test_configure_for_experiment_uses_vehicle_speed_profile(monkeypatch):
    async def _fake_battery_pct(_drone):
        return 100.0

    monkeypatch.setattr("dronevalkit.flight.get_battery_pct", _fake_battery_pct)

    drone = _FakeDrone()
    battery = CustomBattery(drain_rate=1.25)
    wind = WindCondition.calm()

    asyncio.run(
        configure_for_experiment(
            drone,
            battery,
            wind,
            speed_factor=1.0,
            vehicle_speeds=VehicleSpeeds(
                takeoff=7.8232,
                cruise=15.6464,
                landing=3.9116,
                yaw_rate_deg=360.0,
            ),
        )
    )

    assert drone.param.float_params["MPC_XY_CRUISE"] == pytest.approx(15.6464)
    assert drone.param.float_params["MPC_XY_VEL_MAX"] == pytest.approx(15.6464)
    # 2x average-to-max correction: 7.8232 * 1.0 * 2.0 = 15.6464, capped at 8.0
    assert drone.param.float_params["MPC_Z_VEL_MAX_UP"] == pytest.approx(8.0)
    # 3.9116 * 1.0 * 2.0 = 7.8232, capped at 4.0
    assert drone.param.float_params["MPC_Z_VEL_MAX_DN"] == pytest.approx(4.0)
    assert drone.param.float_params["MPC_LAND_SPEED"] == pytest.approx(4.0)
    assert drone.param.float_params["MPC_YAWRAUTO_MAX"] == pytest.approx(360.0)


def test_configure_for_experiment_does_not_set_sih_wind_params(monkeypatch):
    async def _fake_battery_pct(_drone):
        return 100.0

    monkeypatch.setattr("dronevalkit.flight.get_battery_pct", _fake_battery_pct)

    drone = _FakeDrone()
    battery = CustomBattery(drain_rate=1.25)
    wind = WindCondition.moderate(speed=5.0, direction=30.0)

    asyncio.run(
        configure_for_experiment(
            drone,
            battery,
            wind,
            speed_factor=1.0,
        )
    )

    assert "SIH_WIND_N" not in drone.param.float_params
    assert "SIH_WIND_E" not in drone.param.float_params


def test_truck_coordinator_waits_for_actual_rendezvous_before_next_arrival():
    async def _run() -> None:
        coordinator = _TruckCoordinator(
            truck_route_gps=[
                (38.898, -77.036),
                (38.898, -77.036),
                (38.898, -77.036),
            ],
            truck_speed_m_s=8.33,
            sorties=[
                {"launch_visit": 0, "rendezvous_visit": 1},
                {"launch_visit": 2, "rendezvous_visit": 2},
            ],
        )

        await coordinator.notify_launch(0)
        await coordinator.wait_for_truck_arrival(1)

        wait_next_visit = asyncio.create_task(coordinator.wait_for_truck_arrival(2))
        await asyncio.sleep(0.01)
        assert not wait_next_visit.done()

        await coordinator.notify_rendezvous(1)
        await asyncio.wait_for(wait_next_visit, timeout=0.25)

    asyncio.run(_run())


def test_truck_coordinator_uses_planned_timeline_but_slips_when_drone_is_late():
    async def _run() -> None:
        coordinator = _TruckCoordinator(
            truck_route_gps=[
                (38.898, -77.036),
                (38.898, -77.036),
                (38.898, -77.036),
            ],
            truck_speed_m_s=8.33,
            sorties=[
                {"launch_visit": 0, "rendezvous_visit": 1},
            ],
            planned_truck_timeline=[
                TruckTimingSegment(
                    kind="dwell",
                    start_time=0.0,
                    end_time=0.02,
                    start_node=0,
                    end_node=0,
                    label="Launch",
                ),
                TruckTimingSegment(
                    kind="move",
                    start_time=0.02,
                    end_time=0.03,
                    start_node=0,
                    end_node=1,
                    label="Travel 0->1",
                ),
                TruckTimingSegment(
                    kind="dwell",
                    start_time=0.03,
                    end_time=0.05,
                    start_node=1,
                    end_node=1,
                    label="Service",
                ),
                TruckTimingSegment(
                    kind="move",
                    start_time=0.05,
                    end_time=0.06,
                    start_node=1,
                    end_node=2,
                    label="Travel 1->2",
                ),
            ],
        )

        await coordinator.notify_launch(0)
        await coordinator.wait_for_truck_arrival(1)

        wait_next_visit = asyncio.create_task(coordinator.wait_for_truck_arrival(2))
        await asyncio.sleep(0.07)
        assert not wait_next_visit.done()

        await coordinator.notify_rendezvous(1)
        await asyncio.wait_for(wait_next_visit, timeout=0.2)

    asyncio.run(_run())


def test_truck_coordinator_does_not_wait_for_planned_dwell_once_ready():
    async def _run() -> None:
        coordinator = _TruckCoordinator(
            truck_route_gps=[
                (38.898, -77.036),
                (38.898, -77.036),
                (38.898, -77.036),
            ],
            truck_speed_m_s=8.33,
            sorties=[
                {"launch_visit": 0, "rendezvous_visit": 1},
                {"launch_visit": 1, "rendezvous_visit": 2},
            ],
            planned_truck_timeline=[
                TruckTimingSegment(
                    kind="dwell",
                    start_time=0.0,
                    end_time=0.02,
                    start_node=0,
                    end_node=0,
                    label="Launching UAV 1",
                    drone_id=0,
                ),
                TruckTimingSegment(
                    kind="move",
                    start_time=0.02,
                    end_time=0.03,
                    start_node=0,
                    end_node=1,
                    label="Travel 0->1",
                ),
                TruckTimingSegment(
                    kind="dwell",
                    start_time=0.03,
                    end_time=1.00,
                    start_node=1,
                    end_node=1,
                    label="Retrieving UAV 1",
                ),
                TruckTimingSegment(
                    kind="move",
                    start_time=1.00,
                    end_time=1.01,
                    start_node=1,
                    end_node=2,
                    label="Travel 1->2",
                ),
            ],
        )

        await coordinator.notify_launch(0)
        await coordinator.wait_for_truck_arrival(1)

        wait_next_visit = asyncio.create_task(coordinator.wait_for_truck_arrival(2))
        await asyncio.sleep(0.05)
        assert not wait_next_visit.done()

        await coordinator.notify_launch(1)
        await coordinator.notify_rendezvous(1)
        await asyncio.wait_for(wait_next_visit, timeout=0.1)

    asyncio.run(_run())


def test_drone_goto_waypoint_uses_wind_tolerant_distance_scaled_timeout(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.calls = []

        async def goto_location(
            self,
            latitude_deg: float,
            longitude_deg: float,
            altitude_m: float,
            yaw: float,
        ) -> None:
            self.calls.append(("goto_location", latitude_deg, longitude_deg, altitude_m, yaw))

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    async def _fake_wait_for(awaitable, timeout=None):
        return await awaitable

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)
    monkeypatch.setattr("dronevalkit.drone.asyncio.wait_for", _fake_wait_for)
    monkeypatch.setattr("dronevalkit.drone.geo.haversine_distance", lambda *_args: 1778.0)

    drone = Drone(_FakeSystem(), drone_id=8)

    async def _fake_get_position_gps():
        return (0.0, 0.0, 0.0, 0.0)

    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    result = asyncio.run(
        drone.goto_waypoint(
            latitude_deg=1.0,
            longitude_deg=1.0,
            absolute_altitude_m=0.0,
            tolerance=1.0,
        )
    )

    assert result is False
    assert clock.now == pytest.approx(1422.5, abs=0.5)


def test_drone_goto_waypoint_extends_timeout_while_making_progress(monkeypatch):
    class _FakeAction:
        async def goto_location(
            self,
            latitude_deg: float,
            longitude_deg: float,
            altitude_m: float,
            yaw: float,
        ) -> None:
            pass

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += 100.0

    distances = iter([1000.0, 1000.0, 900.0, 800.0, 700.0, 600.0, 500.0, 400.0, 300.0, 200.0, 100.0, 0.0])

    async def _fake_wait_for(awaitable, timeout=None):
        return await awaitable

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)
    monkeypatch.setattr("dronevalkit.drone.asyncio.wait_for", _fake_wait_for)
    monkeypatch.setattr("dronevalkit.drone.geo.haversine_distance", lambda *_args: next(distances))

    drone = Drone(_FakeSystem(), drone_id=8)

    async def _fake_get_position_gps():
        return (0.0, 0.0, 0.0, 0.0)

    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    result = asyncio.run(
        drone.goto_waypoint(
            latitude_deg=1.0,
            longitude_deg=1.0,
            absolute_altitude_m=0.0,
            tolerance=1.0,
        )
    )

    assert result is True
    assert clock.now > 800.0


def test_truck_coordinator_serializes_launches_in_planned_order():
    async def _run() -> None:
        coordinator = _TruckCoordinator(
            truck_route_gps=[
                (38.898, -77.036),
                (38.898, -77.036),
            ],
            truck_speed_m_s=8.33,
            sorties=[
                {"launch_visit": 0, "rendezvous_visit": 1},
                {"launch_visit": 0, "rendezvous_visit": 1},
            ],
            planned_truck_timeline=[
                TruckTimingSegment(
                    kind="dwell",
                    start_time=0.0,
                    end_time=0.02,
                    start_node=0,
                    end_node=0,
                    label="Launching UAV 4",
                    drone_id=2,
                ),
                TruckTimingSegment(
                    kind="dwell",
                    start_time=0.02,
                    end_time=0.04,
                    start_node=0,
                    end_node=0,
                    label="Launching UAV 3",
                    drone_id=1,
                ),
                TruckTimingSegment(
                    kind="move",
                    start_time=0.04,
                    end_time=0.05,
                    start_node=0,
                    end_node=1,
                    label="Travel 0->1",
                ),
            ],
        )

        await coordinator.wait_for_launch_clearance(0, 2)

        blocked = asyncio.create_task(coordinator.wait_for_launch_clearance(0, 1))
        await asyncio.sleep(0.01)
        assert not blocked.done()

        await coordinator.notify_launch(0)
        await asyncio.wait_for(blocked, timeout=0.1)

    asyncio.run(_run())


def test_truck_coordinator_serializes_recoveries_in_planned_order():
    async def _run() -> None:
        coordinator = _TruckCoordinator(
            truck_route_gps=[
                (38.898, -77.036),
                (38.898, -77.036),
            ],
            truck_speed_m_s=8.33,
            sorties=[
                {"launch_visit": 0, "rendezvous_visit": 1},
                {"launch_visit": 0, "rendezvous_visit": 1},
            ],
            planned_truck_timeline=[
                TruckTimingSegment(
                    kind="move",
                    start_time=0.0,
                    end_time=0.01,
                    start_node=0,
                    end_node=1,
                    label="Travel 0->1",
                ),
                TruckTimingSegment(
                    kind="dwell",
                    start_time=0.01,
                    end_time=0.02,
                    start_node=1,
                    end_node=1,
                    label="Retrieving UAV 4",
                    drone_id=2,
                ),
                TruckTimingSegment(
                    kind="dwell",
                    start_time=0.02,
                    end_time=0.03,
                    start_node=1,
                    end_node=1,
                    label="Dropping off package",
                ),
                TruckTimingSegment(
                    kind="dwell",
                    start_time=0.03,
                    end_time=0.04,
                    start_node=1,
                    end_node=1,
                    label="Retrieving UAV 3",
                    drone_id=1,
                ),
            ],
        )

        await coordinator.notify_launch(0)
        await coordinator.notify_launch(0)
        await coordinator.wait_for_truck_arrival(1)
        await coordinator.wait_for_recovery_clearance(1, 2)

        blocked = asyncio.create_task(coordinator.wait_for_recovery_clearance(1, 1))
        await asyncio.sleep(0.015)
        assert not blocked.done()

        await coordinator.notify_rendezvous(1)
        await asyncio.wait_for(blocked, timeout=0.1)

    asyncio.run(_run())


def test_ensure_min_duration_only_waits_remaining_time(monkeypatch):
    async def _run() -> float:
        start = asyncio.get_running_loop().time()
        await _ensure_min_duration(start_monotonic=start, min_duration_s=0.02)
        return asyncio.get_running_loop().time() - start

    elapsed = asyncio.run(_run())

    assert elapsed >= 0.018


def test_ensure_min_duration_skips_sleep_when_already_satisfied(monkeypatch):
    async def _run() -> float:
        loop = asyncio.get_running_loop()
        before = loop.time()
        await _ensure_min_duration(start_monotonic=before - 0.02, min_duration_s=0.01)
        return loop.time() - before

    elapsed = asyncio.run(_run())

    assert elapsed < 0.01


def test_single_mission_waits_above_rendezvous_until_truck_arrives(monkeypatch):
    class _FakeTelemetryCollector:
        def __init__(self, *_args, **_kwargs) -> None:
            self.started = False

        def start(self, _positions: list) -> None:
            self.started = True

        def stop(self) -> None:
            self.started = False

    class _FakeVehicle:
        instances = []

        def __init__(self, _system, drone_id=None) -> None:
            self.drone_id = drone_id
            self.calls = []
            _FakeVehicle.instances.append(self)

        async def get_battery_pct(self) -> float:
            return 100.0

        async def arm_and_takeoff(self, relative_altitude_m: float, _tolerance: float) -> float:
            self.calls.append(("arm_and_takeoff", relative_altitude_m))
            return 120.0

        async def goto_waypoint(
            self,
            latitude_deg: float,
            longitude_deg: float,
            absolute_altitude_m: float,
            tolerance: float = 1.0,
            speed_m_s=None,
        ) -> bool:
            self.calls.append(
                ("goto_waypoint", latitude_deg, longitude_deg, absolute_altitude_m, tolerance, speed_m_s)
            )
            return True

        async def hold_position(
            self,
            latitude_deg: float,
            longitude_deg: float,
            absolute_altitude_m: float,
            duration_s: float,
        ) -> None:
            self.calls.append(("hold_position", latitude_deg, longitude_deg, absolute_altitude_m, duration_s))

        async def land_and_disarm(self) -> None:
            self.calls.append(("land_and_disarm",))

    class _FakeTruckCoordinator:
        def __init__(self) -> None:
            self.arrival_calls = []
            self.recovery_clearance_calls = []
            self.launch_notifications = []
            self.rendezvous_notifications = []
            self._rendezvous_arrived = asyncio.Event()

        async def wait_for_truck_arrival(self, visit_index):
            self.arrival_calls.append(visit_index)
            if visit_index == 1:
                await self._rendezvous_arrived.wait()

        async def wait_for_launch_clearance(self, visit_index, drone_id):
            return None

        async def wait_for_recovery_clearance(self, visit_index, drone_id):
            self.recovery_clearance_calls.append((visit_index, drone_id))
            await self.wait_for_truck_arrival(visit_index)

        async def notify_launch(self, visit_index):
            self.launch_notifications.append(visit_index)

        async def notify_rendezvous(self, visit_index):
            self.rendezvous_notifications.append(visit_index)

    monkeypatch.setattr("dronevalkit.flight._TelemetryCollector", _FakeTelemetryCollector)
    monkeypatch.setattr("dronevalkit.flight.Drone", _FakeVehicle)

    async def _run():
        coordinator = _FakeTruckCoordinator()
        mission_task = asyncio.create_task(
            _fly_single_mission(
                drone=object(),
                sortie_waypoints=[
                    {
                        "launch": (38.898, -77.036),
                        "delivery": (38.899, -77.035),
                        "rendezvous": (38.900, -77.034),
                        "launch_visit": 0,
                        "rendezvous_visit": 1,
                    }
                ],
                altitude=20.0,
                tolerance=1.0,
                reference_gps=(38.898, -77.036),
                drone_id=0,
                truck_coordinator=coordinator,
                default_delivery_time_s=0.0,
            )
        )

        await asyncio.sleep(0.02)
        vehicle = _FakeVehicle.instances[0]
        assert ("land_and_disarm",) not in vehicle.calls

        coordinator._rendezvous_arrived.set()
        mission_log = await asyncio.wait_for(mission_task, timeout=0.25)
        return mission_log, coordinator, vehicle

    mission_log, coordinator, vehicle = asyncio.run(_run())

    assert coordinator.arrival_calls == [0, 1]
    assert coordinator.recovery_clearance_calls == [(1, 0)]
    assert coordinator.launch_notifications == [0]
    assert coordinator.rendezvous_notifications == [1]
    assert vehicle.calls[-1] == ("land_and_disarm",)

    leg_names = [leg.name for leg in mission_log.segments[0].leg_timings]
    assert leg_names == [
        "launch_prep",
        "launch_takeoff",
        "outbound",
        "delivery_land",
        "delivery",
        "delivery_takeoff",
        "return",
        "waiting",
        "recovery_land",
        "recovery",
    ]
    waiting_leg = next(leg for leg in mission_log.segments[0].leg_timings if leg.name == "waiting")
    collection_leg = next(leg for leg in mission_log.segments[0].leg_timings if leg.name == "recovery_land")
    assert waiting_leg.duration > 0.0
    assert collection_leg.start_time >= waiting_leg.end_time


def test_single_mission_notifies_launch_after_prep_before_takeoff_completes(monkeypatch):
    class _FakeTelemetryCollector:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start(self, _positions: list) -> None:
            return None

        def stop(self) -> None:
            return None

    class _FakeVehicle:
        instances = []

        def __init__(self, _system, drone_id=None) -> None:
            self.drone_id = drone_id
            self.takeoff_started = asyncio.Event()
            self.finish_takeoff = asyncio.Event()
            _FakeVehicle.instances.append(self)

        async def get_battery_pct(self) -> float:
            return 100.0

        async def arm_and_takeoff(self, relative_altitude_m: float, _tolerance: float) -> float:
            self.takeoff_started.set()
            await self.finish_takeoff.wait()
            return 120.0

        async def goto_waypoint(self, *args, **kwargs) -> bool:
            return True

        async def hold_position(self, *args, **kwargs) -> None:
            return None

        async def land_and_disarm(self) -> None:
            return None

    class _FakeTruckCoordinator:
        def __init__(self) -> None:
            self.launch_notifications = []

        async def wait_for_truck_arrival(self, visit_index):
            return None

        async def wait_for_launch_clearance(self, visit_index, drone_id):
            return None

        async def wait_for_recovery_clearance(self, visit_index, drone_id):
            return None

        async def notify_launch(self, visit_index):
            self.launch_notifications.append(visit_index)

        async def notify_rendezvous(self, visit_index):
            return None

    monkeypatch.setattr("dronevalkit.flight._TelemetryCollector", _FakeTelemetryCollector)
    monkeypatch.setattr("dronevalkit.flight.Drone", _FakeVehicle)

    async def _run():
        coordinator = _FakeTruckCoordinator()
        mission_task = asyncio.create_task(
            _fly_single_mission(
                drone=object(),
                sortie_waypoints=[
                    {
                        "launch": (38.898, -77.036),
                        "delivery": (38.899, -77.035),
                        "rendezvous": (38.900, -77.034),
                        "launch_visit": 0,
                        "rendezvous_visit": 1,
                    }
                ],
                altitude=20.0,
                tolerance=1.0,
                reference_gps=(38.898, -77.036),
                drone_id=0,
                truck_coordinator=coordinator,
                launch_time_s=0.0,
                recovery_time_s=0.0,
                default_delivery_time_s=0.0,
            )
        )

        await asyncio.sleep(0.02)
        vehicle = _FakeVehicle.instances[0]
        await asyncio.wait_for(vehicle.takeoff_started.wait(), timeout=0.1)
        assert coordinator.launch_notifications == [0]

        vehicle.finish_takeoff.set()
        await asyncio.wait_for(mission_task, timeout=0.25)

    asyncio.run(_run())


def test_single_mission_segment_start_excludes_launch_clearance_wait(monkeypatch):
    class _FakeTelemetryCollector:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start(self, _positions: list, _battery_samples=None) -> None:
            return None

        def stop(self) -> None:
            return None

    class _FakeVehicle:
        def __init__(self, _system, drone_id=None) -> None:
            self.drone_id = drone_id

        async def get_battery_pct(self) -> float:
            return 100.0

        async def arm_and_takeoff(self, relative_altitude_m: float, _tolerance: float) -> float:
            return 120.0

        async def goto_waypoint(self, *args, **kwargs) -> bool:
            return True

        async def hold_position(self, *args, **kwargs) -> None:
            return None

        async def land_and_disarm(self) -> None:
            return None

    class _DelayedCoordinator:
        def __init__(self) -> None:
            self.clearance_released = asyncio.Event()

        async def wait_for_truck_arrival(self, visit_index):
            return None

        async def wait_for_launch_clearance(self, visit_index, drone_id):
            await self.clearance_released.wait()

        async def wait_for_recovery_clearance(self, visit_index, drone_id):
            return None

        async def notify_launch(self, visit_index):
            return None

        async def notify_rendezvous(self, visit_index):
            return None

    monkeypatch.setattr("dronevalkit.flight._TelemetryCollector", _FakeTelemetryCollector)
    monkeypatch.setattr("dronevalkit.flight.Drone", _FakeVehicle)

    async def _run():
        coordinator = _DelayedCoordinator()
        mission_task = asyncio.create_task(
            _fly_single_mission(
                drone=object(),
                sortie_waypoints=[
                    {
                        "launch": (38.898, -77.036),
                        "delivery": (38.899, -77.035),
                        "rendezvous": (38.900, -77.034),
                        "launch_visit": 0,
                        "rendezvous_visit": 1,
                    }
                ],
                altitude=20.0,
                tolerance=1.0,
                reference_gps=(38.898, -77.036),
                drone_id=0,
                truck_coordinator=coordinator,
                launch_time_s=0.0,
                recovery_time_s=0.0,
                default_delivery_time_s=0.0,
            )
        )

        await asyncio.sleep(0.02)
        coordinator.clearance_released.set()
        return await asyncio.wait_for(mission_task, timeout=0.25)

    mission_log = asyncio.run(_run())

    assert mission_log.segments[0].start_time >= 0.018


def test_single_mission_aborts_when_battery_reaches_zero(monkeypatch):
    class _FakeTelemetryCollector:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start(self, _positions: list, _battery_samples=None) -> None:
            return None

        def stop(self) -> None:
            return None

    class _FakeVehicle:
        def __init__(self, _system, drone_id=None) -> None:
            self.drone_id = drone_id

        async def get_battery_pct(self) -> float:
            return 100.0

        async def arm_and_takeoff(self, relative_altitude_m: float, _tolerance: float) -> float:
            return 120.0

        async def goto_waypoint(self, *args, **kwargs) -> bool:
            return True

        async def hold_position(self, *args, **kwargs) -> None:
            return None

        async def land_and_disarm(self) -> None:
            return None

    class _FakeBatteryStatusLogger:
        def __init__(self, *_args, **_kwargs) -> None:
            self._depletion_message = None

        def start(self, *, mission_task=None) -> None:
            self._depletion_message = "[drone=0] Battery reached 0.0%; aborting mission"
            assert mission_task is not None
            mission_task.cancel()

        async def stop(self) -> None:
            return None

        @property
        def depletion_message(self):
            return self._depletion_message

    monkeypatch.setattr("dronevalkit.flight._TelemetryCollector", _FakeTelemetryCollector)
    monkeypatch.setattr("dronevalkit.flight.Drone", _FakeVehicle)
    monkeypatch.setattr("dronevalkit.flight._BatteryStatusLogger", _FakeBatteryStatusLogger)

    async def _run():
        await _fly_single_mission(
            drone=object(),
            sortie_waypoints=[
                {
                    "launch": (38.898, -77.036),
                    "delivery": (38.899, -77.035),
                    "rendezvous": (38.900, -77.034),
                }
            ],
            altitude=20.0,
            tolerance=1.0,
            reference_gps=(38.898, -77.036),
            drone_id=0,
            default_delivery_time_s=0.0,
        )

    with pytest.raises(MissionAbortedError, match="Battery reached 0.0%"):
        asyncio.run(_run())


def test_movement_watchdog_ignores_jitter_but_tracks_meaningful_motion(monkeypatch):
    now = {"value": 100.0}

    monkeypatch.setattr("dronevalkit.flight.time.monotonic", lambda: now["value"])

    watchdog = _MovementWatchdog(
        timeout_s=900.0,
        movement_threshold_m=2.0,
        poll_interval_s=1.0,
    )

    watchdog.observe_position(
        drone_id=0,
        latitude_deg=38.898000,
        longitude_deg=-77.036000,
        absolute_altitude_m=100.0,
    )
    assert watchdog._last_movement_monotonic == pytest.approx(100.0)

    now["value"] = 110.0
    watchdog.observe_position(
        drone_id=0,
        latitude_deg=38.898000,
        longitude_deg=-77.036000,
        absolute_altitude_m=101.0,
    )
    assert watchdog._last_movement_monotonic == pytest.approx(100.0)

    now["value"] = 120.0
    watchdog.observe_position(
        drone_id=0,
        latitude_deg=38.898000,
        longitude_deg=-77.036000,
        absolute_altitude_m=103.5,
    )
    assert watchdog._last_movement_monotonic == pytest.approx(120.0)


def test_fly_mission_aborts_when_multi_drone_episode_stalls(monkeypatch):
    class _FakeMovementWatchdog:
        def __init__(self, *args, **kwargs) -> None:
            self._stall_message = "No drone movement detected for 15 minutes; aborting mission"

        def start(self, *, mission_task=None) -> None:
            assert mission_task is not None

        async def stop(self) -> None:
            return None

        @property
        def stall_message(self):
            return self._stall_message

    async def _fake_gather(*awaitables):
        for awaitable in awaitables:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
        raise asyncio.CancelledError

    monkeypatch.setattr("dronevalkit.flight.System", type("FakeSystemType", (), {}))
    monkeypatch.setattr("dronevalkit.flight._MovementWatchdog", _FakeMovementWatchdog)
    monkeypatch.setattr("dronevalkit.flight.asyncio.gather", _fake_gather)

    async def _run():
        await fly_mission(
            drones={0: object(), 1: object()},
            sorties=[],
        )

    with pytest.raises(
        MissionAbortedError,
        match="No drone movement detected for 15 minutes; aborting mission",
    ):
        asyncio.run(_run())


def test_drone_arm_and_takeoff_retries_until_reaching_5m(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.calls = []

        async def set_takeoff_altitude(self, altitude: float) -> None:
            self.calls.append(("set_takeoff_altitude", altitude))

        async def arm(self) -> None:
            self.calls.append(("arm",))

        async def takeoff(self) -> None:
            self.calls.append(("takeoff",))

        async def hold(self) -> None:
            self.calls.append(("hold",))

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    altitudes = (
        [0.0] * 30
        + [5.2, 5.8, 6.4, 7.0, 7.6, 8.2, 8.8, 9.3, 10.0]
        + [10.0, 10.0, 10.0, 10.0]
    )
    rel_altitudes = iter(altitudes)

    drone = Drone(_FakeSystem(), drone_id=1)

    async def _fake_log_prearm_diagnostics(_target_altitude_m: float) -> None:
        return None

    async def _fake_get_position_gps():
        rel_alt = next(rel_altitudes)
        return (0.0, 0.0, 100.0 + rel_alt, rel_alt)

    monkeypatch.setattr(drone, "log_prearm_diagnostics", _fake_log_prearm_diagnostics)
    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    result = asyncio.run(drone.arm_and_takeoff(relative_altitude_m=10.0, _tolerance=1.0))

    assert result == pytest.approx(110.0)
    assert drone.system.action.calls.count(("takeoff",)) == 1
    assert ("hold",) in drone.system.action.calls


def test_drone_arm_and_takeoff_fails_when_climb_stalls_after_5m(monkeypatch):
    class _FakeAction:
        async def set_takeoff_altitude(self, _altitude: float) -> None:
            return None

        async def arm(self) -> None:
            return None

        async def takeoff(self) -> None:
            return None

        async def hold(self) -> None:
            return None

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    altitudes = [0.0, 5.1] + [5.9] * 12
    rel_altitudes = iter(altitudes)

    drone = Drone(_FakeSystem(), drone_id=2)

    async def _fake_log_prearm_diagnostics(_target_altitude_m: float) -> None:
        return None

    async def _fake_get_position_gps():
        rel_alt = next(rel_altitudes)
        return (0.0, 0.0, 100.0 + rel_alt, rel_alt)

    monkeypatch.setattr(drone, "log_prearm_diagnostics", _fake_log_prearm_diagnostics)
    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    with pytest.raises(WaypointTimeoutError, match="Takeoff climb stalled"):
        asyncio.run(drone.arm_and_takeoff(relative_altitude_m=10.0, _tolerance=1.0))


def test_drone_arm_and_takeoff_uses_current_rel_altitude_as_baseline(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.calls = []

        async def set_takeoff_altitude(self, altitude: float) -> None:
            self.calls.append(("set_takeoff_altitude", altitude))

        async def arm(self) -> None:
            self.calls.append(("arm",))

        async def takeoff(self) -> None:
            self.calls.append(("takeoff",))

        async def hold(self) -> None:
            self.calls.append(("hold",))

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    rel_altitudes = iter([
        -46.4,  # pre-takeoff baseline
        -41.0,
        -32.0,
        -20.0,
        -8.0,
        0.5,
        3.6,
        3.6,
        3.6,
        3.6,
        3.6,
    ])

    drone = Drone(_FakeSystem(), drone_id=6)

    async def _fake_log_prearm_diagnostics(_target_altitude_m: float) -> None:
        return None

    async def _fake_get_position_gps():
        rel_alt = next(rel_altitudes)
        return (0.0, 0.0, 150.0 + rel_alt, rel_alt)

    monkeypatch.setattr(drone, "log_prearm_diagnostics", _fake_log_prearm_diagnostics)
    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    result = asyncio.run(drone.arm_and_takeoff(relative_altitude_m=50.0, _tolerance=1.0))

    assert result == pytest.approx(153.6)
    assert ("takeoff",) in drone.system.action.calls
    assert ("hold",) in drone.system.action.calls


def test_drone_arm_and_takeoff_limits_failed_arm_attempts_to_ten(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.arm_calls = 0

        async def set_takeoff_altitude(self, _altitude: float) -> None:
            return None

        async def arm(self) -> None:
            self.arm_calls += 1
            raise RuntimeError("COMMAND_DENIED")

        async def takeoff(self) -> None:
            raise AssertionError("takeoff should not be called when arming never succeeds")

        async def hold(self) -> None:
            raise AssertionError("hold should not be called when arming never succeeds")

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    drone = Drone(_FakeSystem(), drone_id=7)

    async def _fake_log_prearm_diagnostics(_target_altitude_m: float) -> None:
        return None

    monkeypatch.setattr(drone, "log_prearm_diagnostics", _fake_log_prearm_diagnostics)

    with pytest.raises(WaypointTimeoutError, match="Arm denied after 10 attempts"):
        asyncio.run(
            drone.arm_and_takeoff(
                relative_altitude_m=10.0,
                _tolerance=1.0,
                armable_wait_timeout=180.0,
            )
        )

    assert drone.system.action.arm_calls == 10


def test_drone_land_and_disarm_completes_when_altitude_reaches_ground(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.calls = []

        async def land(self) -> None:
            self.calls.append(("land",))

        async def disarm(self) -> None:
            self.calls.append(("disarm",))

    class _FakeTelemetry:
        def __init__(self) -> None:
            self._states = [True, True, True, True]
            self._index = 0

        async def in_air(self):
            if self._index < len(self._states):
                value = self._states[self._index]
                self._index += 1
            else:
                value = self._states[-1]
            yield value

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()
            self.telemetry = _FakeTelemetry()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    rel_altitudes = iter([12.0, 8.5, 4.0, 0.6, 0.5, 0.4, 0.5, 0.4, 0.5, 0.4, 0.5])
    drone = Drone(_FakeSystem(), drone_id=4)

    async def _fake_get_position_gps():
        rel_alt = next(rel_altitudes)
        return (0.0, 0.0, 100.0 + rel_alt, rel_alt)

    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    asyncio.run(
        drone.land_and_disarm(
            landing_timeout=30.0,
            progress_timeout=5.0,
            touchdown_altitude_m=1.0,
        )
    )

    assert drone.system.action.calls == [("land",), ("disarm",)]


def test_drone_land_and_disarm_fails_when_descent_stalls(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.calls = []

        async def land(self) -> None:
            self.calls.append(("land",))

        async def disarm(self) -> None:
            self.calls.append(("disarm",))

    class _FakeTelemetry:
        async def in_air(self):
            yield True

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()
            self.telemetry = _FakeTelemetry()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    drone = Drone(_FakeSystem(), drone_id=5)

    async def _fake_get_position_gps():
        return (0.0, 0.0, 115.0, 15.0)

    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    with pytest.raises(WaypointTimeoutError, match="Landing stalled"):
        asyncio.run(
            drone.land_and_disarm(
                landing_timeout=20.0,
                progress_timeout=5.0,
                touchdown_altitude_m=1.0,
            )
        )

    assert drone.system.action.calls == [("land",), ("land",), ("land",)]


def test_drone_land_and_disarm_fails_when_final_rel_altitude_does_not_settle_near_zero(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.calls = []

        async def land(self) -> None:
            self.calls.append(("land",))

        async def disarm(self) -> None:
            self.calls.append(("disarm",))

    class _FakeTelemetry:
        def __init__(self) -> None:
            self._states = [True, True, False]
            self._index = 0

        async def in_air(self):
            if self._index < len(self._states):
                value = self._states[self._index]
                self._index += 1
            else:
                value = self._states[-1]
            yield value

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()
            self.telemetry = _FakeTelemetry()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    rel_altitudes = iter([12.0, 4.0, -46.4] + [-46.4] * 20)
    drone = Drone(_FakeSystem(), drone_id=7)

    async def _fake_get_position_gps():
        rel_alt = next(rel_altitudes)
        return (0.0, 0.0, 100.0 + rel_alt, rel_alt)

    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    with pytest.raises(WaypointTimeoutError, match="outside \\+/-3.0m"):
        asyncio.run(
            drone.land_and_disarm(
                landing_timeout=30.0,
                progress_timeout=5.0,
                touchdown_altitude_m=1.0,
                settle_timeout=5.0,
            )
        )

    assert drone.system.action.calls == [("land",)]


def test_drone_land_and_disarm_fails_when_touchdown_sample_does_not_hold_near_zero(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.calls = []

        async def land(self) -> None:
            self.calls.append(("land",))

        async def disarm(self) -> None:
            self.calls.append(("disarm",))

    class _FakeTelemetry:
        def __init__(self) -> None:
            self._states = [True, True, True, True, True, True, True]
            self._index = 0

        async def in_air(self):
            if self._index < len(self._states):
                value = self._states[self._index]
                self._index += 1
            else:
                value = self._states[-1]
            yield value

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()
            self.telemetry = _FakeTelemetry()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    rel_altitudes = iter([12.0, 8.5, 4.0, 0.8, -0.2, -1.2, -2.3, -3.8, -5.0, -6.2, -7.4] + [-7.4] * 20)
    drone = Drone(_FakeSystem(), drone_id=9)

    async def _fake_get_position_gps():
        rel_alt = next(rel_altitudes)
        return (0.0, 0.0, 100.0 + rel_alt, rel_alt)

    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    with pytest.raises(WaypointTimeoutError, match="outside \\+/-3.0m"):
        asyncio.run(
            drone.land_and_disarm(
                landing_timeout=30.0,
                progress_timeout=5.0,
                touchdown_altitude_m=1.0,
                settle_timeout=5.0,
                settle_duration_s=3.0,
            )
        )

    assert drone.system.action.calls == [("land",)]


def test_drone_land_and_disarm_allows_slow_final_descent_near_touchdown(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.calls = []

        async def land(self) -> None:
            self.calls.append(("land",))

        async def disarm(self) -> None:
            self.calls.append(("disarm",))

    class _FakeTelemetry:
        async def in_air(self):
            yield True

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()
            self.telemetry = _FakeTelemetry()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    # Final descent slows below 5m and only drops a few tenths of a meter per sample.
    rel_altitudes = iter(
        [
            12.0,
            8.0,
            4.8,
            4.5,
            4.2,
            3.9,
            3.6,
            3.3,
            3.0,
            2.7,
            2.4,
            2.2,
            1.9,
            0.8,
            0.4,
            0.2,
        ]
        + [0.2] * 10
    )
    drone = Drone(_FakeSystem(), drone_id=10)

    async def _fake_get_position_gps():
        rel_alt = next(rel_altitudes)
        return (0.0, 0.0, 100.0 + rel_alt, rel_alt)

    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    asyncio.run(
        drone.land_and_disarm(
            landing_timeout=60.0,
            progress_timeout=5.0,
            touchdown_altitude_m=2.0,
            settle_timeout=10.0,
            settle_duration_s=2.0,
        )
    )

    assert drone.system.action.calls == [("land",), ("disarm",)]


def test_drone_land_and_disarm_uses_low_altitude_grace_band_up_to_twelve_m(monkeypatch):
    class _FakeAction:
        def __init__(self) -> None:
            self.calls = []

        async def land(self) -> None:
            self.calls.append(("land",))

        async def disarm(self) -> None:
            self.calls.append(("disarm",))

    class _FakeTelemetry:
        async def in_air(self):
            yield True

    class _FakeSystem:
        def __init__(self) -> None:
            self.action = _FakeAction()
            self.telemetry = _FakeTelemetry()

    class _Clock:
        def __init__(self) -> None:
            self.now = 0.0

        def monotonic(self) -> float:
            return self.now

        async def sleep(self, delay: float) -> None:
            self.now += delay

    clock = _Clock()
    monkeypatch.setattr("dronevalkit.drone.time.monotonic", clock.monotonic)
    monkeypatch.setattr("dronevalkit.drone.asyncio.sleep", clock.sleep)

    drone = Drone(_FakeSystem(), drone_id=6)
    rel_alts = iter([11.8, 11.6, 11.4, 11.2, 1.8, 0.1, 0.1, 0.1])

    async def _fake_get_position_gps():
        rel_alt = next(rel_alts)
        return (0.0, 0.0, 100.0 + rel_alt, rel_alt)

    monkeypatch.setattr(drone, "get_position_gps", _fake_get_position_gps)

    asyncio.run(
        drone.land_and_disarm(
            landing_timeout=60.0,
            progress_timeout=5.0,
            touchdown_altitude_m=2.0,
            settle_duration_s=1.0,
        )
    )

    assert drone.system.action.calls == [("land",), ("disarm",)]


def test_telemetry_collector_ignores_socket_closed_after_stop():
    class _FakeAioRpcError(grpc.aio.AioRpcError):
        def __init__(self) -> None:
            super().__init__(
                grpc.StatusCode.UNAVAILABLE,
                None,
                None,
                "Socket closed",
            )

        def code(self):
            return grpc.StatusCode.UNAVAILABLE

    class _DoneIter:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise _FakeAioRpcError()

    class _FakeTelemetry:
        def position(self):
            return _DoneIter()

        def battery(self):
            return _DoneIter()

    class _FakeDrone:
        def __init__(self) -> None:
            self.telemetry = _FakeTelemetry()

    collector = _TelemetryCollector(_FakeDrone(), mission_start=0.0, ref_lat=0.0, ref_lon=0.0)
    collector._positions = []
    collector._battery_samples = []
    collector._active = False

    asyncio.run(collector._run())
    asyncio.run(collector._run_battery())


def test_drone_get_battery_pct_reuses_persistent_subscription():
    class _BatterySample:
        def __init__(self, remaining_percent: float) -> None:
            self.remaining_percent = remaining_percent

    class _FakeTelemetry:
        def __init__(self) -> None:
            self.calls = 0

        async def battery(self):
            self.calls += 1
            while True:
                yield _BatterySample(0.83)
                await asyncio.sleep(0)

    class _FakeSystem:
        def __init__(self) -> None:
            self.telemetry = _FakeTelemetry()

    async def _run():
        drone = Drone(_FakeSystem(), drone_id=3)
        first = await drone.get_battery_pct()
        second = await drone.get_battery_pct()
        return drone, first, second

    drone, first, second = asyncio.run(_run())

    assert first == pytest.approx(0.83)
    assert second == pytest.approx(0.83)
    assert drone.system.telemetry.calls == 1


def test_flight_get_battery_pct_reuses_cached_adapter():
    class _BatterySample:
        def __init__(self, remaining_percent: float) -> None:
            self.remaining_percent = remaining_percent

    class _FakeTelemetry:
        def __init__(self) -> None:
            self.calls = 0

        async def battery(self):
            self.calls += 1
            while True:
                yield _BatterySample(0.72)
                await asyncio.sleep(0)

    class _FakeSystem:
        def __init__(self) -> None:
            self.telemetry = _FakeTelemetry()

    async def _run():
        system = _FakeSystem()
        first = await get_battery_pct(system)
        second = await get_battery_pct(system)
        return system, first, second

    system, first, second = asyncio.run(_run())

    assert first == pytest.approx(0.72)
    assert second == pytest.approx(0.72)
    assert system.telemetry.calls == 1
