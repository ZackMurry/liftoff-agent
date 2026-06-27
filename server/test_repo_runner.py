from __future__ import annotations

import json
import types
from pathlib import Path
from unittest.mock import Mock

from server.models import ExperimentRequest, SourceRepo
from server.repo_runner import UserExperimentError, _resolve_test_command, run_user_experiment
from server.sim_launcher import PX4SimLauncher, SimLaunchConfig


def _request() -> ExperimentRequest:
    return ExperimentRequest(
        scenario="crosswind",
        params={
            "home": [38.9, -77.0],
            "waypoints": [[38.91, -77.01]],
            "wind_speed": 5,
            "wind_direction": 90,
        },
        replications=2,
        speed_factor=3,
        source=SourceRepo(
            clone_url="https://github.com/example/drone-code.git",
            full_name="example/drone-code",
            head_ref="feature",
            head_sha="abc123",
            token="secret-token",
        ),
    )


def test_run_user_experiment_clones_launches_sim_and_runs_user_command(
    monkeypatch,
):
    calls: list[object] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[:2] == ["git", "clone"]:
            checkout = Path(cmd[-1])
            checkout.mkdir()
            entrypoint = checkout / "demo" / "liftoff" / "run_experiment"
            entrypoint.parent.mkdir(parents=True)
            entrypoint.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:2] == ["git", "checkout"]:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if isinstance(cmd, str):
            output_dir = Path(kwargs["env"]["LIFTOFF_OUTPUT_DIR"])
            output_dir.joinpath("result.json").write_text(
                json.dumps(
                    {
                        "status": "passed",
                        "runs": [],
                        "pass_criteria": {"user_checks": True},
                        "verdict": "ok",
                    }
                ),
                encoding="utf-8",
            )
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {cmd}")

    starts: list[PX4SimLauncher] = []
    stops: list[PX4SimLauncher] = []
    monkeypatch.setattr("server.repo_runner.subprocess.run", fake_run)
    monkeypatch.setattr("server.repo_runner._wait_for_sim_startup", lambda _launcher: None)
    monkeypatch.setattr(PX4SimLauncher, "start", lambda self: starts.append(self))
    monkeypatch.setattr(PX4SimLauncher, "stop", lambda self: stops.append(self))

    result = run_user_experiment(_request())

    assert result.status == "passed"
    assert result.pass_criteria == {"user_checks": True}
    assert starts and stops == starts

    clone_cmd = calls[0][0]
    checkout_cmd = calls[1][0]
    user_call = calls[2]
    assert clone_cmd[:5] == ["git", "clone", "--depth", "1", "--branch"]
    assert clone_cmd[5] == "feature"
    assert clone_cmd[6] == "https://github.com/example/drone-code.git"
    assert checkout_cmd == ["git", "checkout", "--detach", "abc123"]

    env = user_call[1]["env"]
    assert user_call[0] == "./demo/liftoff/run_experiment"
    assert env["LIFTOFF_SCENARIO"] == "crosswind"
    assert env["LIFTOFF_REPLICATIONS"] == "2"
    assert env["LIFTOFF_SPEED_FACTOR"] == "3.0"
    assert json.loads(env["LIFTOFF_MAVSDK_ADDRESSES_JSON"]) == ["udpin://0.0.0.0:14540"]
    assert env["LIFTOFF_HEAD_SHA"] == "abc123"


def test_resolve_test_command_prefers_standard_user_repo_entrypoint(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFTOFF_TEST_COMMAND", raising=False)
    entrypoint = tmp_path / "liftoff" / "run_experiment"
    entrypoint.parent.mkdir()
    entrypoint.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    assert _resolve_test_command(tmp_path) == "./liftoff/run_experiment"


def test_resolve_test_command_supports_monorepo_demo_entrypoint(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFTOFF_TEST_COMMAND", raising=False)
    entrypoint = tmp_path / "demo" / "liftoff" / "run_experiment"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    assert _resolve_test_command(tmp_path) == "./demo/liftoff/run_experiment"


def test_resolve_test_command_reports_missing_entrypoint(tmp_path, monkeypatch):
    monkeypatch.delenv("LIFTOFF_TEST_COMMAND", raising=False)

    try:
        _resolve_test_command(tmp_path)
    except UserExperimentError as exc:
        assert "./liftoff/run_experiment" in str(exc)
    else:
        raise AssertionError("expected UserExperimentError")


def test_px4_launcher_preserves_dronevalkit_docker_infra(monkeypatch, tmp_path):
    run_mock = Mock(return_value=types.SimpleNamespace(returncode=0, stdout="cid\n", stderr=""))
    monkeypatch.setattr("server.sim_launcher.subprocess.run", run_mock)

    launcher = PX4SimLauncher(
        SimLaunchConfig(
            home=(38.9, -77.0),
            anchor=(38.905, -77.005),
            wind_speed=5,
            wind_direction=90,
            speed_factor=4,
        ),
        log_dir=str(tmp_path),
    )
    launcher.start()

    cmd = run_mock.call_args.args[0]
    joined = " ".join(cmd)
    assert cmd[:4] == ["docker", "run", "-d", "--rm"]
    assert "--network host" in joined
    assert "zackmurry/dronevalkit-sim:latest" in joined
    assert "/dronevalkit/docker/worlds/default.sdf:" in joined
    assert "/dronevalkit/docker/worlds/windy.sdf:" in joined
    assert f"{tmp_path}:/root/PX4-Autopilot/build/px4_sitl_default/rootfs/log" in joined
    assert "PX4_SIM_SPEED_FACTOR=4" in joined
    assert "PX4_PARAM_BAT1_CAPACITY=5000.0" in joined
    assert "export PX4_GZ_WORLD=dronevalkit_wind" in cmd[-1]
    assert 'WIND_VECTOR = "5.000000 0.000000 0"' in cmd[-1]
    assert cmd[-1].endswith("HEADLESS=1 make px4_sitl gz_x500")
