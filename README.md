# Liftoff

Liftoff is an AI validation engineer for drone autonomy pull requests.

When a GitHub pull request changes flight software, Liftoff reads the diff, identifies flight-safety risk, chooses the most relevant PX4/Gazebo simulation scenario, runs the experiment, stores the result, and posts an evidence-backed review recommendation.

The MVP is split into three parts:

- `portal/` - Next.js dashboard, GitHub webhook handler, AI agent orchestration, and Supabase persistence.
- `server/` - FastAPI simulation service exposing `POST /run` for PX4/Gazebo-backed experiments.
- `demo/` - Example user-owned drone project with a `./liftoff/run_experiment` entrypoint.

## Architecture

```text
GitHub PR webhook
  -> portal/app/api/webhook
  -> AI reviewer in portal/lib/agent.ts
  -> run_experiment tool in portal/lib/agent-tools.ts
  -> FastAPI sim server POST /run
  -> cloned user repo ./liftoff/run_experiment
  -> Supabase pull_requests and experiments
  -> GitHub PR review comment
```

The agent currently runs at most one experiment per PR. If a diff has flight-safety risk, it selects the single highest-value scenario from the allowed templates. If the diff has no flight-safety impact, it can approve without running an experiment.

Allowed scenarios:

- `waypoint_mission`
- `crosswind`
- `tight_turns`
- `low_battery_rtl`

## Portal

The portal is a Next.js app that receives GitHub webhooks, runs the AI reviewer, stores PR and experiment state in Supabase, and renders the validation dashboard.

```bash
cd portal
npm install
npm run dev
```

Open `http://localhost:3000`.

Build:

```bash
cd portal
npm run build
```

Required environment variables:

```bash
NEXT_PUBLIC_SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
GITHUB_APP_ID=
GITHUB_PRIVATE_KEY=
GITHUB_WEBHOOK_SECRET=
OPENAI_API_KEY=
SIM_SERVER_URL=http://localhost:8000
SIM_SERVER_AUTH_TOKEN=
```

`SIM_SERVER_AUTH_TOKEN` is optional unless the sim server is configured to require it.

## Database

The Supabase schema lives in [portal/supabase-schema.sql](portal/supabase-schema.sql).

Core tables:

- `webhook_events` - raw GitHub webhook deliveries.
- `pull_requests` - PR metadata, status, recommendation, and review body.
- `experiments` - scenario, params, status, result JSON, pass criteria, and timestamps.

Apply the schema in Supabase before running the webhook flow.

## Sim Server

The sim server is a FastAPI app that accepts experiment requests, launches PX4/Gazebo through Docker, clones the PR source repo, runs the repo-owned Liftoff entrypoint, and returns structured results.

Install and run:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r server/requirements.txt
uvicorn server.main:app --host 0.0.0.0 --port 8000
```

Endpoints:

- `GET /health` - checks Docker and sim image availability.
- `GET /scenarios` - lists supported scenario templates.
- `POST /run` - runs one experiment.

Example request:

```bash
curl -X POST http://localhost:8000/run \
  -H 'content-type: application/json' \
  -d '{
    "scenario": "crosswind",
    "params": {
      "home": [38.898, -77.036],
      "waypoints": [[38.899, -77.035]],
      "wind_speed": 7,
      "wind_direction": 270
    },
    "replications": 1,
    "speed_factor": 4
  }'
```

If `SIM_SERVER_AUTH_TOKEN` is set for the server, include either:

```bash
Authorization: Bearer <token>
```

or:

```bash
X-Liftoff-Token: <token>
```

## User Repo Contract

Liftoff expects the cloned PR branch to expose an executable command at one of:

- `./liftoff/run_experiment`
- `./demo/liftoff/run_experiment`

The command receives scenario details through environment variables:

- `LIFTOFF_SCENARIO`
- `LIFTOFF_PARAMS_JSON`
- `LIFTOFF_REPLICATIONS`
- `LIFTOFF_SPEED_FACTOR`
- `LIFTOFF_MAVSDK_ADDRESSES_JSON`
- `LIFTOFF_OUTPUT_DIR`
- `LIFTOFF_SIM_LOG_DIR`
- `LIFTOFF_HEAD_SHA`
- `LIFTOFF_REPOSITORY`

It should print JSON to stdout or write `$LIFTOFF_OUTPUT_DIR/result.json`.

Expected result shape:

```json
{
  "scenario": "crosswind",
  "params": {},
  "status": "passed",
  "runs": [],
  "pass_criteria": {
    "mission_completed": true
  },
  "verdict": "Crosswind validation passed.",
  "logs": ["optional log line"]
}
```

The server also captures user command stdout/stderr and PX4/Gazebo Docker log tails into the `logs` field returned to the portal.

## Tests

Server tests:

```bash
python -m pytest server/test_repo_runner.py
```

Demo project tests:

```bash
cd demo
python -m pytest
```

Portal build check:

```bash
cd portal
npm run build
```

## Notes

- The agent is intentionally constrained to known scenario templates.
- The agent does not fly the drone directly; cloned user code owns the mission behavior.
- Experiment logs shown in the portal come from persisted server/user/PX4 log lines in `experiments.result.logs`.
