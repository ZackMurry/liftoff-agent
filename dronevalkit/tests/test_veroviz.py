"""Tests for dronevalkit.io.veroviz_adapter."""

import pandas as pd
import pytest

import dronevalkit as dvk


def _make_nodes_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"id": 0, "lat": 38.8980, "lon": -77.0360},
            {"id": 1, "lat": 38.8986, "lon": -77.0368},
            {"id": 2, "lat": 38.8993, "lon": -77.0352},
            {"id": 3, "lat": 38.8977, "lon": -77.0349},
        ]
    )


def _make_assignments_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "objectID": "truck",
                "modelFile": "truck.glb",
                "startLat": 38.8980,
                "startLon": -77.0360,
                "endLat": 38.8993,
                "endLon": -77.0352,
                "startTimeSec": 0.0,
                "endTimeSec": 100.0,
            },
            {
                "objectID": "drone0",
                "modelFile": "drone.glb",
                "startLat": 38.8980,
                "startLon": -77.0360,
                "endLat": 38.8986,
                "endLon": -77.0368,
                "startTimeSec": 0.0,
                "endTimeSec": 20.0,
            },
            {
                "objectID": "drone1",
                "modelFile": "drone.glb",
                "startLat": 38.8980,
                "startLon": -77.0360,
                "endLat": 38.8977,
                "endLon": -77.0349,
                "startTimeSec": 0.0,
                "endTimeSec": 30.0,
            },
            {
                "objectID": "drone0",
                "modelFile": "drone.glb",
                "startLat": 38.8986,
                "startLon": -77.0368,
                "endLat": 38.8993,
                "endLon": -77.0352,
                "startTimeSec": 20.0,
                "endTimeSec": 40.0,
            },
            {
                "objectID": "drone1",
                "modelFile": "drone.glb",
                "startLat": 38.8977,
                "startLon": -77.0349,
                "endLat": 38.8993,
                "endLon": -77.0352,
                "startTimeSec": 30.0,
                "endTimeSec": 60.0,
            },
            {
                "objectID": "truck",
                "modelFile": "truck.glb",
                "startLat": 38.8993,
                "startLon": -77.0352,
                "endLat": 38.8980,
                "endLon": -77.0360,
                "startTimeSec": 150.0,
                "endTimeSec": 250.0,
            },
        ]
    )


def test_from_veroviz_infers_solution_structure():
    solution = dvk.from_veroviz(_make_assignments_df(), _make_nodes_df())

    assert solution.problem.depot == pytest.approx((38.8980, -77.0360))
    assert solution.truck_route == [0, 2, 0]
    assert solution.num_drones == 2
    assert len(solution.sorties) == 2
    assert solution.sorties[0].launch == 0
    assert solution.sorties[0].delivery == 1
    assert solution.sorties[0].rendezvous == 2
    assert solution.sorties[1].launch == 0
    assert solution.sorties[1].delivery == 3
    assert solution.sorties[1].rendezvous == 2
    assert solution.planned_metrics.sortie_times == pytest.approx([40.0, 60.0])
    assert solution.planned_metrics.makespan == pytest.approx(250.0)
    assert solution.truck_leg_travel_times == pytest.approx([100.0, 100.0])


def test_from_veroviz_allows_overriding_usual_parameters():
    solution = dvk.from_veroviz(
        _make_assignments_df(),
        _make_nodes_df(),
        drone_speed=12.5,
        truck_speed=9.75,
        num_drones=3,
        drone_eligible=[1, 3],
        makespan=300.0,
    )

    assert solution.planned_metrics.drone_speed == pytest.approx(12.5)
    assert solution.truck_speed == pytest.approx(9.75)
    assert solution.num_drones == 3
    assert solution.problem.drone_eligible == [1, 3]
    assert solution.planned_metrics.makespan == pytest.approx(300.0)
