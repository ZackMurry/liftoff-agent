"""Pydantic request/response schemas for the Liftoff sim server."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ExperimentRequest(BaseModel):
    """Payload accepted by ``POST /run``."""

    scenario: str = Field(
        description=(
            'Scenario template name: "waypoint_mission", "crosswind", '
            '"tight_turns", or "low_battery_rtl".'
        ),
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Scenario-specific parameters (see scenario docs).",
    )
    replications: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Number of independent simulation runs.",
    )
    speed_factor: float = Field(
        default=1.0,
        gt=0.0,
        description="Simulation speed multiplier (2.0 = twice realtime).",
    )
    source: Optional["SourceRepo"] = Field(
        default=None,
        description="Server-provided PR source metadata. Hidden from the agent tool schema.",
    )


class SourceRepo(BaseModel):
    """Git source checked out by the sim server for a PR experiment."""

    clone_url: str
    full_name: str
    head_ref: str
    head_sha: str
    token: Optional[str] = None


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class SortieMetrics(BaseModel):
    """Per-sortie results from a simulation run."""

    sortie_index: int
    planned_time_s: float
    actual_time_s: float
    actual_energy_pct: float
    actual_distance_m: float
    max_position_error_m: float
    feasible: bool
    leg_timings: Optional[list[dict[str, Any]]] = None


class RunMetrics(BaseModel):
    """Results from a single simulation replication."""

    condition_label: str
    replication: int
    sorties: list[SortieMetrics]
    total_actual_time_s: float
    total_planned_time_s: float


class ExperimentResult(BaseModel):
    """Top-level response from ``POST /run``."""

    scenario: str
    params: dict[str, Any]
    status: str = Field(description='"passed", "failed", or "error".')
    runs: list[RunMetrics]
    pass_criteria: dict[str, bool] = Field(
        description=(
            "Named pass/fail checks, e.g. "
            '{"all_feasible": true, "time_inflation_ok": false}.'
        ),
    )
    verdict: str = Field(description="One-line human-readable summary.")
    error: Optional[str] = Field(default=None, description="Error message if status is 'error'.")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    docker_available: bool
    sim_image_available: bool


# ---------------------------------------------------------------------------
# Scenario listing
# ---------------------------------------------------------------------------


class ScenarioInfo(BaseModel):
    name: str
    description: str
    params: dict[str, str] = Field(description="Param name -> description.")
