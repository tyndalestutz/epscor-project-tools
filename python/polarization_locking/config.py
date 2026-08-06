from dataclasses import dataclass
from typing import Tuple


@dataclass
class PolarizationLockConfig:
    rp_hostname: str = "192.168.1.98"
    rp_config: str = "scope_config"
    pax_host: str = "localhost"
    pax_port: int = 38400

    update_hz: float = 10.0
    max_points: int = 500

    pid_p: float = 6.9
    pid_i: float = 7900.0
    pid_setpoint: float = 0.5

    # Placeholder target polarization state in theta / eta coordinates.
    target_theta: float = 0.0
    target_eta: float = 0.0

    # Simple gain mapping for the first implementation.
    voltage_gain: Tuple[float, float] = (1.0, 1.0)


DEFAULT_CONFIG = PolarizationLockConfig()
