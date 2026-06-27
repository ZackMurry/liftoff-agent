"""Docker lifecycle management for PX4 SITL experiments.

Each :class:`PX4SimRunner` manages one Docker container per experiment run.
For single-drone runs the container launches one PX4 SITL instance.
For multi-drone runs it launches one Gazebo world and multiple PX4 instances
inside the same container.
"""

import asyncio
import importlib
import logging
import math
import os
import re
import subprocess
import time
from typing import Optional

from . import geo
from .exceptions import ContainerError, SimulationError

logger = logging.getLogger(__name__)

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
_HOST_MULTI_SCRIPT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "docker", "start_multi.sh")
)
_HOST_DEFAULT_WORLD = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "docker",
        "worlds",
        "default.sdf",
    )
)
_HOST_WINDY_WORLD = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "docker",
        "worlds",
        "windy.sdf",
    )
)


class PX4SimRunner:
    """Manages one PX4 SITL container for a single experiment run."""

    def __init__(
        self,
        config,
        log_dir: str,
        base_instance: int = 0,
        wind_condition=None,
    ) -> None:
        self.config = config
        self.log_dir = os.path.abspath(log_dir)
        self.base_instance = base_instance
        self.wind_condition = wind_condition
        self.container_id: Optional[str] = None

    @property
    def num_drones(self) -> int:
        return self.config.solution.num_drones

    @property
    def mavsdk_addresses(self) -> list[str]:
        return [
            f"udpin://0.0.0.0:{14540 + self.base_instance + i}"
            for i in range(self.num_drones)
        ]

    def start(self) -> None:
        """Launch the experiment container."""
        os.makedirs(self.log_dir, exist_ok=True)
        depot = self.config.solution.problem.depot
        anchor_lat, anchor_lon = self._simulation_anchor_gps()
        spawn_east_m, spawn_north_m = self._spawn_pose_enu_m(
            anchor_lat=anchor_lat,
            anchor_lon=anchor_lon,
        )
        wind_prefix = self._wind_setup_prefix()
        if not os.path.isfile(_HOST_DEFAULT_WORLD):
            raise ContainerError(f"Missing Gazebo world on host: {_HOST_DEFAULT_WORLD}")
        if not os.path.isfile(_HOST_WINDY_WORLD):
            raise ContainerError(f"Missing Gazebo world on host: {_HOST_WINDY_WORLD}")

        cmd = [
            "docker", "run", "-d", "--rm",
            "--network", "host",
            "-v", f"{self.log_dir}:{_CONTAINER_LOG_DIR}",
            "-v", f"{_HOST_DEFAULT_WORLD}:{_CONTAINER_DEFAULT_WORLD_PATH}:ro",
            "-v", f"{_HOST_WINDY_WORLD}:{_CONTAINER_WINDY_WORLD_PATH}:ro",
            "-e", f"PX4_HOME_LAT={anchor_lat}",
            "-e", f"PX4_HOME_LON={anchor_lon}",
            "-e", "PX4_HOME_ALT=0",
            "-e", f"NUM_DRONES={self.num_drones}",
            "-e", f"PX4_BASE_INSTANCE={self.base_instance}",
            "-e", f"PX4_GZ_MODEL_POSE={spawn_east_m:.3f},{spawn_north_m:.3f}",
            "-e", f"PX4_GZ_BASE_EAST={spawn_east_m:.3f}",
            "-e", f"PX4_GZ_BASE_NORTH={spawn_north_m:.3f}",
            "-e", f"DRONE_MODEL={self.config.drone.value}",
            "-e", f"PX4_PARAM_BAT1_CAPACITY={float(self.config.battery.capacity_mah)}",
            "-e", f"PX4_PARAM_BAT1_N_CELLS={int(self.config.battery.n_cells)}",
            "-e", f"PX4_PARAM_BAT1_V_CHARGED={float(self.config.battery.v_charged)}",
            "-e", f"PX4_PARAM_BAT1_V_EMPTY={float(self.config.battery.v_empty)}",
            self.config.docker_image,
            "bash", "-lc",
        ]

        if self.num_drones == 1:
            command = (
                f"{wind_prefix}"
                f"export PX4_INSTANCE={self.base_instance} && "
                f"HEADLESS=1 make px4_sitl {self.config.drone.value}"
            )
        else:
            if not os.path.isfile(_HOST_MULTI_SCRIPT):
                raise ContainerError(f"Missing multi-launch script on host: {_HOST_MULTI_SCRIPT}")
            image_index = cmd.index(self.config.docker_image)
            cmd[image_index:image_index] = [
                "-v",
                f"{_HOST_MULTI_SCRIPT}:{_CONTAINER_MULTI_SCRIPT}:ro",
            ]
            command = f"{wind_prefix}bash {_CONTAINER_MULTI_SCRIPT}"
        cmd.append(command)

        logger.info(
            "Starting PX4 SITL container (num_drones=%d, base_instance=%d, image=%s)",
            self.num_drones,
            self.base_instance,
            self.config.docker_image,
        )
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ContainerError(
                f"docker run failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        self.container_id = result.stdout.strip()
        logger.info("Container started: %s", self.container_id[:12])

    def _simulation_anchor_gps(self) -> tuple[float, float]:
        """Choose a route-centered GPS anchor to keep Gazebo near the mission footprint."""
        points = [self.config.solution.problem.depot]
        points.extend(self.config.solution.problem.customers.values())
        latitudes = [float(lat) for lat, _lon in points]
        longitudes = [float(lon) for _lat, lon in points]
        return (
            (min(latitudes) + max(latitudes)) / 2.0,
            (min(longitudes) + max(longitudes)) / 2.0,
        )

    def _spawn_pose_enu_m(self, *, anchor_lat: float, anchor_lon: float) -> tuple[float, float]:
        """Return Gazebo ENU spawn coordinates for the depot relative to the anchor."""
        depot_lat, depot_lon = self.config.solution.problem.depot
        north_m, east_m = geo.gps_to_ned(
            float(depot_lat),
            float(depot_lon),
            float(anchor_lat),
            float(anchor_lon),
        )
        return (float(east_m), float(north_m))

    def _wind_setup_prefix(self) -> str:
        wind = self.wind_condition
        if wind is None or float(wind.speed) <= 0.0:
            return ""

        # WindCondition is defined in N/E terms (0° = North), while Gazebo's
        # ENU world frame uses X=East and Y=North for linear_velocity.
        speed = float(wind.speed)
        direction_deg = float(wind.direction)
        wind_n = speed * math.cos(math.radians(direction_deg))
        wind_e = speed * math.sin(math.radians(direction_deg))
        wind_x = wind_e
        wind_y = wind_n
        logger.info(
            "Applying Gazebo wind: speed=%.3f m/s direction=%.1f° -> ENU=(x=%.3f,y=%.3f,z=0.000)",
            speed,
            direction_deg,
            wind_x,
            wind_y,
        )
        wind_setup_script = self._wind_setup_script(wind_x=wind_x, wind_y=wind_y)
        return (
            "python3 - <<'PY'\n"
            f"{wind_setup_script}"
            "PY\n"
            f"export PX4_GZ_WORLD={_CONTAINER_DYNAMIC_WIND_WORLD_NAME} && "
        )

    def _wind_setup_script(self, *, wind_x: float, wind_y: float) -> str:
        """Return a Python script that patches the wind world and x500 model."""
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
    ("dronevalkit_wind_drag_longitudinal", "1 0 0", "0 0 1", "0.12", "1.35"),
    ("dronevalkit_wind_drag_lateral", "0 1 0", "0 0 1", "0.18", "1.35"),
)
for plugin_id, forward, upward, area, cda in drag_plugins:
    plugin = None
    for existing in model.findall("plugin"):
        if existing.findtext("dronevalkit_plugin_id") == plugin_id:
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

    plugin_fields = (
        ("dronevalkit_plugin_id", plugin_id),
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
    )
    for tag, text in plugin_fields:
        ensure_child(plugin, tag, text)

model_tree.write(X500_BASE_MODEL, encoding="utf-8", xml_declaration=True)
"""

    async def wait_for_ready(self, timeout: float = 60.0) -> list:
        """Wait until all drones in the run are ready for MAVSDK use."""
        flight = importlib.import_module("dronevalkit.flight")

        # Let PX4/Gazebo finish bringing up sockets before MAVSDK binds its listeners.
        await asyncio.sleep(2.0)
        connect_task = asyncio.create_task(
            flight.connect_multi(
                self.num_drones,
                base_instance=self.base_instance,
                timeout=timeout,
            )
        )
        start = time.monotonic()
        try:
            while not connect_task.done():
                # Fail fast if the container exits before MAVSDK discovery.
                running = self._container_is_running()
                if running is False:
                    connect_task.cancel()
                    logs_tail = self._container_logs_tail()
                    details = "Container exited before MAVSDK discovery."
                    if logs_tail:
                        details += f" docker logs tail:\n{logs_tail}"
                    raise SimulationError(
                        f"PX4 SITL not ready after {time.monotonic() - start:.0f}s "
                        f"on {self.mavsdk_addresses}: {details}"
                    )
                await asyncio.sleep(1.0)

            drones = await connect_task
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if connect_task and not connect_task.done():
                connect_task.cancel()
            if isinstance(exc, SimulationError):
                raise
            logs_tail = self._container_logs_tail()
            extra = ""
            if logs_tail:
                extra = f"\nDocker logs tail:\n{logs_tail}"
            raise SimulationError(
                f"PX4 SITL not ready after {timeout:.0f}s on {self.mavsdk_addresses}: {exc}{extra}"
            ) from exc

        logger.info(
            "PX4 SITL ready on %s",
            ", ".join(self.mavsdk_addresses),
        )
        return drones

    def stop(self) -> None:
        """Stop and remove the container."""
        if not self.container_id:
            return
        subprocess.run(["docker", "stop", self.container_id], capture_output=True)
        logger.info("Stopped container %s", self.container_id[:12])
        self.container_id = None

    def _container_is_running(self) -> Optional[bool]:
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

    def _container_logs_tail(self, lines: int = 120) -> str:
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

    def get_latest_ulogs(self) -> dict[int, str]:
        """Return the most recent ULog path for each PX4 instance in the run."""
        groups: dict[int, list[str]] = {}
        for root, _dirs, files in os.walk(self.log_dir):
            for fname in files:
                if not fname.endswith(".ulg"):
                    continue
                path = os.path.join(root, fname)
                instance_id = self._instance_from_path(path)
                groups.setdefault(instance_id, []).append(path)

        latest: dict[int, str] = {}
        for drone_id in range(self.num_drones):
            instance_id = self.base_instance + drone_id
            candidates = groups.get(instance_id, [])
            if candidates:
                latest[drone_id] = max(candidates, key=os.path.getmtime)
        return latest

    def get_latest_ulog(self) -> Optional[str]:
        """Single-drone compatibility helper."""
        return self.get_latest_ulogs().get(0)

    @staticmethod
    def _instance_from_path(path: str) -> int:
        match = re.search(r"instance_(\d+)", path)
        if match:
            return int(match.group(1))
        match = re.search(r"/(\d{8})/(\d{2}_\d{2}_\d{2})\.ulg$", path)
        if match:
            return 0
        return 0
