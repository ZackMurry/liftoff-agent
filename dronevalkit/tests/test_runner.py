"""Tests for dronevalkit.runner orchestration behavior."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from dronevalkit.config import ExperimentConfig, WindCondition
from dronevalkit.exceptions import ContainerError, SimulationError
from dronevalkit.models import PlannedMetrics, Problem, Solution, Sortie
from dronevalkit.runner import PX4SimRunner


def _make_config(num_drones: int = 1) -> ExperimentConfig:
    customers = {i + 1: (38.941 + (i * 1e-4), -92.3277) for i in range(num_drones)}
    sorties = [
        Sortie(delivery=i + 1, rendezvous=0, drone_id=i)
        for i in range(num_drones)
    ]
    solution = Solution(
        problem=Problem(
            depot=(38.9404, -92.3277),
            customers=customers,
            drone_eligible=list(customers.keys()),
        ),
        truck_route=[0] + list(customers.keys()) + [0],
        sorties=sorties,
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=120.0,
            sortie_times=[60.0] * num_drones,
        ),
        num_drones=num_drones,
    )
    return ExperimentConfig(
        solution=solution,
        conditions=[WindCondition.calm()],
        replications=1,
    )


def test_start_single_drone_builds_expected_docker_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _make_config(num_drones=1)
    runner = PX4SimRunner(cfg, log_dir=str(tmp_path), base_instance=0)

    run_mock = Mock(return_value=types.SimpleNamespace(returncode=0, stdout="cid123\n", stderr=""))
    monkeypatch.setattr("dronevalkit.runner.subprocess.run", run_mock)

    runner.start()

    cmd = run_mock.call_args.args[0]
    assert cmd[:4] == ["docker", "run", "-d", "--rm"]
    assert "--network" in cmd and "host" in cmd
    joined = " ".join(cmd)
    assert f"NUM_DRONES={runner.num_drones}" in joined
    assert f"PX4_BASE_INSTANCE={runner.base_instance}" in joined
    assert "PX4_PARAM_BAT1_CAPACITY=5000.0" in joined
    assert "PX4_PARAM_BAT1_N_CELLS=4" in joined
    assert "PX4_PARAM_BAT1_V_CHARGED=4.2" in joined
    assert "PX4_PARAM_BAT1_V_EMPTY=3.5" in joined
    assert "PX4_HOME_LAT=38.9407" in joined
    assert "PX4_HOME_LON=-92.3277" in joined
    assert "PX4_GZ_MODEL_POSE=0.000,-33.358" in joined
    assert "PX4_GZ_BASE_EAST=0.000" in joined
    assert "PX4_GZ_BASE_NORTH=-33.358" in joined
    assert "/docker/worlds/default.sdf:/root/PX4-Autopilot/Tools/simulation/gz/worlds/default.sdf:ro" in joined
    assert "/docker/worlds/windy.sdf:/root/PX4-Autopilot/Tools/simulation/gz/worlds/windy.sdf:ro" in joined
    assert cmd[-1] == "export PX4_INSTANCE=0 && HEADLESS=1 make px4_sitl gz_x500"
    assert runner.container_id == "cid123"


def test_start_multi_drone_mounts_script_and_uses_bash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _make_config(num_drones=3)
    runner = PX4SimRunner(cfg, log_dir=str(tmp_path), base_instance=2)

    run_mock = Mock(return_value=types.SimpleNamespace(returncode=0, stdout="cid456\n", stderr=""))
    monkeypatch.setattr("dronevalkit.runner.subprocess.run", run_mock)
    monkeypatch.setattr("dronevalkit.runner.os.path.isfile", lambda _p: True)

    runner.start()

    cmd = run_mock.call_args.args[0]
    joined = " ".join(cmd)
    assert "PX4_HOME_LAT=38.940799999999996" in joined
    assert "PX4_HOME_LON=-92.3277" in joined
    assert "PX4_GZ_BASE_EAST=0.000" in joined
    assert "PX4_GZ_BASE_NORTH=-44.478" in joined
    assert "/docker/worlds/default.sdf:/root/PX4-Autopilot/Tools/simulation/gz/worlds/default.sdf:ro" in joined
    assert "/docker/worlds/windy.sdf:/root/PX4-Autopilot/Tools/simulation/gz/worlds/windy.sdf:ro" in joined
    assert "/root/dronevalkit/start_multi.sh:ro" in joined
    assert cmd[-1] == "bash /root/dronevalkit/start_multi.sh"


def test_start_single_drone_with_wind_builds_dynamic_gazebo_world(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cfg = _make_config(num_drones=1)
    runner = PX4SimRunner(
        cfg,
        log_dir=str(tmp_path),
        base_instance=0,
        wind_condition=WindCondition.moderate(speed=10.0, direction=90.0),
    )

    run_mock = Mock(return_value=types.SimpleNamespace(returncode=0, stdout="cid789\n", stderr=""))
    monkeypatch.setattr("dronevalkit.runner.subprocess.run", run_mock)

    runner.start()

    cmd = run_mock.call_args.args[0]
    launch = cmd[-1]
    joined = " ".join(cmd)
    assert "/docker/worlds/windy.sdf:/root/PX4-Autopilot/Tools/simulation/gz/worlds/windy.sdf:ro" in joined
    assert "python3 - <<'PY'" in launch
    assert "/Tools/simulation/gz/worlds/windy.sdf" in launch
    assert "/Tools/simulation/gz/worlds/dronevalkit_wind.sdf" in launch
    assert "/Tools/simulation/gz/models/x500_base/model.sdf" in launch
    assert "gz-sim-lift-drag-system" in launch
    assert 'ensure_child(model, "enable_wind", "true")' in launch
    assert 'ensure_child(base_link, "enable_wind", "true")' in launch
    assert '"0.12", "1.35"' in launch
    assert '"0.18", "1.35"' in launch
    assert "export PX4_GZ_WORLD=dronevalkit_wind" in launch
    assert 'WORLD_NAME = "dronevalkit_wind"' in launch
    # 90deg => East in WindCondition convention, so Gazebo ENU X=10, Y=0.
    assert 'WIND_VECTOR = "10.000000 0.000000 0"' in launch


def test_start_multi_drone_with_wind_exports_px4_gz_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _make_config(num_drones=2)
    runner = PX4SimRunner(
        cfg,
        log_dir=str(tmp_path),
        base_instance=0,
        wind_condition=WindCondition.moderate(speed=5.0, direction=0.0),
    )

    run_mock = Mock(return_value=types.SimpleNamespace(returncode=0, stdout="cidmulti\n", stderr=""))
    monkeypatch.setattr("dronevalkit.runner.subprocess.run", run_mock)
    monkeypatch.setattr("dronevalkit.runner.os.path.isfile", lambda _p: True)

    runner.start()

    cmd = run_mock.call_args.args[0]
    launch = cmd[-1]
    joined = " ".join(cmd)
    assert "/docker/worlds/windy.sdf:/root/PX4-Autopilot/Tools/simulation/gz/worlds/windy.sdf:ro" in joined
    assert "/Tools/simulation/gz/models/x500_base/model.sdf" in launch
    assert "gz-sim-lift-drag-system" in launch
    assert 'ensure_child(model, "enable_wind", "true")' in launch
    assert "export PX4_GZ_WORLD=dronevalkit_wind" in launch
    # 0deg => North in WindCondition convention, so Gazebo ENU X=0, Y=5.
    assert 'WIND_VECTOR = "0.000000 5.000000 0"' in launch
    assert launch.endswith("bash /root/dronevalkit/start_multi.sh")


def test_multi_drone_start_script_launches_primary_and_secondary_instances():
    script_path = Path(__file__).resolve().parents[1] / "docker" / "start_multi.sh"
    text = script_path.read_text(encoding="utf-8")

    assert 'NUM_DRONES="${NUM_DRONES:-1}"' in text
    assert 'BASE_INSTANCE="${PX4_BASE_INSTANCE:-0}"' in text
    assert 'BASE_EAST="${PX4_GZ_BASE_EAST:-0}"' in text
    assert 'BASE_NORTH="${PX4_GZ_BASE_NORTH:-0}"' in text
    assert 'POSE_X=$(awk "BEGIN { printf \\"%.3f\\", ${BASE_EAST} + ${X_OFFSET} }")' in text
    assert 'if [[ "${i}" -eq 0 ]]; then' in text
    assert 'make px4_sitl "${DRONE_MODEL}"' in text
    assert '"${PX4_BIN}" -i "${INSTANCE}" -w "${INSTANCE_DIR}"' in text
    assert '>>"${INSTANCE_LOG_DIR}/px4_stdout.log" 2>&1' in text


def test_start_multi_drone_missing_script_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _make_config(num_drones=2)
    runner = PX4SimRunner(cfg, log_dir=str(tmp_path), base_instance=0)

    monkeypatch.setattr("dronevalkit.runner.os.path.isfile", lambda _p: False)
    run_mock = Mock()
    monkeypatch.setattr("dronevalkit.runner.subprocess.run", run_mock)

    with pytest.raises(ContainerError):
        runner.start()

    run_mock.assert_not_called()


def test_start_docker_failure_raises_container_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _make_config(num_drones=1)
    runner = PX4SimRunner(cfg, log_dir=str(tmp_path), base_instance=0)

    monkeypatch.setattr(
        "dronevalkit.runner.subprocess.run",
        Mock(return_value=types.SimpleNamespace(returncode=125, stdout="", stderr="boom")),
    )

    with pytest.raises(ContainerError, match="docker run failed"):
        runner.start()


def test_stop_calls_docker_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _make_config(num_drones=1)
    runner = PX4SimRunner(cfg, log_dir=str(tmp_path), base_instance=0)
    runner.container_id = "abc123"

    run_mock = Mock(return_value=types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr("dronevalkit.runner.subprocess.run", run_mock)

    runner.stop()

    run_mock.assert_called_once_with(["docker", "stop", "abc123"], capture_output=True)
    assert runner.container_id is None


def test_stop_without_container_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _make_config(num_drones=1)
    runner = PX4SimRunner(cfg, log_dir=str(tmp_path), base_instance=0)

    run_mock = Mock()
    monkeypatch.setattr("dronevalkit.runner.subprocess.run", run_mock)
    runner.stop()
    run_mock.assert_not_called()


def test_wait_for_ready_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _make_config(num_drones=2)
    runner = PX4SimRunner(cfg, log_dir=str(tmp_path), base_instance=1)

    async def fake_connect_multi(num_drones: int, base_instance: int, timeout: float):
        assert num_drones == 2
        assert base_instance == 1
        assert timeout == 7.0
        return ["d0", "d1"]

    original_sleep = asyncio.sleep

    async def fast_sleep(_seconds: float):
        await original_sleep(0)

    fake_flight = types.SimpleNamespace(connect_multi=fake_connect_multi)
    monkeypatch.setitem(sys.modules, "dronevalkit.flight", fake_flight)
    monkeypatch.setattr("dronevalkit.runner.asyncio.sleep", fast_sleep)
    monkeypatch.setattr(runner, "_container_is_running", lambda: True)

    drones = asyncio.run(runner.wait_for_ready(timeout=7.0))
    assert drones == ["d0", "d1"]


def test_wait_for_ready_container_exits_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _make_config(num_drones=2)
    runner = PX4SimRunner(cfg, log_dir=str(tmp_path), base_instance=0)

    async def never_connect_multi(*_args, **_kwargs):
        await asyncio.Future()

    original_sleep = asyncio.sleep

    async def fast_sleep(_seconds: float):
        await original_sleep(0)

    fake_flight = types.SimpleNamespace(connect_multi=never_connect_multi)
    monkeypatch.setitem(sys.modules, "dronevalkit.flight", fake_flight)
    monkeypatch.setattr("dronevalkit.runner.asyncio.sleep", fast_sleep)
    monkeypatch.setattr(runner, "_container_is_running", lambda: False)
    monkeypatch.setattr(runner, "_container_logs_tail", lambda: "container log tail")

    with pytest.raises(SimulationError, match="Container exited before MAVSDK discovery"):
        asyncio.run(runner.wait_for_ready(timeout=5.0))


def test_get_latest_ulogs_groups_by_instance(tmp_path: Path):
    cfg = _make_config(num_drones=2)
    runner = PX4SimRunner(cfg, log_dir=str(tmp_path), base_instance=0)

    inst0 = tmp_path / "instance_0"
    inst1 = tmp_path / "instance_1"
    inst0.mkdir()
    inst1.mkdir()
    old0 = inst0 / "old.ulg"
    new0 = inst0 / "new.ulg"
    only1 = inst1 / "one.ulg"
    old0.write_text("a")
    new0.write_text("b")
    only1.write_text("c")

    # Ensure deterministic mtimes for latest selection.
    os.utime(old0, (1, 1))
    os.utime(new0, (2, 2))
    os.utime(only1, (3, 3))

    latest = runner.get_latest_ulogs()
    assert latest[0].endswith("instance_0/new.ulg")
    assert latest[1].endswith("instance_1/one.ulg")
