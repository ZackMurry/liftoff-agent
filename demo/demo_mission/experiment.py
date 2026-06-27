from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from .flight_plan import parse_flight_plan
from .mavsdk_runner import fly_plan
from .result import build_result


def main() -> int:
    scenario = os.environ.get("LIFTOFF_SCENARIO", "waypoint_mission")
    params = _load_params()
    output_dir = Path(os.environ.get("LIFTOFF_OUTPUT_DIR", "."))
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        plan = parse_flight_plan(params)
        if os.environ.get("LIFTOFF_DRY_RUN") == "1":
            flight_metrics = _dry_run_metrics(plan)
        else:
            mavsdk_addresses = _load_mavsdk_addresses()
            flight_metrics = asyncio.run(fly_plan(plan, mavsdk_addresses[0]))

        result = build_result(
            scenario=scenario,
            params=params,
            plan=plan,
            status="passed" if flight_metrics["feasible"] else "failed",
            actual_time_s=float(flight_metrics["actual_time_s"]),
            max_position_error_m=float(flight_metrics["max_position_error_m"]),
            feasible=bool(flight_metrics["feasible"]),
        )
    except Exception as exc:  # noqa: BLE001 - this command must report JSON failures to Liftoff
        fallback_plan = parse_flight_plan({"waypoints": [[38.899, -77.035]]})
        result = build_result(
            scenario=scenario,
            params=params,
            plan=fallback_plan,
            status="error",
            actual_time_s=0.0,
            max_position_error_m=999.0,
            feasible=False,
            error=str(exc),
        )

    encoded = json.dumps(result, indent=2, sort_keys=True)
    (output_dir / "result.json").write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


def _load_params() -> dict[str, Any]:
    raw = os.environ.get("LIFTOFF_PARAMS_JSON", "{}")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("LIFTOFF_PARAMS_JSON must decode to an object")
    return parsed


def _load_mavsdk_addresses() -> list[str]:
    raw = os.environ.get("LIFTOFF_MAVSDK_ADDRESSES_JSON", '["udpin://0.0.0.0:14540"]')
    parsed = json.loads(raw)
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("LIFTOFF_MAVSDK_ADDRESSES_JSON must be a non-empty list")
    return [str(item) for item in parsed]


def _dry_run_metrics(plan) -> dict[str, float | bool]:
    return {
        "actual_time_s": plan.planned_time_s * 1.05,
        "max_position_error_m": min(1.0, plan.acceptance_radius_m / 2.0),
        "feasible": True,
    }


if __name__ == "__main__":
    raise SystemExit(main())
