"""Tests for dronevalkit.io.agatz_adapter."""

from pathlib import Path

import pytest

import dronevalkit as dvk
from dronevalkit.exceptions import InvalidSolutionError
from dronevalkit.io import AgatzCase


AGATZ_ROOT = Path(__file__).resolve().parent.parent / "problems" / "agatz"
ALPHA1_DP_PATH = AGATZ_ROOT / "solutions" / "uniform-alpha_1-39-n8-DP.txt"
ALPHA1_TSP_PATH = AGATZ_ROOT / "solutions" / "uniform-alpha_1-39-n8-tsp.txt"
ALPHA3_DP_PATH = AGATZ_ROOT / "solutions" / "uniform-alpha_3-21-n7-DP.txt"


def test_list_agatz_cases_discovers_known_case():
    cases = dvk.list_agatz_cases(AGATZ_ROOT)

    assert cases
    assert isinstance(cases[0], AgatzCase)
    assert any(case.solution_path == ALPHA1_DP_PATH for case in cases)
    assert not any("ASTAR" in case.solution_path.name for case in cases)
    assert not any("-lim_" in case.solution_path.name for case in cases)

    known_case = next(case for case in cases if case.solution_path == ALPHA1_DP_PATH)
    assert known_case.instance_name == "uniform-alpha_1-39-n8"
    assert known_case.instance_path == AGATZ_ROOT / "uniform-alpha_1-39-n8.txt"
    assert known_case.solution_type == "DP"


def test_from_agatz_parses_alpha1_dp_solution():
    solution = dvk.from_agatz(ALPHA1_DP_PATH)

    assert solution.problem.depot == pytest.approx((38.9457, -92.3299))
    assert len(solution.problem.customers) == 7
    assert solution.problem.drone_eligible == [1, 2, 3, 4, 5, 6, 7]
    assert solution.truck_route == [0, 1, 7, 4, 3, 2, 0]
    assert solution.truck_leg_travel_times == pytest.approx(
        [36.7928996182702, 58.309518948453004, 34.92849839314596, 30.265491900843113, 6.708203932499369, 91.61181606803027]
    )
    assert solution.truck_speed == pytest.approx(10.0)
    assert solution.truck_arrival_times == pytest.approx(
        {
            0: 0.0,
            1: 36.7928996182702,
            7: 95.10241856672321,
            4: 130.03091695986917,
            3: 160.2964088607123,
            2: 257.2888275837939,
        }
    )
    assert solution.planned_metrics.sortie_leg_times is not None
    assert solution.planned_truck_timeline is not None
    assert solution.planned_truck_timeline[-1].kind == "dwell"
    assert solution.planned_truck_timeline[-1].start_node == 0
    assert solution.planned_truck_timeline[-1].end_node == 0
    assert solution.planned_truck_timeline[-1].label == "Wait for drone at node 0"
    assert solution.planned_truck_timeline[-1].start_time == pytest.approx(348.90064365182417)
    assert solution.planned_truck_timeline[-1].end_time == pytest.approx(452.11163439795314)
    assert solution.num_drones == 1
    assert solution.planned_metrics.drone_speed == pytest.approx(10.0)
    assert solution.planned_metrics.makespan == pytest.approx(452.11163439795314)
    assert solution.planned_metrics.sortie_times == pytest.approx([250.58062365129453, 201.53101074665864])
    assert [leg.name for leg in solution.planned_metrics.sortie_leg_times[0]] == [
        "launch_takeoff",
        "outbound",
        "delivery_land",
        "delivery",
        "delivery_takeoff",
        "return",
        "recovery_land",
    ]
    assert [(sortie.drone_id, sortie.launch, sortie.delivery, sortie.rendezvous) for sortie in solution.sorties] == [
        (0, 0, 5, 3),
        (0, 3, 6, 0),
    ]


def test_from_agatz_parses_alpha3_dp_solution():
    solution = dvk.from_agatz(ALPHA3_DP_PATH)

    assert solution.truck_route == [0, 6, 3, 5, 0]
    assert solution.truck_speed == pytest.approx(10.0)
    assert solution.planned_metrics.drone_speed == pytest.approx(30.0)
    assert solution.planned_metrics.sortie_leg_times is not None
    assert solution.planned_metrics.makespan == pytest.approx(448.36411002794273)
    assert solution.planned_metrics.sortie_times == pytest.approx(
        [160.48213680463712, 135.38566502345657, 152.49630819984907]
    )
    assert solution.planned_truck_timeline is not None
    assert any(segment.kind == "dwell" for segment in solution.planned_truck_timeline)
    assert [(sortie.launch, sortie.delivery, sortie.rendezvous) for sortie in solution.sorties] == [
        (0, 2, 6),
        (6, 1, 3),
        (3, 4, 0),
    ]


def test_from_agatz_parses_tsp_baseline_as_truck_only():
    solution = dvk.from_agatz(ALPHA1_TSP_PATH)

    assert solution.truck_route == [0, 6, 2, 3, 4, 7, 5, 1, 0]
    assert solution.sorties == []
    assert solution.planned_metrics.sortie_times == []
    assert solution.planned_metrics.makespan == pytest.approx(sum(solution.truck_leg_travel_times))


def test_from_agatz_rejects_missing_instance(tmp_path):
    solution_path = tmp_path / "uniform-alpha_1-39-n8-DP.txt"
    solution_path.write_text(ALPHA1_DP_PATH.read_text(), encoding="utf-8")

    with pytest.raises(InvalidSolutionError, match="Agatz instance file does not exist"):
        dvk.from_agatz(solution_path)
