"""Tests for explicit MAVSDK server cleanup."""

from __future__ import annotations

import asyncio
import io
import sys
import types

import pytest

import dronevalkit as dvk
from dronevalkit.config import ExperimentConfig, WindCondition
from dronevalkit.exceptions import MissionAbortedError
from dronevalkit.models import PlannedMetrics, Problem, Solution, Sortie


if "mavsdk" not in sys.modules:
    fake_mavsdk = types.ModuleType("mavsdk")

    class _PlaceholderSystem:
        def __init__(self, *args, **kwargs):
            pass

    fake_mavsdk.System = _PlaceholderSystem
    sys.modules["mavsdk"] = fake_mavsdk

import dronevalkit.flight as flight_mod


class _FakePopen:
    def __init__(self) -> None:
        self.killed = False
        self.wait_calls = 0
        self.stdout = io.BytesIO()
        self._returncode = None

    def poll(self):
        return self._returncode

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout=None):
        self.wait_calls += 1
        return self._returncode


def _make_config() -> ExperimentConfig:
    solution = Solution(
        problem=Problem(
            depot=(38.9404, -92.3277),
            customers={1: (38.9410, -92.3270)},
            drone_eligible=[1],
        ),
        truck_route=[0, 1, 0],
        sorties=[Sortie(delivery=1, rendezvous=0, drone_id=0)],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=120.0,
            sortie_times=[60.0],
        ),
        num_drones=1,
    )
    return ExperimentConfig(solution=solution, conditions=[WindCondition.calm()], replications=1)


def test_shutdown_mavsdk_system_kills_and_reaps_process():
    process = _FakePopen()
    drone = types.SimpleNamespace(_server_process=process, _plugins={"x": object()})

    asyncio.run(flight_mod.shutdown_mavsdk_system(drone))

    assert process.killed is True
    assert process.wait_calls == 1
    assert drone._server_process is None
    assert drone._plugins == {}
    assert process.stdout.closed is True


def test_connect_cleans_up_embedded_server_on_failure(monkeypatch: pytest.MonkeyPatch):
    process = _FakePopen()

    class _FakeSystem:
        def __init__(self, port=None):
            self.port = port
            self._server_process = process

        async def connect(self, system_address=None):
            raise RuntimeError("boom")

    monkeypatch.setattr("dronevalkit.flight.System", _FakeSystem)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(flight_mod.connect(address="udpin://0.0.0.0:14540", mavsdk_server_port=50051))

    assert process.killed is True
    assert process.wait_calls == 1


def test_run_single_always_shuts_down_mavsdk_systems(monkeypatch: pytest.MonkeyPatch, tmp_path):
    drones = [object()]
    shutdown_calls: list[list[object]] = []

    class _FakeRunner:
        def __init__(self, *args, **kwargs):
            self.container_id = "cid"

        def start(self) -> None:
            return None

        async def wait_for_ready(self, timeout: float = 60.0):
            return drones

        def stop(self) -> None:
            return None

    async def _fake_configure(*args, **kwargs):
        return None

    async def _fake_fly_mission(*args, **kwargs):
        raise MissionAbortedError("mission failed")

    async def _fake_shutdown(items):
        shutdown_calls.append(list(items))

    monkeypatch.setattr("dronevalkit.runner.PX4SimRunner", _FakeRunner)
    monkeypatch.setattr("dronevalkit.flight.configure_for_experiment", _fake_configure)
    monkeypatch.setattr("dronevalkit.flight.fly_mission", _fake_fly_mission)
    monkeypatch.setattr("dronevalkit.flight.shutdown_mavsdk_systems", _fake_shutdown)

    with pytest.raises(MissionAbortedError, match="mission failed"):
        asyncio.run(
            dvk._run_single(
                config=_make_config(),
                condition=WindCondition.calm(),
                rep=0,
                output_dir=str(tmp_path),
                base_instance=0,
            )
        )

    assert shutdown_calls == [drones]
