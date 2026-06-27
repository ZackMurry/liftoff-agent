"""Tests for dronevalkit.io.mfstsp_adapter."""

from pathlib import Path

import pytest

import dronevalkit as dvk
from dronevalkit.io import (
    MfstspCase,
    build_actual_mfstsp_event_log,
    load_mfstsp_event_log,
    save_mfstsp_event_log,
)
from dronevalkit.logs import DroneRunResult, RunResult, SortieResult


MFSTSP_ROOT = Path(__file__).resolve().parent.parent / "problems" / "mfstsp"
HEURISTIC_PATH = (
    MFSTSP_ROOT
    / "20170608T131251001523"
    / "tbl_solutions_101_1_Heuristic.csv"
)
IP_PATH = (
    MFSTSP_ROOT
    / "20170608T131251001523"
    / "tbl_solutions_101_1_IP.csv"
)
MULTI_UAV_PATH = (
    MFSTSP_ROOT
    / "20170608T131251001523"
    / "tbl_solutions_104_4_Heuristic.csv"
)
VEHICLE_103_PATH = (
    MFSTSP_ROOT
    / "20170608T131251001523"
    / "tbl_solutions_103_1_Heuristic.csv"
)
MULTI_UAV_IP_WAIT_PATH = (
    MFSTSP_ROOT
    / "20170608T121949065533"
    / "tbl_solutions_102_4_IP.csv"
)
MULTI_UAV_IP_PATH = (
    MFSTSP_ROOT
    / "20170608T121949065533"
    / "tbl_solutions_104_4_IP.csv"
)
TRUCK_ONLY_PATH = (
    MFSTSP_ROOT
    / "20170608T121458174165"
    / "tbl_solutions_101_1_Heuristic.csv"
)
WIND_MICRO_PATH = (
    MFSTSP_ROOT
    / "demo_wind_1drone_ns"
    / "tbl_solutions_990_1_IP.csv"
)


def test_list_mfstsp_cases_discovers_known_case():
    cases = dvk.list_mfstsp_cases(MFSTSP_ROOT)

    assert cases
    assert isinstance(cases[0], MfstspCase)
    assert any(case.solution_path == HEURISTIC_PATH for case in cases)

    known_case = next(case for case in cases if case.solution_path == HEURISTIC_PATH)
    assert known_case.problem_name == "20170608T131251001523"
    assert known_case.vehicle_file_id == 101
    assert known_case.num_uavs == 1
    assert known_case.solution_type == "Heuristic"


def test_from_mfstsp_parses_single_uav_heuristic_solution():
    solution = dvk.from_mfstsp(HEURISTIC_PATH)

    assert solution.problem.depot == pytest.approx((47.650031, -122.385323))
    assert solution.problem.drone_eligible == [2, 3, 4, 5, 6, 7, 8]
    assert solution.truck_route == [0, 6, 4, 8, 3, 2, 5, 1, 0]
    assert solution.truck_arrival_times == pytest.approx(
        {
            6: 274.699613,
            4: 1402.723344,
            8: 1620.189471,
            3: 2365.471963,
            2: 3518.135470,
            5: 4077.803195,
            1: 4736.384639,
        }
    )
    assert solution.truck_leg_travel_times == pytest.approx(
        [274.699613, 1098.023731, 187.466127, 715.282492, 1122.663507, 469.667725, 598.581444, 1035.252709]
    )
    assert solution.planned_truck_timeline is not None
    assert solution.planned_truck_timeline[0].kind == "move"
    assert solution.planned_truck_timeline[0].start_node == 0
    assert solution.planned_truck_timeline[0].end_node == 6
    assert any(segment.kind == "dwell" for segment in solution.planned_truck_timeline)
    assert solution.num_drones == 1
    assert solution.truck_speed == pytest.approx(17.082248249816843)
    assert solution.planned_metrics.drone_speed == pytest.approx(31.2928)
    assert solution.planned_metrics.makespan == pytest.approx(5801.637348)
    assert solution.planned_metrics.sortie_times == pytest.approx([559.667725])
    assert [leg.name for leg in solution.planned_metrics.sortie_leg_times[0]] == [
        "launch_prep",
        "launch_takeoff",
        "outbound",
        "delivery_land",
        "delivery",
        "delivery_takeoff",
        "return",
        "waiting",
        "recovery_land",
        "recovery",
    ]
    assert [(sortie.drone_id, sortie.launch, sortie.delivery, sortie.rendezvous) for sortie in solution.sorties] == [
        (0, 2, 7, 5)
    ]


def test_from_mfstsp_parses_single_uav_ip_solution():
    solution = dvk.from_mfstsp(IP_PATH)

    assert solution.truck_route == [0, 5, 2, 1, 3, 8, 4, 6, 0]
    assert solution.num_drones == 1
    assert solution.planned_metrics.makespan == pytest.approx(5604.002792)
    assert solution.planned_metrics.sortie_times == pytest.approx([626.265328])
    assert [(sortie.drone_id, sortie.launch, sortie.delivery, sortie.rendezvous) for sortie in solution.sorties] == [
        (0, 5, 7, 2)
    ]


def test_from_mfstsp_parses_multi_uav_solution():
    solution = dvk.from_mfstsp(MULTI_UAV_PATH)

    assert solution.truck_route == [0, 1, 6, 0]
    assert solution.num_drones == 4
    assert solution.planned_metrics.drone_speed == pytest.approx(15.6464)
    assert solution.planned_metrics.sortie_times == pytest.approx(
        [1438.918133, 1168.037946, 1865.758460, 1865.758460, 1774.878273, 2084.387957]
    )
    assert [(sortie.drone_id, sortie.launch, sortie.delivery, sortie.rendezvous) for sortie in solution.sorties] == [
        (1, 0, 7, 1),
        (0, 0, 5, 1),
        (0, 1, 2, 6),
        (3, 1, 8, 6),
        (2, 1, 4, 6),
        (1, 1, 3, 0),
    ]


def test_from_mfstsp_loads_vehicle_speed_profile():
    solution = dvk.from_mfstsp(VEHICLE_103_PATH)

    assert solution.planned_metrics.drone_speed == pytest.approx(15.6464)
    assert solution.truck_service_time == pytest.approx(30.0)
    assert solution.planned_metrics.vehicle_speeds.takeoff == pytest.approx(7.8232)
    assert solution.planned_metrics.vehicle_speeds.cruise == pytest.approx(15.6464)
    assert solution.planned_metrics.vehicle_speeds.landing == pytest.approx(3.9116)
    assert solution.planned_metrics.vehicle_speeds.yaw_rate_deg == pytest.approx(360.0)
    assert solution.planned_metrics.vehicle_speeds.launch_time == pytest.approx(60.0)
    assert solution.planned_metrics.vehicle_speeds.recovery_time == pytest.approx(30.0)
    assert solution.planned_metrics.vehicle_speeds.cruise_altitude == pytest.approx(50.0)


def test_from_mfstsp_splits_waiting_before_collection_when_truck_is_late():
    solution = dvk.from_mfstsp(MULTI_UAV_IP_WAIT_PATH)

    waiting_legs = [
        leg
        for sortie_legs in solution.planned_metrics.sortie_leg_times or []
        for leg in sortie_legs
        if leg.name == "waiting"
    ]
    collection_legs = [
        leg
        for sortie_legs in solution.planned_metrics.sortie_leg_times or []
        for leg in sortie_legs
        if leg.name == "recovery"
    ]

    assert waiting_legs
    assert collection_legs
    assert max(leg.duration for leg in waiting_legs) > 100.0


def test_from_mfstsp_allows_truck_only_case():
    solution = dvk.from_mfstsp(TRUCK_ONLY_PATH)

    assert solution.num_drones == 1
    assert solution.truck_route == [0, 2, 5, 4, 1, 3, 6, 8, 7, 0]
    assert solution.sorties == []
    assert solution.planned_metrics.sortie_times == []


def test_from_mfstsp_parses_custom_wind_micro_case():
    solution = dvk.from_mfstsp(WIND_MICRO_PATH)

    assert solution.problem.depot == pytest.approx((42.0, -78.0))
    assert solution.problem.customers[1] == pytest.approx((42.00045, -78.0))
    assert solution.problem.customers[2] == pytest.approx((42.0009, -78.0))
    assert solution.problem.drone_eligible == [1]
    assert solution.truck_route == [0, 2, 0]
    assert solution.num_drones == 1
    assert solution.planned_metrics.makespan == pytest.approx(186.0)
    assert solution.planned_metrics.sortie_times == pytest.approx([150.0])
    assert [
        (sortie.drone_id, sortie.launch, sortie.delivery, sortie.rendezvous)
        for sortie in solution.sorties
    ] == [
        (0, 0, 1, 2),
    ]


def test_save_mfstsp_event_log_writes_solver_style_csv(tmp_path):
    solution = dvk.from_mfstsp(HEURISTIC_PATH)
    sortie_timings = solution.planned_metrics.sortie_leg_times[0]

    run = RunResult(
        condition=dvk.WindCondition.calm(),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=0,
                        actual_time=solution.planned_metrics.sortie_times[0],
                        actual_energy=10.0,
                        actual_distance=1000.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=90.0,
                        corrected_battery_at_end=90.0,
                        feasible=True,
                        max_position_error=0.0,
                        start_time=float(sortie_timings[0].start_time),
                        end_time=float(sortie_timings[-1].end_time),
                        leg_timings=sortie_timings,
                    )
                ],
                reposition_results=[],
                actual_makespan=solution.planned_metrics.sortie_times[0],
                raw_makespan=solution.planned_metrics.makespan,
                ulog_path="",
            )
        ],
        actual_makespan=solution.planned_metrics.sortie_times[0],
        raw_makespan=solution.planned_metrics.makespan,
    )

    output_path = tmp_path / "actual_event_log.csv"
    save_mfstsp_event_log(solution, run, HEURISTIC_PATH, output_path)

    text = output_path.read_text()

    assert "Objective Function Value: 5801.637348" in text
    assert "1, Truck, Truck is stationary with UAV(s) on board, 3548.135470, 2, 3608.135470, 2, Launching UAV 2, UAV Launch" in text
    assert "2, UAV, UAV is stationary with a parcel, 3548.135470, 2, 3608.135470, 2, Prepare to launch from truck, UAV Launch" in text
    assert "2, UAV, UAV taking off or landing with no parcels, 4077.803195, 5, 4107.803195, 5, Recovered by truck at customer 5, UAV Recovery" not in text
    assert "2, UAV, UAV is stationary without a parcel, 4077.803195, 5, 4107.803195, 5, Recovered by truck at customer 5, UAV Recovery" in text


def test_load_mfstsp_event_log_round_trips_example_plan_exactly(tmp_path):
    planned_log = load_mfstsp_event_log(MULTI_UAV_IP_PATH)
    output_path = tmp_path / "planned_roundtrip.csv"

    planned_log.to_csv(output_path)

    assert output_path.read_bytes() == MULTI_UAV_IP_PATH.read_bytes()


def test_build_actual_mfstsp_event_log_reuses_exact_planned_header_format():
    planned_log = load_mfstsp_event_log(HEURISTIC_PATH)
    solution = dvk.from_mfstsp(HEURISTIC_PATH)
    sortie_timings = solution.planned_metrics.sortie_leg_times[0]
    run = RunResult(
        condition=dvk.WindCondition.calm(),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=0,
                        actual_time=solution.planned_metrics.sortie_times[0],
                        actual_energy=10.0,
                        actual_distance=1000.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=90.0,
                        corrected_battery_at_end=90.0,
                        feasible=True,
                        max_position_error=0.0,
                        start_time=float(sortie_timings[0].start_time),
                        end_time=float(sortie_timings[-1].end_time),
                        leg_timings=sortie_timings,
                    )
                ],
                reposition_results=[],
                actual_makespan=solution.planned_metrics.sortie_times[0],
                raw_makespan=solution.planned_metrics.makespan,
                ulog_path="",
            )
        ],
        actual_makespan=solution.planned_metrics.sortie_times[0],
        raw_makespan=solution.planned_metrics.makespan,
    )

    actual_log = build_actual_mfstsp_event_log(solution, run, planned_log, source_solution_path=HEURISTIC_PATH)

    assert actual_log.metadata_header_line == planned_log.metadata_header_line
    assert actual_log.metadata_values_line == planned_log.metadata_values_line
    assert actual_log.assignment_header_line == planned_log.assignment_header_line
    assert actual_log.rows[0].vehicle_id == 1
    assert actual_log.rows[0].start_time == pytest.approx(0.0)
    assert actual_log.rows[0].status == "Traveling"
    vehicle_ids = [row.vehicle_id for row in actual_log.rows]
    assert vehicle_ids == sorted(vehicle_ids)


def test_build_actual_mfstsp_event_log_keeps_truck_rows_sequential_without_startup_idle():
    planned_log = load_mfstsp_event_log(MULTI_UAV_IP_PATH)
    solution = dvk.from_mfstsp(MULTI_UAV_IP_PATH)
    sortie_leg_times = solution.planned_metrics.sortie_leg_times or []

    adjusted_sorties = []
    for sortie_index, leg_timings in enumerate(sortie_leg_times):
        adjusted_legs = []
        for leg in leg_timings:
            start_time = float(leg.start_time)
            end_time = float(leg.end_time)
            if sortie_index == 0:
                start_time += 0.523827
                end_time += 0.523827
            if sortie_index == 2 and leg.name == "recovery":
                start_time -= 4.634003
                end_time -= 4.634003
            adjusted_legs.append(
                type(leg)(
                    name=leg.name,
                    start_time=start_time,
                    end_time=end_time,
                )
            )
        adjusted_sorties.append(
            SortieResult(
                drone_id=solution.sorties[sortie_index].drone_id,
                sortie_index=sortie_index,
                actual_time=float(adjusted_legs[-1].end_time) - float(adjusted_legs[0].start_time),
                actual_energy=10.0,
                actual_distance=1000.0,
                actual_path=[],
                raw_battery_at_start=100.0,
                raw_battery_at_end=90.0,
                corrected_battery_at_end=90.0,
                feasible=True,
                max_position_error=0.0,
                start_time=float(adjusted_legs[0].start_time),
                end_time=float(adjusted_legs[-1].end_time),
                leg_timings=adjusted_legs,
            )
        )

    run = RunResult(
        condition=dvk.WindCondition.calm(),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=drone_id,
                sortie_results=[result for result in adjusted_sorties if result.drone_id == drone_id],
                reposition_results=[],
                actual_makespan=max((result.end_time for result in adjusted_sorties if result.drone_id == drone_id), default=0.0),
                raw_makespan=0.0,
                ulog_path="",
            )
            for drone_id in sorted({result.drone_id for result in adjusted_sorties})
        ],
        actual_makespan=max(result.end_time for result in adjusted_sorties),
        raw_makespan=max(result.end_time for result in adjusted_sorties),
    )

    actual_log = build_actual_mfstsp_event_log(
        solution,
        run,
        planned_log,
        source_solution_path=MULTI_UAV_IP_PATH,
    )

    truck_rows = [row for row in actual_log.rows if row.vehicle_id == 1]

    assert truck_rows
    assert truck_rows[0].status != "Idle"
    assert truck_rows[0].start_time == pytest.approx(0.0)

    previous_end = 0.0
    for row in truck_rows:
        assert row.start_time >= previous_end - 1e-9
        if row.end_time >= 0.0:
            previous_end = row.end_time
        else:
            previous_end = row.start_time
