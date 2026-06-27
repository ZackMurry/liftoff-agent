"""Clone PR branches and run user-owned drone management code."""

from __future__ import annotations

import json
import os
import shlex
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .models import ExperimentRequest, ExperimentResult, RunMetrics, SourceRepo
from .sim_launcher import PX4SimLauncher, SimLaunchError, build_launch_config

DEFAULT_TEST_COMMAND = "./liftoff/run_experiment"
TEST_COMMAND_CANDIDATES = (
    "./liftoff/run_experiment",
    "./demo/liftoff/run_experiment",
)
DEFAULT_COMMAND_TIMEOUT_S = 600
DEFAULT_SIM_STARTUP_DELAY_S = 8


class UserExperimentError(RuntimeError):
    """Raised when clone, sim launch, command execution, or parsing fails."""


def run_user_experiment(req: ExperimentRequest) -> ExperimentResult:
    """Run one Liftoff experiment using code from the requested PR branch."""
    if req.source is None:
        raise UserExperimentError("Missing source metadata for cloned experiment run.")

    with tempfile.TemporaryDirectory(prefix="liftoff_run_") as workspace:
        root = Path(workspace)
        checkout_dir = root / "repo"
        output_dir = root / "output"
        sim_log_dir = root / "sim_logs"
        output_dir.mkdir()
        sim_log_dir.mkdir()

        _clone_source(req.source, checkout_dir, root)

        launcher = PX4SimLauncher(
            build_launch_config(req.params, req.speed_factor),
            log_dir=str(sim_log_dir),
        )
        try:
            launcher.start()
            _wait_for_sim_startup(launcher)
            payload = _run_user_command(
                req=req,
                source=req.source,
                checkout_dir=checkout_dir,
                output_dir=output_dir,
                sim_log_dir=sim_log_dir,
                mavsdk_addresses=launcher.mavsdk_addresses,
            )
            return _normalize_result(req, payload)
        finally:
            launcher.stop()


def _clone_source(source: SourceRepo, checkout_dir: Path, workspace: Path) -> None:
    askpass_path: Path | None = None
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"

    if source.token:
        askpass_path = workspace / "git-askpass.sh"
        askpass_path.write_text(
            "#!/usr/bin/env sh\n"
            "case \"$1\" in\n"
            "*Username*) printf '%s\\n' 'x-access-token' ;;\n"
            f"*) printf '%s\\n' {shlex.quote(source.token)} ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        askpass_path.chmod(askpass_path.stat().st_mode | stat.S_IXUSR)
        env["GIT_ASKPASS"] = str(askpass_path)

    clone_cmd = [
        "git",
        "clone",
        "--depth",
        "1",
        "--branch",
        source.head_ref,
        source.clone_url,
        str(checkout_dir),
    ]
    _run_checked(clone_cmd, env=env, cwd=workspace, secret=source.token)

    checkout_cmd = ["git", "checkout", "--detach", source.head_sha]
    _run_checked(checkout_cmd, env=env, cwd=checkout_dir, secret=source.token)


def _wait_for_sim_startup(launcher: PX4SimLauncher) -> None:
    delay = float(os.environ.get("LIFTOFF_SIM_STARTUP_DELAY_S", DEFAULT_SIM_STARTUP_DELAY_S))
    deadline = time.monotonic() + max(delay, 0.0)
    while time.monotonic() < deadline:
        running = launcher.is_running()
        if running is False:
            logs = launcher.logs_tail()
            raise SimLaunchError(
                "PX4/Gazebo container exited during startup."
                + (f"\nDocker logs tail:\n{logs}" if logs else "")
            )
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def _run_user_command(
    *,
    req: ExperimentRequest,
    source: SourceRepo,
    checkout_dir: Path,
    output_dir: Path,
    sim_log_dir: Path,
    mavsdk_addresses: list[str],
) -> dict[str, Any]:
    command = _resolve_test_command(checkout_dir)
    timeout = int(os.environ.get("LIFTOFF_TEST_TIMEOUT_S", DEFAULT_COMMAND_TIMEOUT_S))
    env = os.environ.copy()
    env.update(
        {
            "LIFTOFF_SCENARIO": req.scenario,
            "LIFTOFF_PARAMS_JSON": json.dumps(req.params),
            "LIFTOFF_REPLICATIONS": str(req.replications),
            "LIFTOFF_SPEED_FACTOR": str(req.speed_factor),
            "LIFTOFF_MAVSDK_ADDRESSES_JSON": json.dumps(mavsdk_addresses),
            "LIFTOFF_OUTPUT_DIR": str(output_dir),
            "LIFTOFF_SIM_LOG_DIR": str(sim_log_dir),
            "LIFTOFF_HEAD_SHA": source.head_sha,
            "LIFTOFF_REPOSITORY": source.full_name,
        }
    )

    try:
        result = subprocess.run(
            command,
            cwd=checkout_dir,
            env=env,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise UserExperimentError(f"User experiment timed out after {timeout}s.") from exc

    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise UserExperimentError(
            f"User experiment command failed (exit {result.returncode})."
            + (f" Output: {details[-4000:]}" if details else "")
        )

    result_path = output_dir / "result.json"
    raw = result_path.read_text(encoding="utf-8") if result_path.exists() else result.stdout
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UserExperimentError(
            "User experiment did not produce valid JSON on stdout or result.json."
        ) from exc
    if not isinstance(payload, dict):
        raise UserExperimentError("User experiment JSON result must be an object.")
    return payload


def _normalize_result(req: ExperimentRequest, payload: dict[str, Any]) -> ExperimentResult:
    status = str(payload.get("status", "error"))
    if status not in {"passed", "failed", "error"}:
        status = "error"

    runs_payload = payload.get("runs", [])
    runs: list[RunMetrics] = []
    if isinstance(runs_payload, list):
        for run_payload in runs_payload:
            if isinstance(run_payload, dict):
                runs.append(RunMetrics(**run_payload))

    pass_criteria = payload.get("pass_criteria", {})
    if not isinstance(pass_criteria, dict):
        pass_criteria = {}

    return ExperimentResult(
        scenario=str(payload.get("scenario", req.scenario)),
        params=dict(payload.get("params", req.params) or {}),
        status=status,
        runs=runs,
        pass_criteria={str(k): bool(v) for k, v in pass_criteria.items()},
        verdict=str(payload.get("verdict", "User experiment completed.")),
        error=payload.get("error"),
    )


def _resolve_test_command(checkout_dir: Path) -> str:
    configured = os.environ.get("LIFTOFF_TEST_COMMAND")
    if configured:
        return configured

    for candidate in TEST_COMMAND_CANDIDATES:
        path = checkout_dir / candidate.removeprefix("./")
        if path.is_file():
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
            return candidate

    candidates = ", ".join(TEST_COMMAND_CANDIDATES)
    raise UserExperimentError(
        "No Liftoff experiment entrypoint found in cloned repo. "
        f"Expected one of: {candidates}. "
        "Set LIFTOFF_TEST_COMMAND only for non-standard repos."
    )


def _run_checked(
    cmd: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    secret: str | None,
) -> None:
    result = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = _redact(result.stderr.strip(), secret)
        stdout = _redact(result.stdout.strip(), secret)
        detail = stderr or stdout
        raise UserExperimentError(
            f"Command failed (exit {result.returncode}): {' '.join(cmd[:3])}"
            + (f". Output: {detail[-4000:]}" if detail else "")
        )


def _redact(text: str, secret: str | None) -> str:
    if secret:
        return text.replace(secret, "[redacted]")
    return text
