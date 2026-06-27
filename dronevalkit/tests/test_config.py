"""Tests for dronevalkit.config."""

import pytest
from dronevalkit.config import (
    DEFAULT_MULTI_DRONE_TARGET_OFFSET_RADIUS_M,
    DroneModel,
    CustomBattery,
    SimpleBattery,
    InfiniteBattery,
    WindCondition,
    ExperimentConfig,
)
from dronevalkit.models import Problem, Solution, Sortie, PlannedMetrics


def make_solution():
    problem = Problem(
        depot=(38.898, -77.036),
        customers={1: (38.906, -77.043), 2: (38.912, -77.030)},
        drone_eligible=[1, 2],
    )
    return Solution(
        problem=problem,
        truck_route=[0, 1, 2, 0],
        sorties=[Sortie(delivery=1, rendezvous=2)],
        planned_metrics=PlannedMetrics(drone_speed=10.0, makespan=600, sortie_times=[180]),
        num_drones=1,
    )


def make_multi_drone_solution():
    problem = Problem(
        depot=(38.898, -77.036),
        customers={1: (38.906, -77.043), 2: (38.912, -77.030), 3: (38.915, -77.020)},
        drone_eligible=[1, 2, 3],
    )
    return Solution(
        problem=problem,
        truck_route=[0, 1, 2, 0],
        sorties=[
            Sortie(launch=0, delivery=1, rendezvous=2, drone_id=0),
            Sortie(launch=0, delivery=3, rendezvous=0, drone_id=1),
        ],
        planned_metrics=PlannedMetrics(drone_speed=10.0, makespan=600, sortie_times=[180, 210]),
        num_drones=2,
    )


# ---------------------------------------------------------------------------
# DroneModel
# ---------------------------------------------------------------------------

def test_drone_model_x500_value():
    assert DroneModel.X500.value == "gz_x500"


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------

def test_custom_battery_defaults():
    b = CustomBattery()
    assert b.capacity_mah == 5000
    assert b.n_cells == 4
    assert b.v_charged == pytest.approx(4.2)
    assert b.v_empty == pytest.approx(3.5)
    assert b.full_drain is True
    assert b.drain_rate == pytest.approx(250.0)


def test_custom_battery_custom():
    b = CustomBattery(
        capacity_mah=3000,
        n_cells=6,
        v_charged=4.1,
        v_empty=3.4,
        full_drain=False,
        drain_rate=1.5,
    )
    assert b.capacity_mah == 3000
    assert b.n_cells == 6
    assert b.v_charged == pytest.approx(4.1)
    assert b.v_empty == pytest.approx(3.4)
    assert b.full_drain is False
    assert b.drain_rate == pytest.approx(1.5)


def test_simple_battery_defaults():
    b = SimpleBattery()
    assert b.longevity == pytest.approx(1.0)
    c = b.to_custom()
    assert c.drain_rate == pytest.approx(CustomBattery().drain_rate)


def test_simple_battery_custom_longevity():
    b = SimpleBattery(longevity=2.5)
    c = b.to_custom()
    assert c.drain_rate == pytest.approx(CustomBattery().drain_rate * 2.5)


def test_simple_battery_invalid_longevity():
    with pytest.raises(ValueError, match="must be positive"):
        SimpleBattery(longevity=0.0).to_custom()


def test_infinite_battery_defaults():
    b = InfiniteBattery()
    c = b.to_custom()
    assert c.drain_rate > 1_000_000.0


# ---------------------------------------------------------------------------
# WindCondition
# ---------------------------------------------------------------------------

def test_wind_calm():
    w = WindCondition.calm()
    assert w.speed == pytest.approx(0.0)
    assert w.label == "Calm"


def test_wind_moderate_defaults():
    w = WindCondition.moderate()
    assert w.speed == pytest.approx(5.0)
    assert w.direction == pytest.approx(0.0)
    assert "5.0" in w.label


def test_wind_moderate_custom():
    w = WindCondition.moderate(speed=7.0, direction=90.0)
    assert w.speed == pytest.approx(7.0)
    assert w.direction == pytest.approx(90.0)


def test_wind_strong_defaults():
    w = WindCondition.strong()
    assert w.speed == pytest.approx(10.0)
    assert w.direction == pytest.approx(0.0)


def test_wind_strong_custom():
    w = WindCondition.strong(speed=15.0, direction=180.0)
    assert w.speed == pytest.approx(15.0)
    assert w.direction == pytest.approx(180.0)
    assert "15.0" in w.label


def test_wind_manual_construction():
    w = WindCondition(speed=3.5, direction=45.0, label="light")
    assert w.speed == pytest.approx(3.5)
    assert w.direction == pytest.approx(45.0)
    assert w.label == "light"


# ---------------------------------------------------------------------------
# ExperimentConfig
# ---------------------------------------------------------------------------

def test_experiment_config_defaults():
    sol = make_solution()
    cfg = ExperimentConfig(solution=sol)
    # conditions defaults to [calm]
    assert len(cfg.conditions) == 1
    assert cfg.conditions[0].speed == pytest.approx(0.0)
    # battery defaults to SimpleBattery -> converted to CustomBattery
    assert isinstance(cfg.battery, CustomBattery)
    assert cfg.battery.capacity_mah == 5000
    assert cfg.replications == 5
    assert cfg.altitude is None
    assert cfg.waypoint_tolerance == pytest.approx(1.0)
    assert cfg.altitude_deconfliction_m == pytest.approx(0.0)
    assert cfg.target_offset_radius_m == pytest.approx(0.0)
    assert cfg.headless is True
    assert cfg.speed_factor == pytest.approx(1.0)
    assert cfg.drone == DroneModel.X500


def test_experiment_config_custom_conditions():
    sol = make_solution()
    conditions = [WindCondition.calm(), WindCondition.moderate(), WindCondition.strong()]
    cfg = ExperimentConfig(solution=sol, conditions=conditions, replications=3)
    assert len(cfg.conditions) == 3
    assert cfg.replications == 3


def test_experiment_config_explicit_battery():
    sol = make_solution()
    battery = CustomBattery(capacity_mah=3000, full_drain=False, drain_rate=1.0)
    cfg = ExperimentConfig(solution=sol, battery=battery)
    assert cfg.battery.capacity_mah == 3000


def test_experiment_config_simple_battery_is_normalized():
    sol = make_solution()
    cfg = ExperimentConfig(solution=sol, battery=SimpleBattery(longevity=2.0))
    assert isinstance(cfg.battery, CustomBattery)
    assert cfg.battery.drain_rate == pytest.approx(CustomBattery().drain_rate * 2.0)


def test_experiment_config_infinite_battery_is_normalized():
    sol = make_solution()
    cfg = ExperimentConfig(solution=sol, battery=InfiniteBattery())
    assert isinstance(cfg.battery, CustomBattery)
    assert cfg.battery.drain_rate > 1_000_000.0


def test_experiment_config_docker_image():
    sol = make_solution()
    cfg = ExperimentConfig(solution=sol, docker_image="myrepo/px4:v2")
    assert cfg.docker_image == "myrepo/px4:v2"


def test_experiment_config_multi_drone_auto_target_offset_radius():
    sol = make_multi_drone_solution()
    cfg = ExperimentConfig(solution=sol)
    assert cfg.target_offset_radius_m == pytest.approx(
        DEFAULT_MULTI_DRONE_TARGET_OFFSET_RADIUS_M
    )


def test_experiment_config_multi_drone_explicit_zero_target_offset_radius():
    sol = make_multi_drone_solution()
    cfg = ExperimentConfig(solution=sol, target_offset_radius_m=0.0)
    assert cfg.target_offset_radius_m == pytest.approx(0.0)
