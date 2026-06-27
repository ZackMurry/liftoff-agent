"""MAVSDK vehicle control primitives.

This module isolates low-level vehicle actions from mission routing logic.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from typing import Optional

from mavsdk import System

from . import geo
from .exceptions import ConnectionError, WaypointTimeoutError


class Drone:
    """Thin MAVSDK adapter for one PX4 vehicle."""

    def __init__(self, system: System, drone_id: Optional[int] = None) -> None:
        self.system = system
        self.drone_id = drone_id
        self._battery_monitor_task: Optional[asyncio.Task] = None
        self._latest_battery_pct: Optional[float] = None

    def _log_prefix(self) -> str:
        if self.drone_id is None:
            return "[drone=unknown]"
        return f"[drone={self.drone_id}]"

    def _ensure_battery_monitor(self) -> None:
        if self._battery_monitor_task is None or self._battery_monitor_task.done():
            self._battery_monitor_task = asyncio.create_task(self._run_battery_monitor())

    async def _run_battery_monitor(self) -> None:
        log = logging.getLogger(__name__)
        try:
            async for battery in self.system.telemetry.battery():
                pct = float(getattr(battery, "remaining_percent", float("nan")))
                if not math.isfinite(pct):
                    continue
                self._latest_battery_pct = pct
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.debug("%s Battery monitor stopped: %s", self._log_prefix(), exc)
        finally:
            self._battery_monitor_task = None

    async def goto_waypoint(
        self,
        latitude_deg: float,
        longitude_deg: float,
        absolute_altitude_m: float,
        tolerance: float = 1.0,
        yaw: float = 0.0,
        timeout: Optional[float] = None,
        speed_m_s: Optional[float] = None,
    ) -> bool:
        """Fly to a GPS waypoint and wait until within *tolerance* metres."""
        distance_m: Optional[float] = None
        if timeout is None:
            start_lat, start_lon, start_abs_alt, _start_rel_alt = await asyncio.wait_for(
                self.get_position_gps(),
                timeout=2.5,
            )
            horizontal_dist = geo.haversine_distance(
                start_lat, start_lon, latitude_deg, longitude_deg
            )
            vertical_dist = abs(start_abs_alt - absolute_altitude_m)
            distance_m = math.sqrt(horizontal_dist ** 2 + vertical_dist ** 2)
            # Windy SITL runs can make slow but useful progress for long legs.
            timeout = max(60.0, distance_m * 0.8)

        target_abs_alt = absolute_altitude_m
        if speed_m_s is not None:
            try:
                await self.system.action.set_current_speed(float(speed_m_s))
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "%s set_current_speed(%.2f) failed; continuing with PX4 defaults: %s",
                    self._log_prefix(),
                    float(speed_m_s),
                    exc,
                )
        await self.system.action.goto_location(latitude_deg, longitude_deg, target_abs_alt, yaw)
        deadline = time.monotonic() + timeout
        max_total_timeout = timeout
        if distance_m is not None:
            max_total_timeout = max(timeout * 3.0, distance_m * 2.0, 10.0 * 60.0)
        absolute_deadline = time.monotonic() + max_total_timeout
        progress_grace_s = 3.0 * 60.0
        progress_threshold_m = max(0.5, min(2.0, tolerance))
        best_dist: Optional[float] = None
        last_progress_time = time.monotonic()

        while True:
            current_lat, current_lon, current_abs_alt, _current_rel_alt = await self.get_position_gps()
            horizontal_dist = geo.haversine_distance(
                current_lat, current_lon, latitude_deg, longitude_deg
            )
            vertical_dist = abs(current_abs_alt - target_abs_alt)
            dist = math.sqrt(horizontal_dist ** 2 + vertical_dist ** 2)
            now = time.monotonic()
            if best_dist is None or dist < best_dist - progress_threshold_m:
                best_dist = dist
                last_progress_time = now
            if dist < tolerance:
                return True
            if now > absolute_deadline:
                return False
            if now > deadline and now - last_progress_time > progress_grace_s:
                return False

            await asyncio.sleep(0.5)

    async def hold_position(
        self,
        latitude_deg: float,
        longitude_deg: float,
        absolute_altitude_m: float,
        duration_s: float,
        yaw: float = 0.0,
    ) -> None:
        """Hold a GPS position after commanding it once."""
        target_abs_alt = absolute_altitude_m
        await self.system.action.goto_location(latitude_deg, longitude_deg, target_abs_alt, yaw)
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)

    async def get_position_gps(self) -> tuple[float, float, float, float]:
        """Read one global position sample."""
        async for pos in self.system.telemetry.position():
            return (
                pos.latitude_deg,
                pos.longitude_deg,
                pos.absolute_altitude_m,
                pos.relative_altitude_m,
            )
        raise ConnectionError("No valid global position sample received.")

    async def _get_in_air_state(self, timeout: float = 2.0) -> Optional[bool]:
        """Read one in-air telemetry sample, or ``None`` if unavailable."""

        async def _read_in_air_once() -> Optional[bool]:
            async for in_air in self.system.telemetry.in_air():
                return bool(in_air)
            return None

        try:
            return await asyncio.wait_for(_read_in_air_once(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    async def arm_and_takeoff(
        self,
        relative_altitude_m: float,
        _tolerance: float,
        armable_wait_timeout: float = 180.0,
    ) -> float:
        """Arm, take off, stabilize at altitude, and return stabilized absolute altitude."""
        log = logging.getLogger(__name__)
        await self.log_prearm_diagnostics(relative_altitude_m)

        try:
            log.info("%s Setting takeoff altitude to %.1fm", self._log_prefix(), relative_altitude_m)
            await self.system.action.set_takeoff_altitude(relative_altitude_m)
        except Exception as exc:
            log.warning("%s set_takeoff_altitude(%.1f) failed: %s", self._log_prefix(), relative_altitude_m, exc)

        arm_deadline = time.monotonic() + armable_wait_timeout
        arm_attempt = 0
        max_arm_attempts = 10
        while True:
            try:
                await self.system.action.arm()
                break
            except Exception as exc:
                if "COMMAND_DENIED" not in str(exc):
                    raise
                arm_attempt += 1
                if arm_attempt >= max_arm_attempts:
                    raise WaypointTimeoutError(
                        f"Arm denied after {max_arm_attempts} attempts"
                    ) from exc
                if time.monotonic() > arm_deadline:
                    raise WaypointTimeoutError(
                        f"Timed out waiting to arm after {armable_wait_timeout:.0f}s "
                        f"({arm_attempt} attempts)"
                    ) from exc
                log.info("%s Arm denied (attempt %d); retrying in 2s…", self._log_prefix(), arm_attempt)
                await asyncio.sleep(2.0)
        _lat, _lon, _abs_alt, takeoff_rel_baseline_m = await self.get_position_gps()
        checkpoint_rel_alt_m = takeoff_rel_baseline_m + min(5.0, relative_altitude_m)
        target_rel_alt_m = takeoff_rel_baseline_m + relative_altitude_m
        log.info(
            "%s Takeoff baseline rel_alt=%.2fm target_rel_alt=%.2fm",
            self._log_prefix(),
            takeoff_rel_baseline_m,
            target_rel_alt_m,
        )
        max_takeoff_commands = 1
        last_sample: Optional[tuple[float, float, float, float]] = None

        for takeoff_attempt in range(1, max_takeoff_commands + 1):
            await self.system.action.takeoff()
            attempt_deadline = time.monotonic() + 20.0

            while time.monotonic() < attempt_deadline:
                last_sample = await self.get_position_gps()
                _lat, _lon, _abs_alt, rel_alt = last_sample
                log.info("%s diff: %s", self._log_prefix(), abs(rel_alt - target_rel_alt_m))
                if rel_alt >= checkpoint_rel_alt_m:
                    break
                await asyncio.sleep(0.5)

            if last_sample is None:
                raise WaypointTimeoutError("No position sample received during takeoff")

            _lat, _lon, _abs_alt, rel_alt = last_sample
            if rel_alt >= checkpoint_rel_alt_m:
                break

            if takeoff_attempt == max_takeoff_commands:
                raise WaypointTimeoutError(
                    f"Timed out reaching checkpoint rel_alt {checkpoint_rel_alt_m:.1f}m after "
                    f"{max_takeoff_commands} takeoff commands"
                )

            log.warning(
                "%s Takeoff attempt %d did not reach %.1fm in 20s; retrying takeoff command",
                self._log_prefix(),
                takeoff_attempt,
                checkpoint_rel_alt_m,
            )

        _lat, _lon, _abs_alt, rel_alt = last_sample
        progress_baseline_alt = rel_alt
        progress_deadline = time.monotonic() + 5.0
        while abs(rel_alt - target_rel_alt_m) >= 1.0:
            if time.monotonic() >= progress_deadline:
                if rel_alt < progress_baseline_alt + 1.0:
                    raise WaypointTimeoutError(
                        f"Takeoff climb stalled at rel_alt={rel_alt:.1f}m while targeting "
                        f"rel_alt={target_rel_alt_m:.1f}m"
                    )
                progress_baseline_alt = rel_alt
                progress_deadline = time.monotonic() + 5.0

            await asyncio.sleep(0.5)
            last_sample = await self.get_position_gps()
            _lat, _lon, _abs_alt, rel_alt = last_sample
            log.info("%s diff: %s", self._log_prefix(), abs(rel_alt - target_rel_alt_m))

        stable_samples = 0
        deadline = time.monotonic() + 60.0
        while stable_samples < 3:
            _lat, _lon, abs_alt, rel_alt = await self.get_position_gps()
            if abs(rel_alt - target_rel_alt_m) < 1.0:
                stable_samples += 1
            else:
                stable_samples = 0
            if time.monotonic() > deadline:
                raise WaypointTimeoutError(
                    f"Timed out stabilizing at takeoff altitude {relative_altitude_m}m"
                )
            await asyncio.sleep(1.0)

        await self.system.action.hold()
        await asyncio.sleep(2.0)
        _lat, _lon, abs_alt, _rel_alt = await self.get_position_gps()
        return abs_alt

    async def land_and_disarm(
        self,
        landing_timeout: float = 60.0,
        progress_timeout: float = 30.0,
        touchdown_altitude_m: float = 2.0,
        settle_timeout: float = 90.0,
        settle_duration_s: float = 2.0,
    ) -> None:
        """Land at the current position and disarm.

        Monitors altitude descent and retries the land command if progress
        stalls.  After touchdown (``rel_alt < touchdown_altitude_m``), waits
        for altitude to settle near zero before disarming.

        Raises:
            WaypointTimeoutError: If descent stalls beyond recovery or
                altitude does not settle near zero after touchdown.
        """
        log = logging.getLogger(__name__)

        await self.system.action.land()
        log.info("%s Land command issued", self._log_prefix())

        landing_deadline = time.monotonic() + landing_timeout
        progress_baseline_alt: Optional[float] = None
        progress_deadline = time.monotonic() + progress_timeout
        low_altitude_grace_band_m = 12.0
        nominal_progress_threshold_m = 1.0
        low_altitude_progress_threshold_m = 0.25

        # Phase 1: Monitor descent until altitude drops below touchdown threshold.
        while True:
            _lat, _lon, _abs_alt, rel_alt = await self.get_position_gps()

            if rel_alt < touchdown_altitude_m:
                break

            progress_threshold_m = (
                low_altitude_progress_threshold_m
                if rel_alt <= low_altitude_grace_band_m
                else nominal_progress_threshold_m
            )
            if progress_baseline_alt is None:
                progress_baseline_alt = rel_alt
            elif rel_alt < progress_baseline_alt - progress_threshold_m:
                progress_baseline_alt = rel_alt
                progress_deadline = time.monotonic() + progress_timeout

            if time.monotonic() >= progress_deadline:
                remaining = landing_deadline - time.monotonic()
                if remaining <= progress_timeout:
                    raise WaypointTimeoutError(
                        f"Landing stalled at rel_alt={rel_alt:.1f}m"
                    )
                log.warning(
                    "%s Descent stalled at rel_alt=%.2fm; retrying land command",
                    self._log_prefix(),
                    rel_alt,
                )
                await self.system.action.land()
                progress_baseline_alt = rel_alt
                progress_deadline = time.monotonic() + progress_timeout

            if time.monotonic() >= landing_deadline:
                raise WaypointTimeoutError(
                    f"Landing stalled at rel_alt={rel_alt:.1f}m"
                )

            await asyncio.sleep(0.5)

        # Phase 2: Verify altitude settles near ground level.
        _SETTLE_TOLERANCE_M = 3.0
        settle_deadline = time.monotonic() + settle_timeout
        settle_start: Optional[float] = None

        while True:
            _lat, _lon, _abs_alt, rel_alt = await self.get_position_gps()

            if abs(rel_alt) <= _SETTLE_TOLERANCE_M:
                if settle_start is None:
                    settle_start = time.monotonic()
                elif time.monotonic() - settle_start >= settle_duration_s:
                    break
            else:
                settle_start = None

            if time.monotonic() >= settle_deadline:
                raise WaypointTimeoutError(
                    f"After touchdown, rel_alt={rel_alt:.1f}m is "
                    f"outside +/-{_SETTLE_TOLERANCE_M:.1f}m of ground"
                )

            await asyncio.sleep(0.5)

        with contextlib.suppress(Exception):
            await self.system.action.disarm()

    async def get_battery_pct(self) -> float:
        """Return battery level as percentage (0-100)."""
        self._ensure_battery_monitor()
        deadline = time.monotonic() + 3.5

        while time.monotonic() <= deadline:
            if self._latest_battery_pct is not None and self._latest_battery_pct > 0.1:
                return self._latest_battery_pct
            await asyncio.sleep(0.1)

        if self._latest_battery_pct is not None:
            return self._latest_battery_pct

        logging.getLogger(__name__).warning(
            "%s Battery telemetry unavailable; defaulting to 100%%",
            self._log_prefix(),
        )
        return 100.0

    async def log_prearm_diagnostics(self, target_altitude_m: float) -> None:
        """Log health and telemetry state immediately before arming."""
        log = logging.getLogger(__name__)

        async def _read_health_once() -> Optional[object]:
            async for health in self.system.telemetry.health():
                return health
            return None

        health = None
        try:
            health = await asyncio.wait_for(_read_health_once(), timeout=2.0)
        except asyncio.TimeoutError:
            log.warning("%s Prearm diagnostics: health telemetry unavailable", self._log_prefix())

        if health is not None:
            log.info(
                "%s Prearm health: global_ok=%s home_ok=%s armable=%s local_ok=%s",
                self._log_prefix(),
                getattr(health, "is_global_position_ok", None),
                getattr(health, "is_home_position_ok", None),
                getattr(health, "is_armable", None),
                getattr(health, "is_local_position_ok", None),
            )

        try:
            lat, lon, abs_alt, rel_alt = await asyncio.wait_for(self.get_position_gps(), timeout=2.0)
            log.info(
                "%s Prearm position: lat=%.6f lon=%.6f abs_alt=%.2f rel_alt=%.2f target=%.2f",
                self._log_prefix(),
                lat,
                lon,
                abs_alt,
                rel_alt,
                target_altitude_m,
            )
        except (asyncio.TimeoutError, ConnectionError):
            log.warning("%s Prearm diagnostics: position telemetry unavailable", self._log_prefix())

        batt = await self.get_battery_pct()
        log.info("%s Prearm battery: %.1f%%", self._log_prefix(), batt)

        try:
            in_air = await self._get_in_air_state(timeout=2.0)
            log.info("%s Prearm in_air: %s", self._log_prefix(), in_air)
        except asyncio.TimeoutError:
            log.warning("%s Prearm diagnostics: in_air telemetry unavailable", self._log_prefix())
