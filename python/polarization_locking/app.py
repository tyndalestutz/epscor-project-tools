#!/usr/bin/env python3
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from .config import PolarizationLockConfig
from .control import PolarizationState, SphereAngles, pax_to_sphere_angles, phase_error_to_rp_voltage, sphere_angle_error, sphere_angles_from_stokes
from .hardware.pax_interface import PAXController, PAXReading
from .hardware.rp_interface import RPController
from .routines.diagnostics import DiagnosticsMixin
from .routines.feedback import FeedbackMixin


@dataclass(frozen=True)
class ExperimentPaths:
    """The self-contained directory allocated for one command-line run."""

    directory: Path
    csv: Path
    pdf: Path


class PolarizationLockApp(DiagnosticsMixin, FeedbackMixin):
    """Shared instrument session and coordinate helpers for bench routines."""

    def __init__(self, config: Optional[PolarizationLockConfig] = None) -> None:
        self.config = config or PolarizationLockConfig()
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
        try:
            self.rp.disconnect()
        finally:
            self.pax.disconnect()

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
        try:
            self.connect()
            self.rough_align_once()
        finally:
            self.disconnect()

    @staticmethod
    def _experiment_label(name: str) -> str:
        stem = Path(name).stem or "run"
        label = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")
        return label or "run"

    def _new_experiment_paths(self, kind: str, requested_name: str) -> ExperimentPaths:
        """Allocate a dated folder so a run's raw data and report stay together."""
        now = datetime.now()
        root = Path(self.config.results_directory).expanduser() / now.strftime("%Y-%m-%d")
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
            from .reports.plot_pid_tests import PdfPages, add_page
            with PdfPages(output_file) as pdf:
                add_page(pdf, csv_path)
        elif kind == "single-axis-pid":
            from .reports.plot_single_axis_pid import PdfPages, add_page
            with PdfPages(output_file) as pdf:
                add_page(pdf, csv_path)
        elif kind == "phi1-d-lock":
            from .reports.plot_phi1_d_lock import PdfPages, add_page
            with PdfPages(output_file) as pdf:
                add_page(pdf, csv_path)
        elif kind == "phi1-pd-lock":
            from .reports.plot_phi1_pd_lock import PdfPages, add_page
            with PdfPages(output_file) as pdf:
                add_page(pdf, csv_path)
        elif kind == "cross":
            from .reports.plot_cross_tests import PdfPages, add_report
            with PdfPages(output_file) as pdf:
                add_report(pdf, csv_path)
        else:
            from .reports.plot_calibration_tests import create_report
            create_report(csv_path, output_file, kind, axis)
        print(f"PDF report saved to {output_file}")

    @staticmethod
    def _write_power_balance_plots(csv_file: Path, paths: ExperimentPaths, output_format: str) -> None:
        from .reports.plot_power_balance import create_report
        create_report(
            csv_file,
            pdf_file=paths.pdf if output_format in {"pdf", "both"} else None,
            png_directory=paths.directory if output_format in {"png", "both"} else None,
        )
        if output_format in {"pdf", "both"}:
            print(f"PDF report saved to {paths.pdf}")
        if output_format in {"png", "both"}:
            print(f"PNG plots saved to {paths.directory}")

    def interactive_cli(self) -> None:
        from .cli import DiagnosticsMenu
        DiagnosticsMenu(config=self.config).run()
