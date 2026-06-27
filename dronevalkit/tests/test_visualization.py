"""Tests for route visualization helpers."""

import io
from pathlib import Path

import numpy as np
from PIL import Image

import dronevalkit as dvk
from dronevalkit.analysis import ComparisonReport
from dronevalkit.config import WindCondition
from dronevalkit.io import MfstspEventLog, MfstspEventRow
from dronevalkit.logs import DroneRunResult, LegEnergySample, RepositionResult, RunResult, SortieResult
from dronevalkit.models import LegTiming
from dronevalkit.visualization import (
    _build_actual_gantt_panel,
    _build_event_log_gantt_panel,
    _build_planned_gantt_panel,
    _decode_tile_image,
    _gantt_segment_text,
    _order_route_legend,
    _tile_url,
)


def _make_solution() -> dvk.Solution:
    problem = dvk.Problem(
        depot=(38.9404, -92.3277),
        customers={
            1: (38.9410, -92.3285),
            2: (38.9417, -92.3269),
            3: (38.9401, -92.3266),
        },
        drone_eligible=[1, 3],
    )
    return dvk.Solution(
        problem=problem,
        truck_route=[0, 2, 0],
        sorties=[
            dvk.Sortie(delivery=1, rendezvous=2, drone_id=0),
            dvk.Sortie(delivery=3, rendezvous=2, drone_id=1),
        ],
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=12.0,
            makespan=250.0,
            sortie_times=[40.0, 60.0],
        ),
        num_drones=2,
        truck_speed=9.0,
    )


def test_save_experiment_route_writes_image(tmp_path, monkeypatch):
    def _fake_basemap(lat_min, lat_max, lon_min, lon_max, zoom):
        image = np.ones((32, 32, 3), dtype=float)
        return image, (lon_min, lon_max, lat_min, lat_max)

    monkeypatch.setattr("dronevalkit.visualization._fetch_satellite_basemap", _fake_basemap)

    output_path = tmp_path / "route.png"
    dvk.save_experiment_route(_make_solution(), str(output_path))

    assert output_path.exists()
    assert output_path.stat().st_size > 0
    assert isinstance(output_path, Path)


def test_decode_tile_image_supports_png_and_jpeg():
    rgb = np.zeros((8, 8, 3), dtype=np.uint8)
    rgb[..., 0] = 255

    png_buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(png_buffer, format="PNG")
    jpeg_buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(jpeg_buffer, format="JPEG")

    png = _decode_tile_image(png_buffer.getvalue())
    jpeg = _decode_tile_image(jpeg_buffer.getvalue())

    assert png.shape == (8, 8, 3)
    assert jpeg.shape == (8, 8, 3)
    assert float(png[0, 0, 0]) > 0.9
    assert float(jpeg[0, 0, 0]) > 0.9


def test_tile_url_uses_carto_voyager_template():
    url = _tile_url(10, 20, 12)

    assert url.startswith("https://")
    assert ".basemaps.cartocdn.com/rastertiles/voyager/12/10/20.png" in url


def test_order_route_legend_sorts_drones_numerically():
    handles = list(range(6))
    labels = [
        "Drone 2",
        "Truck route",
        "Drone 0",
        "Customer",
        "Drone 1",
        "Depot",
    ]

    ordered_handles, ordered_labels = _order_route_legend(handles, labels)

    assert ordered_labels == [
        "Truck route",
        "Depot",
        "Customer",
        "Drone 0",
        "Drone 1",
        "Drone 2",
    ]
    assert ordered_handles == [1, 5, 3, 2, 4, 0]


def test_planned_gantt_panel_includes_wait_for_truck_segment():
    problem = dvk.Problem(
        depot=(38.9404, -92.3277),
        customers={
            1: (38.9410, -92.3285),
            2: (38.9425, -92.3270),
            3: (38.9432, -92.3260),
            4: (38.9440, -92.3255),
        },
        drone_eligible=[3, 4],
    )
    solution = dvk.Solution(
        problem=problem,
        truck_route=[0, 1, 2, 0],
        sorties=[
            dvk.Sortie(delivery=3, rendezvous=1, drone_id=0),
            dvk.Sortie(delivery=4, rendezvous=0, launch=2, drone_id=0),
        ],
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=12.0,
            makespan=500.0,
            sortie_times=[40.0, 35.0],
                sortie_leg_times=[
                    [
                        LegTiming(name="launch_prep", start_time=0.0, end_time=2.0),
                        LegTiming(name="launch_takeoff", start_time=2.0, end_time=5.0),
                        LegTiming(name="outbound", start_time=5.0, end_time=20.0),
                        LegTiming(name="delivery_land", start_time=20.0, end_time=22.0),
                        LegTiming(name="delivery", start_time=22.0, end_time=24.0),
                        LegTiming(name="delivery_takeoff", start_time=24.0, end_time=28.0),
                        LegTiming(name="return", start_time=28.0, end_time=36.0),
                        LegTiming(name="waiting", start_time=36.0, end_time=38.0),
                        LegTiming(name="recovery_land", start_time=38.0, end_time=39.0),
                        LegTiming(name="recovery", start_time=39.0, end_time=40.0),
                    ],
                    [
                        LegTiming(name="launch_prep", start_time=0.0, end_time=1.0),
                        LegTiming(name="launch_takeoff", start_time=1.0, end_time=4.0),
                        LegTiming(name="outbound", start_time=4.0, end_time=16.0),
                        LegTiming(name="delivery_land", start_time=16.0, end_time=18.0),
                        LegTiming(name="delivery", start_time=18.0, end_time=20.0),
                        LegTiming(name="delivery_takeoff", start_time=20.0, end_time=23.0),
                        LegTiming(name="return", start_time=23.0, end_time=31.0),
                        LegTiming(name="waiting", start_time=31.0, end_time=33.0),
                        LegTiming(name="recovery_land", start_time=33.0, end_time=34.0),
                        LegTiming(name="recovery", start_time=34.0, end_time=35.0),
                    ],
                ],
            ),
        num_drones=1,
        truck_speed=4.0,
    )

    panel = _build_planned_gantt_panel(solution)
    drone_segments = panel["lanes"][1]["segments"]
    wait_segments = [segment for segment in drone_segments if segment["kind"] == "wait_truck"]
    sortie_leg_segments = [segment for segment in drone_segments if segment["kind"] == "sortie_leg"]

    assert wait_segments
    assert wait_segments[0]["end"] > wait_segments[0]["start"]
    assert wait_segments[0]["label"] == "Wait @ 2"
    assert [segment["leg_name"] for segment in sortie_leg_segments[:8]] == [
        "launch_prep",
        "launch_takeoff",
        "outbound",
        "delivery_land",
        "delivery",
        "delivery_takeoff",
        "return",
        "waiting",
    ]


def test_planned_gantt_panel_prefers_explicit_truck_timeline():
    solution = _make_solution()
    solution.planned_truck_timeline = [
        {
            "kind": "dwell",
            "start_time": 0.0,
            "end_time": 25.0,
            "start_node": 0,
            "end_node": 0,
            "label": "Launching UAV 1",
        },
        {
            "kind": "move",
            "start_time": 25.0,
            "end_time": 40.0,
            "start_node": 0,
            "end_node": 2,
            "label": "Travel from node 0 to node 2",
        },
    ]

    panel = _build_planned_gantt_panel(solution)
    truck_segments = panel["lanes"][0]["segments"]

    assert truck_segments == [
        {"kind": "truck_wait", "start": 0.0, "end": 25.0, "label": "L1"},
        {"kind": "truck_move", "start": 25.0, "end": 40.0, "label": "0->2"},
    ]


def test_planned_gantt_panel_falls_back_when_truck_timeline_label_is_blank():
    solution = _make_solution()
    solution.planned_truck_timeline = [
        {
            "kind": "dwell",
            "start_time": 0.0,
            "end_time": 25.0,
            "start_node": 0,
            "end_node": 0,
            "label": "   ",
        },
        {
            "kind": "move",
            "start_time": 25.0,
            "end_time": 40.0,
            "start_node": 0,
            "end_node": 2,
            "label": "Travel from node 0 to node 2",
        },
    ]

    panel = _build_planned_gantt_panel(solution)
    truck_segments = panel["lanes"][0]["segments"]

    assert truck_segments[0]["label"] == "Node 0"


def test_planned_gantt_panel_uses_absolute_planned_leg_timing_when_available():
    problem = dvk.Problem(
        depot=(38.9404, -92.3277),
        customers={
            1: (38.9410, -92.3285),
            2: (38.9425, -92.3270),
            3: (38.9432, -92.3260),
        },
        drone_eligible=[1, 3],
    )
    solution = dvk.Solution(
        problem=problem,
        truck_route=[0, 2, 0],
        sorties=[
            dvk.Sortie(delivery=1, rendezvous=2, drone_id=1),
            dvk.Sortie(delivery=3, rendezvous=2, drone_id=0),
        ],
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=12.0,
            makespan=180.0,
            sortie_times=[60.0, 70.0],
            sortie_leg_times=[
                [
                    LegTiming(name="launch", start_time=60.0, end_time=70.0),
                    LegTiming(name="outbound", start_time=70.0, end_time=90.0),
                    LegTiming(name="delivery_land", start_time=90.0, end_time=95.0),
                    LegTiming(name="delivery", start_time=95.0, end_time=100.0),
                    LegTiming(name="delivery_takeoff", start_time=100.0, end_time=105.0),
                    LegTiming(name="return", start_time=105.0, end_time=115.0),
                    LegTiming(name="collection", start_time=115.0, end_time=120.0),
                ],
                [
                    LegTiming(name="launch", start_time=0.0, end_time=10.0),
                    LegTiming(name="outbound", start_time=10.0, end_time=30.0),
                    LegTiming(name="delivery_land", start_time=30.0, end_time=35.0),
                    LegTiming(name="delivery", start_time=35.0, end_time=40.0),
                    LegTiming(name="delivery_takeoff", start_time=40.0, end_time=45.0),
                    LegTiming(name="return", start_time=45.0, end_time=65.0),
                    LegTiming(name="collection", start_time=65.0, end_time=70.0),
                ],
            ],
        ),
        num_drones=2,
        truck_speed=4.0,
    )

    panel = _build_planned_gantt_panel(solution)

    drone0_segments = panel["lanes"][1]["segments"]
    drone1_segments = panel["lanes"][2]["segments"]
    drone1_sortie_segments = [segment for segment in drone1_segments if segment["kind"] == "sortie_leg"]

    assert drone0_segments[0]["start"] == 0.0
    assert drone0_segments[-1]["end"] == 70.0
    assert drone1_segments[0]["kind"] == "wait_truck"
    assert drone1_segments[0]["start"] == 0.0
    assert drone1_segments[0]["end"] == 60.0
    assert drone1_sortie_segments[0]["start"] == 60.0
    assert drone1_sortie_segments[-1]["end"] == 120.0


def test_actual_gantt_panel_includes_reposition_and_wait_segments():
    problem = dvk.Problem(
        depot=(38.9404, -92.3277),
        customers={
            1: (38.9410, -92.3285),
            2: (38.9425, -92.3270),
            3: (38.9432, -92.3260),
            4: (38.9440, -92.3255),
        },
        drone_eligible=[3, 4],
    )
    solution = dvk.Solution(
        problem=problem,
        truck_route=[0, 1, 2, 0],
        sorties=[
            dvk.Sortie(delivery=3, rendezvous=1, drone_id=0),
            dvk.Sortie(delivery=4, rendezvous=0, launch=2, drone_id=0),
        ],
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=12.0,
            makespan=500.0,
            sortie_times=[40.0, 35.0],
            sortie_leg_times=[
                [
                    LegTiming(name="launch", start_time=0.0, end_time=5.0),
                    LegTiming(name="outbound", start_time=5.0, end_time=20.0),
                    LegTiming(name="delivery_land", start_time=20.0, end_time=22.0),
                    LegTiming(name="delivery", start_time=22.0, end_time=24.0),
                    LegTiming(name="delivery_takeoff", start_time=24.0, end_time=28.0),
                    LegTiming(name="return", start_time=28.0, end_time=36.0),
                    LegTiming(name="collection", start_time=36.0, end_time=40.0),
                ],
                [
                    LegTiming(name="launch", start_time=0.0, end_time=4.0),
                    LegTiming(name="outbound", start_time=4.0, end_time=16.0),
                    LegTiming(name="delivery_land", start_time=16.0, end_time=18.0),
                    LegTiming(name="delivery", start_time=18.0, end_time=20.0),
                    LegTiming(name="delivery_takeoff", start_time=20.0, end_time=23.0),
                    LegTiming(name="return", start_time=23.0, end_time=31.0),
                    LegTiming(name="collection", start_time=31.0, end_time=35.0),
                ],
            ],
        ),
        num_drones=1,
        truck_speed=4.0,
    )
    run = RunResult(
        condition=WindCondition.calm(),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=0,
                        actual_time=45.0,
                        actual_energy=10.0,
                        actual_distance=500.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=90.0,
                        corrected_battery_at_end=90.0,
                        feasible=True,
                        max_position_error=1.0,
                        start_time=0.0,
                        end_time=45.0,
                        leg_timings=[
                            LegTiming(name="launch", start_time=0.0, end_time=6.0),
                            LegTiming(name="outbound", start_time=6.0, end_time=20.0),
                            LegTiming(name="delivery_land", start_time=20.0, end_time=22.0),
                            LegTiming(name="delivery", start_time=22.0, end_time=24.0),
                            LegTiming(name="delivery_takeoff", start_time=24.0, end_time=28.0),
                            LegTiming(name="return", start_time=28.0, end_time=39.0),
                            LegTiming(name="collection", start_time=39.0, end_time=45.0),
                        ],
                    ),
                    SortieResult(
                        drone_id=0,
                        sortie_index=1,
                        actual_time=35.0,
                        actual_energy=9.0,
                        actual_distance=480.0,
                        actual_path=[],
                        raw_battery_at_start=90.0,
                        raw_battery_at_end=81.0,
                        corrected_battery_at_end=81.0,
                        feasible=True,
                        max_position_error=1.0,
                        start_time=120.0,
                        end_time=155.0,
                        leg_timings=[
                            LegTiming(name="launch", start_time=120.0, end_time=124.0),
                            LegTiming(name="outbound", start_time=124.0, end_time=136.0),
                            LegTiming(name="delivery_land", start_time=136.0, end_time=138.0),
                            LegTiming(name="delivery", start_time=138.0, end_time=140.0),
                            LegTiming(name="delivery_takeoff", start_time=140.0, end_time=143.0),
                            LegTiming(name="return", start_time=143.0, end_time=151.0),
                            LegTiming(name="collection", start_time=151.0, end_time=155.0),
                        ],
                    ),
                ],
                reposition_results=[
                    RepositionResult(
                        drone_id=0,
                        from_rendezvous=1,
                        to_launch=2,
                        time=25.0,
                        energy=2.0,
                        distance=150.0,
                        start_time=45.0,
                        end_time=70.0,
                    )
                ],
                actual_makespan=80.0,
                raw_makespan=200.0,
                ulog_path="",
            )
        ],
        actual_makespan=80.0,
        raw_makespan=200.0,
    )
    solution.planned_truck_timeline = [
        {
            "kind": "dwell",
            "start_time": 0.0,
            "end_time": 20.0,
            "start_node": 0,
            "end_node": 0,
            "label": "Launching UAV 1",
        },
        {
            "kind": "move",
            "start_time": 20.0,
            "end_time": 60.0,
            "start_node": 0,
            "end_node": 1,
            "label": "Travel from node 0 to node 1",
        },
        {
            "kind": "dwell",
            "start_time": 60.0,
            "end_time": 90.0,
            "start_node": 1,
            "end_node": 1,
            "label": "Retrieving UAV 1",
        },
        {
            "kind": "move",
            "start_time": 90.0,
            "end_time": 120.0,
            "start_node": 1,
            "end_node": 2,
            "label": "Travel from node 1 to node 2",
        },
        {
            "kind": "dwell",
            "start_time": 120.0,
            "end_time": 145.0,
            "start_node": 2,
            "end_node": 2,
            "label": "Launching UAV 1",
        },
        {
            "kind": "move",
            "start_time": 145.0,
            "end_time": 170.0,
            "start_node": 2,
            "end_node": 0,
            "label": "Travel from node 2 to node 0",
        },
        {
            "kind": "dwell",
            "start_time": 170.0,
            "end_time": 200.0,
            "start_node": 0,
            "end_node": 0,
            "label": "Retrieving UAV 1",
        },
    ]

    panel = _build_actual_gantt_panel(solution, run)
    truck_segments = panel["lanes"][0]["segments"]
    drone_segments = panel["lanes"][1]["segments"]

    assert [segment["kind"] for segment in truck_segments] == [
        "truck_wait",
        "truck_move",
        "truck_wait",
        "truck_move",
        "truck_wait",
        "truck_move",
    ]
    assert truck_segments[0]["label"] == "L1"
    assert truck_segments[2]["label"] == "R1"
    assert truck_segments[4]["label"] == "L1"
    assert [segment["kind"] for segment in drone_segments] == [
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
        "reposition",
        "wait_truck",
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
        "sortie_leg",
    ]


def test_plot_leg_energy_writes_pdf(tmp_path):
    solution = _make_solution()
    run = RunResult(
        condition=WindCondition.moderate(speed=5.0, direction=0.0),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=1,
                        actual_time=35.0,
                        actual_energy=9.0,
                        actual_distance=480.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=91.0,
                        corrected_battery_at_end=91.0,
                        feasible=True,
                        max_position_error=1.0,
                        start_time=0.0,
                        end_time=35.0,
                        leg_energy_samples=[
                            LegEnergySample(
                                name="launch_takeoff",
                                start_time=0.0,
                                end_time=4.0,
                                raw_battery_at_start=100.0,
                                raw_battery_at_end=98.5,
                                energy_pct=1.5,
                            ),
                            LegEnergySample(
                                name="outbound",
                                start_time=4.0,
                                end_time=16.0,
                                raw_battery_at_start=98.5,
                                raw_battery_at_end=94.0,
                                energy_pct=4.5,
                            ),
                        ],
                    )
                ],
                reposition_results=[
                    RepositionResult(
                        drone_id=0,
                        from_rendezvous=2,
                        to_launch=0,
                        time=10.0,
                        energy=2.0,
                        distance=150.0,
                        start_time=36.0,
                        end_time=46.0,
                        leg_energy_samples=[
                            LegEnergySample(
                                name="reposition_transit",
                                start_time=38.0,
                                end_time=44.0,
                                raw_battery_at_start=90.5,
                                raw_battery_at_end=88.5,
                                energy_pct=2.0,
                            )
                        ],
                    )
                ],
                actual_makespan=35.0,
                raw_makespan=46.0,
                ulog_path="",
            )
        ],
        actual_makespan=35.0,
        raw_makespan=46.0,
    )
    report = ComparisonReport(solution, [run])

    output_path = tmp_path / "leg_energy.pdf"
    report.plot_leg_energy(str(output_path))

    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_actual_gantt_panel_shows_prelaunch_wait_inside_sortie_window():
    problem = dvk.Problem(
        depot=(38.9404, -92.3277),
        customers={
            1: (38.9410, -92.3285),
            2: (38.9425, -92.3270),
        },
        drone_eligible=[1],
    )
    solution = dvk.Solution(
        problem=problem,
        truck_route=[0, 2],
        sorties=[dvk.Sortie(delivery=1, rendezvous=2, drone_id=0)],
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=12.0,
            makespan=180.0,
            sortie_times=[120.0],
        ),
        num_drones=1,
        truck_speed=4.0,
    )
    run = RunResult(
        condition=WindCondition.calm(),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=0,
                        actual_time=120.0,
                        actual_energy=10.0,
                        actual_distance=500.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=90.0,
                        corrected_battery_at_end=90.0,
                        feasible=True,
                        max_position_error=1.0,
                        start_time=0.0,
                        end_time=120.0,
                        leg_timings=[
                            LegTiming(name="launch", start_time=60.0, end_time=70.0),
                            LegTiming(name="outbound", start_time=70.0, end_time=90.0),
                            LegTiming(name="delivery_land", start_time=90.0, end_time=95.0),
                            LegTiming(name="delivery", start_time=95.0, end_time=100.0),
                            LegTiming(name="delivery_takeoff", start_time=100.0, end_time=105.0),
                            LegTiming(name="return", start_time=105.0, end_time=115.0),
                            LegTiming(name="collection", start_time=115.0, end_time=120.0),
                        ],
                    ),
                ],
                reposition_results=[],
                actual_makespan=120.0,
                raw_makespan=120.0,
                ulog_path="",
            )
        ],
        actual_makespan=120.0,
        raw_makespan=120.0,
    )

    panel = _build_actual_gantt_panel(solution, run)
    drone_segments = panel["lanes"][1]["segments"]

    assert drone_segments[0]["kind"] == "wait_truck"
    assert drone_segments[0]["label"] == "Wait @ 0"
    assert drone_segments[0]["start"] == 0.0
    assert drone_segments[0]["end"] == 60.0
    assert drone_segments[1]["kind"] == "sortie_leg"
    assert drone_segments[1]["leg_name"] == "launch"


def test_event_log_gantt_panel_uses_csv_descriptions_and_vehicle_ids():
    event_log = MfstspEventLog(
        metadata_header_line="h",
        metadata_values_line="v",
        objective_value=120.0,
        assignment_header_line="a",
        rows=[
            MfstspEventRow(
                vehicle_id=1,
                vehicle_type="Truck",
                activity_type="Truck is stationary with UAV(s) on board",
                start_time=0.0,
                start_node=0,
                end_time=60.0,
                end_node=0,
                description="Launching UAV 2",
                status="UAV Launch",
            ),
            MfstspEventRow(
                vehicle_id=2,
                vehicle_type="UAV",
                activity_type="UAV travels with parcel",
                start_time=60.0,
                start_node=0,
                end_time=80.0,
                end_node=2,
                description="Fly to UAV customer 2",
                status="Traveling",
            ),
        ],
    )

    panel = _build_event_log_gantt_panel(event_log, title="Planned")

    assert panel["lanes"][0]["label"] == "Truck"
    assert panel["lanes"][0]["segments"][0]["label"] == "Launching UAV 2"
    assert panel["lanes"][0]["segments"][0]["activity_type"] == "Truck is stationary with UAV(s) on board"
    assert panel["lanes"][1]["label"] == "UAV 2"
    assert panel["lanes"][1]["segments"][0]["label"] == "Fly to UAV customer 2"
    assert "Truck is stationary with UAV(s) on board" in panel["activity_types"]
    assert "UAV travels with parcel" in panel["activity_types"]


def test_event_log_gantt_text_wraps_long_descriptions_and_hides_tiny_bars():
    wrapped = _gantt_segment_text(
        {
            "kind": "event_log",
            "label": "Prepare to launch from truck at customer 12",
        },
        duration=25.0,
        xmax=100.0,
    )
    hidden = _gantt_segment_text(
        {
            "kind": "event_log",
            "label": "Prepare to launch from truck",
        },
        duration=1.0,
        xmax=100.0,
    )

    assert "\n" in wrapped
    assert hidden == ""


def test_actual_gantt_panel_keeps_truck_at_launch_node_until_launch_prep_finishes():
    problem = dvk.Problem(
        depot=(42.0, -78.0),
        customers={
            1: (42.0004, -78.0),
            2: (42.00045, -78.00035),
            3: (42.00045, -77.99965),
        },
        drone_eligible=[2, 3],
    )
    solution = dvk.Solution(
        problem=problem,
        truck_route=[0, 1, 0],
        sorties=[
            dvk.Sortie(delivery=2, rendezvous=1, drone_id=0),
            dvk.Sortie(delivery=3, rendezvous=1, drone_id=1),
        ],
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=10.0,
            makespan=270.0,
            sortie_times=[180.0, 180.0],
        ),
        num_drones=2,
        truck_speed=1.5,
        truck_leg_travel_times=[30.0, 30.0],
        truck_service_time=30.0,
    )
    solution.planned_truck_timeline = [
        {
            "kind": "dwell",
            "start_time": 0.0,
            "end_time": 60.0,
            "start_node": 0,
            "end_node": 0,
            "label": "Launching UAV 2",
        },
        {
            "kind": "dwell",
            "start_time": 60.0,
            "end_time": 120.0,
            "start_node": 0,
            "end_node": 0,
            "label": "Launching UAV 3",
        },
        {
            "kind": "move",
            "start_time": 120.0,
            "end_time": 150.0,
            "start_node": 0,
            "end_node": 1,
            "label": "Travel from node 0 to node 1",
        },
        {
            "kind": "dwell",
            "start_time": 150.0,
            "end_time": 180.0,
            "start_node": 1,
            "end_node": 1,
            "label": "Retrieving UAV 2",
        },
        {
            "kind": "dwell",
            "start_time": 180.0,
            "end_time": 210.0,
            "start_node": 1,
            "end_node": 1,
            "label": "Dropping off package to Customer 1",
        },
        {
            "kind": "dwell",
            "start_time": 210.0,
            "end_time": 240.0,
            "start_node": 1,
            "end_node": 1,
            "label": "Retrieving UAV 3",
        },
        {
            "kind": "move",
            "start_time": 240.0,
            "end_time": 270.0,
            "start_node": 1,
            "end_node": 0,
            "label": "Travel from node 1 to node 0",
        },
    ]
    run = RunResult(
        condition=WindCondition.calm(),
        replication=0,
        drone_results=[
            DroneRunResult(
                drone_id=0,
                sortie_results=[
                    SortieResult(
                        drone_id=0,
                        sortie_index=0,
                        actual_time=180.0,
                        actual_energy=5.0,
                        actual_distance=100.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=95.0,
                        corrected_battery_at_end=95.0,
                        feasible=True,
                        max_position_error=0.0,
                        start_time=0.0,
                        end_time=180.0,
                        leg_timings=[
                            LegTiming(name="launch_prep", start_time=0.0, end_time=60.0),
                            LegTiming(name="launch_takeoff", start_time=60.0, end_time=90.0),
                            LegTiming(name="outbound", start_time=90.0, end_time=110.0),
                            LegTiming(name="delivery_land", start_time=110.0, end_time=115.0),
                            LegTiming(name="delivery", start_time=115.0, end_time=135.0),
                            LegTiming(name="delivery_takeoff", start_time=135.0, end_time=140.0),
                            LegTiming(name="return", start_time=140.0, end_time=150.0),
                            LegTiming(name="waiting", start_time=150.0, end_time=175.0),
                            LegTiming(name="recovery_land", start_time=175.0, end_time=180.0),
                        ],
                    ),
                ],
                reposition_results=[],
                actual_makespan=180.0,
                raw_makespan=270.0,
                ulog_path="",
            ),
            DroneRunResult(
                drone_id=1,
                sortie_results=[
                    SortieResult(
                        drone_id=1,
                        sortie_index=1,
                        actual_time=180.0,
                        actual_energy=5.0,
                        actual_distance=100.0,
                        actual_path=[],
                        raw_battery_at_start=100.0,
                        raw_battery_at_end=95.0,
                        corrected_battery_at_end=95.0,
                        feasible=True,
                        max_position_error=0.0,
                        start_time=60.0,
                        end_time=240.0,
                        leg_timings=[
                            LegTiming(name="launch_prep", start_time=60.0, end_time=120.0),
                            LegTiming(name="launch_takeoff", start_time=120.0, end_time=150.0),
                            LegTiming(name="outbound", start_time=150.0, end_time=170.0),
                            LegTiming(name="delivery_land", start_time=170.0, end_time=175.0),
                            LegTiming(name="delivery", start_time=175.0, end_time=195.0),
                            LegTiming(name="delivery_takeoff", start_time=195.0, end_time=200.0),
                            LegTiming(name="return", start_time=200.0, end_time=210.0),
                            LegTiming(name="waiting", start_time=210.0, end_time=235.0),
                            LegTiming(name="recovery_land", start_time=235.0, end_time=240.0),
                        ],
                    ),
                ],
                reposition_results=[],
                actual_makespan=240.0,
                raw_makespan=270.0,
                ulog_path="",
            ),
        ],
        actual_makespan=240.0,
        raw_makespan=270.0,
    )

    panel = _build_actual_gantt_panel(solution, run)
    truck_segments = panel["lanes"][0]["segments"]

    assert truck_segments[0]["kind"] == "truck_wait"
    assert truck_segments[0]["start"] == 0.0
    assert truck_segments[0]["end"] == 60.0
    assert truck_segments[1]["kind"] == "truck_wait"
    assert truck_segments[1]["start"] == 60.0
    assert truck_segments[1]["end"] == 120.0
    assert truck_segments[2]["kind"] == "truck_move"
    assert truck_segments[2]["start"] == 120.0
    assert truck_segments[2]["end"] == 150.0


def test_plot_gantt_uses_mfstsp_event_logs_when_attached(tmp_path):
    solution = _make_solution()
    run = RunResult(
        condition=WindCondition.calm(),
        replication=0,
        drone_results=[],
        actual_makespan=120.0,
        raw_makespan=120.0,
    )
    report = ComparisonReport(solution, [run])
    report.planned_event_log = MfstspEventLog(
        metadata_header_line="h",
        metadata_values_line="v",
        objective_value=120.0,
        assignment_header_line="a",
        rows=[
            MfstspEventRow(
                vehicle_id=1,
                vehicle_type="Truck",
                activity_type="Truck is stationary with UAV(s) on board",
                start_time=0.0,
                start_node=0,
                end_time=60.0,
                end_node=0,
                description="Launching UAV 2",
                status="UAV Launch",
            ),
            MfstspEventRow(
                vehicle_id=2,
                vehicle_type="UAV",
                activity_type="UAV is stationary with a parcel",
                start_time=0.0,
                start_node=0,
                end_time=60.0,
                end_node=0,
                description="Prepare to launch from truck",
                status="UAV Launch",
            ),
        ],
    )
    report.actual_event_logs = {
        ("Calm", 0): MfstspEventLog(
            metadata_header_line="h",
            metadata_values_line="v",
            objective_value=120.0,
            assignment_header_line="a",
            rows=[
                MfstspEventRow(
                    vehicle_id=1,
                    vehicle_type="Truck",
                    activity_type="Truck is stationary with UAV(s) on board",
                    start_time=0.0,
                    start_node=0,
                    end_time=62.0,
                    end_node=0,
                    description="Launching UAV 2",
                    status="UAV Launch",
                ),
                MfstspEventRow(
                    vehicle_id=2,
                    vehicle_type="UAV",
                    activity_type="UAV is stationary with a parcel",
                    start_time=0.0,
                    start_node=0,
                    end_time=62.0,
                    end_node=0,
                    description="Prepare to launch from truck",
                    status="UAV Launch",
                ),
            ],
        )
    }

    output_path = tmp_path / "event_log_gantt.pdf"
    report.plot_gantt(str(output_path))

    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_plot_gantt_writes_detailed_timeline_pdf(tmp_path):
    problem = dvk.Problem(
        depot=(38.9404, -92.3277),
        customers={
            1: (38.9410, -92.3285),
            2: (38.9425, -92.3270),
            3: (38.9432, -92.3260),
            4: (38.9440, -92.3255),
        },
        drone_eligible=[3, 4],
    )
    solution = dvk.Solution(
        problem=problem,
        truck_route=[0, 1, 2, 0],
        sorties=[
            dvk.Sortie(delivery=3, rendezvous=1, drone_id=0),
            dvk.Sortie(delivery=4, rendezvous=0, launch=2, drone_id=0),
        ],
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=12.0,
            makespan=500.0,
            sortie_times=[40.0, 35.0],
        ),
        num_drones=1,
        truck_speed=4.0,
    )
    report = ComparisonReport(
        solution,
        [
            RunResult(
                condition=WindCondition.calm(),
                replication=0,
                drone_results=[
                    DroneRunResult(
                        drone_id=0,
                        sortie_results=[
                            SortieResult(
                                drone_id=0,
                                sortie_index=0,
                                actual_time=45.0,
                                actual_energy=10.0,
                                actual_distance=500.0,
                                actual_path=[],
                                raw_battery_at_start=100.0,
                                raw_battery_at_end=90.0,
                                corrected_battery_at_end=90.0,
                                feasible=True,
                                max_position_error=1.0,
                                start_time=0.0,
                                end_time=45.0,
                            ),
                            SortieResult(
                                drone_id=0,
                                sortie_index=1,
                                actual_time=35.0,
                                actual_energy=9.0,
                                actual_distance=480.0,
                                actual_path=[],
                                raw_battery_at_start=90.0,
                                raw_battery_at_end=81.0,
                                corrected_battery_at_end=81.0,
                                feasible=True,
                                max_position_error=1.0,
                                start_time=120.0,
                                end_time=155.0,
                            ),
                        ],
                        reposition_results=[
                            RepositionResult(
                                drone_id=0,
                                from_rendezvous=1,
                                to_launch=2,
                                time=25.0,
                                energy=2.0,
                                distance=150.0,
                                start_time=45.0,
                                end_time=70.0,
                            )
                        ],
                        actual_makespan=80.0,
                        raw_makespan=155.0,
                        ulog_path="",
                    )
                ],
                actual_makespan=80.0,
                raw_makespan=155.0,
            )
        ],
    )

    output_path = tmp_path / "gantt.pdf"
    report.plot_gantt(str(output_path))

    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_plot_scatter_energy_handles_missing_planned_energy(tmp_path):
    problem = dvk.Problem(
        depot=(38.9404, -92.3277),
        customers={1: (38.9410, -92.3285)},
        drone_eligible=[1],
    )
    solution = dvk.Solution(
        problem=problem,
        truck_route=[0, 0],
        sorties=[dvk.Sortie(delivery=1, rendezvous=0, drone_id=0)],
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=12.0,
            makespan=100.0,
            sortie_times=[40.0],
            sortie_energies=None,
        ),
    )
    report = ComparisonReport(
        solution,
        [
            RunResult(
                condition=WindCondition.calm(),
                replication=0,
                drone_results=[
                    DroneRunResult(
                        drone_id=0,
                        sortie_results=[
                            SortieResult(
                                drone_id=0,
                                sortie_index=0,
                                actual_time=45.0,
                                actual_energy=12.0,
                                actual_distance=100.0,
                                actual_path=[],
                                raw_battery_at_start=100.0,
                                raw_battery_at_end=88.0,
                                corrected_battery_at_end=88.0,
                                feasible=True,
                                max_position_error=0.0,
                            )
                        ],
                        reposition_results=[],
                        actual_makespan=45.0,
                        raw_makespan=45.0,
                        ulog_path="",
                    )
                ],
                actual_makespan=45.0,
                raw_makespan=45.0,
            )
        ],
    )

    output_path = tmp_path / "energy_scatter.pdf"
    report.plot_scatter(str(output_path), metric="energy")

    assert output_path.exists()
    assert output_path.stat().st_size > 0
