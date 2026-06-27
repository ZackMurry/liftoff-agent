"""Tests for curated validation test-bed helpers."""

from __future__ import annotations

import json
from pathlib import Path

import dronevalkit as dvk
from dronevalkit.test_bed import CuratedCase, build_curated_case


AGATZ_ROOT = Path(__file__).resolve().parent.parent / "problems" / "agatz"
MFSTSP_ROOT = Path(__file__).resolve().parent.parent / "problems" / "mfstsp"
AGATZ_PATH = AGATZ_ROOT / "solutions" / "uniform-alpha_1-39-n8-DP.txt"
MFSTSP_PATH = MFSTSP_ROOT / "20170608T131251001523" / "tbl_solutions_101_1_Heuristic.csv"


def test_build_curated_case_for_agatz_solution():
    solution = dvk.from_agatz(AGATZ_PATH)

    record = build_curated_case(
        solution=solution,
        benchmark_family="agatz",
        algorithm_family="agatz",
        algorithm_label="DP",
        source_path=AGATZ_PATH,
        instance_key="uniform-alpha_1-39-n8",
        solution_type="DP",
    )

    assert record.benchmark_family == "agatz"
    assert record.algorithm_label == "DP"
    assert record.num_customers == 7
    assert record.size_tier == "small"
    assert record.num_drones == 1
    assert record.drone_count_tier == "single"
    assert record.sortie_count == 2
    assert record.spatial_pattern in {"clustered", "mixed", "dispersed"}
    assert record.sortie_distance_profile in {"short", "medium", "long"}
    assert record.mean_depot_distance_m > 0.0
    assert record.max_sortie_distance_m >= record.mean_sortie_distance_m


def test_build_curated_case_for_mfstsp_solution():
    solution = dvk.from_mfstsp(MFSTSP_PATH)

    record = build_curated_case(
        solution=solution,
        benchmark_family="mfstsp",
        algorithm_family="mfstsp",
        algorithm_label="Heuristic",
        source_path=MFSTSP_PATH,
        instance_key="20170608T131251001523",
        solution_type="Heuristic",
    )

    assert record.benchmark_family == "mfstsp"
    assert record.algorithm_label == "Heuristic"
    assert record.num_customers == 8
    assert record.sortie_count == 1
    assert record.has_vehicle_profile is True
    assert record.vehicle_profile_cruise_altitude_m == 50.0
    assert record.planned_makespan_s == solution.planned_metrics.makespan
    assert record.mean_sortie_distance_m > 0.0


def test_select_balanced_cases_is_stable_and_skips_truck_only_by_default():
    records = [
        CuratedCase(
            case_id="b",
            benchmark_family="agatz",
            algorithm_family="agatz",
            algorithm_label="DP",
            source_path="b",
            instance_key="b",
            solution_type="DP",
            num_customers=8,
            size_tier="small",
            num_drones=1,
            drone_count_tier="single",
            sortie_count=1,
            planned_makespan_s=10.0,
            planned_drone_speed_m_s=10.0,
            mean_depot_distance_m=10.0,
            max_depot_distance_m=20.0,
            mean_nearest_neighbor_distance_m=5.0,
            clustering_ratio=0.2,
            spatial_pattern="clustered",
            mean_sortie_distance_m=15.0,
            max_sortie_distance_m=15.0,
            sortie_distance_ratio=0.75,
            sortie_distance_profile="short",
            has_vehicle_profile=False,
            vehicle_profile_cruise_altitude_m=None,
        ),
        CuratedCase(
            case_id="a",
            benchmark_family="agatz",
            algorithm_family="agatz",
            algorithm_label="DP",
            source_path="a",
            instance_key="a",
            solution_type="DP",
            num_customers=9,
            size_tier="small",
            num_drones=1,
            drone_count_tier="single",
            sortie_count=1,
            planned_makespan_s=12.0,
            planned_drone_speed_m_s=10.0,
            mean_depot_distance_m=10.0,
            max_depot_distance_m=20.0,
            mean_nearest_neighbor_distance_m=5.0,
            clustering_ratio=0.2,
            spatial_pattern="clustered",
            mean_sortie_distance_m=14.0,
            max_sortie_distance_m=14.0,
            sortie_distance_ratio=0.7,
            sortie_distance_profile="short",
            has_vehicle_profile=False,
            vehicle_profile_cruise_altitude_m=None,
        ),
        CuratedCase(
            case_id="truck-only",
            benchmark_family="agatz",
            algorithm_family="agatz",
            algorithm_label="DP",
            source_path="truck-only",
            instance_key="truck-only",
            solution_type="DP",
            num_customers=9,
            size_tier="small",
            num_drones=1,
            drone_count_tier="single",
            sortie_count=0,
            planned_makespan_s=14.0,
            planned_drone_speed_m_s=10.0,
            mean_depot_distance_m=10.0,
            max_depot_distance_m=20.0,
            mean_nearest_neighbor_distance_m=5.0,
            clustering_ratio=0.2,
            spatial_pattern="clustered",
            mean_sortie_distance_m=0.0,
            max_sortie_distance_m=0.0,
            sortie_distance_ratio=0.0,
            sortie_distance_profile="truck_only",
            has_vehicle_profile=False,
            vehicle_profile_cruise_altitude_m=None,
        ),
    ]

    selected = dvk.select_balanced_cases(records, quota_per_cell=1)

    assert [record.case_id for record in selected] == ["a"]


def test_write_manifest_round_trips_selected_records(tmp_path):
    records = [
        CuratedCase(
            case_id="case-1",
            benchmark_family="mfstsp",
            algorithm_family="mfstsp",
            algorithm_label="Heuristic",
            source_path="problems/mfstsp/example.csv",
            instance_key="example",
            solution_type="Heuristic",
            num_customers=8,
            size_tier="small",
            num_drones=1,
            drone_count_tier="single",
            sortie_count=1,
            planned_makespan_s=100.0,
            planned_drone_speed_m_s=20.0,
            mean_depot_distance_m=1000.0,
            max_depot_distance_m=1200.0,
            mean_nearest_neighbor_distance_m=300.0,
            clustering_ratio=0.3,
            spatial_pattern="mixed",
            mean_sortie_distance_m=900.0,
            max_sortie_distance_m=900.0,
            sortie_distance_ratio=0.75,
            sortie_distance_profile="short",
            has_vehicle_profile=True,
            vehicle_profile_cruise_altitude_m=50.0,
        )
    ]

    csv_path, json_path = dvk.write_manifest(records, tmp_path)

    assert csv_path.exists()
    assert json_path.exists()
    json_data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(json_data) == 1
    assert json_data[0]["case_id"] == "case-1"
    assert "algorithm_label" in csv_path.read_text(encoding="utf-8")


def test_coverage_counts_groups_selected_records():
    records = [
        CuratedCase(
            case_id="case-1",
            benchmark_family="mfstsp",
            algorithm_family="mfstsp",
            algorithm_label="Heuristic",
            source_path="a",
            instance_key="x",
            solution_type="Heuristic",
            num_customers=8,
            size_tier="small",
            num_drones=1,
            drone_count_tier="single",
            sortie_count=1,
            planned_makespan_s=1.0,
            planned_drone_speed_m_s=1.0,
            mean_depot_distance_m=1.0,
            max_depot_distance_m=1.0,
            mean_nearest_neighbor_distance_m=1.0,
            clustering_ratio=0.2,
            spatial_pattern="clustered",
            mean_sortie_distance_m=1.0,
            max_sortie_distance_m=1.0,
            sortie_distance_ratio=0.8,
            sortie_distance_profile="short",
            has_vehicle_profile=False,
            vehicle_profile_cruise_altitude_m=None,
        ),
        CuratedCase(
            case_id="case-2",
            benchmark_family="mfstsp",
            algorithm_family="mfstsp",
            algorithm_label="IP",
            source_path="b",
            instance_key="y",
            solution_type="IP",
            num_customers=12,
            size_tier="medium",
            num_drones=4,
            drone_count_tier="multi_heavy",
            sortie_count=2,
            planned_makespan_s=2.0,
            planned_drone_speed_m_s=2.0,
            mean_depot_distance_m=2.0,
            max_depot_distance_m=2.0,
            mean_nearest_neighbor_distance_m=1.0,
            clustering_ratio=0.6,
            spatial_pattern="dispersed",
            mean_sortie_distance_m=2.0,
            max_sortie_distance_m=2.0,
            sortie_distance_ratio=2.0,
            sortie_distance_profile="long",
            has_vehicle_profile=False,
            vehicle_profile_cruise_altitude_m=None,
        ),
    ]

    summary = dvk.coverage_counts(records)

    assert summary["algorithm_label"] == {"Heuristic": 1, "IP": 1}
    assert summary["size_tier"] == {"small": 1, "medium": 1}
    assert summary["drone_count_tier"] == {"single": 1, "multi_heavy": 1}
