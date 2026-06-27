# Liftoff Demo Drone Project

This directory is a minimal example of the kind of user repository Liftoff can
clone and test.

The project owns the drone-management code. Liftoff owns the PX4/Gazebo Docker
simulator, then calls this repo's `./liftoff/run_experiment` command with
scenario details in environment variables.

## What It Does

- Reads a flight plan from Liftoff scenario params.
- Connects to PX4 through MAVSDK.
- Arms, takes off to a low altitude, hovers briefly, and lands.
- Emits Liftoff-compatible JSON with pass/fail criteria and run metrics.
- Supports `LIFTOFF_DRY_RUN=1` so the parser/result pipeline can be tested
  without Docker, PX4, Gazebo, or MAVSDK.

The flight plan is still parsed and returned in the Liftoff result path so PRs
can change mission shape/speed and the agent can select related scenarios. The
demo intentionally keeps the real simulator action short for fast pipeline
checks; it does not fly every waypoint.

## Flight Plan Input

Liftoff passes `LIFTOFF_PARAMS_JSON`. This demo accepts either an explicit
`flight_plan` object:

```json
{
  "flight_plan": {
    "home": [38.898, -77.036],
    "altitude_m": 5,
    "speed_m_s": 8,
    "waypoints": [
      [38.899, -77.035],
      [38.900, -77.034]
    ],
    "acceptance_radius_m": 3
  }
}
```

or the flatter scenario params Liftoff already uses:

```json
{
  "home": [38.898, -77.036],
  "waypoints": [[38.899, -77.035]],
  "altitude": 5,
  "drone_speed": 8
}
```

Scenario-specific params like `wind_speed`, `wind_direction`, and
`battery_capacity_mah` are not consumed directly by this code; Liftoff uses
them to configure the simulator.

## Local Dry Run

```bash
cd demo
LIFTOFF_DRY_RUN=1 \
LIFTOFF_SCENARIO=crosswind \
LIFTOFF_PARAMS_JSON="$(cat examples/crosswind_plan.json)" \
LIFTOFF_OUTPUT_DIR=/tmp/liftoff-demo \
./liftoff/run_experiment
```

The command prints JSON to stdout and writes the same payload to
`$LIFTOFF_OUTPUT_DIR/result.json`.

## Running Against Liftoff

When Liftoff clones this repo, it runs:

```bash
./liftoff/run_experiment
```

The command expects:

- `LIFTOFF_SCENARIO`
- `LIFTOFF_PARAMS_JSON`
- `LIFTOFF_REPLICATIONS`
- `LIFTOFF_SPEED_FACTOR`
- `LIFTOFF_MAVSDK_ADDRESSES_JSON`
- `LIFTOFF_OUTPUT_DIR`

Install runtime dependencies with:

```bash
python -m pip install -r requirements.txt
```

## Tests

```bash
cd demo
python -m pytest
```

The tests cover flight-plan parsing, scenario-derived plans, and dry-run JSON
output. They do not require MAVSDK or a running simulator.
