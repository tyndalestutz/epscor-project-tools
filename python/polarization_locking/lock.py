#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import time
from pathlib import Path
from typing import Optional

import numpy as np

try:
    from .config import DEFAULT_CONFIG, PolarizationLockConfig
    from .control import PolarizationState, SphereAngles, pax_to_sphere_angles, phase_error_to_rp_voltage, sphere_angle_error, sphere_angles_from_stokes
    from .pax_interface import PAXController, PAXReading
    from .rp_interface import RPController
except ImportError:  # pragma: no cover - support direct execution
    from config import DEFAULT_CONFIG, PolarizationLockConfig
    from control import PolarizationState, SphereAngles, pax_to_sphere_angles, phase_error_to_rp_voltage, sphere_angle_error, sphere_angles_from_stokes
    from pax_interface import PAXController, PAXReading
    from rp_interface import RPController


class PolarizationLockApp:
    """One-shot rough alignment app.

    This intentionally is not a PID loop.  Its job is to test whether the
    measured PAX state and calibrated voltage chain predict the expected move.
    """

    def __init__(self, config: Optional[PolarizationLockConfig] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.rp = RPController(self.config)
        self.pax = PAXController(self.config)
        self.running = False
        self._applied_rp_voltages = np.zeros(2, dtype=float)
        self._target: SphereAngles | None = None
        if self.config.target_u is not None or self.config.target_v is not None:
            if self.config.target_u is None or self.config.target_v is None:
                raise ValueError("Set both target_u and target_v, or neither")
            self.set_target(self.config.target_u, self.config.target_v)

    def connect(self) -> None:
        self.rp.connect()
        self.pax.connect()
        self._applied_rp_voltages[:] = 0.0
        self.running = True

    def disconnect(self) -> None:
        self.running = False
        self.pax.disconnect()
        self.rp.disconnect()

    def set_target(self, u: float, v: float) -> None:
        """Set the desired point directly in the hybrid-MZ (u, v) frame."""
        self._target = SphereAngles(u=u, v=v)
        self.config.target_u = self._target.u
        self.config.target_v = self._target.v

    def capture_target(self, *, require_trusted_dop: bool = True) -> SphereAngles:
        """Capture the current PAX state as a reusable target in the u/v frame."""
        target = self._read_sphere_reading(require_trusted_dop=require_trusted_dop)[0]
        self.set_target(target.u, target.v)
        return target

    def _read_sphere_reading(self, *, require_trusted_dop: bool = True) -> tuple[SphereAngles, PAXReading]:
        if self.pax.client is None and not self.pax._test_mode:
            raise RuntimeError("PAX connection is not established")
        reading = self.pax.read_polarization()
        if require_trusted_dop:
            if not 0.0 <= reading.dop <= 1.0:
                raise RuntimeError(f"PAX reported invalid DOP {reading.dop:.3f}; refusing to move.")
            if reading.dop < self.config.minimum_dop:
                raise RuntimeError(
                    f"PAX DOP {reading.dop:.3f} is below the required {self.config.minimum_dop:.3f}; refusing to move."
                )
        return pax_to_sphere_angles(PolarizationState(theta=reading.theta, eta=reading.eta)), reading

    def _read_sphere_state(self) -> SphereAngles:
        return self._read_sphere_reading()[0]

    def _read_pid_sphere_reading(self) -> tuple[SphereAngles, PAXReading]:
        """Return the Stokes-vector average used for a single PID update.

        Averaging Cartesian Stokes vectors, then renormalizing, avoids the
        azimuth wrap problem that would arise from averaging u directly.
        """
        count = self.config.pid_pax_average_count
        if count < 1:
            raise ValueError("pid_pax_average_count must be at least one")
        readings = [self.pax.read_polarization() for _ in range(count)]
        stokes = np.mean([[item.s1, item.s2, item.s3] for item in readings], axis=0)
        norm = float(np.linalg.norm(stokes))
        if norm == 0.0:
            raise RuntimeError("PAX averaging produced a zero Stokes vector")
        s1, s2, s3 = (stokes / norm).tolist()
        state = sphere_angles_from_stokes(s1, s2, s3)
        # These fields are logged for reference; reconstruct the equivalent
        # ellipse angles from the averaged Stokes state rather than selecting
        # one of the individual PAX readings.
        reading = PAXReading(
            timestamp=readings[-1].timestamp,
            theta=0.5 * math.atan2(s2, s1),
            eta=0.5 * math.asin(float(np.clip(s3, -1.0, 1.0))),
            s1=s1,
            s2=s2,
            s3=s3,
            dop=float(np.mean([item.dop for item in readings])),
        )
        return state, reading

    def _require_target(self) -> SphereAngles:
        if self._target is None:
            raise RuntimeError("Set a u/v target or use 'capture' before rough alignment.")
        return self._target

    def _compute_next_rp_setpoint(self, current: SphereAngles) -> tuple[tuple[float, float], tuple[float, float]]:
        target = self._require_target()
        required = (
            self.config.phi1_v_lambda,
            self.config.phi2_v_lambda,
            self.config.phi1_actuator_volts_per_rp_volt,
            self.config.phi2_actuator_volts_per_rp_volt,
        )
        if any(value is None for value in required):
            raise RuntimeError("Configure V_lambda and RP-to-actuator gains before rough alignment.")
        if not self.config.phase_output_map_confirmed:
            raise RuntimeError("Confirm the phi1/phi2-to-RP-output mapping before rough alignment.")

        delta_phi = sphere_angle_error(current, target, pole_tolerance=self.config.sphere_pole_tolerance)
        if np.linalg.norm(delta_phi) <= self.config.rough_deadband_rad:
            return tuple(self._applied_rp_voltages), delta_phi

        delta_rp = np.asarray(
            phase_error_to_rp_voltage(
                *delta_phi,
                phi1_v_lambda=self.config.phi1_v_lambda,
                phi2_v_lambda=self.config.phi2_v_lambda,
                phi1_actuator_volts_per_rp_volt=self.config.phi1_actuator_volts_per_rp_volt,
                phi2_actuator_volts_per_rp_volt=self.config.phi2_actuator_volts_per_rp_volt,
            ),
            dtype=float,
        )
        if self.config.rough_max_delta_voltage is not None:
            delta_rp = np.clip(delta_rp, -self.config.rough_max_delta_voltage, self.config.rough_max_delta_voltage)

        self._applied_rp_voltages = np.clip(
            self._applied_rp_voltages + delta_rp,
            self.config.rp_output_min_voltage,
            self.config.rp_output_max_voltage,
        )
        return tuple(float(value) for value in self._applied_rp_voltages), delta_phi

    def rough_align_once(self) -> None:
        """Measure, command one rough move, settle, then report the residual."""
        current = self._read_sphere_state()
        setpoint, delta_phi = self._compute_next_rp_setpoint(current)
        target = self._require_target()
        print(f"current=(u={current.u:.4f}, v={current.v:.4f})")
        print(f"target=(u={target.u:.4f}, v={target.v:.4f})")
        print(f"phase error=(d_phi1={delta_phi[0]:.4f}, d_phi2={delta_phi[1]:.4f})")
        print(f"RP setpoints=(out1={setpoint[0]:.4f} V, out2={setpoint[1]:.4f} V)")
        self.rp.set_output_voltage(*setpoint)

        time.sleep(self.config.rough_settle_s)
        verified = self._read_sphere_state()
        remaining = sphere_angle_error(verified, target, pole_tolerance=self.config.sphere_pole_tolerance)
        print(f"verification=(u={verified.u:.4f}, v={verified.v:.4f})")
        print(f"remaining phase error=(d_phi1={remaining[0]:.4f}, d_phi2={remaining[1]:.4f})")

    def run(self) -> None:
        self.connect()
        try:
            self.rough_align_once()
        finally:
            self.disconnect()

    def _run_live_monitor(self, output_file: str | None = None) -> None:
        """Print PAX readings continuously and optionally persist them to CSV."""
        output = None
        writer = None
        if output_file is not None:
            output = Path(output_file).open("w", newline="")
            writer = csv.writer(output)
            writer.writerow(["pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "u", "v"])
            output.flush()
            print(f"Live monitor enabled; saving to {output_file}. Press Ctrl+C to stop.")
        else:
            print("Live monitor enabled. Press Ctrl+C to stop.")
        try:
            while True:
                reading: PAXReading = self.pax.read_polarization()
                state = pax_to_sphere_angles((reading.theta, reading.eta))
                if writer is not None:
                    writer.writerow([
                        reading.timestamp, reading.theta, reading.eta,
                        reading.s1, reading.s2, reading.s3, reading.dop,
                        state.u, state.v,
                    ])
                    output.flush()
                print(
                    f"theta={reading.theta:.5f} eta={reading.eta:.5f} "
                    f"u={state.u:.5f} v={state.v:.5f} dop={reading.dop:.5f}"
                )
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("Live monitor stopped")
        finally:
            if output is not None:
                output.close()

    def _run_calibration_sweep(self, axis: str, output_file: str) -> None:
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_axis_sweep(
                axis=axis,
                rp_values=[i / 100.0 for i in range(11)],
                repeats=1,
                settle_s=self.config.rough_settle_s,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Calibration sweep saved to {output_file}")
            else:
                print("Calibration sweep aborted; outputs were returned to zero.")

    def _one_lambda_rp_voltage(self, axis: str) -> float:
        axis_parameters = {
            "phi1": (self.config.phi1_v_lambda, self.config.phi1_actuator_volts_per_rp_volt),
            "phi2": (self.config.phi2_v_lambda, self.config.phi2_actuator_volts_per_rp_volt),
        }
        if axis not in axis_parameters:
            raise ValueError("Axis must be 'phi1' or 'phi2'")
        v_lambda, actuator_volts_per_rp_volt = axis_parameters[axis]
        if v_lambda is None or actuator_volts_per_rp_volt is None:
            raise RuntimeError(f"Configure {axis} V_lambda and RP-to-actuator gain before sweeping.")

        one_lambda_rp_voltage = v_lambda / actuator_volts_per_rp_volt
        if one_lambda_rp_voltage > self.config.rp_output_max_voltage:
            raise RuntimeError(
                f"One {axis} V_lambda needs {one_lambda_rp_voltage:.4f} V RP command, "
                "which exceeds the configured RP output limit."
            )
        return one_lambda_rp_voltage

    def _one_lambda_sweep_values(self, axis: str, step: float) -> list[float]:
        one_lambda_rp_voltage = self._one_lambda_rp_voltage(axis)
        if step <= 0:
            raise ValueError("Sweep step must be positive")
        step_count = int(one_lambda_rp_voltage // step)
        sweep_values = [index * step for index in range(step_count + 1)]
        if not np.isclose(sweep_values[-1], one_lambda_rp_voltage):
            sweep_values.append(one_lambda_rp_voltage)
        return sweep_values

    def _run_cross_sweep(self, axis: str, output_file: str) -> None:
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        sweep_values = self._one_lambda_sweep_values(axis, self.config.cross_sweep_step_voltage)
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_cross_sweep(
                sweep_axis=axis,
                bias_values=list(self.config.cross_sweep_bias_voltages),
                sweep_values=sweep_values,
                settle_s=self.config.cross_sweep_settle_s,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Cross-sweep saved to {output_file}")
            else:
                print("Cross-sweep aborted; outputs were returned to zero.")

    def _run_bidirectional_sweep(self, axis: str, output_file: str) -> None:
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        if axis == "phi1":
            bias_voltage = self.config.bidirectional_phi1_bias_voltage
        elif axis == "phi2":
            bias_voltage = self.config.bidirectional_phi2_bias_voltage
        else:
            raise ValueError("Bidirectional-sweep axis must be 'phi1' or 'phi2'")
        sweep_values = self._one_lambda_sweep_values(axis, self.config.bidirectional_sweep_step_voltage)
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_bidirectional_sweep(
                sweep_axis=axis,
                bias_voltage=bias_voltage,
                sweep_values=sweep_values,
                settle_s=self.config.bidirectional_sweep_settle_s,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Bidirectional sweep saved to {output_file}")
            else:
                print("Bidirectional sweep aborted; outputs were returned to zero.")

    def _run_diagnostic_suite(self, output_file: str) -> None:
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        fractions = self.config.diagnostic_phase_fractions
        if any(not 0.0 <= fraction <= 1.0 for fraction in fractions):
            raise ValueError("Diagnostic phase fractions must stay within [0, 1]")
        phi1_span = self._one_lambda_rp_voltage("phi1")
        phi2_span = self._one_lambda_rp_voltage("phi2")
        phi1_values = [(fraction, fraction * phi1_span) for fraction in fractions]
        phi2_values = [(fraction, fraction * phi2_span) for fraction in fractions]
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_static_diagnostic_suite(
                phi1_values=phi1_values,
                phi2_values=phi2_values,
                phi1_bias_voltage=self.config.diagnostic_phi1_bias_voltage,
                phi2_bias_voltage=self.config.diagnostic_phi2_bias_voltage,
                settle_s=self.config.diagnostic_settle_s,
                hold_s=self.config.diagnostic_hold_s,
                sample_period_s=self.config.diagnostic_sample_period_s,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Diagnostic suite saved to {output_file}")
            else:
                print("Diagnostic suite aborted; outputs were returned to zero.")

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
        except KeyboardInterrupt:
            print("PID test stopped by user")
        finally:
            output.close()
            self.rp.set_output_zero()
            self._applied_rp_voltages[:] = 0.0
            print(f"PID test log saved to {output_file}; outputs returned to zero.")

    def _run_pid_live(self, duration_s: float, output_file: str) -> None:
        """Run the logged PID test with the PAX-driven interactive sphere view."""
        self._require_target()
        try:
            from .poincare_live_plot import PIDPoincareView
        except ImportError:  # pragma: no cover - support direct execution
            from poincare_live_plot import PIDPoincareView
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

    def interactive_cli(self) -> None:
        self.connect()
        print("Rough alignment CLI")
        print("Commands: set <u> <v> | capture | capture-unchecked | rough | live [file] | sweep <phi1|phi2> <file> | cross-sweep <phi1|phi2> <file> | bidirectional-sweep <phi1|phi2> <file> | diagnostic-suite <file> | pid-test <seconds> <file> | pid-live <seconds> <file> | stop | quit")
        try:
            while True:
                cmd = input(">> ").strip().split()
                if not cmd:
                    continue
                if cmd[0] == "set" and len(cmd) == 3:
                    self.set_target(float(cmd[1]), float(cmd[2]))
                    print(f"Target set to u={self._target.u:.4f}, v={self._target.v:.4f}")
                elif cmd[0] == "capture":
                    target = self.capture_target()
                    print(f"Captured target u={target.u:.4f}, v={target.v:.4f}")
                elif cmd[0] == "capture-unchecked":
                    target = self.capture_target(require_trusted_dop=False)
                    print(f"Captured unchecked target u={target.u:.4f}, v={target.v:.4f}")
                elif cmd[0] == "rough":
                    self.rough_align_once()
                elif cmd[0] == "live" and len(cmd) in {1, 2}:
                    self._run_live_monitor(cmd[1] if len(cmd) == 2 else None)
                elif cmd[0] == "sweep" and len(cmd) == 3:
                    self._run_calibration_sweep(cmd[1], cmd[2])
                elif cmd[0] == "cross-sweep" and len(cmd) == 3:
                    self._run_cross_sweep(cmd[1], cmd[2])
                elif cmd[0] == "bidirectional-sweep" and len(cmd) == 3:
                    self._run_bidirectional_sweep(cmd[1], cmd[2])
                elif cmd[0] == "diagnostic-suite" and len(cmd) == 2:
                    self._run_diagnostic_suite(cmd[1])
                elif cmd[0] == "pid-test" and len(cmd) == 3:
                    self._run_pid_test(float(cmd[1]), cmd[2])
                elif cmd[0] == "pid-live" and len(cmd) == 3:
                    self._run_pid_live(float(cmd[1]), cmd[2])
                elif cmd[0] == "stop":
                    self.rp.set_output_zero()
                    self._applied_rp_voltages[:] = 0.0
                elif cmd[0] in {"quit", "exit"}:
                    break
                else:
                    print("Unknown command")
        finally:
            self.disconnect()


if __name__ == "__main__":
    PolarizationLockApp().interactive_cli()
