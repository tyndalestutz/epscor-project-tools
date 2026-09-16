#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import subprocess
import sys
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

try:
    from .config import DEFAULT_CONFIG, PolarizationLockConfig
    from .control import PolarizationState, SphereAngles, pax_to_sphere_angles, phase_error_to_rp_voltage, sphere_angle_error, sphere_angles_from_stokes, wrap_angle
    from .pax_interface import PAXController, PAXReading
    from .rp_interface import RPController
except ImportError:  # pragma: no cover - support direct execution
    from config import DEFAULT_CONFIG, PolarizationLockConfig
    from control import PolarizationState, SphereAngles, pax_to_sphere_angles, phase_error_to_rp_voltage, sphere_angle_error, sphere_angles_from_stokes, wrap_angle
    from pax_interface import PAXController, PAXReading
    from rp_interface import RPController


@dataclass(frozen=True)
class ExperimentPaths:
    """The self-contained directory allocated for one command-line run."""

    directory: Path
    csv: Path
    pdf: Path


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

    def _read_pid_sphere_reading(self, *, count: int | None = None) -> tuple[SphereAngles, PAXReading]:
        """Return the Stokes-vector average used for a single PID update.

        Averaging Cartesian Stokes vectors, then renormalizing, avoids the
        azimuth wrap problem that would arise from averaging u directly.
        """
        count = self.config.pid_pax_average_count if count is None else count
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

    def _cross_sweep_bias_values(self, sweep_axis: str) -> list[float]:
        """Return full-V_lambda fixed-axis biases, including both endpoints."""
        if sweep_axis not in {"phi1", "phi2"}:
            raise ValueError("Cross-sweep axis must be 'phi1' or 'phi2'")
        intervals = self.config.cross_sweep_bias_intervals
        if intervals < 1:
            raise ValueError("cross_sweep_bias_intervals must be at least one")
        bias_axis = "phi2" if sweep_axis == "phi1" else "phi1"
        return np.linspace(0.0, self._one_lambda_rp_voltage(bias_axis), intervals + 1).tolist()

    @staticmethod
    def _experiment_label(name: str) -> str:
        stem = Path(name).stem or "run"
        label = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")
        return label or "run"

    def _new_experiment_paths(self, kind: str, requested_name: str) -> ExperimentPaths:
        """Allocate a dated folder so a run's raw data and report stay together."""
        now = datetime.now()
        root = Path(__file__).resolve().parents[2] / "experiments" / "polarization_locking" / now.strftime("%Y-%m-%d")
        base_name = f"{now.strftime('%H%M%S')}_{kind}_{self._experiment_label(requested_name)}"
        directory = root / base_name
        suffix = 2
        while directory.exists():
            directory = root / f"{base_name}_{suffix}"
            suffix += 1
        directory.mkdir(parents=True)
        return ExperimentPaths(directory=directory, csv=directory / "data.csv", pdf=directory / "report.pdf")

    def _write_automatic_pdf(
        self,
        kind: str,
        csv_file: str | Path,
        axis: str | None = None,
        output_file: Path | None = None,
    ) -> None:
        """Create the opt-in report that corresponds to a completed test CSV."""
        csv_path = Path(csv_file)
        output_file = output_file or csv_path.with_suffix(".pdf")
        if kind == "pid":
            try:
                from .plot_pid_tests import PdfPages, add_page
            except ImportError:  # pragma: no cover - support direct execution
                from plot_pid_tests import PdfPages, add_page
            with PdfPages(output_file) as pdf:
                add_page(pdf, csv_path)
        elif kind == "single-axis-pid":
            try:
                from .plot_single_axis_pid import PdfPages, add_page
            except ImportError:  # pragma: no cover - support direct execution
                from plot_single_axis_pid import PdfPages, add_page
            with PdfPages(output_file) as pdf:
                add_page(pdf, csv_path)
        elif kind == "phi1-d-lock":
            try:
                from .plot_phi1_d_lock import PdfPages, add_page
            except ImportError:  # pragma: no cover - support direct execution
                from plot_phi1_d_lock import PdfPages, add_page
            with PdfPages(output_file) as pdf:
                add_page(pdf, csv_path)
        elif kind == "phi1-pd-lock":
            try:
                from .plot_phi1_pd_lock import PdfPages, add_page
            except ImportError:  # pragma: no cover - support direct execution
                from plot_phi1_pd_lock import PdfPages, add_page
            with PdfPages(output_file) as pdf:
                add_page(pdf, csv_path)
        elif kind == "cross":
            try:
                from .plot_cross_tests import PdfPages, add_report
            except ImportError:  # pragma: no cover - support direct execution
                from plot_cross_tests import PdfPages, add_report
            with PdfPages(output_file) as pdf:
                add_report(pdf, csv_path)
        else:
            try:
                from .plot_calibration_tests import create_report
            except ImportError:  # pragma: no cover - support direct execution
                from plot_calibration_tests import create_report
            create_report(csv_path, output_file, kind, axis)
        print(f"PDF report saved to {output_file}")

    def _run_cross_sweep(self, axis: str, output_file: str) -> None:
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        sweep_values = self._one_lambda_sweep_values(axis, self.config.cross_sweep_step_voltage)
        bias_values = self._cross_sweep_bias_values(axis)
        bias_axis = "phi2" if axis == "phi1" else "phi1"
        print(
            f"Cross-sweep plan: {len(sweep_values)} {axis} points per slice × {len(bias_values)} "
            f"{bias_axis} biases spanning 0..{bias_values[-1]:.4f} V RP "
            f"({len(sweep_values) * len(bias_values)} total readings)"
        )
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_cross_sweep(
                sweep_axis=axis,
                bias_values=bias_values,
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

    def _run_intensity_diagnostic(self, output_file: str) -> None:
        """Compare final-port PD amplitude and PAX state for independent sweeps."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        phi1_values = self._one_lambda_sweep_values("phi1", self.config.intensity_diagnostic_step_voltage)
        phi2_values = self._one_lambda_sweep_values("phi2", self.config.intensity_diagnostic_step_voltage)
        print(
            f"Intensity diagnostic: {len(phi1_values)} phi1 points at phi2=0 and "
            f"{len(phi2_values)} phi2 points at phi1=0 ({len(phi1_values) + len(phi2_values)} total readings)"
        )
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_intensity_diagnostic(
                phi1_values=phi1_values,
                phi2_values=phi2_values,
                settle_s=self.config.intensity_diagnostic_settle_s,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Intensity diagnostic saved to {output_file}")
            else:
                print("Intensity diagnostic aborted; outputs were returned to zero.")

    def _run_phi2_path_balance_test(self, output_file: str) -> None:
        """Guided three-condition test of the two input-path contributions."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep

        values = self._one_lambda_sweep_values("phi2", self.config.intensity_diagnostic_step_voltage)
        instructions = {
            "path_a_only": (
                "PATH A ONLY: leave optical path A open and block optical path B. "
                "Keep the final BS/PAX/PD connections unchanged."
            ),
            "path_b_only": (
                "PATH B ONLY: block optical path A and leave optical path B open. "
                "Keep the final BS/PAX/PD connections unchanged."
            ),
            "both_paths": "BOTH PATHS: unblock both optical paths.",
        }
        print(
            f"Guided phi2 path-balance test: {len(values)} points per condition, "
            f"{3 * len(values)} simultaneous PAX + PD readings total.\n"
            "Path A/B are deliberately bench labels: use the same physical path consistently."
        )

        def prepare(condition: str) -> None:
            print(f"\n--- {instructions[condition]} ---")
            input("When the beam block is in place and the setup is stable, press Enter to sweep phi2. ")

        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_phi2_path_balance(
                phi2_values=values,
                settle_s=self.config.intensity_diagnostic_settle_s,
                prepare_condition=prepare,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Phi2 path-balance test saved to {output_file}")
            else:
                print("Phi2 path-balance test aborted; outputs were returned to zero.")

    def _run_field_model_calibration(self, output_file: str) -> None:
        """Acquire the intentional two-axis data set consumed by the field fit."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        fractions = self.config.field_model_phi1_fractions
        if len(fractions) < 3 or any(fraction < 0.0 or fraction > 1.0 for fraction in fractions):
            raise ValueError("field_model_phi1_fractions must contain at least three fractions in [0, 1]")
        phi1_lambda = self._one_lambda_rp_voltage("phi1")
        phi1_values = [fraction * phi1_lambda for fraction in fractions]
        phi2_values = self._one_lambda_sweep_values("phi2", self.config.field_model_phi2_step_voltage)
        repeats = self.config.field_model_repeats
        points_per_block = len(phi2_values)
        blocks = len(phi1_values) * repeats * 3
        print(
            "Fit-ready field-model calibration\n"
            f"  phi1 biases (RP V): {', '.join(f'{value:.4f}' for value in phi1_values)}\n"
            f"  phi2 sweep: 0..{phi2_values[-1]:.4f} RP V in {len(phi2_values)} points\n"
            f"  {blocks} manual block settings × {points_per_block} PAX+PD samples = {blocks * points_per_block} rows.\n"
            "Each phi1 bias performs A → B → both forward, then both → B → A reverse. "
            "This brackets drift; keep the final BS, PAX, and PD paths unchanged."
        )
        instructions = {
            "path_a_only": "leave optical path A open and block optical path B",
            "path_b_only": "block optical path A and leave optical path B open",
            "both_paths": "unblock both optical paths",
        }

        def prepare(condition: str, phi1_voltage: float, repeat_index: int, direction: str) -> None:
            print(
                f"\n--- phi1={phi1_voltage:.4f} RP V; pass {repeat_index + 1}/{repeats}; {direction}; "
                f"{condition}: {instructions[condition]} ---"
            )
            input("When stable, press Enter to acquire this phi2 sweep. ")

        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_field_model_calibration(
                phi1_values=phi1_values,
                phi2_values=phi2_values,
                repeats=repeats,
                settle_s=self.config.field_model_settle_s,
                prepare_condition=prepare,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                context_file = Path(output_file).with_name("field-model-context.json")
                context_file.write_text(json.dumps({
                    "schema_version": 1,
                    "purpose": "Whole-system diagnostic; not independent component characterization",
                    "raw_data_file": Path(output_file).name,
                    "rp_vlambda_v": {"phi1": phi1_lambda, "phi2": self._one_lambda_rp_voltage("phi2")},
                    "actuator_vlambda_v": {"phi1": self.config.phi1_v_lambda, "phi2": self.config.phi2_v_lambda},
                    "actuator_volts_per_rp_volt": {
                        "phi1": self.config.phi1_actuator_volts_per_rp_volt,
                        "phi2": self.config.phi2_actuator_volts_per_rp_volt,
                    },
                    "output_assignment": {"rp_out1": "phi1", "rp_out2": "phi2", "pd": "final output with OD 2.0", "pax": "other final output"},
                    "scan": {
                        "phi1_rp_biases_v": phi1_values,
                        "phi2_rp_values_v": phi2_values,
                        "repeats": repeats,
                        "settle_s": self.config.field_model_settle_s,
                        "conditions": ["path_a_only", "path_b_only", "both_paths"],
                    },
                }, indent=2) + "\n")
                print(
                    f"Field-model calibration saved to {output_file}\n"
                    f"Model context saved to {context_file}\n"
                    "This is a whole-system diagnostic, not independent optic characterization.\n"
                    "Measurement-first workflow: python python/field_propogation/acquire.py --help"
                )
            else:
                print("Field-model calibration aborted; outputs were returned to zero.")

    def _run_phi1_fringe_map(self, output_file: str) -> None:
        """Run an isolated both-path phi1 calibration/hysteresis measurement."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        count = self.config.phi1_fringe_map_points
        if count < 3:
            raise ValueError("phi1_fringe_map_points must be at least 3")
        phi1_lambda = self._one_lambda_rp_voltage("phi1")
        phi1_values = np.linspace(0.0, phi1_lambda, count).tolist()
        phi2_values = self._one_lambda_sweep_values("phi2", self.config.phi1_fringe_map_phi2_step_voltage)
        print(
            "Focused phi1 fringe map (both paths open throughout)\n"
            f"  phi1: 0..{phi1_lambda:.4f} RP V in {len(phi1_values)} points, forward then reverse\n"
            f"  phi2: 0..{phi2_values[-1]:.4f} RP V in {len(phi2_values)} points at each phi1 value\n"
            f"  total: {2 * len(phi1_values) * len(phi2_values)} simultaneous PAX + PD samples.\n"
            "This isolates the phi1-voltage-to-fringe-phase map; do not change beam blocks during acquisition."
        )
        input("Set BOTH PATHS OPEN and let the interferometer settle, then press Enter to begin. ")
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_phi1_fringe_map(
                phi1_values=phi1_values,
                phi2_values=phi2_values,
                settle_s=self.config.phi1_fringe_map_settle_s,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(
                    f"Phi1 fringe map saved to {output_file}\n"
                    "Keep this run separate from independent component measurements.\n"
                    "Measurement-first workflow: python python/field_propogation/acquire.py --help"
                )
            else:
                print("Phi1 fringe map aborted; outputs were returned to zero.")

    def _run_pax_path_hold(self, duration_s: float, output_file: str) -> None:
        """Guided no-motion comparison of the raw PAX fields by optical path."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        instructions = {
            "path_a_only": "PATH A ONLY: leave path A open and block path B.",
            "path_b_only": "PATH B ONLY: block path A and leave path B open.",
            "both_paths": "BOTH PATHS: unblock both paths.",
        }
        print(
            f"Guided PAX path-hold: {duration_s:.1f} s per condition, both RP outputs held at 0 V.\n"
            "This test does not sweep either actuator; it records raw PAX telemetry and the PD simultaneously."
        )

        def prepare(condition: str) -> None:
            print(f"\n--- {instructions[condition]} ---")
            input("When stable, press Enter to begin this fixed-state acquisition. ")

        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_pax_path_hold(
                duration_s=duration_s,
                sample_period_s=self.config.pax_path_hold_sample_period_s,
                prepare_condition=prepare,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"PAX path-hold saved to {output_file}")
            else:
                print("PAX path-hold aborted; outputs were returned to zero.")

    def _run_phi2_power_balance(self, duration_s: float, output_file: str) -> None:
        """Guided sine-driven comparison of final-port PD and PAX power."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        phi2_lambda = self._one_lambda_rp_voltage("phi2")
        frequency = self.config.power_balance_phi2_frequency_hz
        instructions = {
            "path_a_only": "PATH A ONLY: leave path A open and block path B.",
            "path_b_only": "PATH B ONLY: block path A and leave path B open.",
            "both_paths": "BOTH PATHS: unblock both paths.",
        }
        print(
            f"Guided phi2 power-balance: {duration_s:.1f} s per condition; "
            f"OUT2 sine = {phi2_lambda / 2:.4f} ± {phi2_lambda / 2:.4f} V at {frequency:.2f} Hz.\n"
            "It spans 0..one phi2 V_lambda while OUT1/phi1 remains at 0 V. "
            "Set the blocks at whichever physical plane you want to characterize."
        )

        def prepare(condition: str) -> None:
            print(f"\n--- {instructions[condition]} ---")
            input("When stable, press Enter to begin the sine measurement. ")

        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_phi2_power_balance(
                duration_s=duration_s,
                frequency_hz=frequency,
                phi2_v_lambda_rp=phi2_lambda,
                sample_period_s=self.config.power_balance_sample_period_s,
                prepare_condition=prepare,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Phi2 power-balance saved to {output_file}")
            else:
                print("Phi2 power-balance aborted; outputs were returned to zero.")

    def _run_first_npbs_d_test(self, output_file: str, report_format: str | None = None) -> None:
        """Record the static and phi1-driven polarization state at first-NPBS D."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        phi1_lambda = self._one_lambda_rp_voltage("phi1")
        static_s = self.config.first_npbs_d_static_duration_s
        driven_s = self.config.first_npbs_d_driven_duration_s
        frequency = self.config.first_npbs_d_phi1_frequency_hz
        print(
            "First-NPBS D-port polarization test\n"
            "  Connect and align the PAX at D: the REFLECTED output of the first NPBS, before the phi2/C arm and final NPBS.\n"
            f"  Static: phi1=phi2=0 for {static_s:.0f} s.\n"
            f"  Driven: OUT1/phi1 sine = {phi1_lambda / 2:.4f} +/- {phi1_lambda / 2:.4f} RP V "
            f"(0..one V_lambda) at {frequency:.2f} Hz for {driven_s:.0f} s; OUT2/phi2 remains 0.\n"
            "Ideal D prediction: S1=0 and (S2,S3)=(-sin(phi1+delta), cos(phi1+delta)); "
            "the unknown static delta rotates this equator but does not change its shape."
        )
        input("When the PAX is aligned at D and stable, press Enter to begin. ")
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_first_npbs_d_test(
                static_duration_s=static_s, driven_duration_s=driven_s, frequency_hz=frequency,
                phi1_v_lambda_rp=phi1_lambda, sample_period_s=self.config.first_npbs_d_sample_period_s,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"First-NPBS D-port test saved to {output_file}")
                if report_format is not None:
                    analyzer = Path(__file__).resolve().parent / "analysis" / "analyze_first_npbs_d.py"
                    subprocess.run([sys.executable, str(analyzer), output_file, "--format", report_format], check=True)
            else:
                print("First-NPBS D-port test aborted; outputs were returned to zero.")

    def _run_first_npbs_d_isolation(self, output_file: str, report_format: str | None = None) -> None:
        """Guided static A/B isolation at D, prior to any commanded phi1 test."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        duration = self.config.first_npbs_d_isolation_duration_s
        instructions = {
            "path_a_only": "leave input PATH A open (PBS transmitted/phi1 arm) and block input PATH B",
            "path_b_only": "block input PATH A and leave input PATH B open (PBS reflected arm)",
            "both_paths": "unblock both input paths A and B",
        }
        print(
            "First-NPBS D-port A/B isolation test\n"
            "Keep the PAX aligned at D, the REFLECTED output of the first NPBS. Both RP outputs remain at 0 V.\n"
            f"Each of A-only, B-only, and both-open is recorded for {duration:.0f} s.\n"
            "Ideal predictions at D: A-only is fixed S=(-1,0,0); B-only is fixed S=(+1,0,0); "
            "both-open is an S1=0 equatorial state whose angle exposes A/B relative phase."
        )

        def prepare(condition: str) -> None:
            print(f"\n--- {condition}: {instructions[condition]} ---")
            input("When stable, press Enter to begin this 60-second capture. ")

        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_first_npbs_d_isolation(
                duration_s=duration, sample_period_s=self.config.first_npbs_d_sample_period_s,
                prepare_condition=prepare, output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"First-NPBS D-port isolation saved to {output_file}")
                if report_format is not None:
                    analyzer = Path(__file__).resolve().parent / "analysis" / "analyze_first_npbs_d_isolation.py"
                    subprocess.run([sys.executable, str(analyzer), output_file, "--format", report_format], check=True)
            else:
                print("First-NPBS D-port isolation aborted; outputs were returned to zero.")

    def _run_phi1_step_map(self, output_file: str, report_format: str | None = None) -> None:
        """Perform the timing-independent phi1 voltage-to-D-phase calibration."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        phi1_lambda = self._one_lambda_rp_voltage("phi1")
        points = self.config.phi1_step_map_points
        settle = self.config.phi1_step_map_settle_s
        samples = self.config.phi1_step_map_samples_per_step
        print(
            "Phi1 held-step D-port calibration\n"
            "  Keep BOTH A and B input paths open. Align the PAX directly at first-NPBS reflected output D.\n"
            "  OUT2/phi2 is held at 0 V. OUT1/phi1 steps from 0 to one candidate V_lambda and back.\n"
            f"  {points} voltage positions per direction; {settle:.2f} s settling then {samples} PAX samples per position.\n"
            "  This deliberately uses no sine wave: each record has an unambiguous held output voltage."
        )
        input("When D is aligned and stable, press Enter to begin. ")
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_phi1_step_map(
                phi1_v_lambda_rp=phi1_lambda, points=points, settle_s=settle,
                samples_per_step=samples, inter_sample_s=self.config.phi1_step_map_inter_sample_s,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Phi1 held-step map saved to {output_file}")
                if report_format is not None:
                    analyzer = Path(__file__).resolve().parent / "analysis" / "analyze_phi1_step_map.py"
                    subprocess.run([sys.executable, str(analyzer), output_file, "--format", report_format], check=True)
            else:
                print("Phi1 held-step map aborted; outputs were returned to zero.")

    def _run_first_npbs_d_polarizer_test(self, output_file: str, report_format: str | None = None) -> None:
        """Drive phi1 with raw PAX at D and a linear analyzer in C."""
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        phi1_lambda = self._one_lambda_rp_voltage("phi1")
        static_s = self.config.first_npbs_d_polarizer_static_duration_s
        driven_s = self.config.first_npbs_d_polarizer_driven_duration_s
        frequency = self.config.first_npbs_d_polarizer_phi1_frequency_hz
        print(
            "Phi1 C-arm polarizer / D-port PAX test\n"
            "  Keep BOTH input paths A and B open. Keep PAX directly at first-NPBS D with NO polarizer before it.\n"
            "  Place a linear polarizer in arm C immediately before the final NPBS (therefore before the PD path). Set it approximately 45 degrees "
            "to the A/B linear eigenstates for substantial phase-to-power conversion at final F.\n"
            "  The PAX records the raw D-port polarization trajectory; PD at final port F is the intentional intensity analyzer.\n"
            f"  Static: {static_s:.0f} s at phi1=0. Driven: OUT1 = {phi1_lambda / 2:.4f} +/- {phi1_lambda / 2:.4f} RP V "
            f"at {frequency:.3f} Hz for {driven_s:.0f} s; OUT2=0. This is intentionally slow enough for the PAX to resolve."
        )
        input("When the polarizer/PAX are aligned at D and both A/B paths are open, press Enter to begin. ")
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            points = sweep.run_first_npbs_d_polarizer_test(
                static_duration_s=static_s, driven_duration_s=driven_s, frequency_hz=frequency,
                phi1_v_lambda_rp=phi1_lambda, sample_period_s=self.config.first_npbs_d_polarizer_sample_period_s,
            )
            sweep.save_first_npbs_d_polarizer_csv(
                output_file, points, frequency_hz=frequency, phi1_v_lambda_rp=phi1_lambda,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Phi1 C-arm polarizer / D-port PAX test saved to {output_file}")
                if report_format is not None:
                    analyzer = Path(__file__).resolve().parent / "analysis" / "analyze_first_npbs_d_polarizer.py"
                    subprocess.run([sys.executable, str(analyzer), output_file, "--format", report_format], check=True)
            else:
                print("D-port phi1 analyzer test aborted; outputs were returned to zero.")

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

    @staticmethod
    def _write_power_balance_plots(csv_file: Path, paths: ExperimentPaths, output_format: str) -> None:
        try:
            from .plot_power_balance import create_report
        except ImportError:  # pragma: no cover - support direct execution
            from plot_power_balance import create_report
        create_report(
            csv_file,
            pdf_file=paths.pdf if output_format in {"pdf", "both"} else None,
            png_directory=paths.directory if output_format in {"png", "both"} else None,
        )
        if output_format in {"pdf", "both"}:
            print(f"PDF report saved to {paths.pdf}")
        if output_format in {"png", "both"}:
            print(f"PNG plots saved to {paths.directory}")

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
        print("Commands: set <u> <v> | capture | capture-unchecked | rough | live [file] | sweep <phi1|phi2> <file> [pdf] | cross-sweep <phi1|phi2> <file> [pdf] | bidirectional-sweep <phi1|phi2> <file> [pdf] | diagnostic-suite <file> [pdf] | intensity-diagnostic <file> [pdf] | phi2-path-test <file> [pdf] | first-npbs-d-test <file> [png|pdf|both] | first-npbs-d-isolation <file> [png|pdf|both] | phi1-step-map <file> [png|pdf|both] | phi1-lock-test <seconds> <file> [pdf] | phi1-pd-lock-test <seconds> <file> [pdf] | phi1-pd-hybrid-test <seconds> <file> [pdf] | phi1-pd-gain-scan <file> | d-polarizer-phi1-test <file> [png|pdf|both] | field-model-calibration <file> | phi1-fringe-map <file> | pax-path-hold <seconds> <file> [pdf] | power-balance <seconds> <file> [pdf|png|both] | single-axis-pid <phi1|phi2> <seconds> <file> [pdf] | pid-test <seconds> <file> [pdf] | pid-live <seconds> <file> [pdf] | stop | quit")
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
                    paths = self._new_experiment_paths("live", cmd[1]) if len(cmd) == 2 else None
                    self._run_live_monitor(str(paths.csv) if paths is not None else None)
                    if paths is not None:
                        print(f"Live log saved to {paths.csv}")
                elif cmd[0] == "sweep" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() == "pdf"):
                    paths = self._new_experiment_paths(f"sweep-{cmd[1]}", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_calibration_sweep(cmd[1], str(paths.csv))
                    if len(cmd) == 4:
                        self._write_automatic_pdf("sweep", paths.csv, cmd[1], paths.pdf)
                elif cmd[0] == "cross-sweep" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() == "pdf"):
                    paths = self._new_experiment_paths(f"cross-sweep-{cmd[1]}", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_cross_sweep(cmd[1], str(paths.csv))
                    if len(cmd) == 4:
                        self._write_automatic_pdf("cross", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "bidirectional-sweep" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() == "pdf"):
                    paths = self._new_experiment_paths(f"bidirectional-{cmd[1]}", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_bidirectional_sweep(cmd[1], str(paths.csv))
                    if len(cmd) == 4:
                        self._write_automatic_pdf("bidirectional", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "diagnostic-suite" and len(cmd) in {2, 3} and (len(cmd) == 2 or cmd[2].lower() == "pdf"):
                    paths = self._new_experiment_paths("diagnostic-suite", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_diagnostic_suite(str(paths.csv))
                    if len(cmd) == 3:
                        self._write_automatic_pdf("diagnostic", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "intensity-diagnostic" and len(cmd) in {2, 3} and (len(cmd) == 2 or cmd[2].lower() == "pdf"):
                    paths = self._new_experiment_paths("intensity-diagnostic", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_intensity_diagnostic(str(paths.csv))
                    if len(cmd) == 3:
                        self._write_automatic_pdf("intensity", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "phi2-path-test" and len(cmd) in {2, 3} and (len(cmd) == 2 or cmd[2].lower() == "pdf"):
                    paths = self._new_experiment_paths("phi2-path-test", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_phi2_path_balance_test(str(paths.csv))
                    if len(cmd) == 3:
                        self._write_automatic_pdf("phi2-path-test", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "first-npbs-d-test" and len(cmd) in {2, 3} and (len(cmd) == 2 or cmd[2].lower() in {"png", "pdf", "both"}):
                    paths = self._new_experiment_paths("first-npbs-d-test", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_first_npbs_d_test(str(paths.csv), cmd[2].lower() if len(cmd) == 3 else None)
                elif cmd[0] == "first-npbs-d-isolation" and len(cmd) in {2, 3} and (len(cmd) == 2 or cmd[2].lower() in {"png", "pdf", "both"}):
                    paths = self._new_experiment_paths("first-npbs-d-isolation", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_first_npbs_d_isolation(str(paths.csv), cmd[2].lower() if len(cmd) == 3 else None)
                elif cmd[0] == "phi1-step-map" and len(cmd) in {2, 3} and (len(cmd) == 2 or cmd[2].lower() in {"png", "pdf", "both"}):
                    paths = self._new_experiment_paths("phi1-step-map", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_phi1_step_map(str(paths.csv), cmd[2].lower() if len(cmd) == 3 else None)
                elif cmd[0] == "phi1-lock-test" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() == "pdf"):
                    paths = self._new_experiment_paths("phi1-lock-test", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_phi1_d_lock_test(float(cmd[1]), str(paths.csv))
                    if len(cmd) == 4:
                        self._write_automatic_pdf("phi1-d-lock", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "phi1-pd-lock-test" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() == "pdf"):
                    paths = self._new_experiment_paths("phi1-pd-lock-test", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_phi1_pd_lock_test(float(cmd[1]), str(paths.csv))
                    if len(cmd) == 4:
                        self._write_automatic_pdf("phi1-pd-lock", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "phi1-pd-hybrid-test" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() == "pdf"):
                    paths = self._new_experiment_paths("phi1-pd-hybrid-test", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_phi1_pd_lock_test(float(cmd[1]), str(paths.csv), hybrid_outer=True)
                    if len(cmd) == 4:
                        self._write_automatic_pdf("phi1-pd-lock", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "phi1-pd-gain-scan" and len(cmd) == 2:
                    paths = self._new_experiment_paths("phi1-pd-gain-scan", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_phi1_pd_lock_test(0.0, str(paths.csv), gain_scan=True)
                elif cmd[0] == "d-polarizer-phi1-test" and len(cmd) in {2, 3} and (len(cmd) == 2 or cmd[2].lower() in {"png", "pdf", "both"}):
                    paths = self._new_experiment_paths("d-polarizer-phi1-test", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_first_npbs_d_polarizer_test(str(paths.csv), cmd[2].lower() if len(cmd) == 3 else None)
                elif cmd[0] == "field-model-calibration" and len(cmd) == 2:
                    paths = self._new_experiment_paths("field-model-calibration", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_field_model_calibration(str(paths.csv))
                elif cmd[0] == "phi1-fringe-map" and len(cmd) == 2:
                    paths = self._new_experiment_paths("phi1-fringe-map", cmd[1])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_phi1_fringe_map(str(paths.csv))
                elif cmd[0] == "pax-path-hold" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() == "pdf"):
                    paths = self._new_experiment_paths("pax-path-hold", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_pax_path_hold(float(cmd[1]), str(paths.csv))
                    if len(cmd) == 4:
                        self._write_automatic_pdf("pax-path-hold", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "power-balance" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() in {"pdf", "png", "both"}):
                    paths = self._new_experiment_paths("power-balance", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_phi2_power_balance(float(cmd[1]), str(paths.csv))
                    if len(cmd) == 4:
                        self._write_power_balance_plots(paths.csv, paths, cmd[3].lower())
                elif cmd[0] == "single-axis-pid" and len(cmd) in {4, 5} and (len(cmd) == 4 or cmd[4].lower() == "pdf"):
                    paths = self._new_experiment_paths(f"single-axis-pid-{cmd[1]}", cmd[3])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_single_axis_pid_test(cmd[1], float(cmd[2]), str(paths.csv))
                    if len(cmd) == 5:
                        self._write_automatic_pdf("single-axis-pid", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "pid-test" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() == "pdf"):
                    paths = self._new_experiment_paths("pid-test", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_pid_test(float(cmd[1]), str(paths.csv))
                    if len(cmd) == 4:
                        self._write_automatic_pdf("pid", paths.csv, output_file=paths.pdf)
                elif cmd[0] == "pid-live" and len(cmd) in {3, 4} and (len(cmd) == 3 or cmd[3].lower() == "pdf"):
                    paths = self._new_experiment_paths("pid-live", cmd[2])
                    print(f"Experiment folder: {paths.directory}")
                    self._run_pid_live(float(cmd[1]), str(paths.csv))
                    if len(cmd) == 4:
                        self._write_automatic_pdf("pid", paths.csv, output_file=paths.pdf)
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
