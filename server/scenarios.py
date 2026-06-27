"""Scenario catalog for the Liftoff sim server.

These entries document the experiment intents the agent may request. Execution
is owned by the cloned PR branch via ``LIFTOFF_TEST_COMMAND``.
"""

from __future__ import annotations

from typing import Any


SCENARIOS: dict[str, dict[str, Any]] = {
    "waypoint_mission": {
        "description": "Fly a set of waypoints under calm conditions.",
        "params": {
            "home": "[lat, lon] home position; alias: depot",
            "waypoints": "[[lat, lon], ...] mission points",
            "altitude": "float m flight altitude",
            "drone_speed": "float m/s target speed",
            "num_drones": "int vehicle count; default 1",
        },
    },
    "crosswind": {
        "description": "Fly a waypoint mission with configurable wind.",
        "params": {
            "home": "[lat, lon] home position; alias: depot",
            "waypoints": "[[lat, lon], ...] mission points",
            "altitude": "float m flight altitude",
            "drone_speed": "float m/s target speed",
            "wind_speed": "float m/s",
            "wind_direction": "float degrees, 0=North",
            "num_drones": "int vehicle count; default 1",
        },
    },
    "tight_turns": {
        "description": "Dense or sharp waypoint geometry to test agility and tracking.",
        "params": {
            "home": "[lat, lon] home position; alias: depot",
            "waypoints": "[[lat, lon], ...] optional explicit mission points",
            "altitude": "float m flight altitude",
            "drone_speed": "float m/s target speed",
            "num_waypoints": "int, for commands that generate a zig-zag mission",
            "spacing_m": "float meters between generated waypoints",
            "wind_speed": "float m/s",
            "wind_direction": "float degrees, 0=North",
            "num_drones": "int vehicle count; default 1",
        },
    },
    "low_battery_rtl": {
        "description": "Mission with constrained battery to test failsafe/RTL behavior.",
        "params": {
            "home": "[lat, lon] home position; alias: depot",
            "waypoints": "[[lat, lon], ...] mission points",
            "altitude": "float m flight altitude",
            "drone_speed": "float m/s target speed",
            "battery_capacity_mah": "int mAh",
            "battery_n_cells": "int cell count",
            "battery_v_charged": "float charged voltage per cell",
            "battery_v_empty": "float empty voltage per cell",
            "num_drones": "int vehicle count; default 1",
        },
    },
}
