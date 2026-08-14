from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class PolarizationLockConfig:
    rp_hostname: str = "192.168.1.98"
    rp_config: str = "scope_config"
    pax_host: str = "localhost"
    pax_port: int = 38400
    # Match the existing PAX live-plot behavior: use an already-running daemon
    # when available, otherwise start this local configuration automatically.
    pax_autostart_daemon: bool = True
    pax_daemon_config_path: str = str(Path(__file__).resolve().parents[1] / "PAX1000" / "pax1000.toml")
    pax_daemon_start_timeout_s: float = 5.0
    # The YAQD PAX driver has its own 100 ms acquisition wait. Keep this short
    # so the lock loop does not add another 60 ms of idle time per sample.
    pax_measurement_wait_s: float = 0.01
    pax_read_retries: int = 3
    pax_retry_wait_s: float = 0.25

    # Target in the hybrid-MZ sphere coordinates. u is azimuth in [-pi, pi)
    # and v is polar angle in [0, pi]. Both must be set before a rough move.
    target_u: Optional[float] = None
    target_v: Optional[float] = None

    # Rough alignment: measured actuator volts for a 2*pi phase shift. phi1's
    # 12.2 V value is the current candidate from the static diagnostic; retain
    # it as a tunable calibration until a repeat forward/reverse test confirms it.
    phi1_v_lambda: Optional[float] = 12.2
    phi2_v_lambda: Optional[float] = 30.0

    # Electrical transfer from an RP command voltage to actuator voltage, in
    # actuator-volts / RP-command-volt. At a 1.0 V command the measured RP
    # output is 1.125 V. OUT1 -> phi1 goes through the x15 voltage controller;
    # OUT2 -> phi2 also passes through the x8.89 preamp before that controller.
    phi1_actuator_volts_per_rp_volt: Optional[float] = 16.875  # 1.125 * 15
    phi2_actuator_volts_per_rp_volt: Optional[float] = 150.0  # 1.125 * 8.89 * 15

    # The RP commands are unipolar 0–1 V. Physical cabling is OUT1 -> phi1 and
    # OUT2 -> phi2.
    rp_output_min_voltage: float = 0.0
    rp_output_max_voltage: float = 1.0
    phase_output_map_confirmed: bool = True
    rough_deadband_rad: float = 1e-3
    rough_max_delta_voltage: Optional[float] = None  # RP command volts
    rough_settle_s: float = 1.0
    sphere_pole_tolerance: float = 1e-6
    minimum_dop: float = 0.9

    # Conservative, data-logging PI lock experiment. Gains are dimensionless
    # phase corrections scaled by the calibrated RP-volts/radian conversion.
    # D is initially disabled because the measured sphere noise makes a raw
    # derivative term counterproductive. This is a test routine, not a
    # production lock configuration.
    pid_seed_phi1_rp_voltage: float = 0.36
    pid_seed_phi2_rp_voltage: float = 0.10
    pid_seed_settle_s: float = 3.0
    pid_rough_fraction: float = 0.7
    pid_rough_max_phase_step_rad: tuple[float, float] = (1.5, 1.0)
    pid_rough_settle_s: float = 1.0
    pid_kp: tuple[float, float] = (0.20, 0.10)
    pid_ki_per_s: tuple[float, float] = (0.010, 0.005)
    pid_kd_s: tuple[float, float] = (0.0, 0.0)
    pid_integral_limit_rad_s: float = 3.0
    pid_max_phase_step_rad: tuple[float, float] = (0.25, 0.20)
    # Use deliberately gentler PI corrections in the local lock region. The
    # thresholds are phase errors in (phi1, phi2), not PAX theta/eta. These
    # are an experimental tuning choice to reduce noise chasing, not V_lambda
    # calibration constants.
    pid_fine_error_threshold_rad: tuple[float, float] = (0.50, 0.35)
    # Test 12: modestly raise local stiffness after test 11 established that
    # the lock is unbiased but phi1 has the larger residual variance. These
    # multipliers apply only inside the thresholds above: effective local Kp
    # becomes (0.090, 0.042), up from (0.070, 0.035).
    pid_fine_gain: tuple[float, float] = (0.45, 0.42)
    # Desired lower bound between PID iterations. Actual cadence is presently
    # limited by the YAQD PAX driver's own acquisition wait.
    pid_sample_period_s: float = 0.15
    pid_visual_refresh_s: float = 0.10
    # Test 13: average two successive normalized Stokes measurements before
    # each PID update. This targets the residual zero-mean PAX-scale scatter
    # seen in tests 11–12 without increasing feedback gain.
    pid_pax_average_count: int = 2
    pid_pre_acquisition_settle_s: float = 0.015
    # When an output is pinned at a rail but the remaining phase error is
    # substantial, shift it by one V_lambda (the same ideal sphere point) to
    # recover control headroom. Each shift is logged and bounded.
    pid_recenter_error_threshold_rad: float = 0.4
    pid_recenter_cooldown_s: float = 5.0
    pid_recenter_max_events_per_axis: int = 2
    pid_recenter_settle_s: float = 0.75

    # Single-actuator characterization/lock test. A short one-V_lambda sweep
    # selects the measured coordinate at the midpoint command as the target;
    # the other RP output remains exactly zero throughout the subsequent PI
    # hold. These are intentionally conservative enough to compare actuator
    # authority before revisiting the two-axis controller.
    single_axis_pid_sweep_points: int = 17
    single_axis_pid_settle_s: float = 0.18
    single_axis_pid_target_settle_s: float = 0.75

    # Cross-sweep defaults for characterizing phase cross-coupling. Each
    # swept axis covers one of its own V_lambda values, and the fixed/bias
    # axis spans its *entire* V_lambda in cross_sweep_bias_intervals equally
    # sized intervals. Ten intervals yields eleven bias slices, including
    # both endpoints.
    cross_sweep_bias_intervals: int = 10
    cross_sweep_step_voltage: float = 0.005
    cross_sweep_settle_s: float = 0.25

    # Bidirectional V_lambda characterizations use the cleanest bias slices
    # identified in the first cross-sweep.  The slower dwell makes forward /
    # reverse differences attributable to hysteresis rather than transients.
    bidirectional_phi1_bias_voltage: float = 0.1
    bidirectional_phi2_bias_voltage: float = 0.2
    bidirectional_sweep_step_voltage: float = 0.005
    bidirectional_sweep_settle_s: float = 0.5

    # Static diagnostic suite. It records a baseline plus five held states for
    # each phase axis. Fractions are converted to one axis-specific V_lambda
    # at run time, keeping the test valid if a V_lambda is refined later.
    diagnostic_phase_fractions: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
    diagnostic_phi1_bias_voltage: float = 0.1
    diagnostic_phi2_bias_voltage: float = 0.2
    diagnostic_settle_s: float = 3.0
    diagnostic_hold_s: float = 60.0
    diagnostic_sample_period_s: float = 0.5

    # Final-output photodiode diagnostic. The PD is wired to Red Pitaya IN1;
    # a short scope acquisition yields its mean voltage and within-window
    # noise alongside each PAX polarization measurement.  The PD arm includes
    # an OD 2.0 neutral-density filter, so the detector sees 10**-2 of the
    # incident optical power.  The diagnostic records both the RP-measured
    # voltage and its linear, pre-filter-equivalent value (100x).  This does
    # not calibrate the PD against PAX ``ptotal`` units, but it prevents an
    # accidental 100x error when comparing their *relative* responses.
    pd_input: str = "in1"
    pd_nd_optical_density: float = 2.0
    pd_scope_duration_s: float = 0.01
    pd_scope_decimation: int = 64
    pd_scope_timeout_s: float = 2.0
    intensity_diagnostic_step_voltage: float = 0.005
    intensity_diagnostic_settle_s: float = 0.25

    # Guided, fixed-actuator PAX diagnostic. This is intentionally slower than
    # a lock update so the comparison is about the PAX/path condition rather
    # than a transient following an actuator move.
    pax_path_hold_sample_period_s: float = 0.25

    # Guided phi2 power-balance test. The RP sine is centered at half a
    # calibrated phi2 V_lambda, so it spans 0..V_lambda without asking the
    # unipolar RP output to generate a negative voltage.
    power_balance_phi2_frequency_hz: float = 0.5
    power_balance_sample_period_s: float = 0.05

    # Fit-ready field-propagation calibration. At each phi1 bias, sweep one
    # phi2 V_lambda for path A only, path B only, and both paths. Alternating
    # the condition order and sweep direction on the second repeat brackets
    # slow interferometer drift while providing the independent Stokes states
    # needed to fit P = B + h.S.
    field_model_phi1_fractions: tuple[float, ...] = (0.0, 0.25, 0.50, 0.75)
    field_model_phi2_step_voltage: float = 0.005
    field_model_repeats: int = 2
    field_model_settle_s: float = 0.30

    # Focused both-path map for calibrating phi1 from the measured phi2 fringe
    # phase. This is intentionally independent of manual block changes.
    phi1_fringe_map_points: int = 17
    phi1_fringe_map_phi2_step_voltage: float = 0.010
    phi1_fringe_map_settle_s: float = 0.30

    # First-NPBS D-port isolation test. The PAX is temporarily moved to D;
    # phi1 is held static, then driven across one V_lambda with OUT1.
    first_npbs_d_static_duration_s: float = 60.0
    first_npbs_d_driven_duration_s: float = 60.0
    first_npbs_d_phi1_frequency_hz: float = 0.5
    first_npbs_d_sample_period_s: float = 0.05
    first_npbs_d_isolation_duration_s: float = 60.0
    first_npbs_d_polarizer_static_duration_s: float = 30.0
    # PAX returns a fresh measurement about every 0.12 s in this setup. A
    # 0.15-Hz sine (6.7-s period) gives roughly 50 PAX samples/cycle: dense
    # enough to resolve the trajectory while avoiding the slow-drift regime.
    first_npbs_d_polarizer_driven_duration_s: float = 80.0
    first_npbs_d_polarizer_phi1_frequency_hz: float = 0.15
    first_npbs_d_polarizer_sample_period_s: float = 0.10

DEFAULT_CONFIG = PolarizationLockConfig()
