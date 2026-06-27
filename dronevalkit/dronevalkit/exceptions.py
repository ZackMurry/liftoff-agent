"""Custom exceptions for dronevalkit."""


class DroneValKitError(Exception):
    """Base exception for all dronevalkit errors."""


class SimulationError(DroneValKitError):
    """Raised when the PX4 SITL simulation fails or times out."""


class ContainerError(DroneValKitError):
    """Raised when Docker container lifecycle management fails."""


class ConnectionError(DroneValKitError):
    """Raised when MAVSDK cannot connect to PX4."""


class MissionAbortedError(DroneValKitError):
    """Raised when a flight mission is aborted due to timeout or crash."""


class WaypointTimeoutError(DroneValKitError):
    """Raised when the drone fails to reach a waypoint within the timeout."""


class ULogParseError(DroneValKitError):
    """Raised when a ULog file cannot be parsed."""


class InvalidSolutionError(DroneValKitError):
    """Raised when a solution JSON file is malformed or missing required fields."""


class InfeasibleSortieError(DroneValKitError):
    """Raised when a sortie is detected as infeasible due to battery constraints."""
