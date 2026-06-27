"""Tests for dronevalkit.logs."""

import pytest

from dronevalkit.logs import extract_mission_results
from dronevalkit.models import LegTiming


def test_extract_mission_results_scales_time_by_speed_factor():
    segments = [
        {
            "segment_type": "sortie",
            "sortie_index": 0,
            "start_time": 0.0,
            "end_time": 33.0,
            "positions": [],
            "battery_at_start": 100.0,
            "battery_at_end": 90.0,
        },
        {
            "segment_type": "reposition",
            "sortie_index": None,
            "start_time": 40.0,
            "end_time": 50.0,
            "positions": [],
            "battery_at_start": 90.0,
            "battery_at_end": 88.0,
        },
    ]

    sortie_results, reposition_results = extract_mission_results(
        ulog_data={},
        segments=segments,
        drone_id=0,
        time_scale_factor=2.0,
    )

    assert sortie_results[0].actual_time == pytest.approx(66.0)
    assert reposition_results[0].time == pytest.approx(20.0)


def test_extract_mission_results_rejects_nonpositive_time_scale_factor():
    with pytest.raises(ValueError, match="time_scale_factor must be positive"):
        extract_mission_results(
            ulog_data={},
            segments=[],
            drone_id=0,
            time_scale_factor=0.0,
        )


def test_extract_mission_results_normalizes_battery_resets_after_landing():
    segments = [
        {
            "segment_type": "sortie",
            "sortie_index": 0,
            "start_time": 0.0,
            "end_time": 30.0,
            "positions": [],
            "battery_at_start": 100.0,
            "battery_at_end": 80.0,
        },
        {
            "segment_type": "reposition",
            "sortie_index": None,
            "start_time": 35.0,
            "end_time": 45.0,
            "positions": [],
            "battery_at_start": 100.0,
            "battery_at_end": 95.0,
        },
        {
            "segment_type": "sortie",
            "sortie_index": 1,
            "start_time": 50.0,
            "end_time": 80.0,
            "positions": [],
            "battery_at_start": 100.0,
            "battery_at_end": 90.0,
        },
    ]

    sortie_results, reposition_results = extract_mission_results(
        ulog_data={},
        segments=segments,
        drone_id=0,
    )

    assert sortie_results[0].actual_energy == pytest.approx(20.0)
    assert reposition_results[0].energy == pytest.approx(5.0)
    assert sortie_results[1].raw_battery_at_start == pytest.approx(75.0)
    assert sortie_results[1].raw_battery_at_end == pytest.approx(65.0)
    assert sortie_results[1].actual_energy == pytest.approx(10.0)
    assert sortie_results[1].corrected_battery_at_end == pytest.approx(70.0)


def test_extract_mission_results_scales_leg_timings():
    segments = [
        {
            "segment_type": "sortie",
            "sortie_index": 0,
            "start_time": 0.0,
            "end_time": 12.0,
            "positions": [],
            "battery_at_start": 100.0,
            "battery_at_end": 95.0,
            "leg_timings": [
                {"name": "launch", "start_time": 0.0, "end_time": 2.0},
                {"name": "outbound", "start_time": 2.0, "end_time": 6.0},
                {"name": "delivery_land", "start_time": 6.0, "end_time": 7.0},
                {"name": "delivery", "start_time": 7.0, "end_time": 9.0},
                {"name": "delivery_takeoff", "start_time": 9.0, "end_time": 10.0},
            ],
        },
    ]

    sortie_results, _ = extract_mission_results(
        ulog_data={},
        segments=segments,
        drone_id=0,
        time_scale_factor=2.0,
    )

    assert sortie_results[0].leg_timings == [
        LegTiming(name="launch", start_time=0.0, end_time=4.0),
        LegTiming(name="outbound", start_time=4.0, end_time=12.0),
        LegTiming(name="delivery_land", start_time=12.0, end_time=14.0),
        LegTiming(name="delivery", start_time=14.0, end_time=18.0),
        LegTiming(name="delivery_takeoff", start_time=18.0, end_time=20.0),
    ]


def test_extract_mission_results_preserves_leg_energy_samples():
    segments = [
        {
            "segment_type": "sortie",
            "sortie_index": 0,
            "start_time": 10.0,
            "end_time": 20.0,
            "positions": [],
            "battery_at_start": 100.0,
            "battery_at_end": 92.0,
            "leg_energy_samples": [
                {
                    "name": "launch_takeoff",
                    "start_time": 10.0,
                    "end_time": 12.0,
                    "raw_battery_at_start": 100.0,
                    "raw_battery_at_end": 98.5,
                    "energy_pct": 1.5,
                },
                {
                    "name": "outbound",
                    "start_time": 12.0,
                    "end_time": 18.0,
                    "raw_battery_at_start": 98.5,
                    "raw_battery_at_end": 93.0,
                    "energy_pct": 5.5,
                },
            ],
        },
    ]

    sortie_results, _ = extract_mission_results(
        ulog_data={},
        segments=segments,
        drone_id=0,
        time_scale_factor=2.0,
    )

    samples = sortie_results[0].leg_energy_samples
    assert samples is not None
    assert [(sample.name, sample.start_time, sample.end_time) for sample in samples] == [
        ("launch_takeoff", 20.0, 24.0),
        ("outbound", 24.0, 36.0),
    ]
    assert [sample.energy_pct for sample in samples] == pytest.approx([1.5, 5.5])
