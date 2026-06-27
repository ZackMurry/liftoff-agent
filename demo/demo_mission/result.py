from __future__ import annotations

from typing import Any

from .flight_plan import FlightPlan


def build_result(
    *,
    scenario: str,
    params: dict[str, Any],
    plan: FlightPlan,
    status: str,
    actual_time_s: float,
    max_position_error_m: float,
    feasible: bool,
    error: str | None = None,
) -> dict[str, Any]:
    time_inflation_ok = actual_time_s <= plan.planned_time_s * 1.5
    tracking_ok = max_position_error_m <= plan.acceptance_radius_m
    all_passed = feasible and time_inflation_ok and tracking_ok and error is None

    if error:
        verdict = f"Experiment error: {error}"
    elif all_passed:
        verdict = "Mission completed within demo thresholds."
    else:
        failures = []
        if not feasible:
            failures.append("mission did not complete")
        if not time_inflation_ok:
            failures.append("time inflation exceeded 1.5x")
        if not tracking_ok:
            failures.append("tracking error exceeded acceptance radius")
        verdict = "Failed: " + "; ".join(failures) + "."

    return {
        "scenario": scenario,
        "params": params,
        "status": status if error is None else "error",
        "runs": [
            {
                "condition_label": scenario,
                "replication": 0,
                "sorties": [
                    {
                        "sortie_index": 0,
                        "planned_time_s": plan.planned_time_s,
                        "actual_time_s": actual_time_s,
                        "actual_energy_pct": 0.0,
                        "actual_distance_m": plan.planned_distance_m,
                        "max_position_error_m": max_position_error_m,
                        "feasible": feasible,
                        "leg_timings": [
                            {
                                "name": "mission",
                                "duration_s": actual_time_s,
                            }
                        ],
                    }
                ],
                "total_actual_time_s": actual_time_s,
                "total_planned_time_s": plan.planned_time_s,
            }
        ],
        "pass_criteria": {
            "mission_completed": feasible,
            "time_inflation_ok": time_inflation_ok,
            "tracking_ok": tracking_ok,
        },
        "verdict": verdict,
        "error": error,
    }
