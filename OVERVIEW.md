# Liftoff - Hackathon MVP

## Pitch

Liftoff is an AI validation engineer for autonomous drones.

When a GitHub pull request modifies autonomy code, Liftoff automatically reviews the diff, determines the relevant flight risks, generates targeted simulation experiments, runs them in PX4/Gazebo, and posts an evidence-backed review back to the PR.

Instead of running a fixed regression suite, Liftoff chooses *which* simulations should be run based on what changed.

---

## Demo Flow

1. A GitHub PR is opened or updated.
2. A GitHub webhook triggers the Liftoff backend.
3. An AI agent reads the PR diff.
4. The agent identifies likely failure modes.
5. The agent selects or parameterizes relevant simulation scenarios.
6. The simulations execute on a remote VPS running PX4 + Gazebo in Docker.
7. Results stream back to the dashboard.
8. The agent posts a GitHub review summarizing findings and recommending whether the PR should merge.

---

## Example

PR:

> Increase maximum waypoint velocity.

Agent reasoning:

* This changes mission following behavior.
* Higher velocity may increase overshoot.
* Crosswind performance should be validated.
* Dense waypoint missions should be tested.

Generated experiments:

* Crosswind + tight turns
* GPS noise
* Dense waypoint inspection mission

Results:

* Crosswind: Failed (cross-track error increased)
* GPS noise: Passed
* Dense waypoint mission: Passed

GitHub comment:

> Generated three validation experiments based on the modified navigation code.
>
> Recommendation: Request changes.
>
> Crosswind mission exceeded acceptable tracking error.

---

## Tech Stack

### Frontend / Orchestration

* Next.js
* Vercel
* Vercel AI SDK / Eve (if practical)
* TypeScript

### Persistence

Supabase

Tables:

* runs
* experiments

Store:

* run status
* agent plan
* experiment metadata
* metrics
* artifacts

### Simulation

Existing VPS

* Docker
* PX4
* Gazebo

Expose a simple REST endpoint:

POST /run

that accepts experiment definitions and returns metrics.

---

## GitHub Integration

Implement a lightweight GitHub App or webhook.

Flow:

GitHub PR

↓

Webhook

↓

Read diff

↓

Invoke agent

↓

Run simulations

↓

Post PR comment

---

## Agent Responsibilities

The agent should **not** fly the drone.

Instead, it should:

* Read the code diff
* Infer what changed
* Predict likely failure modes
* Select appropriate simulation scenarios
* Explain why each experiment is necessary
* Summarize simulation evidence
* Produce a final merge recommendation

This is the core value of the project.

---

## Simulation Scenarios

Keep the MVP constrained to a handful of predefined scenario templates.

Examples:

* Crosswind
* GPS degradation
* Dense waypoint mission
* Tight turns
* Low battery RTL

The agent chooses among these templates and fills in parameters rather than generating arbitrary Gazebo worlds.

---

## Dashboard

The dashboard should emphasize the reasoning process.

Display:

* PR summary
* Agent reasoning
* Selected validation scenarios
* Live experiment progress
* Metrics
* Final recommendation

Think of it as an AI-powered CI dashboard rather than a robotics UI.

---

## MVP Goal

The demo should clearly communicate one idea:

> GitHub Actions runs the tests you wrote.
>
> Liftoff decides which simulation tests should exist for this pull request, runs them, and reviews the results before merge.

The intelligence is in selecting and justifying the validation experiments—not in controlling the drone itself.
