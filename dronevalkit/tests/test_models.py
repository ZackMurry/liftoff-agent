"""Tests for dronevalkit.models."""

import pytest
import dronevalkit as dvk
from dronevalkit.models import Problem, Solution, Sortie, PlannedMetrics


# ---------------------------------------------------------------------------
# Problem
# ---------------------------------------------------------------------------

def make_problem():
    return Problem(
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


def test_problem_fields():
    p = make_problem()
    assert p.depot == (38.898, -77.036)
    assert p.customers[1] == (38.906, -77.043)
    assert p.customers[3] == (38.904, -77.022)
    assert 2 in p.drone_eligible
    assert 3 not in p.drone_eligible


def test_problem_customers_count():
    p = make_problem()
    assert len(p.customers) == 5


# ---------------------------------------------------------------------------
# Sortie
# ---------------------------------------------------------------------------

def test_sortie_fields():
    s = Sortie(delivery=1, rendezvous=3)
    assert s.delivery == 1
    assert s.rendezvous == 3
    assert s.drone_id == 0


# ---------------------------------------------------------------------------
# PlannedMetrics
# ---------------------------------------------------------------------------

def test_planned_metrics_with_energies():
    pm = PlannedMetrics(
        drone_speed=10.0,
        makespan=1680,
        sortie_times=[180, 210, 195],
        sortie_energies=[12.5, 14.8, 13.2],
    )
    assert pm.drone_speed == 10.0
    assert pm.makespan == 1680
    assert pm.sortie_times == [180, 210, 195]
    assert pm.sortie_energies == [12.5, 14.8, 13.2]


def test_planned_metrics_no_energies():
    pm = PlannedMetrics(drone_speed=8.0, makespan=900, sortie_times=[300, 350])
    assert pm.sortie_energies is None


# ---------------------------------------------------------------------------
# Solution
# ---------------------------------------------------------------------------

def make_solution():
    problem = make_problem()
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


def test_solution_default_truck_speed():
    sol = make_solution()
    assert sol.truck_speed == pytest.approx(8.33)


def test_solution_default_arrival_times_none():
    sol = make_solution()
    assert sol.truck_arrival_times is None


def test_solution_default_truck_leg_travel_times_none():
    sol = make_solution()
    assert sol.truck_leg_travel_times is None


def test_solution_sorties_count():
    sol = make_solution()
    assert len(sol.sorties) == 3
    assert sol.sorties[0].drone_id == 0
    assert sol.sorties[2].drone_id == 1


def test_solution_truck_route():
    sol = make_solution()
    assert sol.truck_route[0] == 0
    assert sol.truck_route[-1] == 0


def test_solution_with_arrival_times():
    sol = make_solution()
    sol.truck_arrival_times = {0: 0.0, 3: 400.0, 5: 800.0, 2: 1200.0}
    assert sol.truck_arrival_times[3] == pytest.approx(400.0)


def test_solution_custom_truck_speed():
    sol = make_solution()
    sol.truck_speed = 11.11
    assert sol.truck_speed == pytest.approx(11.11)


def test_solution_uses_truck_leg_travel_times_in_planned_schedule():
    sol = Solution(
        problem=make_problem(),
        truck_route=[0, 3, 5, 0],
        sorties=[],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=300.0,
            sortie_times=[],
        ),
        truck_speed=100.0,
        truck_leg_travel_times=[111.0, 222.0, 333.0],
    )

    schedule = sol.planned_schedule()

    assert schedule["truck_arrivals"] == pytest.approx([0.0, 111.0, 333.0, 666.0])


def test_solution_planned_schedule_includes_truck_service_time():
    sol = Solution(
        problem=make_problem(),
        truck_route=[0, 3, 0],
        sorties=[Sortie(delivery=1, rendezvous=0, launch=3, drone_id=0)],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=300.0,
            sortie_times=[60.0],
        ),
        truck_leg_travel_times=[100.0, 100.0],
        truck_service_time=30.0,
    )

    schedule = sol.planned_schedule()

    assert schedule["truck_arrivals"] == pytest.approx([0.0, 100.0, 230.0])
    assert schedule["sortie_launch_times"][0] == pytest.approx(130.0)
    assert schedule["truck_departures"][1] == pytest.approx(130.0)


def test_solution_rejects_misaligned_truck_leg_travel_times():
    with pytest.raises(ValueError, match="truck_leg_travel_times must align"):
        Solution(
            problem=make_problem(),
            truck_route=[0, 1, 0],
            sorties=[],
            planned_metrics=PlannedMetrics(
                drone_speed=10.0,
                makespan=120.0,
                sortie_times=[],
            ),
            truck_leg_travel_times=[60.0],
        )


def test_solution_requires_metrics_alignment():
    with pytest.raises(ValueError):
        Solution(
            problem=make_problem(),
            truck_route=[0, 1, 0],
            sorties=[Sortie(delivery=1, rendezvous=0)],
            planned_metrics=PlannedMetrics(
                drone_speed=10.0,
                makespan=120.0,
                sortie_times=[60.0, 70.0],
            ),
        )


def test_solution_requires_positive_num_drones():
    with pytest.raises(ValueError):
        Solution(
            problem=make_problem(),
            truck_route=[0, 1, 0],
            sorties=[Sortie(delivery=1, rendezvous=0)],
            planned_metrics=PlannedMetrics(
                drone_speed=10.0,
                makespan=120.0,
                sortie_times=[60.0],
            ),
            num_drones=0,
        )


def test_planned_sortie_leg_total_uses_simulator_leg_granularity_for_coarse_plans():
    solution = Solution(
        problem=make_problem(),
        truck_route=[0, 1, 0],
        sorties=[Sortie(delivery=1, rendezvous=0)],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=120.0,
            sortie_times=[60.0],
            sortie_leg_times=[
                [
                    {"name": "launch", "start_time": 0.0, "end_time": 10.0},
                    {"name": "outbound", "start_time": 10.0, "end_time": 50.0},
                    {"name": "recovery", "start_time": 50.0, "end_time": 60.0},
                ]
            ],
        ),
    )

    assert dvk._planned_sortie_leg_total(solution) == 9


def test_planned_sortie_leg_total_restores_implicit_launch_and_recovery_legs():
    solution = Solution(
        problem=make_problem(),
        truck_route=[0, 1, 0],
        sorties=[Sortie(delivery=1, rendezvous=0)],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=120.0,
            sortie_times=[60.0],
            sortie_leg_times=[
                [
                    {"name": "launch_takeoff", "start_time": 0.0, "end_time": 10.0},
                    {"name": "outbound", "start_time": 10.0, "end_time": 20.0},
                    {"name": "delivery_land", "start_time": 20.0, "end_time": 25.0},
                    {"name": "delivery", "start_time": 25.0, "end_time": 30.0},
                    {"name": "delivery_takeoff", "start_time": 30.0, "end_time": 35.0},
                    {"name": "return", "start_time": 35.0, "end_time": 50.0},
                    {"name": "recovery_land", "start_time": 50.0, "end_time": 60.0},
                ]
            ],
        ),
    )

    assert dvk._planned_sortie_leg_total(solution) == 9


def test_planned_sortie_leg_total_preserves_waiting_leg_when_planned():
    solution = Solution(
        problem=make_problem(),
        truck_route=[0, 1, 0],
        sorties=[Sortie(delivery=1, rendezvous=0)],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=120.0,
            sortie_times=[60.0],
            sortie_leg_times=[
                [
                    {"name": "launch_prep", "start_time": 0.0, "end_time": 1.0},
                    {"name": "launch_takeoff", "start_time": 1.0, "end_time": 10.0},
                    {"name": "outbound", "start_time": 10.0, "end_time": 20.0},
                    {"name": "delivery_land", "start_time": 20.0, "end_time": 25.0},
                    {"name": "delivery", "start_time": 25.0, "end_time": 30.0},
                    {"name": "delivery_takeoff", "start_time": 30.0, "end_time": 35.0},
                    {"name": "return", "start_time": 35.0, "end_time": 45.0},
                    {"name": "waiting", "start_time": 45.0, "end_time": 50.0},
                    {"name": "recovery_land", "start_time": 50.0, "end_time": 55.0},
                    {"name": "recovery", "start_time": 55.0, "end_time": 60.0},
                ]
            ],
        ),
    )

    assert dvk._planned_sortie_leg_total(solution) == 10


def test_planned_sortie_leg_total_falls_back_to_default_sortie_leg_count():
    solution = Solution(
        problem=make_problem(),
        truck_route=[0, 1, 0],
        sorties=[Sortie(delivery=1, rendezvous=0), Sortie(delivery=2, rendezvous=0)],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=180.0,
            sortie_times=[60.0, 60.0],
        ),
    )

    assert dvk._planned_sortie_leg_total(solution) == 18


def test_solution_rejects_out_of_range_drone_id():
    with pytest.raises(ValueError):
        Solution(
            problem=make_problem(),
            truck_route=[0, 1, 0],
            sorties=[Sortie(delivery=1, rendezvous=0, drone_id=2)],
            planned_metrics=PlannedMetrics(
                drone_speed=10.0,
                makespan=120.0,
                sortie_times=[60.0],
            ),
            num_drones=2,
        )


def test_solution_infers_launch_from_previous_rendezvous_per_drone():
    sol = Solution(
        problem=make_problem(),
        truck_route=[0, 3, 4, 0],
        sorties=[
            Sortie(delivery=1, rendezvous=3, drone_id=0),
            Sortie(delivery=2, rendezvous=4, drone_id=1),
            Sortie(delivery=5, rendezvous=0, drone_id=0),
        ],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=300.0,
            sortie_times=[60.0, 60.0, 60.0],
        ),
        num_drones=2,
    )
    assert sol.launch_node(0) == 0
    assert sol.launch_node(1) == 0
    assert sol.launch_node(2) == 3


def test_solution_planned_schedule_truck_waits_for_all_rendezvous_drones():
    sol = Solution(
        problem=make_problem(),
        truck_route=[0, 3, 0],
        sorties=[
            Sortie(delivery=1, rendezvous=3, drone_id=0),
            Sortie(delivery=2, rendezvous=3, drone_id=1),
        ],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=300.0,
            sortie_times=[60.0, 90.0],
        ),
        num_drones=2,
    )

    schedule = sol.planned_schedule()
    truck_arrival_at_3 = schedule["truck_arrivals"][1]
    truck_departure_at_3 = schedule["truck_departures"][1]
    sortie_0_end = schedule["sortie_end_times"][0]
    sortie_1_end = schedule["sortie_end_times"][1]

    assert truck_departure_at_3 >= truck_arrival_at_3
    assert truck_departure_at_3 >= sortie_0_end
    assert truck_departure_at_3 >= sortie_1_end
    assert truck_departure_at_3 == pytest.approx(max(truck_arrival_at_3, sortie_0_end, sortie_1_end))


def test_solution_planned_schedule_supports_same_stop_rendezvous_then_relaunch():
    sol = Solution(
        problem=make_problem(),
        truck_route=[0, 0],
        sorties=[
            Sortie(delivery=1, rendezvous=0, drone_id=0),
            Sortie(delivery=2, rendezvous=0, drone_id=0),
        ],
        planned_metrics=PlannedMetrics(
            drone_speed=10.0,
            makespan=300.0,
            sortie_times=[60.0, 70.0],
        ),
        num_drones=1,
    )

    schedule = sol.planned_schedule()

    assert schedule["sortie_launch_times"][0] == pytest.approx(0.0)
    assert schedule["sortie_end_times"][0] == pytest.approx(60.0)
    assert schedule["sortie_launch_times"][1] == pytest.approx(60.0)
    assert schedule["sortie_end_times"][1] == pytest.approx(130.0)
    assert schedule["truck_departures"][0] == pytest.approx(130.0)


def test_solution_rejects_duplicate_customer_visits_in_truck_route():
    with pytest.raises(ValueError, match="must not visit the same customer more than once"):
        Solution(
            problem=make_problem(),
            truck_route=[0, 3, 4, 3, 0],
            sorties=[Sortie(delivery=1, rendezvous=3, drone_id=0)],
            planned_metrics=PlannedMetrics(
                drone_speed=10.0,
                makespan=120.0,
                sortie_times=[60.0],
            ),
        )


def test_solution_rejects_deadlock_when_rendezvous_is_only_before_launch():
    with pytest.raises(ValueError, match="Deadlock or invalid route"):
        Solution(
            problem=make_problem(),
            truck_route=[0, 1, 2, 0],
            sorties=[Sortie(launch=2, delivery=3, rendezvous=1, drone_id=0)],
            planned_metrics=PlannedMetrics(
                drone_speed=10.0,
                makespan=120.0,
                sortie_times=[60.0],
            ),
        )
