from dataclasses import dataclass
from typing import Optional


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

    # Rough alignment: measured actuator volts for a 2*pi phase shift.
    phi1_v_lambda: Optional[float] = 11.0
    phi2_v_lambda: Optional[float] = 30.0

    # Electrical transfer from Red Pitaya output to the actuator itself, in
    # actuator-volts / RP-volts.  These must be measured for the installed
    # wiring (including any pre-amplifier and high-voltage driver); the rough
    # mover will not run until both are supplied.
    phi1_actuator_volts_per_rp_volt: Optional[float] = None
    phi2_actuator_volts_per_rp_volt: Optional[float] = None

    # The RP outputs are unipolar 0–1 V in this setup.  phi1/phi2 routing must
    # be confirmed against the physical cabling before enabling a rough move.
    rp_output_min_voltage: float = 0.0
    rp_output_max_voltage: float = 1.0
    phase_output_map_confirmed: bool = False
    rough_deadband_rad: float = 1e-3
    rough_max_delta_voltage: Optional[float] = None
    rough_settle_s: float = 1.0
    sphere_pole_tolerance: float = 1e-6
    minimum_dop: float = 0.9

    plot_live: bool = True
    plot_window: int = 200


DEFAULT_CONFIG = PolarizationLockConfig()
