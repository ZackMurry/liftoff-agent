"""Tests for dronevalkit.io.json_io."""

import json
import os
import tempfile
import pytest

from dronevalkit.io.json_io import load_solution, save_solution
from dronevalkit.models import Problem, Solution, Sortie, PlannedMetrics
from dronevalkit.exceptions import InvalidSolutionError


SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "sample_solution.json")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_solution():
    problem = Problem(
        depot=(38.898, -77.036),
        customers={
            1: (38.906, -77.043),
            2: (38.912, -77.030),
            3: (38.904, -77.022),
            4: (38.895, -77.048),
            5: (38.910, -77.052),
        },
        drone_eligible=[1, 2, 4, 5],
    )
    sorties = [
        Sortie(delivery=1, rendezvous=3, drone_id=0),
        Sortie(delivery=4, rendezvous=5, drone_id=0),
        Sortie(delivery=2, rendezvous=0, drone_id=1),
    ]
    planned = PlannedMetrics(
        drone_speed=10.0,
        makespan=1680,
        sortie_times=[180, 210, 195],
        sortie_energies=[12.5, 14.8, 13.2],
    )
    return Solution(
        problem=problem,
        truck_route=[0, 3, 5, 2, 0],
        sorties=sorties,
        planned_metrics=planned,
        num_drones=2,
    )


# ---------------------------------------------------------------------------
# load_solution — sample file
# ---------------------------------------------------------------------------

def test_load_sample_solution_exists():
    assert os.path.isfile(SAMPLE_PATH), "examples/sample_solution.json not found"


def test_load_sample_solution():
    sol = load_solution(SAMPLE_PATH)
    assert isinstance(sol, Solution)


def test_load_sample_depot():
    sol = load_solution(SAMPLE_PATH)
    assert sol.problem.depot == pytest.approx((38.898, -77.036))


def test_load_sample_customers():
    sol = load_solution(SAMPLE_PATH)
    assert len(sol.problem.customers) == 5
    assert sol.problem.customers[1] == pytest.approx((38.906, -77.043))
    assert sol.problem.customers[3] == pytest.approx((38.904, -77.022))


def test_load_sample_drone_eligible():
    sol = load_solution(SAMPLE_PATH)
    assert set(sol.problem.drone_eligible) == {1, 2, 4, 5}


def test_load_sample_num_drones():
    sol = load_solution(SAMPLE_PATH)
    assert sol.num_drones == 2


def test_load_sample_truck_route():
    sol = load_solution(SAMPLE_PATH)
    assert sol.truck_route == [0, 3, 5, 2, 0]


def test_load_sample_sorties():
    sol = load_solution(SAMPLE_PATH)
    assert len(sol.sorties) == 3
    assert sol.sorties[0].delivery == 1
    assert sol.sorties[0].rendezvous == 3
    assert sol.sorties[0].drone_id == 0
    assert sol.sorties[2].rendezvous == 0
    assert sol.sorties[2].drone_id == 1
    assert sol.launch_node(0) == 0
    assert sol.launch_node(2) == 5


def test_load_sample_planned_metrics():
    sol = load_solution(SAMPLE_PATH)
    pm = sol.planned_metrics
    assert pm.drone_speed == pytest.approx(10.0)
    assert pm.makespan == pytest.approx(1680)
    assert pm.sortie_times == [180.0, 210.0, 195.0]
    assert pm.sortie_energies == [12.5, 14.8, 13.2]


def test_load_sample_truck_speed():
    sol = load_solution(SAMPLE_PATH)
    assert sol.truck_speed == pytest.approx(8.33)


def test_load_sample_arrival_times_null():
    sol = load_solution(SAMPLE_PATH)
    assert sol.truck_arrival_times is None
    assert sol.truck_leg_travel_times is None
    assert sol.planned_truck_timeline is None


# ---------------------------------------------------------------------------
# save_solution + round-trip
# ---------------------------------------------------------------------------

def test_save_and_reload_round_trip():
    sol = make_solution()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        save_solution(sol, tmp_path)
        reloaded = load_solution(tmp_path)

        assert reloaded.problem.depot == pytest.approx(sol.problem.depot)
        assert reloaded.problem.drone_eligible == sol.problem.drone_eligible
        assert reloaded.truck_route == sol.truck_route
        assert len(reloaded.sorties) == len(sol.sorties)
        assert reloaded.planned_metrics.makespan == pytest.approx(sol.planned_metrics.makespan)
        assert reloaded.planned_metrics.sortie_energies == sol.planned_metrics.sortie_energies
        assert reloaded.num_drones == sol.num_drones
        assert reloaded.truck_speed == pytest.approx(sol.truck_speed)
    finally:
        os.unlink(tmp_path)


def test_save_round_trip_no_energies():
    sol = make_solution()
    sol.planned_metrics.sortie_energies = None
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        save_solution(sol, tmp_path)
        reloaded = load_solution(tmp_path)
        assert reloaded.planned_metrics.sortie_energies is None
    finally:
        os.unlink(tmp_path)


def test_save_round_trip_with_arrival_times():
    sol = make_solution()
    sol.truck_arrival_times = {0: 0.0, 3: 420.0, 5: 840.0, 2: 1260.0}
    sol.truck_leg_travel_times = [420.0, 420.0, 420.0, 420.0]
    sol.planned_truck_timeline = [
        {
            "kind": "dwell",
            "start_time": 0.0,
            "end_time": 60.0,
            "start_node": 0,
            "end_node": 0,
            "label": "Launch",
        },
        {
            "kind": "move",
            "start_time": 60.0,
            "end_time": 120.0,
            "start_node": 0,
            "end_node": 3,
            "label": "Travel from node 0 to node 3",
        },
    ]
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        save_solution(sol, tmp_path)
        reloaded = load_solution(tmp_path)
        assert reloaded.truck_arrival_times is not None
        assert reloaded.truck_arrival_times[3] == pytest.approx(420.0)
        assert reloaded.truck_leg_travel_times == pytest.approx([420.0, 420.0, 420.0, 420.0])
        assert reloaded.planned_truck_timeline is not None
        assert reloaded.planned_truck_timeline[0].kind == "dwell"
        assert reloaded.planned_truck_timeline[1].kind == "move"
    finally:
        os.unlink(tmp_path)


def test_save_produces_valid_json():
    sol = make_solution()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        tmp_path = f.name
    try:
        save_solution(sol, tmp_path)
        with open(tmp_path) as f:
            data = json.load(f)
        assert "problem" in data
        assert "sorties" in data
        assert "planned_metrics" in data
    finally:
        os.unlink(tmp_path)


def test_save_customer_keys_are_strings():
    """JSON keys must be strings; customers should be saved with str keys."""
    sol = make_solution()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name
    try:
        save_solution(sol, tmp_path)
        with open(tmp_path) as f:
            data = json.load(f)
        for key in data["problem"]["customers"]:
            assert isinstance(key, str)
    finally:
        os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

def test_load_missing_file():
    with pytest.raises(InvalidSolutionError):
        load_solution("/nonexistent/path/solution.json")


def test_load_invalid_json():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        f.write("not valid json {{{")
        tmp_path = f.name
    try:
        with pytest.raises(InvalidSolutionError):
            load_solution(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_load_missing_required_field():
    data = {
        "problem": {
            "depot": [38.898, -77.036],
            "customers": {"1": [38.906, -77.043]},
            # missing drone_eligible
        },
        "truck_route": [0, 1, 0],
        "sorties": [],
        "num_drones": 1,
        "planned_metrics": {
            "drone_speed": 10.0,
            "makespan": 600,
            "sortie_times": [],
        },
    }
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump(data, f)
        tmp_path = f.name
    try:
        with pytest.raises(InvalidSolutionError):
            load_solution(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_save_unwritable_path():
    sol = make_solution()
    with pytest.raises(InvalidSolutionError):
        save_solution(sol, "/nonexistent_dir/solution.json")


def test_load_legacy_sortie_launch_field_is_ignored():
    data = {
        "problem": {
            "depot": [38.898, -77.036],
            "customers": {"1": [38.906, -77.043]},
            "drone_eligible": [1],
        },
        "truck_route": [0, 1, 0],
        "sorties": [
            {"launch": 0, "delivery": 1, "rendezvous": 0},
        ],
        "num_drones": 1,
        "planned_metrics": {
            "drone_speed": 10.0,
            "makespan": 120,
            "sortie_times": [60.0],
        },
    }
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump(data, f)
        tmp_path = f.name
    try:
        sol = load_solution(tmp_path)
        assert sol.sorties[0].drone_id == 0
        assert sol.launch_node(0) == 0
    finally:
        os.unlink(tmp_path)


def test_load_sorties_without_launch_infers_from_previous_rendezvous():
    data = {
        "problem": {
            "depot": [38.898, -77.036],
            "customers": {"1": [38.906, -77.043], "2": [38.912, -77.030]},
            "drone_eligible": [1, 2],
        },
        "truck_route": [0, 1, 2, 0],
        "sorties": [
            {"delivery": 1, "rendezvous": 2, "drone_id": 0},
            {"delivery": 2, "rendezvous": 0, "drone_id": 0},
        ],
        "num_drones": 1,
        "planned_metrics": {
            "drone_speed": 10.0,
            "makespan": 120,
            "sortie_times": [60.0, 60.0],
        },
    }
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
        json.dump(data, f)
        tmp_path = f.name
    try:
        sol = load_solution(tmp_path)
        assert sol.launch_node(0) == 0
        assert sol.launch_node(1) == 2
    finally:
        os.unlink(tmp_path)
