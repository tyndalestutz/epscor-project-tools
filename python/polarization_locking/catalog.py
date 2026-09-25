"""The bench-test catalog: descriptions, parameters, and method dispatch in one place."""
from dataclasses import dataclass, fields

from .config import PolarizationLockConfig


@dataclass(frozen=True)
class TestCase:
    key: str
    title: str
    description: str
    setup: str
    method: str
    groups: tuple[str, ...] = ()
    axis: bool = False
    timed: bool = False
    report: str = "overview"
    target: bool = False
    flags: tuple[str, ...] = ()
    scope_only: bool = False
    pax_only: bool = False

    def config_names(self) -> list[str]:
        if self.key == "pax-vibration":
            return ["bench_notes", "bench_pax_location", "bench_voltage_chain", "rp_hostname", "rp_config", "pd_input", "results_directory", "sphere_pole_tolerance"] + [
                f.name for f in fields(PolarizationLockConfig)
                if f.name.startswith("pax_") and not f.name.startswith(("pax_live_", "pax_path_"))]
        if self.pax_only:
            return ["bench_notes", "bench_pax_location", "results_directory", "minimum_dop"] + [
                f.name for f in fields(PolarizationLockConfig)
                if f.name.startswith("pax_") and not f.name.startswith("pax_path_")]
        if self.scope_only:
            return ["bench_notes", "bench_pax_location", "rp_hostname", "rp_config", "rp_scope_port", "pd_input", "phase_output_map_confirmed", "rp_output_max_voltage", "results_directory"] + [f.name for f in fields(PolarizationLockConfig) if f.name.startswith(("visibility_", "pax_"))]
        common = ("bench_", "rp_", "pax_", "phi1_v_lambda", "phi2_v_lambda", "phi1_actuator_", "phi2_actuator_", "phase_output_", "minimum_dop", "results_directory")
        return [f.name for f in fields(PolarizationLockConfig) if f.name.startswith(common + self.groups)]


FINAL = "PAX at final output; both paths open unless prompted."
PD = FINAL + " Photodiode connected to configured RP input."
D = "PAX at first-NPBS D port (requires moving it from the current C position)."
TESTS = (
    TestCase("live", "PAX monitor", "Log raw PAX angles, Stokes, DOP and u/v until Ctrl+C. No actuator scan; RP outputs are initialized to zero.", "PAX at the location recorded in bench_pax_location.", "_run_live_monitor", ("live_",)),
    TestCase("sweep", "Single-axis voltage sweep", "Step one output through an explicit RP voltage range; hold the other at zero. Log PAX response. This collects data; it does not fit V_pi.", "PAX must observe the selected actuator's optical response; record its location.", "_run_calibration_sweep", ("sweep_",), axis=True, report="sweep"),
    TestCase("bidirectional-sweep", "Forward / reverse sweep", "Sweep one configured V_lambda in both directions with the other actuator at a fixed bias; compare hysteresis.", FINAL, "_run_bidirectional_sweep", ("bidirectional_",), axis=True, report="bidirectional"),
    TestCase("cross-sweep", "Cross-coupling sweep", "Sweep one V_lambda at each fixed-axis bias across the other V_lambda.", FINAL, "_run_cross_sweep", ("cross_sweep_",), axis=True, report="cross"),
    TestCase("diagnostic-suite", "Static stability holds", "Record baseline and held fractions of each V_lambda to distinguish drift from motion effects.", FINAL, "_run_diagnostic_suite", ("diagnostic_",), report="diagnostic"),
    TestCase("intensity-diagnostic", "Intensity versus polarization", "Sweep each axis independently; record PAX and photodiode mean/noise with ND correction.", PD, "_run_intensity_diagnostic", ("intensity_", "pd_"), report="intensity"),
    TestCase("phi2-path-test", "Phi2 path isolation", "Sweep phi2 for A only, B only and both paths; pause for manual beam-block changes.", PD, "_run_phi2_path_balance_test", ("intensity_", "pd_"), report="phi2-path-test"),
    TestCase("pax-path-hold", "PAX path stability", "Hold both outputs at zero and record PAX/PD for each manual path condition. Duration is per condition.", PD, "_run_pax_path_hold", ("pax_path_", "pd_"), timed=True, report="pax-path-hold"),
    TestCase("power-balance", "Phi2 power balance", "Drive a bounded phi2 sine over one V_lambda for each manual path condition. Duration is per condition.", PD, "_run_phi2_power_balance", ("power_balance_", "pd_"), timed=True, report="power"),
    TestCase("first-npbs-d-test", "D-port static / driven", "Compare static polarization with a phi1 sine at first NPBS D.", D + " Photodiode at final F on the configured RP input.", "_run_first_npbs_d_test", ("first_npbs_d_static_duration_s", "first_npbs_d_driven_duration_s", "first_npbs_d_phi1_frequency_hz", "first_npbs_d_sample_period_s", "pd_"), report="analyze_first_npbs_d"),
    TestCase("first-npbs-d-isolation", "D-port path isolation", "Measure static polarization for manual blocked-path conditions.", D + " Photodiode at final F on the configured RP input.", "_run_first_npbs_d_isolation", ("first_npbs_d_isolation_duration_s", "first_npbs_d_sample_period_s", "pd_"), report="analyze_first_npbs_d_isolation"),
    TestCase("d-polarizer-phi1-test", "D-port analyzer test", "Compare raw D-port polarization and final-F photodiode response with a linear polarizer in C.", D + " No polarizer before PAX. Linear polarizer in C before final NPBS; PD at final F; both paths open.", "_run_first_npbs_d_polarizer_test", ("first_npbs_d_polarizer_", "pd_"), report="analyze_first_npbs_d_polarizer"),
    TestCase("phi1-step-map", "D-port phi1 step map", "Acquire settled forward/reverse voltage steps for phase-slope and hysteresis analysis.", D + " Photodiode at final F on the configured RP input.", "_run_phi1_step_map", ("phi1_step_", "pd_"), report="analyze_phi1_step_map"),
    TestCase("field-model-calibration", "Field-model data collection", "Sweep phi2 at phi1 biases for A, B and both paths, repeating in reverse order to bracket drift.", PD, "_run_field_model_calibration", ("field_model_", "pd_")),
    TestCase("phi1-fringe-map", "Phi1 fringe map", "Forward/reverse phi1 biases with a phi2 sweep at each bias; log both PAX and PD.", PD, "_run_phi1_fringe_map", ("phi1_fringe_", "pd_")),
    TestCase("single-axis-pid", "Single-axis PI hold", "Calibrate one axis and hold its midpoint coordinate with PI; the other output stays zero. DOP is logged without gating feedback.", FINAL, "_run_single_axis_pid_test", ("single_axis_", "pid_"), axis=True, timed=True, report="single-axis-pid"),
    TestCase("phi1-lock-test", "D-port phi1 PI hold", "Use settled steps to estimate slope, then hold a local PAX target. DOP is logged without gating feedback.", D, "_run_phi1_d_lock_test", ("phi1_d_lock_", "pid_pax_average_count", "pid_pre_acquisition_settle_s"), timed=True, report="phi1-d-lock"),
    TestCase("phi1-pd-lock-test", "Photodiode FPGA lock", "Calibrate the local PD slope and hold a fringe branch with the RP FPGA PID.", PD, "_run_phi1_pd_lock_test", ("phi1_pd_", "phi1_d_lock_", "pd_", "pid_pax_average_count"), timed=True, report="phi1-pd-lock"),
    TestCase("phi1-pd-hybrid-test", "Hybrid PD / PAX lock", "Run the FPGA photodiode loop with slow PAX correction of the PD setpoint.", PD, "_run_phi1_pd_lock_test", ("phi1_pd_", "phi1_d_lock_", "pd_", "pid_pax_average_count"), timed=True, report="phi1-pd-lock", flags=("hybrid_outer",)),
    TestCase("phi1-pd-gain-scan", "Photodiode gain comparison", "Hold each configured gain on the same fringe branch and log PAX validation.", PD, "_run_phi1_pd_lock_test", ("phi1_pd_", "phi1_d_lock_", "pd_", "pid_pax_average_count"), flags=("gain_scan",)),
    TestCase("pid-test", "Two-axis PI hold", "Apply a rough correction followed by bounded PI feedback. DOP is logged without gating feedback; current-target capture uses the DOP gate.", FINAL, "_run_pid_test", ("pid_", "target_", "sphere_"), timed=True, report="pid", target=True),
    TestCase("pid-live", "Two-axis PI with sphere view", "Run the two-axis PI experiment with an interactive Poincare sphere. Requires PyVista; DOP behavior matches the two-axis PI hold.", FINAL, "_run_pid_live", ("pid_", "target_", "sphere_"), timed=True, report="pid", target=True),
    TestCase("rough", "One-shot target move", "Measure, make one bounded target correction, settle, and report the residual. Uses the DOP gate; summary goes to the run log.", FINAL, "rough_align_once", ("rough_", "target_", "sphere_"), target=True),
    TestCase("pd-visibility", "Contrast (PD / PAX / both)", "Measure PD voltage contrast, PAX power contrast, or both concurrently. Passive mode leaves the drive untouched; active mode drives the selected phase actuator and returns outputs to zero on exit.", "PD needs a DC-coupled input and measured/provided signed dark baseline. PAX needs the recorded port/wavelength. Both requires light delivered to both sensors (e.g. a beam split), with that arrangement recorded. Active mapping: phi1=OUT1, phi2=OUT2; the other output is zero. Passive PD needs the existing Pyrpl FPGA. Drive must span fringes.", "_run_pd_visibility", ("visibility_", "pd_input"), timed=True, report="visibility", scope_only=True),
    TestCase("stokes-phase-sweep", "Power and Stokes phase sweep", "Measure synchronized optical power and Stokes parameters while sweeping an external phase actuator. Repeated OUT1 sine cycles; IN1 is the measured voltage reference, with acquisition timing retained.", "PAX in path A / E4, observing the combined field. OUT1 drives phi1 and is physically T-ed into IN1 as the voltage reference (not a photodiode). OUT2 stays at zero.", "_run_stokes_phase_sweep", ("stokes_phase_sweep_", "pd_scope_"), timed=True, report="stokes-phase-sweep"),
    TestCase("pax-live", "PAX alignment panel", "Large live power, DoP, Stokes and ellipse-angle readouts for manual alignment. Stop to Save or Discard; all fresh readings are logged while open. Red Pitaya is left untouched.", "PAX at the location recorded in bench_pax_location; align optics by hand. Requires a graphical desktop (Qt).", "_run_pax_live", report="pax-live", pax_only=True),
    TestCase("pax-vibration", "PAX vibration diagnostics", "Compare fast PD variance with the PAX motor running and stopped. Log all fresh PAX telemetry and static sphere-angle variance during the on window. Duration is per condition; no actuator waveform.", "PD on selected IN1/IN2 with DC coupling and unchanged light/gain in both conditions. Keep PAX physically mounted and optics fixed. Motor-off means rotation stopped, not power unplugged. Outputs hold the configured static biases (default zero).", "_run_pax_vibration", ("pax_vibration_", "pd_input"), timed=True, report="pax-vibration"),
)
BY_KEY = {case.key: case for case in TESTS}


def default_options(case: TestCase) -> dict:
    options = {"label": case.key, "comment": ""}
    if case.axis:
        options["axis"] = "phi1"
    if case.timed:
        options["duration_s"] = 30.0 if case.key == "pax-vibration" else 12.0 if case.scope_only else 60.0
    options["report"] = "pdf"
    if case.target:
        options["target_mode"] = "current"
    return options
