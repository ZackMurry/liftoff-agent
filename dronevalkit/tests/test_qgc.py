"""Tests for QGroundControl overlay export helpers."""

from pathlib import Path

import dronevalkit as dvk


def _make_solution() -> dvk.Solution:
    problem = dvk.Problem(
        depot=(38.898, -77.036),
        customers={
            1: (38.906, -77.043),
            2: (38.912, -77.030),
            3: (38.904, -77.022),
        },
        drone_eligible=[1, 2],
    )
    return dvk.Solution(
        problem=problem,
        truck_route=[0, 3, 0],
        sorties=[
            dvk.Sortie(delivery=1, rendezvous=3, drone_id=0),
            dvk.Sortie(delivery=2, rendezvous=0, drone_id=1),
        ],
        planned_metrics=dvk.PlannedMetrics(
            drone_speed=10.0,
            makespan=600.0,
            sortie_times=[180.0, 210.0],
        ),
        num_drones=2,
    )


def test_render_qgc_overlay_qml_contains_map_items_and_labels():
    qml = dvk.render_qgc_overlay_qml(_make_solution(), overlay_name="Test Overlay")

    assert "MapItemGroup" in qml
    assert "MapPolyline" in qml
    assert "MapQuickItem" in qml
    assert 'readonly property string overlayName: "Test Overlay"' in qml
    assert "Truck Route" in qml
    assert "Sortie 0 (drone 0)" in qml
    assert "Sortie 1 (drone 1)" in qml
    assert "Depot" in qml
    assert "Customer 1" in qml
    assert "S0 delivery n1 d0" in qml
    assert "38.898" in qml
    assert "-77.043" in qml


def test_save_qgc_overlay_writes_qml_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    output_path = Path(dvk.save_qgc_overlay(_make_solution(), overlay_name="Mission Overlay"))

    saved = output_path.read_text(encoding="utf-8")
    assert output_path.exists()
    assert output_path.name == "mission_overlay.qml"
    assert "Mission Overlay" in saved
    assert "MapItemGroup" in saved
    assert "Truck Route" in saved
    assert "S1 rv n0 d1" in saved


def test_save_qgc_overlay_uses_default_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    output_path = Path(dvk.save_qgc_overlay(_make_solution()))

    assert output_path.exists()
    assert output_path.name == "dronevalkit_overlay.qml"


def test_save_qgc_overlay_honors_explicit_path(tmp_path):
    output_path = tmp_path / "exports" / "custom_overlay.qml"

    saved_path = dvk.save_qgc_overlay(
        _make_solution(),
        path=str(output_path),
        overlay_name="Ignored For Path",
    )

    assert Path(saved_path) == output_path
    assert output_path.exists()
    assert "Ignored For Path" in output_path.read_text(encoding="utf-8")
