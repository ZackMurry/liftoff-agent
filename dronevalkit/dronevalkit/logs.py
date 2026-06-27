"""ULog parsing and per-sortie metric extraction.

Design notes
------------
Energy accounting
    Energy is reported as **battery percentage consumed** within a segment.
    This is read directly from PX4's battery telemetry at segment boundaries
    and is independent of ULog timestamp alignment.  The :func:`compute_motor_energy`
    function (motor-output integration from ULog) is provided for research use
    where higher physical accuracy is desired, but it requires caller-supplied
    timestamp alignment between wall-clock segment times and PX4 boot-relative
    ULog timestamps.

Corrected battery curve
    The corrected battery curve reconstructs the battery state that the drone
    *would* have had in a real truck-mounted deployment, where repositioning
    legs between sorties do not exist.  Starting from 100 %, only sortie
    energy is subtracted — repositioning energy is excluded::

        corrected_battery[sortie_i_end] = 100% − Σ energy[sortie_0..i]

    A sortie is flagged **infeasible** if ``corrected_battery`` drops below
    ``min_battery_pct`` at its end.

Battery resets after landing
    PX4 SITL can reset reported battery telemetry back to 100 % after a
    land/disarm cycle. For analysis we reconstruct a continuous effective
    battery curve by detecting upward jumps between consecutive segment
    boundaries and subtracting the reset amount from subsequent readings.
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .exceptions import ULogParseError
from .models import LegTiming

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result data structures
# ---------------------------------------------------------------------------


@dataclass
class SortieResult:
    """Metrics extracted from a single sortie segment of a mission run."""

    drone_id: int
    sortie_index: int
    actual_time: float              # seconds (sortie segment only)
    actual_energy: float            # battery % consumed (sortie segment only)
    actual_distance: float          # integrated 3-D path length in metres
    actual_path: list               # [(north_m, east_m, down_m, ts_s), ...]
    raw_battery_at_start: float     # PX4's reported battery % at sortie start
    raw_battery_at_end: float       # PX4's reported battery % at sortie end
    corrected_battery_at_end: float # % after removing repositioning drain
    feasible: bool                  # corrected % stayed above min threshold
    max_position_error: float       # max cross-track deviation in metres
    start_time: float = 0.0         # mission-relative start time (seconds)
    end_time: float = 0.0           # mission-relative end time (seconds)
    leg_timings: Optional[list[LegTiming]] = None
    leg_energy_samples: Optional[list["LegEnergySample"]] = None


@dataclass
class LegEnergySample:
    """Battery telemetry sampled at one mission-leg boundary pair."""

    name: str
    start_time: float
    end_time: float
    raw_battery_at_start: float
    raw_battery_at_end: float
    energy_pct: float


@dataclass
class RepositionResult:
    """Metrics for a repositioning leg (excluded from primary analysis)."""

    drone_id: int
    from_rendezvous: int            # rendezvous node ID of the preceding sortie
    to_launch: int                  # launch node ID of the following sortie
    time: float                     # seconds
    energy: float                   # battery % consumed
    distance: float                 # metres
    start_time: float = 0.0         # mission-relative start time (seconds)
    end_time: float = 0.0           # mission-relative end time (seconds)
    leg_timings: Optional[list[LegTiming]] = None
    leg_energy_samples: Optional[list[LegEnergySample]] = None


@dataclass
class DroneRunResult:
    """Results for one drone within a single experiment run."""

    drone_id: int
    sortie_results: list            # list[SortieResult]
    reposition_results: list        # list[RepositionResult]
    actual_makespan: float          # sorties-only time (excluding repositioning)
    raw_makespan: float             # wall-clock mission time including repositioning
    ulog_path: str


@dataclass
class RunResult:
    """Results from one complete simulation run (one condition, one replication)."""

    condition: object               # WindCondition
    replication: int
    drone_results: list             # list[DroneRunResult]
    actual_makespan: float          # sorties-only time (excluding repositioning)
    raw_makespan: float             # wall-clock mission time including repositioning
    per_drone_results: Optional[dict] = None

    def __post_init__(self) -> None:
        if self.per_drone_results is None:
            self.per_drone_results = {
                drone_result.drone_id: {
                    "sortie_results": drone_result.sortie_results,
                    "reposition_results": drone_result.reposition_results,
                }
                for drone_result in self.drone_results
            }

    @property
    def sortie_results(self) -> list:
        """Flattened per-sortie view across all drones for compatibility."""
        return [
            sortie_result
            for drone_result in self.drone_results
            for sortie_result in drone_result.sortie_results
        ]

    @property
    def reposition_results(self) -> list:
        """Flattened per-reposition view across all drones for compatibility."""
        return [
            reposition_result
            for drone_result in self.drone_results
            for reposition_result in drone_result.reposition_results
        ]

    @property
    def ulog_path(self) -> str:
        """Return the first ULog path for compatibility with single-drone callers."""
        for drone_result in self.drone_results:
            if drone_result.ulog_path:
                return drone_result.ulog_path
        return ""


# ---------------------------------------------------------------------------
# ULog parsing
# ---------------------------------------------------------------------------


def parse_ulog(ulog_path: str) -> dict:
    """Parse a PX4 ULog file and return topic arrays.

    Extracts ``vehicle_local_position``, ``battery_status``,
    ``actuator_outputs``, and ``vehicle_attitude``.

    Args:
        ulog_path: Filesystem path to a ``.ulg`` file.

    Returns:
        ``{topic_name: {field_name: np.ndarray, ...}, ...}``
        Topics missing from the log are simply absent from the dict.

    Raises:
        ULogParseError: If the file cannot be opened or parsed.
    """
    try:
        from pyulog import ULog  # type: ignore[import]
    except ImportError as exc:
        raise ULogParseError("pyulog is not installed") from exc

    try:
        ulog = ULog(ulog_path)
    except Exception as exc:
        raise ULogParseError(f"Cannot parse {ulog_path}: {exc}") from exc

    topics_wanted = {
        "vehicle_local_position",
        "battery_status",
        "actuator_outputs",
        "vehicle_attitude",
    }
    data: dict = {}
    for d in ulog.data_list:
        if d.name in topics_wanted:
            data[d.name] = {field: np.array(d.data[field]) for field in d.data}

    for t in topics_wanted:
        if t not in data:
            logger.debug("ULog topic '%s' not found in %s", t, ulog_path)
    return data


def compute_motor_energy(
    actuator_data: dict,
    start_us: int,
    end_us: int,
) -> float:
    """Integrate motor power over a ULog timestamp window.

    Motor outputs are normalised [0, 1].  Power is approximated as
    ``output ** 1.5`` (thrust ∝ RPM², power ∝ RPM³ for a quadrotor).
    Uses trapezoidal integration.

    .. note::
        ``start_us`` and ``end_us`` must be in the **same timestamp domain**
        as the ``actuator_outputs.timestamp`` field (PX4 boot-relative
        microseconds in SITL).  The caller is responsible for aligning
        wall-clock segment times with PX4 timestamps.

    Args:
        actuator_data: ``actuator_outputs`` topic dict from :func:`parse_ulog`.
        start_us: Window start in microseconds (ULog timestamp domain).
        end_us: Window end in microseconds.

    Returns:
        Energy in motor-power-seconds (arbitrary but consistent units).
        Returns ``0.0`` if fewer than 2 samples fall within the window.
    """
    if not actuator_data:
        return 0.0

    timestamps = actuator_data.get("timestamp")
    if timestamps is None or len(timestamps) == 0:
        return 0.0

    mask = (timestamps >= start_us) & (timestamps <= end_us)
    ts = timestamps[mask]
    if len(ts) < 2:
        return 0.0

    total_power = np.zeros(len(ts))
    for m in range(4):
        key = f"output[{m}]"
        if key in actuator_data:
            total_power += np.clip(actuator_data[key][mask], 0.0, 1.0) ** 1.5

    dt = np.diff(ts) / 1e6  # µs → s
    return float(np.sum(0.5 * (total_power[:-1] + total_power[1:]) * dt))


# ---------------------------------------------------------------------------
# Mission result extraction
# ---------------------------------------------------------------------------


def extract_mission_results(
    ulog_data: dict,
    segments: list,
    drone_id: int,
    min_battery_pct: float = 20.0,
    expected_drain_rate: Optional[float] = None,
    time_scale_factor: float = 1.0,
) -> tuple:
    """Build :class:`SortieResult` and :class:`RepositionResult` lists from
    segment telemetry.

    Energy is taken from PX4's reported battery percentage at segment
    boundaries (always available, no timestamp alignment required).
    Spatial metrics — actual path, distance, and maximum cross-track error —
    are derived from the MAVSDK position samples collected by
    :class:`~dronevalkit.flight._TelemetryCollector` during flight.

    Args:
        ulog_data: Parsed ULog dict from :func:`parse_ulog` (used for future
            motor-energy integration; may be empty ``{}`` without error).
        segments: List of segment dicts.  Each must contain:
            ``segment_type`` ("sortie" or "reposition"),
            ``sortie_index`` (int or None),
            ``start_time`` (float, mission-relative seconds),
            ``end_time`` (float),
            ``positions`` (list of ``(n, e, d, ts)`` tuples),
            ``battery_at_start`` (float, %),
            ``battery_at_end`` (float, %).
        min_battery_pct: Corrected battery threshold for feasibility.
        drone_id: Drone id associated with these segments.
        expected_drain_rate: Optional configured ``SIM_BAT_DRAIN`` value. If
            provided and ``<= 0``, battery telemetry deltas are ignored and
            segment energy is forced to ``0`` to avoid SITL telemetry artifacts.
        time_scale_factor: Multiplier applied to measured wall-clock durations
            to recover real-time-equivalent mission timing when the simulator
            is sped up (for example ``2.0`` means a measured ``33s`` is
            reported as ``66s``).

    Returns:
        ``(sortie_results, reposition_results)``
    """
    sortie_results: list = []
    reposition_results: list = []
    corrected_battery = 100.0
    if time_scale_factor <= 0.0:
        raise ValueError("time_scale_factor must be positive")
    battery_reset_credit = 0.0
    previous_raw_end: Optional[float] = None

    for seg in segments:
        seg_type: str = seg["segment_type"]
        start_t: float = seg["start_time"]
        end_t: float = seg["end_time"]
        batt_start: float = seg["battery_at_start"]
        batt_end: float = seg["battery_at_end"]
        positions: list = seg.get("positions", [])
        battery_samples = _coerce_battery_samples(seg.get("battery_samples"))
        leg_timings = _coerce_leg_timings(seg.get("leg_timings"))
        leg_energy_samples = _coerce_leg_energy_samples(seg.get("leg_energy_samples"))

        if previous_raw_end is not None and batt_start > previous_raw_end + 1.0:
            battery_reset_credit += batt_start - previous_raw_end

        effective_batt_start = batt_start - battery_reset_credit
        effective_batt_end = batt_end - battery_reset_credit

        if expected_drain_rate is not None and expected_drain_rate <= 0.0:
            energy_pct = 0.0
            scaled_leg_energy_samples = _zero_leg_energy_samples(
                _scale_leg_energy_samples(
                    _build_leg_energy_samples(
                        leg_timings=leg_timings,
                        battery_samples=battery_samples,
                        fallback_samples=leg_energy_samples,
                    ),
                    time_scale_factor,
                )
            )
        else:
            # Battery % consumed — clamped to ≥0 to guard against sensor noise.
            energy_pct = max(0.0, effective_batt_start - effective_batt_end)
            scaled_leg_energy_samples = _scale_leg_energy_samples(
                _normalize_leg_energy_samples(
                    _build_leg_energy_samples(
                        leg_timings=leg_timings,
                        battery_samples=battery_samples,
                        fallback_samples=leg_energy_samples,
                    ),
                    battery_reset_credit,
                ),
                time_scale_factor,
            )

        actual_path = list(positions)
        actual_distance = _path_length_m(actual_path)
        max_pos_error = _max_cross_track_error_m(actual_path)
        scaled_duration_s = (end_t - start_t) * time_scale_factor

        if seg_type == "sortie":
            corrected_battery -= energy_pct
            sortie_results.append(SortieResult(
                drone_id=drone_id,
                sortie_index=seg["sortie_index"],
                start_time=start_t * time_scale_factor,
                end_time=end_t * time_scale_factor,
                actual_time=scaled_duration_s,
                actual_energy=energy_pct,
                actual_distance=actual_distance,
                actual_path=actual_path,
                raw_battery_at_start=effective_batt_start,
                raw_battery_at_end=effective_batt_end,
                corrected_battery_at_end=corrected_battery,
                feasible=corrected_battery >= min_battery_pct,
                max_position_error=max_pos_error,
                leg_timings=_scale_leg_timings(leg_timings, time_scale_factor),
                leg_energy_samples=scaled_leg_energy_samples,
            ))

        elif seg_type == "reposition":
            reposition_results.append(RepositionResult(
                drone_id=drone_id,
                from_rendezvous=-1,  # populated by caller from sortie definitions
                to_launch=-1,
                start_time=start_t * time_scale_factor,
                end_time=end_t * time_scale_factor,
                time=scaled_duration_s,
                energy=energy_pct,
                distance=actual_distance,
                leg_timings=_scale_leg_timings(leg_timings, time_scale_factor),
                leg_energy_samples=scaled_leg_energy_samples,
            ))

        previous_raw_end = batt_end

    return sortie_results, reposition_results


# ---------------------------------------------------------------------------
# Spatial helpers
# ---------------------------------------------------------------------------


def _path_length_m(positions: list) -> float:
    """3-D Euclidean path length in metres from ``(n, e, d, ts)`` tuples."""
    if len(positions) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(positions)):
        dn = positions[i][0] - positions[i - 1][0]
        de = positions[i][1] - positions[i - 1][1]
        dd = positions[i][2] - positions[i - 1][2]
        total += math.sqrt(dn * dn + de * de + dd * dd)
    return total


def _coerce_leg_timings(value: object) -> list[LegTiming]:
    if not value:
        return []
    leg_timings: list[LegTiming] = []
    for item in value:
        if isinstance(item, LegTiming):
            leg_timings.append(item)
        else:
            leg_timings.append(
                LegTiming(
                    name=str(item["name"]),
                    start_time=float(item["start_time"]),
                    end_time=float(item["end_time"]),
                )
            )
    return leg_timings


def _scale_leg_timings(leg_timings: list[LegTiming], time_scale_factor: float) -> list[LegTiming]:
    return [
        LegTiming(
            name=leg_timing.name,
            start_time=leg_timing.start_time * time_scale_factor,
            end_time=leg_timing.end_time * time_scale_factor,
        )
        for leg_timing in leg_timings
    ]


def _coerce_leg_energy_samples(value: object) -> list[LegEnergySample]:
    if not value:
        return []
    samples: list[LegEnergySample] = []
    for item in value:
        if isinstance(item, LegEnergySample):
            samples.append(item)
        else:
            samples.append(
                LegEnergySample(
                    name=str(item["name"]),
                    start_time=float(item["start_time"]),
                    end_time=float(item["end_time"]),
                    raw_battery_at_start=float(item["raw_battery_at_start"]),
                    raw_battery_at_end=float(item["raw_battery_at_end"]),
                    energy_pct=float(item["energy_pct"]),
                )
            )
    return samples


def _coerce_battery_samples(value: object) -> list[tuple[float, float]]:
    if not value:
        return []
    samples: list[tuple[float, float]] = []
    for item in value:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            samples.append((float(item[0]), float(item[1])))
    return sorted(samples, key=lambda item: item[0])


def _build_leg_energy_samples(
    *,
    leg_timings: list[LegTiming],
    battery_samples: list[tuple[float, float]],
    fallback_samples: list[LegEnergySample],
) -> list[LegEnergySample]:
    if leg_timings and battery_samples:
        samples: list[LegEnergySample] = []
        for leg_timing in leg_timings:
            start_battery = _battery_pct_at_time(battery_samples, leg_timing.start_time)
            end_battery = _battery_pct_at_time(battery_samples, leg_timing.end_time)
            samples.append(
                LegEnergySample(
                    name=leg_timing.name,
                    start_time=leg_timing.start_time,
                    end_time=leg_timing.end_time,
                    raw_battery_at_start=start_battery,
                    raw_battery_at_end=end_battery,
                    energy_pct=max(0.0, start_battery - end_battery),
                )
            )
        return samples
    return fallback_samples


def _battery_pct_at_time(samples: list[tuple[float, float]], target_time: float) -> float:
    if not samples:
        return 100.0
    if target_time <= samples[0][0]:
        return samples[0][1]
    if target_time >= samples[-1][0]:
        return samples[-1][1]

    for index in range(1, len(samples)):
        left_time, left_value = samples[index - 1]
        right_time, right_value = samples[index]
        if target_time <= right_time:
            span = max(right_time - left_time, 1e-9)
            weight = (target_time - left_time) / span
            return float(left_value + weight * (right_value - left_value))
    return samples[-1][1]


def _normalize_leg_energy_samples(
    samples: list[LegEnergySample],
    battery_reset_credit: float,
) -> list[LegEnergySample]:
    normalized: list[LegEnergySample] = []
    for sample in samples:
        raw_start = sample.raw_battery_at_start - battery_reset_credit
        raw_end = sample.raw_battery_at_end - battery_reset_credit
        normalized.append(
            LegEnergySample(
                name=sample.name,
                start_time=sample.start_time,
                end_time=sample.end_time,
                raw_battery_at_start=raw_start,
                raw_battery_at_end=raw_end,
                energy_pct=max(0.0, raw_start - raw_end),
            )
        )
    return normalized


def _scale_leg_energy_samples(
    samples: list[LegEnergySample],
    time_scale_factor: float,
) -> list[LegEnergySample]:
    return [
        LegEnergySample(
            name=sample.name,
            start_time=sample.start_time * time_scale_factor,
            end_time=sample.end_time * time_scale_factor,
            raw_battery_at_start=sample.raw_battery_at_start,
            raw_battery_at_end=sample.raw_battery_at_end,
            energy_pct=sample.energy_pct,
        )
        for sample in samples
    ]


def _zero_leg_energy_samples(samples: list[LegEnergySample]) -> list[LegEnergySample]:
    return [
        LegEnergySample(
            name=sample.name,
            start_time=sample.start_time,
            end_time=sample.end_time,
            raw_battery_at_start=sample.raw_battery_at_start,
            raw_battery_at_end=sample.raw_battery_at_end,
            energy_pct=0.0,
        )
        for sample in samples
    ]


def _max_cross_track_error_m(positions: list) -> float:
    """Max perpendicular deviation from the straight line first→last point.

    Returns ``0.0`` if fewer than 3 positions are available or if start and
    end are coincident.
    """
    if len(positions) < 3:
        return 0.0

    p0 = np.array(positions[0][:3], dtype=float)
    p1 = np.array(positions[-1][:3], dtype=float)
    line_vec = p1 - p0
    line_len = float(np.linalg.norm(line_vec))
    if line_len < 1e-6:
        return 0.0
    line_unit = line_vec / line_len

    max_err = 0.0
    for pos in positions[1:-1]:
        v = np.array(pos[:3], dtype=float) - p0
        proj = float(np.dot(v, line_unit))
        perp = v - proj * line_unit
        err = float(np.linalg.norm(perp))
        if err > max_err:
            max_err = err
    return max_err
