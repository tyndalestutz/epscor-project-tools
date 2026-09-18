from __future__ import annotations

import csv
import math
import time
from pathlib import Path

import numpy as np

from ..control import SphereAngles, phase_error_to_rp_voltage, sphere_angle_error, sphere_angles_from_stokes, wrap_angle
from ..hardware.pax_interface import PAXReading

class FeedbackMixin:
    def _run_single_axis_pid_test(self, axis: str, duration_s: float, output_file: str) -> None:
        """Characterize and PI-hold one actuator with the other output fixed at zero."""
        if axis not in {"phi1", "phi2"}:
            raise ValueError("single-axis-pid axis must be 'phi1' or 'phi2'")
        if duration_s <= 0.0:
            raise ValueError("single-axis-pid duration must be positive")
        count = self.config.single_axis_pid_sweep_points
        if count < 3 or count % 2 == 0:
            raise ValueError("single_axis_pid_sweep_points must be an odd integer of at least 3")

        controlled_index = 0 if axis == "phi1" else 1
        coordinate_name = "u" if axis == "phi1" else "v"
        other_coordinate = "v" if axis == "phi1" else "u"
        command_values = np.linspace(0.0, self._one_lambda_rp_voltage(axis), count)
        midpoint = count // 2
        # u is undefined at the S1 poles. An isolated phi1 test must therefore
        # hold phi2 at a *static* equatorial bias (phi2 = pi/2 = Vlambda/4),
        # rather than at zero. Phi2's isolated v test can correctly hold phi1
        # at zero.
        fixed_other_command = self._one_lambda_rp_voltage("phi2") / 4.0 if axis == "phi1" else 0.0

        def setpoint_for(command: float) -> np.ndarray:
            return np.asarray((command, fixed_other_command), dtype=float) if axis == "phi1" else np.asarray((0.0, command), dtype=float)
        output = Path(output_file).open("w", newline="")
        writer = csv.writer(output)
        writer.writerow([
            "test_type", "axis", "coordinate", "elapsed_s", "stage", "sweep_index", "pax_timestamp",
            "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            "target_coordinate", "controlled_error_rad", "orthogonal_coordinate", "integral_rad_s",
            "delta_rp_v", "rp_out1_v", "rp_out2_v", "saturated",
        ])
        output.flush()
        started = time.monotonic()
        calibration: list[tuple[float, SphereAngles, PAXReading]] = []
        completed = False
        try:
            print(
                f"Single-axis {axis} test: {count}-point 0..V_lambda characterization; "
                f"the other actuator is static at {fixed_other_command:.4f} V RP. Target will be the measured {coordinate_name} at "
                f"{command_values[midpoint]:.4f} V RP (half V_lambda)."
            )
            for index, command in enumerate(command_values):
                setpoint = setpoint_for(command)
                self._apply_pid_output(setpoint)
                self._applied_rp_voltages = setpoint
                time.sleep(self.config.single_axis_pid_settle_s)
                state, reading = self._read_pid_sphere_reading()
                calibration.append((command, state, reading))
                writer.writerow([
                    "single-axis-pid", axis, coordinate_name, time.monotonic() - started, "calibration", index,
                    reading.timestamp, reading.theta, reading.eta, reading.s1, reading.s2, reading.s3,
                    reading.dop, reading.ptotal, state.u, state.v, "", "", getattr(state, other_coordinate), "", "",
                    setpoint[0], setpoint[1], 0,
                ])
                output.flush()

            target_coordinate = float(getattr(calibration[midpoint][1], coordinate_name))
            target_other = float(getattr(calibration[midpoint][1], other_coordinate))
            target_command = command_values[midpoint]
            print(
                f"Single-axis target: {coordinate_name}={target_coordinate:+.4f} rad from midpoint "
                f"command {target_command:.4f} V RP; settling {self.config.single_axis_pid_target_settle_s:.2f} s"
            )
            time.sleep(self.config.single_axis_pid_target_settle_s)

            integral = 0.0
            previous_error = 0.0
            previous_time = time.monotonic()
            deadline = previous_time + duration_s
            while time.monotonic() < deadline:
                cycle_started = time.monotonic()
                state, reading = self._read_pid_sphere_reading()
                current_coordinate = float(getattr(state, coordinate_name))
                error = wrap_angle(target_coordinate - current_coordinate) if axis == "phi1" else target_coordinate - current_coordinate
                dt = max(cycle_started - previous_time, 1e-6)
                trial_integral = float(np.clip(
                    integral + error * dt,
                    -self.config.pid_integral_limit_rad_s,
                    self.config.pid_integral_limit_rad_s,
                ))
                fine = self.config.pid_fine_gain[controlled_index] if abs(error) < self.config.pid_fine_error_threshold_rad[controlled_index] else 1.0
                correction_phase = fine * self.config.pid_kp[controlled_index] * error + fine * self.config.pid_ki_per_s[controlled_index] * trial_integral
                correction_phase = float(np.clip(
                    correction_phase,
                    -self.config.pid_max_phase_step_rad[controlled_index],
                    self.config.pid_max_phase_step_rad[controlled_index],
                ))
                delta_rp = self._phase_to_rp_delta(correction_phase, 0.0)[0] if axis == "phi1" else self._phase_to_rp_delta(0.0, correction_phase)[1]
                requested = self._applied_rp_voltages[controlled_index] + delta_rp
                applied = float(np.clip(requested, self.config.rp_output_min_voltage, self.config.rp_output_max_voltage))
                saturated = not np.isclose(requested, applied)
                # Do not integrate farther into a rail.
                if saturated and np.sign(error) == np.sign(delta_rp):
                    trial_integral = integral
                setpoint = setpoint_for(applied)
                self._apply_pid_output(setpoint)
                self._applied_rp_voltages = setpoint
                integral = trial_integral
                writer.writerow([
                    "single-axis-pid", axis, coordinate_name, time.monotonic() - started, "pid", "",
                    reading.timestamp, reading.theta, reading.eta, reading.s1, reading.s2, reading.s3,
                    reading.dop, reading.ptotal, state.u, state.v, target_coordinate, error,
                    getattr(state, other_coordinate), integral, delta_rp, setpoint[0], setpoint[1], int(saturated),
                ])
                output.flush()
                previous_error, previous_time = error, cycle_started
                time.sleep(max(self.config.pid_pre_acquisition_settle_s, self.config.pid_sample_period_s - (time.monotonic() - cycle_started)))
            completed = True
        finally:
            output.close()
            self.rp.set_output_zero()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Single-axis {axis} PID log saved to {output_file}; outputs returned to zero.")
            else:
                print("Single-axis PID test aborted; outputs were returned to zero.")

    def _run_phi1_d_lock_test(self, duration_s: float, output_file: str) -> None:
        """Characterize then PI-hold the measured D-port azimuth ``u``.

        This is intentionally independent of the full two-axis u/v locking
        model: with PAX at D and OUT2=0, phi1 moves the state around the
        S1-polar Poincare azimuth ``u``.  The voltage sign/scale comes from
        the calibration immediately preceding this hold.
        """
        if duration_s <= 0.0:
            raise ValueError("phi1-lock-test duration must be positive")
        count = self.config.phi1_d_lock_sweep_points
        if count < 3 or count % 2 == 0:
            raise ValueError("phi1_d_lock_sweep_points must be an odd integer of at least 3")
        candidate_lambda = self._one_lambda_rp_voltage("phi1")
        commands = np.linspace(0.0, candidate_lambda, count)
        midpoint = count // 2
        output = Path(output_file).open("w", newline="")
        writer = csv.writer(output)
        writer.writerow([
            "test_type", "stage", "elapsed_s", "sweep_index", "pax_timestamp", "theta", "eta", "s1", "s2", "s3",
            "equatorial_radius", "u_rad", "v_rad", "target_u_rad", "u_error_rad", "integral_rad_s",
            "u_slope_rad_per_rp_v", "delta_rp_v", "rp_out1_v", "rp_out2_v", "saturated",
            "kp", "ki_per_s", "pax_average_count", "stokes_filter_alpha",
            "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
        ])
        output.flush()
        started = time.monotonic()
        completed = False

        filtered_stokes: np.ndarray | None = None

        def averaged_measure(count: int) -> tuple[PAXReading, SphereAngles, float, object]:
            state, reading = self._read_pid_sphere_reading(count=count)
            radius = float(math.hypot(reading.s2, reading.s3))
            return reading, state, radius, read_pd()

        def filtered_measure() -> tuple[PAXReading, SphereAngles, float, object]:
            """One fresh PAX record plus a causal Cartesian Stokes IIR filter."""
            nonlocal filtered_stokes
            _, raw = self._read_pid_sphere_reading(count=self.config.phi1_d_lock_pax_average_count)
            raw_stokes = np.asarray((raw.s1, raw.s2, raw.s3), dtype=float)
            if filtered_stokes is None:
                filtered_stokes = raw_stokes
            else:
                alpha = self.config.phi1_d_lock_stokes_filter_alpha
                filtered_stokes = alpha * raw_stokes + (1.0 - alpha) * filtered_stokes
            norm = float(np.linalg.norm(filtered_stokes))
            if norm == 0.0:
                raise RuntimeError("Phi1 lock Stokes filter produced a zero vector")
            s1, s2, s3 = (filtered_stokes / norm).tolist()
            state = sphere_angles_from_stokes(s1, s2, s3)
            reading = PAXReading(
                timestamp=raw.timestamp,
                theta=0.5 * math.atan2(s2, s1),
                eta=0.5 * math.asin(float(np.clip(s3, -1.0, 1.0))),
                s1=s1, s2=s2, s3=s3, dop=raw.dop, ptotal=raw.ptotal,
                revisions=raw.revisions, adc_min=raw.adc_min, adc_max=raw.adc_max, rev_time=raw.rev_time,
            )
            return reading, state, float(math.hypot(s2, s3)), read_pd()

        try:
            print(
                f"Phi1 D-port lock: held {count}-point 0..{candidate_lambda:.4f} V RP calibration; "
                "then lock to a freshly measured midpoint state. OUT2/phi2 remains 0 V."
            )
            with self.rp.photodiode_monitor() as read_pd:
                calibration: list[tuple[float, float]] = []
                for index, command in enumerate(commands):
                    self._apply_pid_output(np.asarray((command, 0.0)))
                    self._applied_rp_voltages[:] = (command, 0.0)
                    time.sleep(self.config.phi1_d_lock_sweep_settle_s)
                    reading, state, radius, pd = averaged_measure(self.config.phi1_d_lock_calibration_pax_average_count)
                    calibration.append((float(command), state.u))
                    writer.writerow([
                        "phi1-d-lock", "calibration", time.monotonic() - started, index, reading.timestamp,
                        reading.theta, reading.eta, reading.s1, reading.s2, reading.s3, radius, state.u, state.v,
                        "", "", "", "", "", command, 0.0, 0,
                        self.config.phi1_d_lock_kp, self.config.phi1_d_lock_ki_per_s, self.config.phi1_d_lock_pax_average_count,
                        self.config.phi1_d_lock_stokes_filter_alpha,
                        pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    ])
                    output.flush()

                calibration_v = np.asarray([item[0] for item in calibration])
                calibration_u = np.unwrap(np.asarray([item[1] for item in calibration]))
                u_slope = float(np.polyfit(calibration_v, calibration_u, 1)[0])
                if abs(u_slope) < 1.0:
                    raise RuntimeError("Phi1 D-port calibration has insufficient u slope to safely close the loop")
                midpoint_command = float(commands[midpoint])
                self._apply_pid_output(np.asarray((midpoint_command, 0.0)))
                self._applied_rp_voltages[:] = (midpoint_command, 0.0)
                time.sleep(self.config.phi1_d_lock_target_settle_s)
                target_reading, target_state, _, _ = averaged_measure(self.config.phi1_d_lock_calibration_pax_average_count)
                target_u = target_state.u
                filtered_stokes = np.asarray((target_reading.s1, target_reading.s2, target_reading.s3), dtype=float)
                print(
                    f"Phi1 u target: u={target_u:+.4f} rad at OUT1={midpoint_command:.4f} V RP; "
                    f"measured slope={u_slope:+.3f} rad/V (V_lambda≈{2.0 * math.pi / abs(u_slope):.4f} V RP)."
                )

                integral = 0.0
                previous_time = time.monotonic()
                deadline = previous_time + duration_s
                while time.monotonic() < deadline:
                    cycle_started = time.monotonic()
                    reading, state, radius, pd = filtered_measure()
                    error = float(wrap_angle(target_u - state.u))
                    dt = max(cycle_started - previous_time, 1e-6)
                    trial_integral = float(np.clip(
                        integral + error * dt,
                        -self.config.phi1_d_lock_integral_limit_rad_s,
                        self.config.phi1_d_lock_integral_limit_rad_s,
                    ))
                    correction_phase = self.config.phi1_d_lock_kp * error + self.config.phi1_d_lock_ki_per_s * trial_integral
                    correction_phase = float(np.clip(
                        correction_phase,
                        -self.config.phi1_d_lock_max_phase_step_rad,
                        self.config.phi1_d_lock_max_phase_step_rad,
                    ))
                    delta_rp = correction_phase / u_slope
                    requested = float(self._applied_rp_voltages[0] + delta_rp)
                    applied = float(np.clip(requested, self.config.rp_output_min_voltage, self.config.rp_output_max_voltage))
                    saturated = not np.isclose(requested, applied)
                    if saturated and np.sign(error) == np.sign(delta_rp * u_slope):
                        trial_integral = integral
                    self._apply_pid_output(np.asarray((applied, 0.0)))
                    self._applied_rp_voltages[:] = (applied, 0.0)
                    integral = trial_integral
                    writer.writerow([
                        "phi1-d-lock", "pid", time.monotonic() - started, "", reading.timestamp,
                        reading.theta, reading.eta, reading.s1, reading.s2, reading.s3, radius, state.u, state.v,
                        target_u, error, integral, u_slope, delta_rp, applied, 0.0, int(saturated),
                        self.config.phi1_d_lock_kp, self.config.phi1_d_lock_ki_per_s, self.config.phi1_d_lock_pax_average_count,
                        self.config.phi1_d_lock_stokes_filter_alpha,
                        pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    ])
                    output.flush()
                    previous_time = cycle_started
                    time.sleep(max(self.config.pid_pre_acquisition_settle_s, self.config.phi1_d_lock_sample_period_s - (time.monotonic() - cycle_started)))
            completed = True
        finally:
            output.close()
            self.rp.set_output_zero()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Phi1 u-lock log saved to {output_file}; outputs returned to zero.")
            else:
                print("Phi1 u-lock test aborted; outputs were returned to zero.")

    def _run_phi1_pd_lock_test(
        self, duration_s: float, output_file: str, *, gain_scan: bool = False, hybrid_outer: bool = False,
    ) -> None:
        """PAX-assisted acquisition followed by a native Red Pitaya PD lock."""
        if duration_s <= 0.0 and not gain_scan:
            raise ValueError("phi1-pd-lock-test duration must be positive")
        rough_count = self.config.phi1_pd_lock_sweep_points
        local_count = self.config.phi1_pd_lock_local_calibration_points
        if rough_count < 3 or rough_count % 2 == 0 or local_count < 3:
            raise ValueError("PD lock sweep counts require an odd rough count >=3 and local count >=3")
        candidate_lambda = self._one_lambda_rp_voltage("phi1")
        rough_commands = np.linspace(0.0, candidate_lambda, rough_count)
        output = Path(output_file).open("w", newline="")
        writer = csv.writer(output)
        writer.writerow([
            "test_type", "stage", "elapsed_s", "rough_index", "local_index", "pax_sample",
            "rp_out1_v", "rp_out2_v", "pid_output_v", "pid_integrator_v", "pid_setpoint_v", "pid_p", "pid_i_hz",
            "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count", "pd_target_v", "pd_error_v",
            "local_pd_slope_v_per_rp_v", "outer_u_error_rad", "outer_pd_setpoint_delta_v",
            "theta", "eta", "s1", "s2", "s3", "u", "v", "pax_ptotal",
        ])
        output.flush()
        started = time.monotonic()
        completed = False

        def write_row(
            *, stage: str, pd, out1: float, pd_target: float | None = None, pd_slope: float | None = None,
            pid=None, reading: PAXReading | None = None, state: SphereAngles | None = None,
            rough_index: int | str = "", local_index: int | str = "", outer_u_error: float | None = None,
            outer_pd_delta: float | None = None,
        ) -> None:
            pid_output = float(pid.current_output_signal) if pid is not None else float("nan")
            pid_integrator = float(pid.ival) if pid is not None else float("nan")
            pid_setpoint = float(pid.setpoint) if pid is not None else float("nan")
            pid_p = float(pid.p) if pid is not None else float("nan")
            pid_i = float(pid.i) if pid is not None else float("nan")
            target = float("nan") if pd_target is None else pd_target
            writer.writerow([
                "phi1-pd-lock", stage, time.monotonic() - started, rough_index, local_index, int(reading is not None),
                out1, 0.0, pid_output, pid_integrator, pid_setpoint, pid_p, pid_i,
                pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                target, pd.mean_voltage - target if np.isfinite(target) else float("nan"),
                float("nan") if pd_slope is None else pd_slope,
                float("nan") if outer_u_error is None else outer_u_error,
                float("nan") if outer_pd_delta is None else outer_pd_delta,
                *([reading.theta, reading.eta, reading.s1, reading.s2, reading.s3, state.u, state.v, reading.ptotal]
                  if reading is not None and state is not None else [float("nan")] * 8),
            ])
            output.flush()

        try:
            if hybrid_outer:
                print(
                    "Phi1 hybrid PD/PAX lock: FPGA pid1 continuously locks IN1→OUT1; a slow averaged PAX "
                    "outer loop trims only the FPGA PD setpoint. OUT2 stays 0 V."
                )
            elif gain_scan:
                print(
                    "Phi1 PD gain scan: PAX+PD selects one steep local fringe, then pid1 runs IN1→OUT1 "
                    "through P values with the 5-Hz integral term retained. OUT2 stays 0 V."
                )
            else:
                print(
                    "Phi1 PD-lock test: PAX+PD rough sweep identifies the accessible u branch; "
                    "a local PD sweep selects a steep monotonic operating point, then FPGA pid1 locks IN1→OUT1. "
                    "OUT2 stays 0 V."
                )
            with self.rp.photodiode_monitor() as read_pd:
                # 1. Slow PAX-assisted rough map of the accessible u branch.
                # Simultaneous PD samples identify a side of the analyzer
                # fringe, rather than locking at the nominal V_lambda/2 point
                # which can be a fringe maximum with no usable discriminator.
                rough_pd: list[float] = []
                rough_u: list[float] = []
                for index, command in enumerate(rough_commands):
                    self._apply_pid_output(np.asarray((command, 0.0)))
                    self._applied_rp_voltages[:] = (command, 0.0)
                    time.sleep(self.config.phi1_pd_lock_sweep_settle_s)
                    pd = read_pd()
                    rough_pd.append(pd.mean_voltage)
                    state, reading = self._read_pid_sphere_reading(count=self.config.phi1_d_lock_calibration_pax_average_count)
                    rough_u.append(state.u)
                    write_row(stage="pax-rough", pd=pd, out1=float(command), reading=reading, state=state, rough_index=index)

                # 2. Choose the strongest *interior* PD discriminator from
                # the coarse map, then map its local neighborhood.  Endpoints
                # are excluded because their one-sided derivatives are less
                # reliable.  This preserves PAX's role as branch acquisition
                # while deliberately avoiding a PD maximum/minimum.
                rough_derivative = np.gradient(np.asarray(rough_pd), rough_commands)
                rough_lock_index = 1 + int(np.argmax(np.abs(rough_derivative[1:-1])))
                center = float(rough_commands[rough_lock_index])
                print(
                    f"PD discriminator selected from rough index {rough_lock_index}: "
                    f"OUT1={center:.4f} V, coarse dPD/dOUT1={rough_derivative[rough_lock_index]:+.3f} V/V."
                )
                span = self.config.phi1_pd_lock_local_calibration_span_rp_v
                local_commands = np.linspace(
                    max(self.config.rp_output_min_voltage, center - span),
                    min(self.config.rp_output_max_voltage, center + span),
                    local_count,
                )
                local_pd: list[float] = []
                for index, command in enumerate(local_commands):
                    self._apply_pid_output(np.asarray((command, 0.0)))
                    self._applied_rp_voltages[:] = (command, 0.0)
                    time.sleep(self.config.phi1_pd_lock_local_settle_s)
                    pd = read_pd()
                    local_pd.append(pd.mean_voltage)
                    write_row(stage="pd-local-calibration", pd=pd, out1=float(command), local_index=index)
                # A whole-span line fit can be badly misleading near a fringe
                # maximum/minimum.  Select an *interior*, locally steep point
                # and fit only its three nearest samples.  That is the branch
                # on which the PD PID is actually allowed to operate.
                local_derivative = np.gradient(np.asarray(local_pd), local_commands)
                lock_index = 1 + int(np.argmax(np.abs(local_derivative[1:-1])))
                fit_slice = slice(lock_index - 1, lock_index + 2)
                pd_slope = float(np.polyfit(local_commands[fit_slice], np.asarray(local_pd)[fit_slice], 1)[0])
                if abs(pd_slope) < self.config.phi1_pd_lock_min_pd_slope_v_per_rp_v:
                    raise RuntimeError(
                        f"Local PD slope {pd_slope:.4g} V/V is too small for a safe fringe lock; move the LP/analyzer to a steeper fringe."
                    )
                rough_u_unwrapped = np.unwrap(np.asarray(rough_u))
                u_fit_slice = slice(rough_lock_index - 1, rough_lock_index + 2)
                u_slope = float(np.polyfit(rough_commands[u_fit_slice], rough_u_unwrapped[u_fit_slice], 1)[0])
                if hybrid_outer and abs(u_slope) < 1.0:
                    raise RuntimeError("Local phi1 u slope is too small for the PAX outer-loop calibration")

                # 3. Capture the actual local operating point after the sweep.
                center = float(local_commands[lock_index])
                self._apply_pid_output(np.asarray((center, 0.0)))
                self._applied_rp_voltages[:] = (center, 0.0)
                time.sleep(self.config.phi1_pd_lock_target_settle_s)
                target_state, target_reading = self._read_pid_sphere_reading(count=self.config.phi1_d_lock_calibration_pax_average_count)
                # Acquire the fast PD setpoint *after* the relatively slow PAX
                # average.  Previously we sampled PD first, then spent roughly
                # half a second on PAX; Lock-6 entered the FPGA handoff with a
                # 12 mV stale-PD discrepancy and immediately moved away from
                # the PAX target before feedback could settle.
                target_pd = read_pd()
                pd_target = target_pd.mean_voltage
                # The FPGA PID computes output = P * (input - setpoint). For
                # negative feedback, P must oppose the measured d(PD)/d(OUT1).
                p_magnitude = min(
                    self.config.phi1_pd_lock_max_p,
                    self.config.phi1_pd_lock_loop_fraction / abs(pd_slope),
                )
                pid_p = -math.copysign(p_magnitude, pd_slope)
                pid_i = math.copysign(self.config.phi1_pd_lock_integral_unity_gain_hz, pid_p)
                write_row(
                    stage="handoff-target", pd=target_pd, out1=center, pd_target=pd_target, pd_slope=pd_slope,
                    reading=target_reading, state=target_state,
                )
                print(
                    f"PD handoff: u={target_state.u:+.4f} rad, PD target={pd_target:.5f} V, "
                    f"local dPD/dOUT1={pd_slope:+.4f} V/V, FPGA P={pid_p:+.3f}; "
                    f"local du/dOUT1={u_slope:+.3f} rad/V; P-only preflight then I={pid_i:+.1f} Hz."
                )

                if gain_scan:
                    # Keep one freshly acquired optical branch and compare P
                    # values directly.  This is more relevant than a generic
                    # PD-only stability limit because PAX u is the score.
                    fractions = self.config.phi1_pd_gain_scan_fractions
                    if not fractions or any(value <= 0.0 for value in fractions):
                        raise ValueError("phi1_pd_gain_scan_fractions must contain positive values")
                    first_p = -math.copysign(
                        min(self.config.phi1_pd_lock_max_p, fractions[0] / abs(pd_slope)), pd_slope,
                    )
                    with self.rp.photodiode_pid_lock(
                        setpoint=pd_target, initial_output=center, proportional_gain=first_p, integral_gain_hz=0.0,
                    ) as pid:
                        next_pax = time.monotonic()
                        # The existing P-only gate prevents an erroneous local
                        # slope from being handed to the integral term.
                        preflight_deadline = time.monotonic() + self.config.phi1_pd_lock_p_only_preflight_s
                        while time.monotonic() < preflight_deadline:
                            cycle_started = time.monotonic()
                            pd = read_pd()
                            write_row(stage="pd-gain-scan-p-only", pd=pd, out1=float(pid.current_output_signal),
                                      pd_target=pd_target, pd_slope=pd_slope, pid=pid)
                            time.sleep(max(0.0, self.config.phi1_pd_lock_log_period_s - (time.monotonic() - cycle_started)))
                        if not 0.02 < float(pid.current_output_signal) < 0.98:
                            raise RuntimeError("PD gain-scan P-only preflight reached an OUT1 rail; PID was disabled.")
                        pid.i = pid_i
                        previous_p: float | None = None
                        for gain_index, fraction in enumerate(fractions):
                            test_p = -math.copysign(
                                min(self.config.phi1_pd_lock_max_p, fraction / abs(pd_slope)), pd_slope,
                            )
                            if previous_p is not None and np.isclose(test_p, previous_p):
                                continue
                            previous_p = test_p
                            pid.p = test_p
                            print(
                                f"PD gain-scan step {gain_index + 1}/{len(fractions)}: "
                                f"local loop gain={fraction:.3f}, FPGA P={test_p:+.3f}, I={pid_i:+.1f} Hz for "
                                f"{self.config.phi1_pd_gain_scan_hold_s:.1f} s."
                            )
                            deadline = time.monotonic() + self.config.phi1_pd_gain_scan_hold_s
                            while time.monotonic() < deadline:
                                cycle_started = time.monotonic()
                                pd = read_pd()
                                reading = state = None
                                if cycle_started >= next_pax:
                                    state, reading = self._read_pid_sphere_reading(count=1)
                                    next_pax = cycle_started + self.config.phi1_pd_lock_pax_period_s
                                output_value = float(pid.current_output_signal)
                                write_row(
                                    stage="pd-gain-scan", pd=pd, out1=output_value, pd_target=pd_target,
                                    pd_slope=pd_slope, pid=pid, reading=reading, state=state,
                                    rough_index=gain_index,
                                )
                                if not 0.02 < output_value < 0.98:
                                    raise RuntimeError(
                                        "PD gain scan reached an OUT1 rail; PID was disabled before testing a higher P."
                                    )
                                time.sleep(max(0.0, self.config.phi1_pd_lock_log_period_s - (time.monotonic() - cycle_started)))
                    completed = True
                    return

                # 4. Hardware PID runs in the FPGA continuously. Python only
                # logs PD quickly and PAX slowly; neither cadence controls the
                # actual feedback bandwidth.
                next_pax = time.monotonic()
                with self.rp.photodiode_pid_lock(
                    setpoint=pd_target, initial_output=center, proportional_gain=pid_p, integral_gain_hz=0.0,
                ) as pid:
                    # Prove that the continuously running FPGA proportional
                    # path stays on this fringe before allowing any integral
                    # accumulation.  Python only observes this; it never
                    # produces the feedback waveform.
                    preflight_deadline = time.monotonic() + self.config.phi1_pd_lock_p_only_preflight_s
                    while time.monotonic() < preflight_deadline:
                        cycle_started = time.monotonic()
                        pd = read_pd()
                        write_row(
                            stage="pd-fpga-p-only", pd=pd, out1=float(pid.current_output_signal),
                            pd_target=pd_target, pd_slope=pd_slope, pid=pid,
                        )
                        time.sleep(max(0.0, self.config.phi1_pd_lock_log_period_s - (time.monotonic() - cycle_started)))
                    preflight_output = float(pid.current_output_signal)
                    if not 0.02 < preflight_output < 0.98:
                        raise RuntimeError(
                            "FPGA P-only preflight reached an OUT1 rail; PID was disabled before integral action. "
                            "Repeat after checking the PD fringe/local operating point."
                        )
                    pid.i = pid_i
                    deadline = time.monotonic() + duration_s
                    next_outer = time.monotonic()
                    while time.monotonic() < deadline:
                        cycle_started = time.monotonic()
                        pd = read_pd()
                        reading = state = None
                        outer_error = outer_delta = None
                        if hybrid_outer and cycle_started >= next_outer:
                            state, reading = self._read_pid_sphere_reading(
                                count=self.config.phi1_d_lock_calibration_pax_average_count,
                            )
                            outer_error = float(wrap_angle(target_state.u - state.u))
                            # du/dPD = (du/dV)/(dPD/dV).  Trim the PD setpoint
                            # toward the PAX target, bounded so PAX never makes
                            # a fast or discontinuous actuator command.
                            outer_delta = float(np.clip(
                                self.config.phi1_pd_pax_outer_gain * outer_error * pd_slope / u_slope,
                                -self.config.phi1_pd_pax_outer_max_setpoint_step_v,
                                self.config.phi1_pd_pax_outer_max_setpoint_step_v,
                            ))
                            pid.setpoint = float(pid.setpoint + outer_delta)
                            next_outer = cycle_started + self.config.phi1_pd_pax_outer_period_s
                            next_pax = next_outer
                        elif cycle_started >= next_pax:
                            state, reading = self._read_pid_sphere_reading(count=1)
                            next_pax = cycle_started + self.config.phi1_pd_lock_pax_period_s
                        write_row(
                            stage="pd-fpga-pid", pd=pd, out1=float(pid.current_output_signal),
                            pd_target=float(pid.setpoint), pd_slope=pd_slope, pid=pid, reading=reading, state=state,
                            outer_u_error=outer_error, outer_pd_delta=outer_delta,
                        )
                        time.sleep(max(0.0, self.config.phi1_pd_lock_log_period_s - (time.monotonic() - cycle_started)))
            completed = True
        finally:
            output.close()
            self.rp.set_output_zero()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Phi1 PD-lock log saved to {output_file}; outputs returned to zero.")
            else:
                print("Phi1 PD-lock test aborted; outputs returned to zero.")

    def _write_pid_row(
        self,
        writer: csv.writer,
        *,
        elapsed_s: float,
        stage: str,
        reading: PAXReading,
        state: SphereAngles,
        error: tuple[float, float],
        integral: np.ndarray,
        delta_rp: np.ndarray,
        setpoint: np.ndarray,
        saturated: np.ndarray,
        recentered: np.ndarray,
    ) -> None:
        target = self._require_target()
        writer.writerow([
            elapsed_s, stage, reading.timestamp, reading.theta, reading.eta,
            reading.s1, reading.s2, reading.s3, reading.dop, state.u, state.v,
            target.u, target.v, error[0], error[1], integral[0], integral[1],
            delta_rp[0], delta_rp[1], setpoint[0], setpoint[1],
            int(saturated[0]), int(saturated[1]), int(recentered[0]), int(recentered[1]),
        ])

    def _apply_pid_output(self, target: np.ndarray) -> None:
        """Apply the PID command directly while characterizing PAX/PID behavior."""
        self.rp.set_output_voltage(float(target[0]), float(target[1]))

    def _run_pid_test(self, duration_s: float, output_file: str, view: object | None = None) -> None:
        """Run one bounded rough-move plus PI anti-drift experiment.

        The raw PAX DOP is logged but deliberately does not gate this test,
        because the diagnostic suite established that the daemon's field is
        currently not a valid absolute DOP measurement.
        """
        if duration_s <= 0 or self.config.pid_sample_period_s <= 0:
            raise ValueError("PID duration and sample period must be positive")
        target = self._require_target()
        seed = np.asarray((self.config.pid_seed_phi1_rp_voltage, self.config.pid_seed_phi2_rp_voltage), dtype=float)
        if np.any(seed < self.config.rp_output_min_voltage) or np.any(seed > self.config.rp_output_max_voltage):
            raise ValueError("PID seed voltages must be within the configured RP output limits")

        output = Path(output_file).open("w", newline="")
        writer = csv.writer(output)
        writer.writerow([
            "elapsed_s", "stage", "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "u", "v",
            "target_u", "target_v", "error_phi1", "error_phi2", "integral_phi1", "integral_phi2",
            "delta_rp_phi1", "delta_rp_phi2", "rp_out1_v", "rp_out2_v", "saturated_phi1", "saturated_phi2",
            "recentered_phi1", "recentered_phi2",
        ])
        output.flush()
        started = time.monotonic()
        try:
            self._applied_rp_voltages = seed.copy()
            self._apply_pid_output(seed)
            print(f"PID seed: out1={seed[0]:.4f} V, out2={seed[1]:.4f} V; settling {self.config.pid_seed_settle_s:.1f} s")
            time.sleep(self.config.pid_seed_settle_s)

            current, reading = self._read_pid_sphere_reading()
            if view is not None:
                view.update(reading, current)
                if not view.running:
                    return
            target = self._require_target()
            rough_error = sphere_angle_error(current, target, pole_tolerance=self.config.sphere_pole_tolerance)
            rough_phase = np.clip(
                self.config.pid_rough_fraction * np.asarray(rough_error),
                -np.asarray(self.config.pid_rough_max_phase_step_rad),
                np.asarray(self.config.pid_rough_max_phase_step_rad),
            )
            rough_delta = np.asarray(self._phase_to_rp_delta(*rough_phase), dtype=float)
            unclipped = self._applied_rp_voltages + rough_delta
            self._applied_rp_voltages = np.clip(unclipped, self.config.rp_output_min_voltage, self.config.rp_output_max_voltage)
            self._apply_pid_output(self._applied_rp_voltages)
            saturated = ~np.isclose(unclipped, self._applied_rp_voltages)
            self._write_pid_row(
                writer, elapsed_s=time.monotonic() - started, stage="rough", reading=reading, state=current,
                error=rough_error, integral=np.zeros(2), delta_rp=rough_delta,
                setpoint=self._applied_rp_voltages, saturated=saturated, recentered=np.zeros(2, dtype=bool),
            )
            output.flush()
            print(
                f"PID rough error=({rough_error[0]:+.3f}, {rough_error[1]:+.3f}); "
                f"RP command=(out1={self._applied_rp_voltages[0]:.4f} V, out2={self._applied_rp_voltages[1]:.4f} V)"
            )
            time.sleep(self.config.pid_rough_settle_s)

            integral = np.zeros(2, dtype=float)
            previous_error = np.asarray(rough_error, dtype=float)
            previous_time = time.monotonic()
            deadline = previous_time + duration_s
            recenter_counts = np.zeros(2, dtype=int)
            last_recenter_time = np.full(2, -np.inf)
            phase_period_rp = np.asarray((self._one_lambda_rp_voltage("phi1"), self._one_lambda_rp_voltage("phi2")))
            while time.monotonic() < deadline:
                if view is not None and not view.running:
                    break
                cycle_started = time.monotonic()
                current, reading = self._read_pid_sphere_reading()
                if view is not None:
                    view.update(reading, current)
                    if not view.running:
                        break
                target = self._require_target()
                error = np.asarray(sphere_angle_error(current, target, pole_tolerance=self.config.sphere_pole_tolerance), dtype=float)
                dt = max(cycle_started - previous_time, 1e-6)
                trial_integral = np.clip(
                    integral + error * dt,
                    -self.config.pid_integral_limit_rad_s,
                    self.config.pid_integral_limit_rad_s,
                )
                fine_scale = np.where(
                    np.abs(error) < np.asarray(self.config.pid_fine_error_threshold_rad),
                    np.asarray(self.config.pid_fine_gain),
                    1.0,
                )
                phase_correction = (
                    fine_scale * np.asarray(self.config.pid_kp) * error
                    + fine_scale * np.asarray(self.config.pid_ki_per_s) * trial_integral
                    + np.asarray(self.config.pid_kd_s) * (error - previous_error) / dt
                )
                phase_correction = np.clip(
                    phase_correction,
                    -np.asarray(self.config.pid_max_phase_step_rad),
                    np.asarray(self.config.pid_max_phase_step_rad),
                )
                delta_rp = np.asarray(self._phase_to_rp_delta(*phase_correction), dtype=float)
                unclipped = self._applied_rp_voltages + delta_rp
                next_setpoint = np.clip(unclipped, self.config.rp_output_min_voltage, self.config.rp_output_max_voltage)
                saturated = ~np.isclose(unclipped, next_setpoint)
                recentered = np.zeros(2, dtype=bool)
                for axis in range(2):
                    if not saturated[axis] or abs(error[axis]) < self.config.pid_recenter_error_threshold_rad:
                        continue
                    if recenter_counts[axis] >= self.config.pid_recenter_max_events_per_axis:
                        continue
                    if cycle_started - last_recenter_time[axis] < self.config.pid_recenter_cooldown_s:
                        continue
                    # A lower-rail correction needs more negative phase command:
                    # add one phase period to restore room below. At the upper
                    # rail, subtract one period for the symmetric case.
                    direction = 1.0 if unclipped[axis] < self.config.rp_output_min_voltage else -1.0
                    recentered_command = self._applied_rp_voltages[axis] + direction * phase_period_rp[axis]
                    if self.config.rp_output_min_voltage <= recentered_command <= self.config.rp_output_max_voltage:
                        next_setpoint[axis] = recentered_command
                        delta_rp[axis] = recentered_command - self._applied_rp_voltages[axis]
                        saturated[axis] = False
                        recentered[axis] = True
                        recenter_counts[axis] += 1
                        last_recenter_time[axis] = cycle_started
                # Do not wind up farther into a rail. If the measured error is
                # pulling the command back toward the available range, let the
                # integral unwind even while the output is still clipped.
                pushing_lower_rail = (
                    unclipped < self.config.rp_output_min_voltage
                ) & (error < 0.0)
                pushing_upper_rail = (
                    unclipped > self.config.rp_output_max_voltage
                ) & (error > 0.0)
                freeze_integral = (pushing_lower_rail | pushing_upper_rail) & ~recentered
                integral = np.where(freeze_integral, integral, trial_integral)
                integral[recentered] = 0.0
                self._applied_rp_voltages = next_setpoint
                self._apply_pid_output(next_setpoint)
                self._write_pid_row(
                    writer, elapsed_s=cycle_started - started,
                    stage="pid-recenter" if np.any(recentered) else "pid", reading=reading, state=current,
                    error=(float(error[0]), float(error[1])), integral=integral, delta_rp=delta_rp,
                    setpoint=next_setpoint, saturated=saturated, recentered=recentered,
                )
                output.flush()
                if np.any(recentered):
                    axes = ", ".join(f"phi{index + 1}" for index, value in enumerate(recentered) if value)
                    print(f"PID recentered {axes} by one V_lambda to restore control headroom")
                    time.sleep(self.config.pid_recenter_settle_s)
                previous_error = error
                previous_time = cycle_started
                remaining = max(
                    self.config.pid_pre_acquisition_settle_s,
                    self.config.pid_sample_period_s - (time.monotonic() - cycle_started),
                )
                if remaining > 0:
                    time.sleep(remaining)
        finally:
            output.close()
            self.rp.set_output_zero()
            self._applied_rp_voltages[:] = 0.0
            print(f"PID test log saved to {output_file}; outputs returned to zero.")

    def _run_pid_live(self, duration_s: float, output_file: str) -> None:
        """Run the logged PID test with the PAX-driven interactive sphere view."""
        self._require_target()
        from ..reports.poincare_live_plot import PIDPoincareView
        view = PIDPoincareView(
            self._require_target,
            self.set_target,
            refresh_period_s=self.config.pid_visual_refresh_s,
        )
        try:
            self._run_pid_test(duration_s, output_file, view=view)
        finally:
            view.close()

    def _phase_to_rp_delta(self, delta_phi1: float, delta_phi2: float) -> tuple[float, float]:
        required = (
            self.config.phi1_v_lambda, self.config.phi2_v_lambda,
            self.config.phi1_actuator_volts_per_rp_volt, self.config.phi2_actuator_volts_per_rp_volt,
        )
        if any(value is None for value in required):
            raise RuntimeError("Configure V_lambda and RP-to-actuator gains before a PID test.")
        if not self.config.phase_output_map_confirmed:
            raise RuntimeError("Confirm the phi1/phi2-to-RP-output mapping before a PID test.")
        return phase_error_to_rp_voltage(
            delta_phi1, delta_phi2,
            phi1_v_lambda=self.config.phi1_v_lambda,
            phi2_v_lambda=self.config.phi2_v_lambda,
            phi1_actuator_volts_per_rp_volt=self.config.phi1_actuator_volts_per_rp_volt,
            phi2_actuator_volts_per_rp_volt=self.config.phi2_actuator_volts_per_rp_volt,
        )


