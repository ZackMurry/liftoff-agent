from __future__ import annotations

import pytest

from experiments.run_experiments import build_scenarios


def test_build_scenarios_defaults_to_baseline_moderate_strong():
    scenarios = build_scenarios(
        wind_speed_override=None,
        wind_angle_override=None,
    )

    assert [scenario.scenario_id for scenario in scenarios] == [
        "baseline",
        "wind_moderate",
        "wind_strong",
    ]
    assert [scenario.wind_speed for scenario in scenarios] == [0.0, 5.0, 10.0]
    assert [scenario.wind_direction for scenario in scenarios] == [0.0, 0.0, 0.0]


def test_build_scenarios_overrides_fixed_wind_angle_for_non_calm_only():
    scenarios = build_scenarios(
        wind_speed_override=None,
        wind_angle_override=135.0,
    )

    assert [scenario.scenario_id for scenario in scenarios] == [
        "baseline",
        "wind_moderate",
        "wind_strong",
    ]
    assert [scenario.wind_direction for scenario in scenarios] == [0.0, 135.0, 135.0]


def test_build_scenarios_replaces_default_wind_set_with_custom_speed():
    scenarios = build_scenarios(
        wind_speed_override=7.5,
        wind_angle_override=210.0,
    )

    assert [scenario.scenario_id for scenario in scenarios] == [
        "baseline",
        "wind_strong",
    ]
    assert [scenario.wind_speed for scenario in scenarios] == [0.0, 7.5]
    assert [scenario.wind_direction for scenario in scenarios] == [0.0, 210.0]
    assert scenarios[-1].label == "Wind 7.5m/s"


def test_build_scenarios_rejects_non_positive_custom_wind_speed():
    with pytest.raises(ValueError, match="wind-speed"):
        build_scenarios(
            wind_speed_override=0.0,
            wind_angle_override=None,
        )
