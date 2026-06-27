"""PX4/Gazebo Docker launcher used by the Liftoff sim server.

This intentionally reuses dronevalkit's containerization approach without
using dronevalkit's mission-control path. The cloned user repo is responsible
for connecting to MAVSDK and flying the vehicle.
"""

from __future__ import annotations

import logging
import math
import os
import re
import subprocess
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("liftoff.sim")

DEFAULT_DOCKER_IMAGE = "zackmurry/dronevalkit-sim:latest"
DEFAULT_DEPOT = (38.898, -77.036)
DEFAULT_BATTERY_CAPACITY_MAH = 5000
DEFAULT_BATTERY_N_CELLS = 4
DEFAULT_BATTERY_V_CHARGED = 4.20
DEFAULT_BATTERY_V_EMPTY = 3.50

_CONTAINER_LOG_DIR = "/root/PX4-Autopilot/build/px4_sitl_default/rootfs/log"
_CONTAINER_MULTI_SCRIPT = "/root/dronevalkit/start_multi.sh"
_CONTAINER_DEFAULT_WORLD_PATH = "/root/PX4-Autopilot/Tools/simulation/gz/worlds/default.sdf"
_CONTAINER_WINDY_WORLD_PATH = "/root/PX4-Autopilot/Tools/simulation/gz/worlds/windy.sdf"
_CONTAINER_DYNAMIC_WIND_WORLD_PATH = (
    "/root/PX4-Autopilot/Tools/simulation/gz/worlds/dronevalkit_wind.sdf"
)
_CONTAINER_DYNAMIC_WIND_WORLD_NAME = "dronevalkit_wind"
_CONTAINER_X500_BASE_MODEL_PATH = (
    "/root/PX4-Autopilot/Tools/simulation/gz/models/x500_base/model.sdf"
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DRONEVALKIT_DOCKER = _REPO_ROOT / "dronevalkit" / "docker"
_HOST_MULTI_SCRIPT = _DRONEVALKIT_DOCKER / "start_multi.sh"
_HOST_DEFAULT_WORLD = _DRONEVALKIT_DOCKER / "worlds" / "default.sdf"
_HOST_WINDY_WORLD = _DRONEVALKIT_DOCKER / "worlds" / "windy.sdf"


class SimLaunchError(RuntimeError):
    """Raised when PX4/Gazebo cannot be launched or inspected."""


@dataclass
class BatteryConfig:
    capacity_mah: int = DEFAULT_BATTERY_CAPACITY_MAH
    n_cells: int = DEFAULT_BATTERY_N_CELLS
    v_charged: float = DEFAULT_BATTERY_V_CHARGED
    v_empty: float = DEFAULT_BATTERY_V_EMPTY


@dataclass
class SimLaunchConfig:
    home: tuple[float, float] = DEFAULT_DEPOT
    anchor: tuple[float, float] = DEFAULT_DEPOT
    num_drones: int = 1
    base_instance: int = 0
    drone_model: str = "gz_x500"
    docker_image: str = DEFAULT_DOCKER_IMAGE
    wind_speed: float = 0.0
    wind_direction: float = 0.0
    battery: BatteryConfig = field(default_factory=BatteryConfig)
    speed_factor: float = 1.0


def build_launch_config(params: dict, speed_factor: float) -> SimLaunchConfig:
    """Build infrastructure-only sim config from scenario params."""
    home_raw = params.get("home", params.get("depot", DEFAULT_DEPOT))
    home = _lat_lon(home_raw, "home/depot")
    waypoints = [_lat_lon(wp, "waypoint") for wp in params.get("waypoints", [])]
    anchor = _center([home, *waypoints])

    num_drones = int(params.get("num_drones", 1))
    if num_drones < 1:
        raise ValueError("num_drones must be at least 1")

    return SimLaunchConfig(
        home=home,
        anchor=anchor,
        num_drones=num_drones,
        base_instance=int(params.get("base_instance", 0)),
        drone_model=str(params.get("drone_model", "gz_x500")),
        docker_image=str(params.get("docker_image", DEFAULT_DOCKER_IMAGE)),
        wind_speed=float(params.get("wind_speed", 0.0)),
        wind_direction=float(params.get("wind_direction", 0.0)),
        battery=BatteryConfig(
            capacity_mah=int(params.get("battery_capacity_mah", DEFAULT_BATTERY_CAPACITY_MAH)),
            n_cells=int(params.get("battery_n_cells", DEFAULT_BATTERY_N_CELLS)),
            v_charged=float(params.get("battery_v_charged", DEFAULT_BATTERY_V_CHARGED)),
            v_empty=float(params.get("battery_v_empty", DEFAULT_BATTERY_V_EMPTY)),
        ),
        speed_factor=float(speed_factor),
    )


def _lat_lon(raw: object, label: str) -> tuple[float, float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        raise ValueError(f"{label} must be [lat, lon]")
    return (float(raw[0]), float(raw[1]))


def _center(points: list[tuple[float, float]]) -> tuple[float, float]:
    return (
        (min(lat for lat, _lon in points) + max(lat for lat, _lon in points)) / 2.0,
        (min(lon for _lat, lon in points) + max(lon for _lat, lon in points)) / 2.0,
    )


def _gps_to_ned(
    lat: float,
    lon: float,
    ref_lat: float,
    ref_lon: float,
) -> tuple[float, float]:
    """Approximate GPS offset in meters; accurate enough for spawn placement."""
    earth_radius_m = 6_378_137.0
    d_lat = math.radians(lat - ref_lat)
    d_lon = math.radians(lon - ref_lon)
    north = d_lat * earth_radius_m
    east = d_lon * earth_radius_m * math.cos(math.radians(ref_lat))
    return north, east


class PX4SimLauncher:
    """Manage one PX4 SITL container for user-owned flight code."""

    def __init__(self, config: SimLaunchConfig, log_dir: str) -> None:
        self.config = config
        self.log_dir = os.path.abspath(log_dir)
        self.container_id: Optional[str] = None

    @property
    def mavsdk_addresses(self) -> list[str]:
        return [
            f"udpin://0.0.0.0:{14540 + self.config.base_instance + i}"
            for i in range(self.config.num_drones)
        ]

    def start(self) -> None:
        os.makedirs(self.log_dir, exist_ok=True)
        self._validate_host_assets()

        anchor_lat, anchor_lon = self.config.anchor
        north_m, east_m = _gps_to_ned(
            self.config.home[0],
            self.config.home[1],
            anchor_lat,
            anchor_lon,
        )
        wind_prefix = self._wind_setup_prefix()

        cmd = [
            "docker", "run", "-d", "--rm",
            "--network", "host",
            "-v", f"{self.log_dir}:{_CONTAINER_LOG_DIR}",
            "-v", f"{_HOST_DEFAULT_WORLD}:{_CONTAINER_DEFAULT_WORLD_PATH}:ro",
            "-v", f"{_HOST_WINDY_WORLD}:{_CONTAINER_WINDY_WORLD_PATH}:ro",
            "-e", f"PX4_HOME_LAT={anchor_lat}",
            "-e", f"PX4_HOME_LON={anchor_lon}",
            "-e", "PX4_HOME_ALT=0",
            "-e", f"NUM_DRONES={self.config.num_drones}",
            "-e", f"PX4_BASE_INSTANCE={self.config.base_instance}",
            "-e", f"PX4_GZ_MODEL_POSE={east_m:.3f},{north_m:.3f}",
            "-e", f"PX4_GZ_BASE_EAST={east_m:.3f}",
            "-e", f"PX4_GZ_BASE_NORTH={north_m:.3f}",
            "-e", f"DRONE_MODEL={self.config.drone_model}",
            "-e", f"PX4_SIM_SPEED_FACTOR={self.config.speed_factor}",
            "-e", f"PX4_PARAM_BAT1_CAPACITY={float(self.config.battery.capacity_mah)}",
            "-e", f"PX4_PARAM_BAT1_N_CELLS={int(self.config.battery.n_cells)}",
            "-e", f"PX4_PARAM_BAT1_V_CHARGED={float(self.config.battery.v_charged)}",
            "-e", f"PX4_PARAM_BAT1_V_EMPTY={float(self.config.battery.v_empty)}",
            self.config.docker_image,
            "bash", "-lc",
        ]

        if self.config.num_drones == 1:
            command = (
                f"{wind_prefix}"
                f"export PX4_INSTANCE={self.config.base_instance} && "
                f"HEADLESS=1 make px4_sitl {self.config.drone_model}"
            )
        else:
            image_index = cmd.index(self.config.docker_image)
            cmd[image_index:image_index] = [
                "-v",
                f"{_HOST_MULTI_SCRIPT}:{_CONTAINER_MULTI_SCRIPT}:ro",
            ]
            command = f"{wind_prefix}bash {_CONTAINER_MULTI_SCRIPT}"
        cmd.append(command)

        logger.info(
            "Starting PX4 SITL container (num_drones=%d, image=%s)",
            self.config.num_drones,
            self.config.docker_image,
        )
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise SimLaunchError(
                f"docker run failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        self.container_id = result.stdout.strip()

    def stop(self) -> None:
        if not self.container_id:
            return
        subprocess.run(["docker", "stop", self.container_id], capture_output=True)
        logger.info("Stopped container %s", self.container_id[:12])
        self.container_id = None

    def is_running(self) -> Optional[bool]:
        if not self.container_id:
            return None
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.container_id],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        state = result.stdout.strip().lower()
        if state == "true":
            return True
        if state == "false":
            return False
        return None

    def logs_tail(self, lines: int = 120) -> str:
        if not self.container_id:
            return ""
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), self.container_id],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return ""
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stdout and stderr:
            return f"{stdout}\n{stderr}"
        return stdout or stderr

    def latest_ulogs(self) -> dict[int, str]:
        groups: dict[int, list[str]] = {}
        for root, _dirs, files in os.walk(self.log_dir):
            for fname in files:
                if not fname.endswith(".ulg"):
                    continue
                path = os.path.join(root, fname)
                instance_id = self._instance_from_path(path)
                groups.setdefault(instance_id, []).append(path)

        latest: dict[int, str] = {}
        for drone_id in range(self.config.num_drones):
            instance_id = self.config.base_instance + drone_id
            candidates = groups.get(instance_id, [])
            if candidates:
                latest[drone_id] = max(candidates, key=os.path.getmtime)
        return latest

    def _validate_host_assets(self) -> None:
        for path in (_HOST_DEFAULT_WORLD, _HOST_WINDY_WORLD):
            if not path.is_file():
                raise SimLaunchError(f"Missing Gazebo world on host: {path}")
        if self.config.num_drones > 1 and not _HOST_MULTI_SCRIPT.is_file():
            raise SimLaunchError(f"Missing multi-launch script on host: {_HOST_MULTI_SCRIPT}")

    def _wind_setup_prefix(self) -> str:
        if self.config.wind_speed <= 0.0:
            return ""

        wind_n = self.config.wind_speed * math.cos(math.radians(self.config.wind_direction))
        wind_e = self.config.wind_speed * math.sin(math.radians(self.config.wind_direction))
        wind_script = self._wind_setup_script(wind_x=wind_e, wind_y=wind_n)
        return (
            "python3 - <<'PY'\n"
            f"{wind_script}"
            "PY\n"
            f"export PX4_GZ_WORLD={_CONTAINER_DYNAMIC_WIND_WORLD_NAME} && "
        )

    def _wind_setup_script(self, *, wind_x: float, wind_y: float) -> str:
        return f"""from pathlib import Path
import xml.etree.ElementTree as ET

WORLD_TEMPLATE = Path("{_CONTAINER_WINDY_WORLD_PATH}")
WORLD_OUTPUT = Path("{_CONTAINER_DYNAMIC_WIND_WORLD_PATH}")
X500_BASE_MODEL = Path("{_CONTAINER_X500_BASE_MODEL_PATH}")
WORLD_NAME = "{_CONTAINER_DYNAMIC_WIND_WORLD_NAME}"
WIND_VECTOR = "{wind_x:.6f} {wind_y:.6f} 0"


def ensure_child(parent, tag, text=None, attrib=None):
    child = parent.find(tag)
    if child is None:
        child = ET.SubElement(parent, tag, attrib or {{}})
    if text is not None:
        child.text = text
    return child


world_tree = ET.parse(WORLD_TEMPLATE)
world_root = world_tree.getroot()
world = world_root.find("world")
if world is None:
    raise RuntimeError(f"Missing <world> in {{WORLD_TEMPLATE}}")
world.set("name", WORLD_NAME)
wind = ensure_child(world, "wind")
ensure_child(wind, "linear_velocity", WIND_VECTOR)
world_tree.write(WORLD_OUTPUT, encoding="utf-8", xml_declaration=True)

model_tree = ET.parse(X500_BASE_MODEL)
model_root = model_tree.getroot()
model = model_root.find("model")
if model is None:
    raise RuntimeError(f"Missing <model> in {{X500_BASE_MODEL}}")
ensure_child(model, "enable_wind", "true")

base_link = None
for link in model.iter("link"):
    if link.get("name") == "base_link":
        base_link = link
        break
if base_link is None:
    raise RuntimeError(f"Missing base_link in {{X500_BASE_MODEL}}")
ensure_child(base_link, "enable_wind", "true")

drag_plugins = (
    ("liftoff_wind_drag_longitudinal", "1 0 0", "0 0 1", "0.12", "1.35"),
    ("liftoff_wind_drag_lateral", "0 1 0", "0 0 1", "0.18", "1.35"),
)
for plugin_id, forward, upward, area, cda in drag_plugins:
    plugin = None
    for existing in model.findall("plugin"):
        if existing.findtext("liftoff_plugin_id") == plugin_id:
            plugin = existing
            break
    if plugin is None:
        plugin = ET.SubElement(
            model,
            "plugin",
            {{"filename": "gz-sim-lift-drag-system", "name": "gz::sim::systems::LiftDrag"}},
        )
    else:
        plugin.attrib.clear()
        plugin.attrib.update(
            {{"filename": "gz-sim-lift-drag-system", "name": "gz::sim::systems::LiftDrag"}}
        )
        for child in list(plugin):
            plugin.remove(child)

    for tag, text in (
        ("liftoff_plugin_id", plugin_id),
        ("air_density", "1.2041"),
        ("cla", "0.0"),
        ("cla_stall", "0.0"),
        ("cda", cda),
        ("cda_stall", cda),
        ("alpha_stall", "1.57079632679"),
        ("a0", "0.0"),
        ("area", area),
        ("forward", forward),
        ("upward", upward),
        ("link_name", "base_link"),
        ("cp", "0 0 0"),
    ):
        ensure_child(plugin, tag, text)

model_tree.write(X500_BASE_MODEL, encoding="utf-8", xml_declaration=True)
"""

    @staticmethod
    def _instance_from_path(path: str) -> int:
        match = re.search(r"instance_(\d+)", path)
        if match:
            return int(match.group(1))
        match = re.search(r"/(\d{8})/(\d{2}_\d{2}_\d{2})\.ulg$", path)
        if match:
            return 0
        return 0
