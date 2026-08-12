#!/usr/bin/env python3
from __future__ import annotations

import csv
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    from .config import PolarizationLockConfig
    from .control import pax_to_sphere_angles
    from .pax_interface import PAXController, PAXReading
    from .rp_interface import PhotodiodeReading, RPController
except ImportError:  # pragma: no cover - support direct execution
    from config import PolarizationLockConfig
    from control import pax_to_sphere_angles
    from pax_interface import PAXController, PAXReading
    from rp_interface import PhotodiodeReading, RPController


@dataclass
class SweepPoint:
    rp_out1_voltage: float
    rp_out2_voltage: float
    reading: PAXReading


@dataclass
class CrossSweepPoint:
    sweep_axis: str
    bias_axis: str
    bias_rp_voltage: float
    sweep_rp_voltage: float
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

    def run_cross_sweep(
        self,
        sweep_axis: str,
        bias_values: list[float],
        sweep_values: list[float],
        settle_s: float,
        output_file: Optional[str] = None,
    ) -> list[CrossSweepPoint]:
        """Sweep one phase at several fixed RP-voltage biases of the other.

        The output CSV records both the electrical state and the PAX-derived
        state, making each bias slice independently analysable.
        """
        if sweep_axis not in {"phi1", "phi2"}:
            raise ValueError("sweep_axis must be 'phi1' or 'phi2'")
        bias_axis = "phi2" if sweep_axis == "phi1" else "phi1"
        lower = self.config.rp_output_min_voltage
        upper = self.config.rp_output_max_voltage
        values = [*bias_values, *sweep_values]
        if any(value < lower or value > upper for value in values):
            raise ValueError(f"Cross-sweep values must remain within [{lower}, {upper}] V")

        points: list[CrossSweepPoint] = []
        low_dop_count = 0
        invalid_dop_count = 0
        for bias in bias_values:
            for sweep in sweep_values:
                out1, out2 = (sweep, bias) if sweep_axis == "phi1" else (bias, sweep)
                self.rp.set_output_voltage(out1, out2)
                time.sleep(settle_s)
                reading = self.pax.read_polarization()
                if reading.dop < self.config.minimum_dop:
                    low_dop_count += 1
                if not 0.0 <= reading.dop <= 1.0:
                    invalid_dop_count += 1
                points.append(
                    CrossSweepPoint(
                        sweep_axis=sweep_axis,
                        bias_axis=bias_axis,
                        bias_rp_voltage=bias,
                        sweep_rp_voltage=sweep,
                        rp_out1_voltage=out1,
                        rp_out2_voltage=out2,
                        reading=reading,
                    )
                )

        if output_file is not None:
            self.save_cross_csv(output_file, points)
        if low_dop_count:
            print(
                f"Warning: {low_dop_count}/{len(points)} cross-sweep readings had DOP below "
                f"{self.config.minimum_dop:.3f}. They were logged, but are not suitable for a rough move."
            )
        if invalid_dop_count:
            print(f"Warning: {invalid_dop_count}/{len(points)} cross-sweep readings had an invalid PAX DOP outside [0, 1].")
        return points

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

        if output_file is not None:
            self.save_phi2_path_balance_csv(output_file, points)
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
        if output_file is not None:
            self.save_pax_path_hold_csv(output_file, points)
        return points

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

    def save_cross_csv(self, output_file: str, points: list[CrossSweepPoint]) -> None:
        path = Path(output_file)
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "sweep_axis", "bias_axis", "bias_rp_v", "sweep_rp_v", "rp_out1_v", "rp_out2_v",
                "pax_timestamp", "theta", "eta", "s1", "s2", "s3", "dop", "pax_ptotal", "u", "v",
            ])
            for point in points:
                sphere = pax_to_sphere_angles((point.reading.theta, point.reading.eta))
                writer.writerow([
                    point.sweep_axis,
                    point.bias_axis,
                    point.bias_rp_voltage,
                    point.sweep_rp_voltage,
                    point.rp_out1_voltage,
                    point.rp_out2_voltage,
                    point.reading.timestamp,
                    point.reading.theta,
                    point.reading.eta,
                    point.reading.s1,
                    point.reading.s2,
                    point.reading.s3,
                    point.reading.dop,
                    point.reading.ptotal,
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
