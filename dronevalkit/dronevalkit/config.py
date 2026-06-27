"""Configuration dataclasses for dronevalkit experiments."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .models import Solution


DEFAULT_MULTI_DRONE_TARGET_OFFSET_RADIUS_M = 3.0


class DroneModel(Enum):
    X500 = "gz_x500"   # default PX4 quadcopter


@dataclass
class CustomBattery:
    capacity_mah: int = 5000
    n_cells: int = 4
    v_charged: float = 4.20
    v_empty: float = 3.50
    full_drain: bool = True       # set SIM_BAT_MIN_PCT=0
    # Interval at which battery is drained; higher drain rate = longer lasting battery
    drain_rate: float = 250.0 # Default drain rate is scaled by SimpleBattery, so make it a reasonable level


@dataclass
class SimpleBattery:
    # Scale factor over baseline SITL drain behavior.
    # Higher values mean longer battery life.
    longevity: float = 1.0

    def to_custom(self) -> CustomBattery:
        if self.longevity <= 0.0:
            raise ValueError("SimpleBattery.longevity must be positive")
        base = CustomBattery()
        base.drain_rate *= float(self.longevity)
        return base


@dataclass
class InfiniteBattery(SimpleBattery):
    # Practical "never dies" preset for normal experiment timescales.
    longevity: float = 1_000_000.0


@dataclass
class WindCondition:
    speed: float                  # m/s
    direction: float = 0.0        # degrees, 0=North
    label: str = ""

    @classmethod
    def calm(cls) -> "WindCondition":
        return cls(speed=0.0, label="Calm")

    @classmethod
    def moderate(cls, speed: float = 5.0, direction: float = 0.0) -> "WindCondition":
        return cls(speed=speed, direction=direction, label=f"Wind {speed}m/s")

    @classmethod
    def strong(cls, speed: float = 10.0, direction: float = 0.0) -> "WindCondition":
        return cls(speed=speed, direction=direction, label=f"Wind {speed}m/s")


@dataclass
class ExperimentConfig:
    solution: Solution
    drone: DroneModel = DroneModel.X500
    conditions: Optional[list[WindCondition]] = None   # defaults to [calm]
    replications: int = 5
    battery: Optional[CustomBattery | SimpleBattery | InfiniteBattery] = None
    speed_factor: float = 1.0                          # PX4_SIM_SPEED_FACTOR (>1 = faster than realtime)
    docker_image: str = "zackmurry/dronevalkit-sim:latest"
    headless: bool = True
    waypoint_tolerance: float = 1.0                    # meters, for arrival detection
    altitude: Optional[float] = None                   # flight altitude in meters; None uses solution/profile default
    altitude_deconfliction_m: float = 0.0              # per-drone altitude step; 0 disables
    target_offset_radius_m: Optional[float] = None     # None auto-enables offsets for multi-drone

    def __post_init__(self):
        if self.conditions is None:
            self.conditions = [WindCondition.calm()]
        if self.battery is None:
            self.battery = SimpleBattery()
        if isinstance(self.battery, SimpleBattery):
            self.battery = self.battery.to_custom()
        self.speed_factor = float(self.speed_factor)
        if self.speed_factor <= 0.0:
            raise ValueError("speed_factor must be positive")
        if self.altitude is not None:
            self.altitude = float(self.altitude)
            if self.altitude <= 0.0:
                raise ValueError("altitude must be positive when provided")
        self.altitude_deconfliction_m = float(self.altitude_deconfliction_m)
        if self.altitude_deconfliction_m < 0.0:
            raise ValueError("altitude_deconfliction_m must be non-negative")
        if self.target_offset_radius_m is None:
            self.target_offset_radius_m = (
                DEFAULT_MULTI_DRONE_TARGET_OFFSET_RADIUS_M
                if self.solution.num_drones > 1
                else 0.0
            )
        self.target_offset_radius_m = float(self.target_offset_radius_m)
        if self.target_offset_radius_m < 0.0:
            raise ValueError("target_offset_radius_m must be non-negative")
