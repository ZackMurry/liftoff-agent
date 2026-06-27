# Liftoff Sim Server — Implementation Plan

## Goal

Create a FastAPI server (`server/`) that wraps dronevalkit's simulation pipeline behind a `POST /run` endpoint. The AI agent on the frontend calls this endpoint with an experiment definition; the server runs PX4/Gazebo in Docker and returns structured metrics.

## Architecture

```
AI Agent (Next.js / Vercel AI SDK)
    │
    │  POST /run  { scenario, params }
    ▼
┌─────────────────────────┐
│  FastAPI Server          │
│  server/main.py          │
│                          │
│  1. Parse request        │
│  2. Build Solution +     │
│     ExperimentConfig     │
│  3. Call dvk.run()       │
│  4. Serialize results    │
│  5. Return JSON metrics  │
└─────────────────────────┘
    │
    │  Docker (host networking)
    ▼
┌─────────────────────────┐
│  PX4 SITL + Gazebo      │
│  (existing Docker image) │
└─────────────────────────┘
```

## What We Build (4 files)

### 1. `server/main.py` — FastAPI app

- `GET /health` — liveness check (is Docker available, is the sim image pulled)
- `POST /run` — accepts an experiment definition, runs simulation, returns metrics
- Imports dronevalkit directly (the package is installed locally)

### 2. `server/scenarios.py` — Scenario templates

Predefined scenario templates the agent can choose from. Each template is a function that takes parameters and returns a `(Solution, ExperimentConfig)` pair.

Templates for the demo:

| Scenario | What it tests | Key params |
|----------|--------------|------------|
| `waypoint_mission` | Fly a set of waypoints and return to depot | `drone_speed`, `waypoints` (list of lat/lon), `altitude` |
| `crosswind` | Same mission but with wind | `wind_speed`, `wind_direction`, plus mission params |
| `tight_turns` | Dense waypoints with sharp angles | `drone_speed`, `num_waypoints`, `spacing_m` |
| `low_battery_rtl` | Mission with constrained battery | `battery_capacity_mah`, plus mission params |

Each template builds the dronevalkit `Solution` and `ExperimentConfig` objects internally. The agent never needs to know about dronevalkit's OR-solver data model — it just passes scenario name + params.

### 3. `server/models.py` — Pydantic request/response schemas

**Request:**
```python
class ExperimentRequest(BaseModel):
    scenario: str                          # "waypoint_mission", "crosswind", etc.
    params: dict[str, Any]                 # scenario-specific parameters
    replications: int = 1                  # number of runs (keep at 1 for demo speed)
    speed_factor: float = 1.0              # sim speedup (e.g. 2.0 = 2x realtime)
```

**Response:**
```python
class SortieMetrics(BaseModel):
    sortie_index: int
    actual_time_s: float
    planned_time_s: float
    actual_energy_pct: float
    actual_distance_m: float
    max_position_error_m: float
    feasible: bool

class ExperimentResult(BaseModel):
    scenario: str
    params: dict[str, Any]
    status: str                            # "passed" | "failed" | "error"
    sorties: list[SortieMetrics]
    total_time_s: float
    planned_time_s: float
    verdict: str                           # human-readable summary line
    pass_criteria: dict[str, bool]         # e.g. {"all_feasible": true, "time_inflation_ok": false}
```

### 4. `server/requirements.txt`

```
fastapi
uvicorn[standard]
```

dronevalkit is already installed as a local package — no need to duplicate it.

## How Scenarios Work

The agent doesn't construct raw `Solution` objects. Instead, each scenario template generates the dronevalkit structures from simple params. Example for `crosswind`:

```python
def crosswind(params):
    depot = params.get("depot", [38.898, -77.036])
    waypoints = params.get("waypoints", [[38.906, -77.043]])
    drone_speed = params.get("drone_speed", 10.0)
    wind_speed = params.get("wind_speed", 5.0)
    wind_direction = params.get("wind_direction", 90.0)

    # Build a simple one-sortie-per-waypoint Solution
    solution = build_mission(depot, waypoints, drone_speed)

    config = ExperimentConfig(
        solution=solution,
        conditions=[WindCondition(speed=wind_speed, direction=wind_direction)],
        replications=1,
        battery=InfiniteBattery(),   # isolate wind effect
        speed_factor=params.get("speed_factor", 1.0),
    )
    return solution, config
```

A helper `build_mission()` constructs `Problem`, `Sortie` list, `PlannedMetrics`, and `Solution` from a depot + waypoint list. This is the bridge between "simple agent params" and "dronevalkit's OR-solver data model."

## Pass/Fail Criteria

Each scenario defines its own pass criteria. For the demo:

- **Time inflation**: actual time < 1.5x planned time
- **All feasible**: no sortie ran out of battery
- **Position tracking**: max cross-track error < 5m

The response includes both the raw metrics and the boolean pass/fail verdicts.

## What We Reuse From dronevalkit

Imported directly — no copying or forking:

- `dronevalkit.config` — ExperimentConfig, WindCondition, batteries
- `dronevalkit.models` — Problem, Solution, Sortie, PlannedMetrics
- `dronevalkit.run()` — the full simulation pipeline (Docker → PX4 → MAVSDK → ULog → metrics)
- `dronevalkit.geo` — haversine distance for planned time estimation
- `dronevalkit.logs` — RunResult, SortieResult (result types)
- `dronevalkit.exceptions` — error types
- `docker/` — Dockerfile, worlds, start_multi.sh (used by runner.py)

## Steps

1. Create `server/` directory with `main.py`, `scenarios.py`, `models.py`, `requirements.txt`
2. Implement `build_mission()` helper that constructs Solution from depot + waypoints
3. Implement the 4 scenario templates
4. Implement `POST /run` endpoint that dispatches to scenarios and calls `dvk.run()`
5. Implement result serialization (RunResult → ExperimentResult)
6. Implement `GET /health` endpoint
7. Test that the server starts and the endpoint schema is correct
