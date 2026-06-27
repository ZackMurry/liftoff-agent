"""Liftoff sim server.

Run with:
    uvicorn server.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
import subprocess
import traceback

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .models import ExperimentRequest, ExperimentResult, HealthResponse, ScenarioInfo
from .repo_runner import UserExperimentError, run_user_experiment
from .scenarios import SCENARIOS
from .sim_launcher import DEFAULT_DOCKER_IMAGE, SimLaunchError

logger = logging.getLogger("liftoff")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

app = FastAPI(
    title="Liftoff Sim Server",
    description="Run targeted drone experiments against PX4/Gazebo.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Check whether Docker and the sim image are available."""
    docker_ok = _check_docker()
    image_ok = _check_image(DEFAULT_DOCKER_IMAGE) if docker_ok else False
    return HealthResponse(
        status="ok" if docker_ok and image_ok else "degraded",
        docker_available=docker_ok,
        sim_image_available=image_ok,
    )


@app.get("/scenarios", response_model=list[ScenarioInfo])
def list_scenarios() -> list[ScenarioInfo]:
    """List available scenario templates and their parameters."""
    return [
        ScenarioInfo(
            name=name,
            description=info["description"],
            params=info["params"],
        )
        for name, info in SCENARIOS.items()
    ]


@app.post("/run", response_model=ExperimentResult)
def run_experiment(
    req: ExperimentRequest,
    authorization: str | None = Header(default=None),
    x_liftoff_token: str | None = Header(default=None),
) -> ExperimentResult:
    """Clone PR code, launch PX4/Gazebo, run user code, and return metrics."""
    _authorize_run_request(authorization, x_liftoff_token)

    if req.scenario not in SCENARIOS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario '{req.scenario}'. Available: {list(SCENARIOS.keys())}",
        )

    try:
        return run_user_experiment(req)
    except (UserExperimentError, SimLaunchError, ValueError) as exc:
        logger.error("Experiment failed: %s", traceback.format_exc())
        return ExperimentResult(
            scenario=req.scenario,
            params=req.params,
            status="error",
            runs=[],
            pass_criteria={},
            verdict=f"Experiment error: {exc}",
            error=str(exc),
        )


def _check_docker() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_image(image: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _authorize_run_request(
    authorization: str | None,
    x_liftoff_token: str | None,
) -> None:
    expected = os.environ.get("SIM_SERVER_AUTH_TOKEN")
    print('expected auth token', expected)
    if not expected:
        return
    bearer = None
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    supplied = x_liftoff_token or bearer
    print('supplied', supplied)
    if supplied != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")
