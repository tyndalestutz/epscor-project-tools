from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import numpy as np

from ..control import pax_to_sphere_angles
from ..hardware.pax_interface import PAXReading

from .prompts import prepare_setup


class DiagnosticsMixin:
    def _run_pax_vibration(self, output_file: str, duration_s: float) -> None:
        from .pax_vibration import acquire
        acquire(self.rp, self.pax, self.config, output_file, duration_s)

    def _run_pax_live(self, output_file: str) -> dict:
        # Select cleanup even if constructing the GUI fails before connection.
        self._pax_only = True
        from .pax_live import run_panel
        return run_panel(self, output_file)

    def _run_stokes_phase_sweep(self, output_file: str, duration_s: float) -> None:
        from .stokes_phase_sweep import acquire_stokes_phase_sweep
        acquire_stokes_phase_sweep(self.rp, self.pax, self.config, output_file, duration_s)

    def _run_pd_visibility(self, output_file: str, duration_s: float) -> None:
        from .visibility import acquire_contrast
        acquire_contrast(self.rp, self.pax, self.config, output_file, duration_s)

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
                time.sleep(self.config.live_sample_period_s)
        finally:
            if output is not None:
                output.close()

    def _run_calibration_sweep(self, axis: str, output_file: str) -> None:
        from .calibration import CalibrationSweep
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_axis_sweep(
                axis=axis,
                rp_values=np.linspace(self.config.sweep_start_rp_v, self.config.sweep_stop_rp_v, self.config.sweep_points).tolist(),
                repeats=self.config.sweep_repeats,
                settle_s=self.config.sweep_settle_s,
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

    def _run_cross_sweep(self, axis: str, output_file: str) -> None:
        from .calibration import CalibrationSweep
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
        from .calibration import CalibrationSweep
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
        from .calibration import CalibrationSweep
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
        from .calibration import CalibrationSweep
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
        from .calibration import CalibrationSweep

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
            prepare_setup("When the beam block is in place and the setup is stable, press Enter to sweep phi2. ")

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
        from .calibration import CalibrationSweep
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
            prepare_setup("When stable, press Enter to acquire this phi2 sweep. ")

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
        from .calibration import CalibrationSweep
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
        prepare_setup("Set BOTH PATHS OPEN and let the interferometer settle, then press Enter to begin. ")
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
        from .calibration import CalibrationSweep
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
            prepare_setup("When stable, press Enter to begin this fixed-state acquisition. ")

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
        from .calibration import CalibrationSweep
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
            prepare_setup("When stable, press Enter to begin the sine measurement. ")

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

    def _run_first_npbs_d_test(self, output_file: str) -> None:
        """Record the static and phi1-driven polarization state at first-NPBS D."""
        from .calibration import CalibrationSweep
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
        prepare_setup("When the PAX is aligned at D and stable, press Enter to begin. ")
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
            else:
                print("First-NPBS D-port test aborted; outputs were returned to zero.")

    def _run_first_npbs_d_isolation(self, output_file: str) -> None:
        """Guided static A/B isolation at D, prior to any commanded phi1 test."""
        from .calibration import CalibrationSweep
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
            prepare_setup(f"When stable, press Enter to begin this {duration:g}-second capture. ")

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
            else:
                print("First-NPBS D-port isolation aborted; outputs were returned to zero.")

    def _run_phi1_step_map(self, output_file: str) -> None:
        """Perform the timing-independent phi1 voltage-to-D-phase calibration."""
        from .calibration import CalibrationSweep
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
        prepare_setup("When D is aligned and stable, press Enter to begin. ")
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
            else:
                print("Phi1 held-step map aborted; outputs were returned to zero.")

    def _run_first_npbs_d_polarizer_test(self, output_file: str) -> None:
        """Drive phi1 with raw PAX at D and a linear analyzer in C."""
        from .calibration import CalibrationSweep
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
        prepare_setup("When the C polarizer and D PAX are aligned and both A/B paths are open, press Enter to begin. ")
        sweep = CalibrationSweep(self.config, rp=self.rp, pax=self.pax)
        completed = False
        try:
            sweep.run_first_npbs_d_polarizer_test(
                static_duration_s=static_s, driven_duration_s=driven_s, frequency_hz=frequency,
                phi1_v_lambda_rp=phi1_lambda, sample_period_s=self.config.first_npbs_d_polarizer_sample_period_s,
                output_file=output_file,
            )
            completed = True
        finally:
            sweep.disconnect()
            self._applied_rp_voltages[:] = 0.0
            if completed:
                print(f"Phi1 C-arm polarizer / D-port PAX test saved to {output_file}")
            else:
                print("D-port phi1 analyzer test aborted; outputs were returned to zero.")
