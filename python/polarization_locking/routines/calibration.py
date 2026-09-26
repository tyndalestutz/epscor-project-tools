#!/usr/bin/env python3
from __future__ import annotations

import csv
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ..config import PolarizationLockConfig
from ..control import pax_to_sphere_angles
from ..hardware.pax_interface import PAXController, PAXReading
from ..hardware.rp_interface import PhotodiodeReading, RPController


@dataclass
class SweepPoint:
    rp_out1_voltage: float
    rp_out2_voltage: float
    reading: PAXReading


@dataclass
class BidirectionalSweepPoint:
    sweep_axis: str
    bias_axis: str
    bias_rp_voltage: float
    direction: str
    sweep_rp_voltage: float
    rp_out1_voltage: float
    rp_out2_voltage: float
    reading: PAXReading


@dataclass
class DiagnosticPoint:
    test_name: str
    phase_axis: str
    phase_fraction: float
    sample_index: int
    rp_out1_voltage: float
    rp_out2_voltage: float
    reading: PAXReading


@dataclass
class IntensityDiagnosticPoint:
    sweep_axis: str
    sweep_rp_voltage: float
    rp_out1_voltage: float
    rp_out2_voltage: float
    photodiode: PhotodiodeReading
    reading: PAXReading


@dataclass
class Phi2PathBalancePoint:
    condition: str
    sweep_rp_voltage: float
    photodiode: PhotodiodeReading
    reading: PAXReading


@dataclass
class PAXPathHoldPoint:
    condition: str
    elapsed_s: float
    photodiode: PhotodiodeReading
    reading: PAXReading


@dataclass
class PowerBalancePoint:
    condition: str
    elapsed_s: float
    phi2_rp_command_estimated_v: float
    photodiode: PhotodiodeReading
    reading: PAXReading


@dataclass
class FirstNPBSDPoint:
    """PAX observation at D, the reflected output of the first NPBS."""

    stage: str
    elapsed_s: float
    phi1_rp_command_estimated_v: float
    photodiode: PhotodiodeReading
    reading: PAXReading


@dataclass
class FirstNPBSDIsolationPoint:
    """Fixed-actuator D-port observation for one A/B blocking condition."""

    condition: str
    elapsed_s: float
    photodiode: PhotodiodeReading
    reading: PAXReading


@dataclass
class Phi1StepMapPoint:
    """One settled PAX sample for a static OUT1/phi1 calibration step at D."""

    direction: str
    step_index: int
    sample_index: int
    elapsed_s: float
    phi1_rp_voltage: float
    photodiode: PhotodiodeReading
    reading: PAXReading


@dataclass
class FieldModelCalibrationPoint:
    """One deliberately structured A/B/both two-axis calibration sample."""

    phi1_bias_index: int
    phi1_rp_voltage: float
    repeat_index: int
    direction: str
    condition: str
    phi2_rp_voltage: float
    photodiode: PhotodiodeReading
    reading: PAXReading


@dataclass
class Phi1FringeMapPoint:
    """One both-path sample for extracting fringe phase versus phi1 voltage."""

    phi1_index: int
    phi1_direction: str
    phi1_rp_voltage: float
    phi2_direction: str
    phi2_rp_voltage: float
    photodiode: PhotodiodeReading
    reading: PAXReading


class CalibrationSweep:
    def __init__(
        self,
        config: PolarizationLockConfig,
        *,
        rp: RPController | None = None,
        pax: PAXController | None = None,
    ) -> None:
        if (rp is None) != (pax is None):
            raise ValueError("Provide both RP and PAX controllers, or neither")
        self.config = config
        self.rp = rp or RPController(config)
        self.pax = pax or PAXController(config)
        self._owns_connections = rp is None and pax is None
        self.points: list[SweepPoint] = []

    def connect(self) -> None:
        if self._owns_connections:
            self.rp.connect()
            self.pax.connect()

    def disconnect(self) -> None:
        self.rp.set_output_zero()
        if self._owns_connections:
            self.pax.disconnect()
            self.rp.disconnect()

    def run_axis_sweep(
        self,
        axis: str,
        rp_values: Optional[list[float]] = None,
        repeats: int = 3,
        settle_s: float = 0.2,
        output_file: Optional[str] = None,
    ) -> list[SweepPoint]:
        """Sweep one RP output while holding the other at zero.

        ``axis`` is ``phi1`` (OUT1) or ``phi2`` (OUT2).  Separate sweeps make
        it straightforward to verify the model's expected u/v separation and
        identify cross-coupling before trying a rough target move.
        """
        if axis not in {"phi1", "phi2"}:
            raise ValueError("axis must be 'phi1' or 'phi2'")
        if rp_values is None:
            rp_values = [i / 100.0 for i in range(11)]

        self.points = []
        low_dop_count = 0
        invalid_dop_count = 0
        try:
            for value in rp_values:
                v1, v2 = (value, 0.0) if axis == "phi1" else (0.0, value)
                for _ in range(repeats):
                    self.rp.set_output_voltage(v1, v2)
                    time.sleep(settle_s)
                    reading = self.pax.read_polarization()
                    if reading.dop < self.config.minimum_dop:
                        low_dop_count += 1
                    if not 0.0 <= reading.dop <= 1.0:
                        invalid_dop_count += 1
                    self.points.append(SweepPoint(rp_out1_voltage=v1, rp_out2_voltage=v2, reading=reading))

        finally:
            if output_file is not None:
                self.save_csv(output_file)

        if low_dop_count:
            print(
                f"Warning: {low_dop_count}/{len(self.points)} sweep readings had DOP below "
                f"{self.config.minimum_dop:.3f}. They were logged, but are not suitable for a rough move."
            )
        if invalid_dop_count:
            print(f"Warning: {invalid_dop_count}/{len(self.points)} sweep readings had an invalid PAX DOP outside [0, 1].")

        return self.points

    def run_bidirectional_sweep(
        self,
        sweep_axis: str,
        bias_voltage: float,
        sweep_values: list[float],
        settle_s: float,
        output_file: Optional[str] = None,
    ) -> list[BidirectionalSweepPoint]:
        """Measure one full V_lambda scan forward and reverse at a fixed bias."""
        if sweep_axis not in {"phi1", "phi2"}:
            raise ValueError("sweep_axis must be 'phi1' or 'phi2'")
        if not sweep_values:
            raise ValueError("sweep_values must not be empty")
        lower = self.config.rp_output_min_voltage
        upper = self.config.rp_output_max_voltage
        if any(value < lower or value > upper for value in [bias_voltage, *sweep_values]):
            raise ValueError(f"Bidirectional-sweep values must remain within [{lower}, {upper}] V")

        bias_axis = "phi2" if sweep_axis == "phi1" else "phi1"
        points: list[BidirectionalSweepPoint] = []
        low_dop_count = 0
        invalid_dop_count = 0
        try:
            for direction, values in (("forward", sweep_values), ("reverse", list(reversed(sweep_values)))):
                for sweep in values:
                    out1, out2 = (sweep, bias_voltage) if sweep_axis == "phi1" else (bias_voltage, sweep)
                    self.rp.set_output_voltage(out1, out2)
                    time.sleep(settle_s)
                    reading = self.pax.read_polarization()
                    low_dop_count += reading.dop < self.config.minimum_dop
                    invalid_dop_count += not 0.0 <= reading.dop <= 1.0
                    points.append(BidirectionalSweepPoint(
                        sweep_axis=sweep_axis, bias_axis=bias_axis, bias_rp_voltage=bias_voltage,
                        direction=direction, sweep_rp_voltage=sweep, rp_out1_voltage=out1,
                        rp_out2_voltage=out2, reading=reading,
                    ))

        finally:
            if output_file is not None:
                self.save_bidirectional_csv(output_file, points)
        if low_dop_count:
            print(f"Warning: {low_dop_count}/{len(points)} bidirectional readings had DOP below {self.config.minimum_dop:.3f}.")
        if invalid_dop_count:
            print(f"Warning: {invalid_dop_count}/{len(points)} bidirectional readings had an invalid PAX DOP outside [0, 1].")
        return points

    def run_intensity_diagnostic(
        self,
        phi1_values: list[float],
        phi2_values: list[float],
        settle_s: float,
        output_file: Optional[str] = None,
    ) -> list[IntensityDiagnosticPoint]:
        """Sweep each actuator alone while logging final-port PD and PAX data.

        The non-swept actuator is held at 0 V RP. This directly tests whether
        either nominal phase axis changes final-output amplitude and whether
        raw PAX DOP covaries with that amplitude.
        """
        if settle_s < 0:
            raise ValueError("settle_s must be non-negative")
        lower = self.config.rp_output_min_voltage
        upper = self.config.rp_output_max_voltage
        values = [*phi1_values, *phi2_values]
        if not values or any(value < lower or value > upper for value in values):
            raise ValueError(f"Intensity-diagnostic values must remain within [{lower}, {upper}] V")

        points: list[IntensityDiagnosticPoint] = []
        low_dop_count = 0
        invalid_dop_count = 0
        try:
            with self.rp.photodiode_monitor() as read_pd:
                for sweep_axis, sweep_values in (("phi1", phi1_values), ("phi2", phi2_values)):
                    for sweep_voltage in sweep_values:
                        out1, out2 = (sweep_voltage, 0.0) if sweep_axis == "phi1" else (0.0, sweep_voltage)
                        self.rp.set_output_voltage(out1, out2)
                        time.sleep(settle_s)
                        photodiode = read_pd()
                        reading = self.pax.read_polarization()
                        low_dop_count += reading.dop < self.config.minimum_dop
                        invalid_dop_count += not 0.0 <= reading.dop <= 1.0
                        points.append(IntensityDiagnosticPoint(
                            sweep_axis=sweep_axis,
                            sweep_rp_voltage=sweep_voltage,
                            rp_out1_voltage=out1,
                            rp_out2_voltage=out2,
                            photodiode=photodiode,
                            reading=reading,
                        ))

        finally:
            if output_file is not None:
                self.save_intensity_diagnostic_csv(output_file, points)
        if low_dop_count:
            print(f"Warning: {low_dop_count}/{len(points)} intensity-diagnostic readings had DOP below {self.config.minimum_dop:.3f}.")
        if invalid_dop_count:
            print(f"Warning: {invalid_dop_count}/{len(points)} intensity-diagnostic readings had invalid raw DOP outside [0, 1].")
        return points

    def run_phi2_path_balance(
        self,
        phi2_values: list[float],
        settle_s: float,
        prepare_condition: Callable[[str], None],
        output_file: Optional[str] = None,
    ) -> list[Phi2PathBalancePoint]:
        """Sweep phi2 with path A only, path B only, and both paths open.

        ``prepare_condition`` is intentionally supplied by the CLI: changing
        which optical path is blocked is a manual bench operation, while the
        PAX and PD are acquired together at every voltage point.
        """
        if not phi2_values:
            raise ValueError("phi2_values must not be empty")
        lower, upper = self.config.rp_output_min_voltage, self.config.rp_output_max_voltage
        if any(value < lower or value > upper for value in phi2_values):
            raise ValueError(f"Phi2 sweep values must remain within [{lower}, {upper}] V")

        points: list[Phi2PathBalancePoint] = []
        try:
            with self.rp.photodiode_monitor() as read_pd:
                for condition in ("path_a_only", "path_b_only", "both_paths"):
                    self.rp.set_output_zero()
                    prepare_condition(condition)
                    for voltage in phi2_values:
                        self.rp.set_output_voltage(0.0, voltage)
                        time.sleep(settle_s)
                        points.append(Phi2PathBalancePoint(
                            condition=condition,
                            sweep_rp_voltage=voltage,
                            photodiode=read_pd(),
                            reading=self.pax.read_polarization(),
                        ))

        finally:
            if output_file is not None:
                self.save_phi2_path_balance_csv(output_file, points)
        return points

    def run_field_model_calibration(
        self,
        *,
        phi1_values: list[float],
        phi2_values: list[float],
        repeats: int,
        settle_s: float,
        prepare_condition: Callable[[str, float, int, str], None],
        output_file: Optional[str] = None,
    ) -> list[FieldModelCalibrationPoint]:
        """Acquire fit-ready A/B/both scans at multiple phi1 biases.

        Conditions are cycled in alternating order and phi2 direction reverses
        on alternate repeats. This is not simultaneous blocking, but it
        brackets slow drift much more fairly than acquiring all A scans, then
        all B scans, then all both-path scans.
        """
        if len(phi1_values) < 3 or not phi2_values:
            raise ValueError("Field-model calibration needs at least three phi1 biases and one phi2 value")
        if repeats < 1 or settle_s < 0.0:
            raise ValueError("repeats must be positive and settle_s non-negative")
        lower, upper = self.config.rp_output_min_voltage, self.config.rp_output_max_voltage
        if any(value < lower or value > upper for value in [*phi1_values, *phi2_values]):
            raise ValueError(f"Field-model RP values must remain within [{lower}, {upper}] V")

        points: list[FieldModelCalibrationPoint] = []
        try:
            with self.rp.photodiode_monitor() as read_pd:
                for bias_index, phi1_voltage in enumerate(phi1_values):
                    for repeat_index in range(repeats):
                        forward = repeat_index % 2 == 0
                        direction = "forward" if forward else "reverse"
                        conditions = ("path_a_only", "path_b_only", "both_paths") if forward else ("both_paths", "path_b_only", "path_a_only")
                        voltages = phi2_values if forward else list(reversed(phi2_values))
                        for condition in conditions:
                            prepare_condition(condition, phi1_voltage, repeat_index, direction)
                            for phi2_voltage in voltages:
                                self.rp.set_output_voltage(phi1_voltage, phi2_voltage)
                                time.sleep(settle_s)
                                points.append(FieldModelCalibrationPoint(
                                    phi1_bias_index=bias_index,
                                    phi1_rp_voltage=phi1_voltage,
                                    repeat_index=repeat_index,
                                    direction=direction,
                                    condition=condition,
                                    phi2_rp_voltage=phi2_voltage,
                                    photodiode=read_pd(),
                                    reading=self.pax.read_polarization(),
                                ))
        finally:
            if output_file is not None:
                self.save_field_model_calibration_csv(output_file, points)
        return points

    def run_phi1_fringe_map(
        self,
        *,
        phi1_values: list[float],
        phi2_values: list[float],
        settle_s: float,
        output_file: Optional[str] = None,
    ) -> list[Phi1FringeMapPoint]:
        """Map the phi2 fringe at dense forward/reverse phi1 commands.

        Both optical paths remain open for the complete measurement. For each
        phi1 value, phi2 is scanned forward on the outward phi1 pass and in
        reverse on the return pass. This isolates phi1 calibration/hysteresis
        from manual beam-block changes.
        """
        if len(phi1_values) < 3 or len(phi2_values) < 4 or settle_s < 0.0:
            raise ValueError("Phi1 fringe map needs >=3 phi1 values, >=4 phi2 values, and non-negative settling")
        lower, upper = self.config.rp_output_min_voltage, self.config.rp_output_max_voltage
        if any(value < lower or value > upper for value in [*phi1_values, *phi2_values]):
            raise ValueError(f"Phi1 fringe-map RP values must remain within [{lower}, {upper}] V")
        points: list[Phi1FringeMapPoint] = []
        passes = (("forward", phi1_values, "forward", phi2_values), ("reverse", list(reversed(phi1_values)), "reverse", list(reversed(phi2_values))))
        try:
            with self.rp.photodiode_monitor() as read_pd:
                for phi1_direction, biases, phi2_direction, phi2_scan in passes:
                    for index, phi1_voltage in enumerate(biases):
                        for phi2_voltage in phi2_scan:
                            self.rp.set_output_voltage(phi1_voltage, phi2_voltage)
                            time.sleep(settle_s)
                            points.append(Phi1FringeMapPoint(
                                phi1_index=index,
                                phi1_direction=phi1_direction,
                                phi1_rp_voltage=phi1_voltage,
                                phi2_direction=phi2_direction,
                                phi2_rp_voltage=phi2_voltage,
                                photodiode=read_pd(),
                                reading=self.pax.read_polarization(),
                            ))
        finally:
            if output_file is not None:
                self.save_phi1_fringe_map_csv(output_file, points)
        return points

    def run_pax_path_hold(
        self,
        duration_s: float,
        sample_period_s: float,
        prepare_condition: Callable[[str], None],
        output_file: Optional[str] = None,
    ) -> list[PAXPathHoldPoint]:
        """Acquire fixed-state PAX telemetry for each manually selected path."""
        if duration_s <= 0.0 or sample_period_s <= 0.0:
            raise ValueError("duration_s and sample_period_s must be positive")
        points: list[PAXPathHoldPoint] = []
        try:
            with self.rp.photodiode_monitor() as read_pd:
                for condition in ("path_a_only", "path_b_only", "both_paths"):
                    self.rp.set_output_zero()
                    prepare_condition(condition)
                    started = time.monotonic()
                    next_sample = started
                    while True:
                        now = time.monotonic()
                        if now - started >= duration_s:
                            break
                        if now < next_sample:
                            time.sleep(next_sample - now)
                        points.append(PAXPathHoldPoint(
                            condition=condition,
                            elapsed_s=time.monotonic() - started,
                            photodiode=read_pd(),
                            reading=self.pax.read_polarization(),
                        ))
                        next_sample += sample_period_s
        finally:
            if output_file is not None:
                self.save_pax_path_hold_csv(output_file, points)
        return points

    def run_phi2_power_balance(
        self,
        duration_s: float,
        frequency_hz: float,
        phi2_v_lambda_rp: float,
        sample_period_s: float,
        prepare_condition: Callable[[str], None],
        output_file: Optional[str] = None,
    ) -> list[PowerBalancePoint]:
        """Measure simultaneous PD/PAX fringe contrast for A, B, and both paths.

        Phi2 is driven continuously by RP OUT2.  The logged command is the
        known generator waveform evaluated from the local trigger time; it is
        not a separate analog monitor measurement.
        """
        if duration_s <= 0.0 or frequency_hz <= 0.0 or sample_period_s <= 0.0:
            raise ValueError("duration, frequency, and sample period must be positive")
        lower, upper = self.config.rp_output_min_voltage, self.config.rp_output_max_voltage
        if not lower <= phi2_v_lambda_rp <= upper:
            raise ValueError(f"phi2 V_lambda RP command must be in [{lower}, {upper}] V")

        points: list[PowerBalancePoint] = []
        offset = amplitude = phi2_v_lambda_rp / 2.0
        try:
            with self.rp.photodiode_monitor() as read_pd:
                for condition in ("path_a_only", "path_b_only", "both_paths"):
                    self.rp.set_output_zero()
                    prepare_condition(condition)
                    started = time.monotonic()
                    self.rp.set_phi2_sine(offset=offset, amplitude=amplitude, frequency_hz=frequency_hz)
                    next_sample = started
                    while True:
                        now = time.monotonic()
                        elapsed = now - started
                        if elapsed >= duration_s:
                            break
                        if now < next_sample:
                            time.sleep(next_sample - now)
                        elapsed = time.monotonic() - started
                        command = offset + amplitude * math.sin(2.0 * math.pi * frequency_hz * elapsed)
                        points.append(PowerBalancePoint(
                            condition=condition,
                            elapsed_s=elapsed,
                            phi2_rp_command_estimated_v=command,
                            photodiode=read_pd(),
                            reading=self.pax.read_polarization(),
                        ))
                        next_sample += sample_period_s
                    self.rp.set_output_zero()
        finally:
            if output_file is not None:
                self.save_phi2_power_balance_csv(output_file, points)
        return points

    def run_first_npbs_d_test(
        self,
        *,
        static_duration_s: float,
        driven_duration_s: float,
        frequency_hz: float,
        phi1_v_lambda_rp: float,
        sample_period_s: float,
        output_file: Optional[str] = None,
        save_csv: Callable | None = None,
    ) -> list[FirstNPBSDPoint]:
        """Test the ideal D-port equatorial trajectory under phi1 modulation.

        The static stage holds phi1=phi2=0.  The driven stage applies an OUT1
        sine from 0 to one calibrated phi1 V_lambda; OUT2 stays at zero.
        PAX is expected to be physically connected at D for the whole run.
        """
        if min(static_duration_s, driven_duration_s, frequency_hz, phi1_v_lambda_rp, sample_period_s) <= 0.0:
            raise ValueError("durations, frequency, V_lambda, and sample period must be positive")
        lower, upper = self.config.rp_output_min_voltage, self.config.rp_output_max_voltage
        if not lower <= phi1_v_lambda_rp <= upper:
            raise ValueError(f"phi1 V_lambda RP command must be in [{lower}, {upper}] V")
        points: list[FirstNPBSDPoint] = []

        def acquire(stage: str, duration_s: float, started: float, command) -> None:
            next_sample = started
            while True:
                now = time.monotonic()
                elapsed = now - started
                if elapsed >= duration_s:
                    return
                if now < next_sample:
                    time.sleep(next_sample - now)
                elapsed = time.monotonic() - started
                points.append(FirstNPBSDPoint(
                    stage=stage, elapsed_s=elapsed, phi1_rp_command_estimated_v=command(elapsed),
                    photodiode=read_pd(), reading=self.pax.read_polarization(),
                ))
                next_sample += sample_period_s

        offset = amplitude = phi1_v_lambda_rp / 2.0
        try:
            with self.rp.photodiode_monitor() as read_pd:
                self.rp.set_output_zero()
                static_started = time.monotonic()
                acquire("static", static_duration_s, static_started, lambda _elapsed: 0.0)
                self.rp.set_phi1_sine(offset=offset, amplitude=amplitude, frequency_hz=frequency_hz)
                driven_started = time.monotonic()
                acquire(
                    "phi1_sine", driven_duration_s, driven_started,
                    lambda elapsed: offset + amplitude * math.sin(2.0 * math.pi * frequency_hz * elapsed),
                )
                self.rp.set_output_zero()
        finally:
            if output_file is not None:
                (save_csv or self.save_first_npbs_d_csv)(output_file, points, frequency_hz=frequency_hz, phi1_v_lambda_rp=phi1_v_lambda_rp)
        return points

    def run_first_npbs_d_isolation(
        self,
        *,
        duration_s: float,
        sample_period_s: float,
        prepare_condition: Callable[[str], None],
        output_file: Optional[str] = None,
    ) -> list[FirstNPBSDIsolationPoint]:
        """Hold A-only, B-only, and both-open states while PAX observes D.

        At D and zero actuator command, ideal A-only and B-only inputs have
        fixed orthogonal Stokes states; only both-open can show relative-phase
        motion around the equator. This intentionally uses manual blocking at
        the *input* A/B paths, not downstream C/D points.
        """
        if duration_s <= 0.0 or sample_period_s <= 0.0:
            raise ValueError("duration_s and sample_period_s must be positive")
        points: list[FirstNPBSDIsolationPoint] = []
        try:
            with self.rp.photodiode_monitor() as read_pd:
                for condition in ("path_a_only", "path_b_only", "both_paths"):
                    self.rp.set_output_zero()
                    prepare_condition(condition)
                    started = time.monotonic()
                    next_sample = started
                    while True:
                        now = time.monotonic()
                        if now - started >= duration_s:
                            break
                        if now < next_sample:
                            time.sleep(next_sample - now)
                        points.append(FirstNPBSDIsolationPoint(
                            condition=condition, elapsed_s=time.monotonic() - started,
                            photodiode=read_pd(), reading=self.pax.read_polarization(),
                        ))
                        next_sample += sample_period_s
        finally:
            if output_file is not None:
                self.save_first_npbs_d_isolation_csv(output_file, points)
        return points

    def run_phi1_step_map(
        self,
        *,
        phi1_v_lambda_rp: float,
        points: int,
        settle_s: float,
        samples_per_step: int,
        inter_sample_s: float,
        output_file: Optional[str] = None,
    ) -> list[Phi1StepMapPoint]:
        """Map held OUT1 voltages to D-port equatorial phase, forward/reverse.

        OUT2 remains at zero.  Every PAX measurement follows a static command
        and an explicit settle interval; this intentionally avoids assigning a
        host timestamp to an asynchronously started RP waveform.
        """
        if points < 3:
            raise ValueError("points must be at least 3")
        if min(phi1_v_lambda_rp, settle_s, samples_per_step, inter_sample_s) <= 0:
            raise ValueError("V_lambda, timing values, and samples_per_step must be positive")
        lower, upper = self.config.rp_output_min_voltage, self.config.rp_output_max_voltage
        if not lower <= phi1_v_lambda_rp <= upper:
            raise ValueError(f"phi1 V_lambda RP command must be in [{lower}, {upper}] V")
        values = np.linspace(0.0, phi1_v_lambda_rp, points)
        result: list[Phi1StepMapPoint] = []
        started = time.monotonic()
        try:
            with self.rp.photodiode_monitor() as read_pd:
                for direction, commands in (("forward", values), ("reverse", values[::-1])):
                    for step_index, command in enumerate(commands):
                        self.rp.set_output_voltage(float(command), 0.0)
                        time.sleep(settle_s)
                        for sample_index in range(samples_per_step):
                            if sample_index:
                                time.sleep(inter_sample_s)
                            result.append(Phi1StepMapPoint(
                                direction=direction,
                                step_index=step_index,
                                sample_index=sample_index,
                                elapsed_s=time.monotonic() - started,
                                phi1_rp_voltage=float(command),
                                photodiode=read_pd(),
                                reading=self.pax.read_polarization(),
                            ))
                self.rp.set_output_zero()
        finally:
            if output_file is not None:
                self.save_phi1_step_map_csv(output_file, result, phi1_v_lambda_rp=phi1_v_lambda_rp)
        return result

    def run_first_npbs_d_polarizer_test(
        self,
        *,
        static_duration_s: float,
        driven_duration_s: float,
        frequency_hz: float,
        phi1_v_lambda_rp: float,
        sample_period_s: float,
        output_file: Optional[str] = None,
    ) -> list[FirstNPBSDPoint]:
        """Drive phi1 with a polarizer in C, PAX at D, and PD at final F.

        PAX observes the unfiltered D-port Stokes trajectory.  The polarizer
        acts in C before the final NPBS, so the final-F PD is the intentional
        phase-to-power analyzer.  Its exact fringe phase/contrast depends on
        the manually selected polarizer axis.
        """
        # This is deliberately a separate public method so its CSV contract and
        # guided CLI language stay tied to the physical polarizer geometry.
        return self.run_first_npbs_d_test(
            static_duration_s=static_duration_s, driven_duration_s=driven_duration_s,
            frequency_hz=frequency_hz, phi1_v_lambda_rp=phi1_v_lambda_rp,
            sample_period_s=sample_period_s, output_file=output_file,
            save_csv=self.save_first_npbs_d_polarizer_csv,
        )

    def run_static_diagnostic_suite(
        self,
        phi1_values: list[tuple[float, float]],
        phi2_values: list[tuple[float, float]],
        phi1_bias_voltage: float,
        phi2_bias_voltage: float,
        settle_s: float,
        hold_s: float,
        sample_period_s: float,
        output_file: Optional[str] = None,
    ) -> list[DiagnosticPoint]:
        """Characterize stable PAX behavior at baseline and fixed phase states.

        ``phi1_values`` and ``phi2_values`` contain ``(phase_fraction,
        rp_voltage)`` pairs. Each condition is held before repeatedly reading
        the PAX, so DOP quality can be distinguished from a moving-state
        artifact.
        """
        if settle_s < 0 or hold_s <= 0 or sample_period_s <= 0:
            raise ValueError("settle_s must be non-negative; hold_s and sample_period_s must be positive")
        lower = self.config.rp_output_min_voltage
        upper = self.config.rp_output_max_voltage
        commanded = [0.0, phi1_bias_voltage, phi2_bias_voltage]
        commanded.extend(value for _, value in phi1_values)
        commanded.extend(value for _, value in phi2_values)
        if any(value < lower or value > upper for value in commanded):
            raise ValueError(f"Diagnostic-suite values must remain within [{lower}, {upper}] V")

        conditions = [("baseline", "none", 0.0, 0.0, 0.0)]
        conditions.extend(("phi1_hold", "phi1", fraction, value, phi1_bias_voltage) for fraction, value in phi1_values)
        conditions.extend(("phi2_hold", "phi2", fraction, phi2_bias_voltage, value) for fraction, value in phi2_values)

        points: list[DiagnosticPoint] = []
        output = None
        writer = None
        if output_file is not None:
            output = Path(output_file).open("w", newline="")
            writer = csv.writer(output)
            writer.writerow([
                "test_name", "phase_axis", "phase_fraction", "sample_index", "rp_out1_v", "rp_out2_v",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            ])
            output.flush()
        try:
            for test_name, phase_axis, fraction, out1, out2 in conditions:
                print(
                    f"Diagnostic {test_name}: out1={out1:.4f} V, out2={out2:.4f} V; "
                    f"settling {settle_s:.1f} s, logging {hold_s:.1f} s"
                )
                self.rp.set_output_voltage(out1, out2)
                time.sleep(settle_s)
                condition_points: list[DiagnosticPoint] = []
                deadline = time.monotonic() + hold_s
                while time.monotonic() < deadline:
                    reading = self.pax.read_polarization()
                    point = DiagnosticPoint(
                        test_name=test_name, phase_axis=phase_axis, phase_fraction=fraction,
                        sample_index=len(condition_points), rp_out1_voltage=out1,
                        rp_out2_voltage=out2, reading=reading,
                    )
                    condition_points.append(point)
                    points.append(point)
                    if writer is not None:
                        sphere = pax_to_sphere_angles((reading.theta, reading.eta))
                        writer.writerow([
                            point.test_name, point.phase_axis, point.phase_fraction, point.sample_index,
                            point.rp_out1_voltage, point.rp_out2_voltage, reading.timestamp,
                            reading.theta, reading.eta, reading.s1, reading.s2, reading.s3, reading.dop, reading.ptotal,
                            sphere.u, sphere.v,
                        ])
                        output.flush()
                    time.sleep(sample_period_s)

                dops = [point.reading.dop for point in condition_points]
                low = sum(dop < self.config.minimum_dop for dop in dops)
                invalid = sum(not 0.0 <= dop <= 1.0 for dop in dops)
                print(
                    f"  {len(condition_points)} samples; DOP median={statistics.median(dops):.3f}, "
                    f"range={min(dops):.3f}..{max(dops):.3f}, low={low}, invalid={invalid}"
                )
        finally:
            if output is not None:
                output.close()
        return points

    def save_csv(self, output_file: str) -> None:
        path = Path(output_file)
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["rp_out1_v", "rp_out2_v", "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "u", "v"])
            for point in self.points:
                sphere = pax_to_sphere_angles((point.reading.theta, point.reading.eta))
                writer.writerow([
                    point.rp_out1_voltage,
                    point.rp_out2_voltage,
                    point.reading.timestamp,
                    point.reading.theta,
                    point.reading.eta,
                    point.reading.s1,
                    point.reading.s2,
                    point.reading.s3,
                    point.reading.dop,
                    sphere.u,
                    sphere.v,
                ])

    def save_bidirectional_csv(self, output_file: str, points: list[BidirectionalSweepPoint]) -> None:
        path = Path(output_file)
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "sweep_axis", "bias_axis", "bias_rp_v", "direction", "sweep_rp_v", "rp_out1_v", "rp_out2_v",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "u", "v",
            ])
            for point in points:
                sphere = pax_to_sphere_angles((point.reading.theta, point.reading.eta))
                writer.writerow([
                    point.sweep_axis, point.bias_axis, point.bias_rp_voltage, point.direction,
                    point.sweep_rp_voltage, point.rp_out1_voltage, point.rp_out2_voltage,
                    point.reading.timestamp, point.reading.theta, point.reading.eta,
                    point.reading.s1, point.reading.s2, point.reading.s3, point.reading.dop,
                    sphere.u, sphere.v,
                ])

    def save_intensity_diagnostic_csv(self, output_file: str, points: list[IntensityDiagnosticPoint]) -> None:
        path = Path(output_file)
        nd_od = self.config.pd_nd_optical_density
        nd_transmission = 10.0 ** (-nd_od)
        # PAX ptotal and the RP photodiode are different instruments with no
        # shared absolute-power calibration. Normalize each actuator sweep to
        # its own observed [min, max] range so their *responses* can be
        # compared without conflating detector units, responsivity, or the PD
        # arm's neutral-density attenuation.
        normalized: dict[int, tuple[float, float]] = {}
        for axis in ("phi1", "phi2"):
            axis_points = [point for point in points if point.sweep_axis == axis]
            pd_values = [point.photodiode.mean_voltage for point in axis_points]
            ptotal_values = [point.reading.ptotal for point in axis_points]

            def scale(values: list[float]) -> list[float]:
                low, high = min(values), max(values)
                span = high - low
                return [(value - low) / span if span > 0.0 else 0.5 for value in values]

            for point, pd_norm, ptotal_norm in zip(axis_points, scale(pd_values), scale(ptotal_values)):
                normalized[id(point)] = (pd_norm, ptotal_norm)
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "sweep_axis", "sweep_rp_v", "rp_out1_v", "rp_out2_v",
                "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
                "pd_nd_optical_density", "pd_nd_transmission", "pd_pre_nd_equivalent_v", "pd_normalized",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
                "pax_ptotal_normalized",
            ])
            for point in points:
                sphere = pax_to_sphere_angles((point.reading.theta, point.reading.eta))
                pd_norm, ptotal_norm = normalized[id(point)]
                writer.writerow([
                    point.sweep_axis, point.sweep_rp_voltage, point.rp_out1_voltage, point.rp_out2_voltage,
                    point.photodiode.mean_voltage, point.photodiode.std_voltage,
                    point.photodiode.min_voltage, point.photodiode.max_voltage, point.photodiode.sample_count,
                    nd_od, nd_transmission, point.photodiode.mean_voltage / nd_transmission, pd_norm,
                    point.reading.timestamp, point.reading.theta, point.reading.eta,
                    point.reading.s1, point.reading.s2, point.reading.s3, point.reading.dop, point.reading.ptotal,
                    sphere.u, sphere.v, ptotal_norm,
                ])

    def save_phi2_path_balance_csv(self, output_file: str, points: list[Phi2PathBalancePoint]) -> None:
        """Persist raw simultaneous measurements; plotting normalizes per condition."""
        path = Path(output_file)
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "condition", "sweep_axis", "sweep_rp_v", "rp_out1_v", "rp_out2_v",
                "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
                "pd_nd_optical_density", "pd_nd_transmission", "pax_timestamp",
                "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            ])
            transmission = 10.0 ** (-self.config.pd_nd_optical_density)
            for point in points:
                sphere = pax_to_sphere_angles((point.reading.theta, point.reading.eta))
                pd = point.photodiode
                reading = point.reading
                writer.writerow([
                    point.condition, "phi2", point.sweep_rp_voltage, 0.0, point.sweep_rp_voltage,
                    pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    self.config.pd_nd_optical_density, transmission, reading.timestamp,
                    reading.theta, reading.eta, reading.s1, reading.s2, reading.s3, reading.dop,
                    reading.ptotal, sphere.u, sphere.v,
                ])

    def save_pax_path_hold_csv(self, output_file: str, points: list[PAXPathHoldPoint]) -> None:
        path = Path(output_file)
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "condition", "elapsed_s", "rp_out1_v", "rp_out2_v",
                "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
                "pax_timestamp", "pax_revisions", "pax_adc_min", "pax_adc_max", "pax_rev_time",
                "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            ])
            for point in points:
                pd, reading = point.photodiode, point.reading
                sphere = pax_to_sphere_angles((reading.theta, reading.eta))
                writer.writerow([
                    point.condition, point.elapsed_s, 0.0, 0.0,
                    pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    reading.timestamp, reading.revisions, reading.adc_min, reading.adc_max, reading.rev_time,
                    reading.theta, reading.eta, reading.s1, reading.s2, reading.s3, reading.dop,
                    reading.ptotal, sphere.u, sphere.v,
                ])

    def save_field_model_calibration_csv(self, output_file: str, points: list[FieldModelCalibrationPoint]) -> None:
        """Save the stable input contract for ``fit_phi2_interference.py``."""
        path = Path(output_file)
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "phi1_bias_index", "phi1_rp_v", "repeat_index", "direction", "condition", "phi2_rp_v",
                "rp_out1_v", "rp_out2_v", "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
                "pd_nd_optical_density", "pd_nd_transmission", "pax_timestamp", "theta", "eta", "s1", "s2", "s3",
                "dop", "pax_ptotal", "u", "v",
            ])
            transmission = 10.0 ** (-self.config.pd_nd_optical_density)
            for point in points:
                pd, reading = point.photodiode, point.reading
                sphere = pax_to_sphere_angles((reading.theta, reading.eta))
                writer.writerow([
                    point.phi1_bias_index, point.phi1_rp_voltage, point.repeat_index, point.direction, point.condition,
                    point.phi2_rp_voltage, point.phi1_rp_voltage, point.phi2_rp_voltage,
                    pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    self.config.pd_nd_optical_density, transmission, reading.timestamp, reading.theta, reading.eta,
                    reading.s1, reading.s2, reading.s3, reading.dop, reading.ptotal, sphere.u, sphere.v,
                ])

    def save_phi1_fringe_map_csv(self, output_file: str, points: list[Phi1FringeMapPoint]) -> None:
        """Save a standalone, directly fit-ready phi1 fringe-map data set."""
        path = Path(output_file)
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "condition", "phi1_index", "phi1_direction", "phi1_rp_v", "phi2_direction", "phi2_rp_v",
                "rp_out1_v", "rp_out2_v", "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            ])
            for point in points:
                pd, reading = point.photodiode, point.reading
                sphere = pax_to_sphere_angles((reading.theta, reading.eta))
                writer.writerow([
                    "both_paths", point.phi1_index, point.phi1_direction, point.phi1_rp_voltage,
                    point.phi2_direction, point.phi2_rp_voltage, point.phi1_rp_voltage, point.phi2_rp_voltage,
                    pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    reading.timestamp, reading.theta, reading.eta, reading.s1, reading.s2, reading.s3, reading.dop,
                    reading.ptotal, sphere.u, sphere.v,
                ])

    def save_phi2_power_balance_csv(self, output_file: str, points: list[PowerBalancePoint]) -> None:
        """Save raw signals plus per-condition normalized detector response."""
        path = Path(output_file)
        normalized: dict[int, tuple[float, float]] = {}
        summaries: dict[str, tuple[float, float, float, float]] = {}

        def normalize(values: list[float]) -> tuple[list[float], float, float]:
            low, high = min(values), max(values)
            span = high - low
            normalized_values = [(value - low) / span if span > 0.0 else 0.5 for value in values]
            # Long captures occasionally include isolated acquisition spikes.
            # Use robust 5–95% extrema for the reported fringe contrast.
            lower, upper = statistics.quantiles(values, n=20)[0], statistics.quantiles(values, n=20)[-1]
            contrast = (upper - lower) / (upper + lower) if upper + lower != 0.0 else float("nan")
            return normalized_values, float(statistics.fmean(values)), contrast

        for condition in ("path_a_only", "path_b_only", "both_paths"):
            group = [point for point in points if point.condition == condition]
            pd_norm, pd_mean, pd_contrast = normalize([point.photodiode.mean_voltage for point in group])
            pax_norm, pax_mean, pax_contrast = normalize([point.reading.ptotal for point in group])
            summaries[condition] = (pd_mean, pax_mean, pd_contrast, pax_contrast)
            for point, pd_value, pax_value in zip(group, pd_norm, pax_norm):
                normalized[id(point)] = (pd_value, pax_value)

        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "condition", "elapsed_s", "phi2_rp_command_estimated_v", "rp_out1_v",
                "phi2_sine_frequency_hz", "phi2_sine_offset_v", "phi2_sine_amplitude_v",
                "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count", "pd_normalized",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "pax_ptotal_normalized",
                "pd_condition_mean_v", "pax_ptotal_condition_mean", "pd_contrast_5_95", "pax_ptotal_contrast_5_95", "u", "v",
            ])
            frequency = self.config.power_balance_phi2_frequency_hz
            # Use the configured V_lambda exactly: observed extrema can miss a
            # sine peak between PAX acquisitions.
            if self.config.phi2_v_lambda is None or self.config.phi2_actuator_volts_per_rp_volt is None:
                raise RuntimeError("Phi2 V_lambda and output gain are required to save power-balance data")
            offset = amplitude = self.config.phi2_v_lambda / self.config.phi2_actuator_volts_per_rp_volt / 2.0
            for point in points:
                pd_norm, pax_norm = normalized[id(point)]
                pd_mean, pax_mean, pd_contrast, pax_contrast = summaries[point.condition]
                pd, reading = point.photodiode, point.reading
                sphere = pax_to_sphere_angles((reading.theta, reading.eta))
                writer.writerow([
                    point.condition, point.elapsed_s, point.phi2_rp_command_estimated_v, 0.0,
                    frequency, offset, amplitude, pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage,
                    pd.sample_count, pd_norm, reading.timestamp, reading.theta, reading.eta, reading.s1, reading.s2,
                    reading.s3, reading.dop, reading.ptotal, pax_norm, pd_mean, pax_mean, pd_contrast, pax_contrast, sphere.u, sphere.v,
                ])

    def save_first_npbs_d_csv(
        self, output_file: str, points: list[FirstNPBSDPoint], *, frequency_hz: float, phi1_v_lambda_rp: float,
    ) -> None:
        """Save raw D-port telemetry and the ideal Jones/Stokes reference."""
        path = Path(output_file)
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "stage", "elapsed_s", "phi1_rp_command_estimated_v", "phi1_ideal_rad", "phi2_rp_v",
                "sine_frequency_hz", "phi1_vlambda_rp_v", "ideal_s1", "ideal_s2", "ideal_s3",
                "ideal_u_rad", "ideal_v_rad", "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            ])
            for point in points:
                phi1 = 2.0 * math.pi * point.phi1_rp_command_estimated_v / phi1_v_lambda_rp
                # D = (i exp(i phi1), 1) / sqrt(2) in the transmitted-first
                # convention. A static unknown phase just rotates S2/S3.
                ideal_s1, ideal_s2, ideal_s3 = 0.0, -math.sin(phi1), math.cos(phi1)
                sphere = pax_to_sphere_angles((point.reading.theta, point.reading.eta))
                pd, reading = point.photodiode, point.reading
                writer.writerow([
                    point.stage, point.elapsed_s, point.phi1_rp_command_estimated_v, phi1, 0.0,
                    frequency_hz, phi1_v_lambda_rp, ideal_s1, ideal_s2, ideal_s3,
                    math.atan2(ideal_s3, ideal_s2), math.pi / 2.0,
                    pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    reading.timestamp, reading.theta, reading.eta, reading.s1, reading.s2, reading.s3,
                    reading.dop, reading.ptotal, sphere.u, sphere.v,
                ])

    def save_first_npbs_d_isolation_csv(self, output_file: str, points: list[FirstNPBSDIsolationPoint]) -> None:
        """Save raw D-port A/B isolation telemetry with ideal reference states."""
        ideal = {
            "path_a_only": (-1.0, 0.0, 0.0),  # D = i A / sqrt(2): x-polarized
            "path_b_only": (1.0, 0.0, 0.0),   # D = B / sqrt(2): y-polarized
            "both_paths": (0.0, 0.0, 0.0),    # phase unknown; ideal locus is S1=0 equator
        }
        path = Path(output_file)
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "condition", "elapsed_s", "rp_out1_v", "rp_out2_v", "ideal_s1", "ideal_s2", "ideal_s3",
                "ideal_description", "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            ])
            for point in points:
                reference = ideal[point.condition]
                description = {
                    "path_a_only": "ideal fixed x state at D: S=(-1,0,0)",
                    "path_b_only": "ideal fixed y state at D: S=(+1,0,0)",
                    "both_paths": "ideal equatorial locus at D: S1=0; phase may vary",
                }[point.condition]
                pd, reading = point.photodiode, point.reading
                sphere = pax_to_sphere_angles((reading.theta, reading.eta))
                writer.writerow([
                    point.condition, point.elapsed_s, 0.0, 0.0, *reference, description,
                    pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    reading.timestamp, reading.theta, reading.eta, reading.s1, reading.s2, reading.s3,
                    reading.dop, reading.ptotal, sphere.u, sphere.v,
                ])

    def save_phi1_step_map_csv(
        self, output_file: str, points: list[Phi1StepMapPoint], *, phi1_v_lambda_rp: float,
    ) -> None:
        """Persist raw held-step telemetry and the derived D-equatorial phase."""
        path = Path(output_file)
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "direction", "step_index", "sample_index", "elapsed_s", "phi1_rp_voltage", "phi1_vlambda_rp_v",
                "phi1_ideal_rad", "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "equatorial_radius", "equatorial_phase_rad",
                "dop", "pax_ptotal", "u", "v",
            ])
            for point in points:
                reading, pd = point.reading, point.photodiode
                phase = math.atan2(reading.s3, -reading.s2)
                radius = math.hypot(reading.s2, reading.s3)
                sphere = pax_to_sphere_angles((reading.theta, reading.eta))
                writer.writerow([
                    point.direction, point.step_index, point.sample_index, point.elapsed_s, point.phi1_rp_voltage,
                    phi1_v_lambda_rp, 2.0 * math.pi * point.phi1_rp_voltage / phi1_v_lambda_rp,
                    pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    reading.timestamp, reading.theta, reading.eta, reading.s1, reading.s2, reading.s3, radius, phase,
                    reading.dop, reading.ptotal, sphere.u, sphere.v,
                ])

    def save_first_npbs_d_polarizer_csv(
        self, output_file: str, points: list[FirstNPBSDPoint], *, frequency_hz: float, phi1_v_lambda_rp: float,
    ) -> None:
        """Save raw D-port polarization and C-polarizer/final-F PD telemetry."""
        path = Path(output_file)
        with path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "stage", "elapsed_s", "phi1_rp_command_estimated_v", "phi1_ideal_rad", "phi2_rp_v",
                "sine_frequency_hz", "phi1_vlambda_rp_v", "ideal_d_s1", "ideal_d_s2", "ideal_d_s3",
                "c_polarizer_expected", "pd_final_f_ideal_relative_power", "pd_mean_v", "pd_std_v", "pd_min_v", "pd_max_v", "pd_sample_count",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            ])
            for point in points:
                phi1 = 2.0 * math.pi * point.phi1_rp_command_estimated_v / phi1_v_lambda_rp
                # PAX is directly at D: its ideal state is equatorial. The
                # C-arm polarizer produces the PD fringe at final F; because
                # its installed angle is manually chosen, do not encode a
                # false numerical contrast prediction here.
                ideal_d_s1, ideal_d_s2, ideal_d_s3 = 0.0, -math.sin(phi1), math.cos(phi1)
                pd, reading = point.photodiode, point.reading
                sphere = pax_to_sphere_angles((reading.theta, reading.eta))
                writer.writerow([
                    point.stage, point.elapsed_s, point.phi1_rp_command_estimated_v, phi1, 0.0,
                    frequency_hz, phi1_v_lambda_rp, ideal_d_s1, ideal_d_s2, ideal_d_s3,
                    "linear polarizer installed in C before final NPBS", float("nan"),
                    pd.mean_voltage, pd.std_voltage, pd.min_voltage, pd.max_voltage, pd.sample_count,
                    reading.timestamp, reading.theta, reading.eta, reading.s1, reading.s2, reading.s3,
                    reading.dop, reading.ptotal, sphere.u, sphere.v,
                ])

    def save_diagnostic_csv(self, output_file: str, points: list[DiagnosticPoint]) -> None:
        path = Path(output_file)
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "test_name", "phase_axis", "phase_fraction", "sample_index", "rp_out1_v", "rp_out2_v",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            ])
            for point in points:
                sphere = pax_to_sphere_angles((point.reading.theta, point.reading.eta))
                writer.writerow([
                    point.test_name, point.phase_axis, point.phase_fraction, point.sample_index,
                    point.rp_out1_voltage, point.rp_out2_voltage, point.reading.timestamp,
                    point.reading.theta, point.reading.eta, point.reading.s1, point.reading.s2,
                    point.reading.s3, point.reading.dop, point.reading.ptotal, sphere.u, sphere.v,
                ])
