#!/usr/bin/env python3
from __future__ import annotations

import time
from typing import Optional

import numpy as np
try:
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - optional dependency
    plt = None

try:
    from .config import DEFAULT_CONFIG, PolarizationLockConfig
    from .control import (
        PolarizationState,
        hybrid_phase_error,
        pax_to_sphere_angles,
        phase_error_to_rp_voltage,
    )
    from .pax_interface import PAXController
    from .rp_interface import RPController
except ImportError:  # pragma: no cover - support direct execution
    from config import DEFAULT_CONFIG, PolarizationLockConfig
    from control import PolarizationState, hybrid_phase_error, pax_to_sphere_angles, phase_error_to_rp_voltage
    from pax_interface import PAXController
    from rp_interface import RPController


class PolarizationLockApp:
    def __init__(self, config: Optional[PolarizationLockConfig] = None) -> None:
        self.config = config or DEFAULT_CONFIG
        self.rp = RPController(self.config)
        self.pax = PAXController(self.config)
        self.running = False
        self._theta_history = []
        self._eta_history = []
        self._fig = None
        self._ax = None
        self._sphere_fig = None
        self._sphere_ax = None
        self._sphere_point = None
        self._sphere_trace = None
        self._sphere_history = []
        self._sphere_anim = None
        # ASGs are initialized to 0 V in RPController.connect().  These are RP
        # output voltages, not the amplified actuator voltages used for Vlambda.
        self._applied_rp_voltages = np.zeros(2, dtype=float)

    def connect(self) -> None:
        self.rp.connect()
        self.pax.connect()
        self._applied_rp_voltages[:] = 0.0
        self.running = True

    def disconnect(self) -> None:
        self.running = False
        self.pax.disconnect()
        self.rp.disconnect()

    def set_target(self, theta: float, eta: float) -> None:
        self.config.target_theta = theta
        self.config.target_eta = eta

    def _update_plot(self, theta: float, eta: float) -> None:
        if not self.config.plot_live or plt is None:
            return

        self._theta_history.append(theta)
        self._eta_history.append(eta)
        if len(self._theta_history) > self.config.plot_window:
            self._theta_history = self._theta_history[-self.config.plot_window:]
            self._eta_history = self._eta_history[-self.config.plot_window:]

        if self._fig is None or self._ax is None:
            self._fig, self._ax = plt.subplots(1, 1, figsize=(6, 4))
            self._ax.set_title("Polarization lock state")
            self._ax.set_xlabel("Sample")
            self._ax.set_ylabel("Angle (rad)")

        self._ax.clear()
        x = np.arange(len(self._theta_history))
        self._ax.plot(x, self._theta_history, label="theta", color="C0")
        self._ax.plot(x, self._eta_history, label="eta", color="C1")
        self._ax.set_xlim(0, max(1, len(self._theta_history)))
        self._ax.legend()
        self._fig.canvas.draw_idle()
        self._fig.canvas.flush_events()

    def _show_sphere_view(self, reading) -> None:
        if plt is None:
            return

        if self._sphere_fig is None:
            self._sphere_fig = plt.figure(figsize=(12, 6), facecolor="#808080")
            self._sphere_ax = self._sphere_fig.add_subplot(111, projection="3d", facecolor="#808080")
            self._sphere_ax.set_title("Poincaré Sphere")
            self._sphere_ax.set_box_aspect([1, 1, 1])
            self._sphere_ax.set_axis_off()
            self._sphere_ax.view_init(elev=25, azim=45)

            u = np.linspace(0, 2 * np.pi, 80)
            v = np.linspace(0, np.pi, 40)
            x = np.outer(np.cos(u), np.sin(v))
            y = np.outer(np.sin(u), np.sin(v))
            z = np.outer(np.ones(np.size(u)), np.cos(v))
            self._sphere_ax.plot_surface(x, y, z, color="#c0c0c0", alpha=0.95, linewidth=0, shade=True)

            circle = np.linspace(0, 2 * np.pi, 400)
            self._sphere_ax.plot(np.zeros_like(circle), np.cos(circle), np.sin(circle), color="black", linewidth=1.5, alpha=0.7)
            self._sphere_ax.plot(np.cos(circle), np.zeros_like(circle), np.sin(circle), color="black", linewidth=1.5, alpha=0.7)
            self._sphere_ax.plot(np.cos(circle), np.sin(circle), np.zeros_like(circle), color="black", linewidth=1.5, alpha=0.7)

            self._sphere_point = self._sphere_ax.scatter([0], [0], [0], s=60, color="red", edgecolors="black", linewidths=0.5, depthshade=False)
            self._sphere_trace, = self._sphere_ax.plot([], [], [], color="red", linewidth=1.5, alpha=0.8)

            label_offset = 1.15
            self._sphere_ax.text(label_offset, 0, 0, "S1", fontsize=9)
            self._sphere_ax.text(0, label_offset, 0, "S2", fontsize=9)
            self._sphere_ax.text(0, 0, label_offset, "S3", fontsize=9)
            self._sphere_ax.text(-label_offset, 0, 0, "-S1", fontsize=8)
            self._sphere_ax.text(0, -label_offset, 0, "-S2", fontsize=8)
            self._sphere_ax.text(0, 0, -label_offset, "-S3", fontsize=8)

        s1, s2, s3 = self._stokes_from_reading(reading)
        self._sphere_history.append((s1, s2, s3))
        if len(self._sphere_history) > self.config.max_points:
            self._sphere_history = self._sphere_history[-self.config.max_points:]

        r = 1.03
        self._sphere_point._offsets3d = (
            np.array([r * s1]),
            np.array([r * s2]),
            np.array([r * s3]),
        )
        self._sphere_trace._verts3d = (
            r * np.array([item[0] for item in self._sphere_history]),
            r * np.array([item[1] for item in self._sphere_history]),
            r * np.array([item[2] for item in self._sphere_history]),
        )
        self._sphere_fig.canvas.draw_idle()
        self._sphere_fig.canvas.flush_events()

    def _stokes_from_reading(self, reading) -> tuple[float, float, float]:
        return (
            np.cos(2 * reading.eta) * np.cos(2 * reading.theta),
            np.cos(2 * reading.eta) * np.sin(2 * reading.theta),
            np.sin(2 * reading.eta),
        )

    def _compute_lock_output(self, current_state: PolarizationState, target_state: PolarizationState) -> tuple[float, float]:
        """Return the next absolute 0–1 V Red-Pitaya setpoints for a rough move."""
        required = (
            self.config.phi1_v_lambda,
            self.config.phi2_v_lambda,
            self.config.phi1_actuator_volts_per_rp_volt,
            self.config.phi2_actuator_volts_per_rp_volt,
        )
        if any(value is None for value in required):
            raise RuntimeError(
                "Set V_lambda and actuator-volts-per-RP-volt values in PolarizationLockConfig "
                "before enabling rough alignment."
            )
        if not self.config.phase_output_map_confirmed:
            raise RuntimeError("Confirm the phi1/phi2-to-RP-output mapping before enabling rough alignment.")

        delta_phi = hybrid_phase_error(
            current_state,
            target_state,
            pole_tolerance=self.config.sphere_pole_tolerance,
        )
        if np.linalg.norm(delta_phi) <= self.config.rough_deadband_rad:
            return tuple(self._applied_rp_voltages)

        delta_voltage = np.asarray(
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
            max_delta = self.config.rough_max_delta_voltage
            delta_voltage = np.clip(delta_voltage, -max_delta, max_delta)

        self._applied_rp_voltages = np.clip(
            self._applied_rp_voltages + delta_voltage,
            self.config.rp_output_min_voltage,
            self.config.rp_output_max_voltage,
        )
        return tuple(float(value) for value in self._applied_rp_voltages)

    def run_once(self) -> None:
        if not self.running:
            self.running = True

        if self.pax.client is None and not self.pax._test_mode:
            raise RuntimeError("PAX connection is not established")
        reading = self.pax.read_polarization()
        if reading.dop < self.config.minimum_dop:
            raise RuntimeError(
                f"PAX DOP {reading.dop:.3f} is below the required {self.config.minimum_dop:.3f}; refusing to move."
            )
        current_state = PolarizationState(theta=reading.theta, eta=reading.eta)
        target_state = PolarizationState(theta=self.config.target_theta, eta=self.config.target_eta)

        current_uv = pax_to_sphere_angles(current_state)
        target_uv = pax_to_sphere_angles(target_state)
        delta_phi = hybrid_phase_error(
            current_state,
            target_state,
            pole_tolerance=self.config.sphere_pole_tolerance,
        )
        clamped_voltages = self._compute_lock_output(current_state, target_state)

        self._update_plot(current_state.theta, current_state.eta)

        print(f"current=(theta={current_state.theta:.4f}, eta={current_state.eta:.4f})")
        print(f"target=(theta={target_state.theta:.4f}, eta={target_state.eta:.4f})")
        print(f"sphere current=(u={current_uv.u:.4f}, v={current_uv.v:.4f})")
        print(f"sphere target=(u={target_uv.u:.4f}, v={target_uv.v:.4f})")
        print(f"phase error=(d_phi_eom={delta_phi[0]:.4f}, d_phi_pzt={delta_phi[1]:.4f})")
        print(f"RP setpoints=(out1={clamped_voltages[0]:.4f} V, out2={clamped_voltages[1]:.4f} V)")

        if self.rp.p is not None:
            self.rp.set_output_voltage(*clamped_voltages)

        time.sleep(1.0 / self.config.update_hz)

    def rough_align_once(self) -> None:
        """Perform one safe rough move, wait for settling, then verify it.

        This deliberately does not repeat the correction.  Repeated corrections
        from stale PAX readings are not a substitute for a fine feedback loop.
        """
        self.run_once()
        time.sleep(self.config.rough_settle_s)
        verified = self.pax.read_polarization()
        if verified.dop < self.config.minimum_dop:
            raise RuntimeError(
                f"Verification DOP {verified.dop:.3f} is below the required "
                f"{self.config.minimum_dop:.3f}."
            )
        verified_state = PolarizationState(theta=verified.theta, eta=verified.eta)
        target_state = PolarizationState(theta=self.config.target_theta, eta=self.config.target_eta)
        remaining = hybrid_phase_error(
            verified_state,
            target_state,
            pole_tolerance=self.config.sphere_pole_tolerance,
        )
        print(
            f"verification=(u={pax_to_sphere_angles(verified_state).u:.4f}, "
            f"v={pax_to_sphere_angles(verified_state).v:.4f}, dop={verified.dop:.4f})"
        )
        print(f"remaining phase error=(d_phi1={remaining[0]:.4f}, d_phi2={remaining[1]:.4f})")

    def run(self) -> None:
        self.connect()
        try:
            self.rough_align_once()
        finally:
            self.disconnect()

    def interactive_cli(self) -> None:
        self.connect()
        print("Polarization lock CLI")
        print("Commands: set-pax <theta> <eta> | capture | rough | stop | quit | live | sweep <file>")
        try:
            while True:
                cmd = input(">> ").strip().split()
                if not cmd:
                    continue
                if cmd[0] in {"set", "set-pax"} and len(cmd) == 3:
                    try:
                        theta = float(cmd[1])
                        eta = float(cmd[2])
                        self.set_target(theta, eta)
                        print(f"Target set to theta={theta}, eta={eta}")
                    except ValueError:
                        print("Expected numeric values for theta and eta")
                elif cmd[0] in {"run", "rough"}:
                    self.running = True
                    self.rough_align_once()
                elif cmd[0] == "capture":
                    reading = self.pax.read_polarization()
                    self.set_target(reading.theta, reading.eta)
                    print(f"Captured PAX target theta={reading.theta}, eta={reading.eta}")
                elif cmd[0] == "lock":
                    print("Fine PID locking is not enabled yet; use 'rough' for a one-shot move.")
                elif cmd[0] == "stop":
                    self.running = False
                    self.rp.set_output_zero()
                    self._applied_rp_voltages[:] = 0.0
                elif cmd[0] == "live":
                    self._run_live_monitor()
                elif cmd[0] == "sweep" and len(cmd) == 2:
                    self._run_calibration_sweep(cmd[1])
                elif cmd[0] in {"quit", "exit"}:
                    break
                else:
                    print("Unknown command")
        finally:
            self.disconnect()

    def _run_lock_loop(self) -> None:
        print("Lock loop started. Press Ctrl+C to stop.")
        self.running = True
        try:
            while self.running:
                self.run_once()
        except KeyboardInterrupt:
            print("Lock loop interrupted")
        finally:
            self.running = False
            self.rp.set_output_zero()
            self._applied_rp_voltages[:] = 0.0

    def _run_live_monitor(self) -> None:
        print("Live monitor enabled. Adjust the physical knobs and press Ctrl+C to stop.")
        try:
            while True:
                reading = self.pax.read_polarization()
                self._show_sphere_view(reading)
                print(
                    f"theta={reading.theta:.5f} eta={reading.eta:.5f} "
                    f"s1={reading.s1:.5f} s2={reading.s2:.5f} s3={reading.s3:.5f} dop={reading.dop:.5f}"
                )
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("Live monitor stopped")

    def _run_calibration_sweep(self, output_file: str) -> None:
        try:
            from .calibration import CalibrationSweep
        except ImportError:  # pragma: no cover - support direct execution
            from calibration import CalibrationSweep
        sweep = CalibrationSweep(self.config)
        sweep.connect()
        try:
            print("Starting calibration sweep from the current polarization state")
            sweep.run_grid_sweep(
                v1_values=[i / 19.0 for i in range(20)],
                v2_values=[0.0],
                repeats=1,
                settle_s=0.5,
                output_file=output_file,
            )
        finally:
            sweep.disconnect()
            print(f"Calibration sweep saved to {output_file}")


if __name__ == "__main__":
    app = PolarizationLockApp()
    try:
        app.interactive_cli()
    except KeyboardInterrupt:
        print("Stopping polarization lock loop")
