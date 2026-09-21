"""Live polarization-phase diagnostic for the hybrid MZI.

Uses the same RPController/PAXController interfaces and the same command-line
interface as live_stokes_plot.py, but plots the measurement in coordinates that
are more diagnostic of the E3 model:

    S2 ~ cos(phi1)
    S3 ~ -sin(phi1)
    phi_meas = atan2(-S3, S2)

The live display emphasizes:
  1. synchronized command + Stokes traces versus time,
  2. the S2-S3 phase-plane trajectory,
  3. measured polarization phase versus command voltage, split into rising and
     falling voltage branches,
  4. S1 and sqrt(S2^2 + S3^2) versus time.

Important synchronization change relative to the original script:
The sine-wave time origin is established at the RP set_*_sine() call, rather
than before instrument connection.  Each PAX reading is associated with the
midpoint of the blocking read call, which is a better approximation to the
measurement time than the time after the read returns.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import math
from pathlib import Path
import time

try:
    from .config import PolarizationLockConfig
    from .hardware.pax_interface import PAXController
    from .hardware.rp_interface import RPController
except ImportError:
    # Also support `python live_stokes_diagnostic.py` from this package directory.
    from config import PolarizationLockConfig
    from hardware.pax_interface import PAXController
    from hardware.rp_interface import RPController


RECORDED_PHI1_COMMAND_V_PI = 0.35745170986774194
DEFAULT_FREQUENCY_HZ = 0.75
LIVE_PAX_WAIT_S = 0.03
TWO_PI = 2.0 * math.pi


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_output(config: PolarizationLockConfig, axis: str) -> Path:
    now = datetime.now()
    directory = (
        Path(config.results_directory)
        / now.strftime("%Y-%m-%d")
        / f"{now:%H%M%S}_live-phase-diagnostic-{axis}"
    )
    directory.mkdir(parents=True, exist_ok=False)
    return directory / "data.csv"


def _parser() -> argparse.ArgumentParser:
    # Keep the same CLI as live_stokes_plot.py so existing commands still work.
    parser = argparse.ArgumentParser(
        description=(
            "Drive one RP axis, log PAX Stokes data, and plot synchronized time, "
            "S2-S3 phase-plane, and extracted polarization-phase diagnostics."
        )
    )
    parser.add_argument("--axis", choices=("phi1", "phi2"), default="phi1")
    parser.add_argument(
        "--v-pi-rp",
        type=float,
        default=RECORDED_PHI1_COMMAND_V_PI,
        help="RP command voltage for one recorded V_pi (default: %(default).6f V)",
    )
    parser.add_argument(
        "--frequency",
        type=float,
        default=DEFAULT_FREQUENCY_HZ,
        help="Drive frequency in Hz",
    )
    parser.add_argument(
        "--offset",
        type=float,
        default=None,
        help="RP command midpoint; defaults to half of --v-pi-rp (the drive spans 0..V_pi)",
    )
    parser.add_argument(
        "--amplitude",
        type=float,
        default=None,
        help="RP command amplitude; defaults to half of --v-pi-rp",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Stop after this many seconds (default: Ctrl-C)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="CSV path (default: dated experiments folder)",
    )
    parser.add_argument(
        "--plot-points",
        type=int,
        default=2000,
        help="Maximum recent samples shown in each plot",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Log hardware data without opening a matplotlib window",
    )
    return parser


def _validate(args: argparse.Namespace, config: PolarizationLockConfig) -> tuple[float, float]:
    if not math.isfinite(args.v_pi_rp) or args.v_pi_rp <= 0.0:
        raise ValueError("--v-pi-rp must be positive")
    if not math.isfinite(args.frequency) or args.frequency <= 0.0:
        raise ValueError("--frequency must be positive")
    if args.duration is not None and (
        not math.isfinite(args.duration) or args.duration <= 0.0
    ):
        raise ValueError("--duration must be positive")
    if args.plot_points < 2:
        raise ValueError("--plot-points must be at least 2")

    offset = args.v_pi_rp / 2.0 if args.offset is None else args.offset
    amplitude = args.v_pi_rp / 2.0 if args.amplitude is None else args.amplitude

    if not math.isfinite(offset):
        raise ValueError("--offset must be finite")
    if not math.isfinite(amplitude) or amplitude < 0.0:
        raise ValueError("--amplitude must be non-negative")

    lower = config.rp_output_min_voltage
    upper = config.rp_output_max_voltage
    if offset - amplitude < lower or offset + amplitude > upper:
        raise ValueError(
            f"Drive must stay within [{lower:.3f}, {upper:.3f}] V; "
            f"received [{offset - amplitude:.3f}, {offset + amplitude:.3f}] V"
        )
    return float(offset), float(amplitude)


def _unwrap_next(
    wrapped: float,
    previous_wrapped: float | None,
    previous_unwrapped: float | None,
) -> float:
    """Incrementally unwrap one angular sample without requiring NumPy."""
    if previous_wrapped is None or previous_unwrapped is None:
        return wrapped

    delta = wrapped - previous_wrapped
    while delta > math.pi:
        delta -= TWO_PI
    while delta < -math.pi:
        delta += TWO_PI
    return previous_unwrapped + delta


def _make_plot(
    axis: str,
    frequency_hz: float,
    v_pi_rp: float,
    actuator_gain: float,
):
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - instrument environment
        raise RuntimeError("matplotlib is required unless --no-plot is used") from exc

    figure, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    ax_time = axes[0, 0]
    ax_plane = axes[0, 1]
    ax_phase_v = axes[1, 0]
    ax_quality = axes[1, 1]

    # 1) Time-domain synchronization diagnostic.
    line_cmd, = ax_time.plot([], [], label="command (normalized)")
    line_s2, = ax_time.plot([], [], label="S2")
    line_s3, = ax_time.plot([], [], label="S3")
    ax_time.set_ylim(-1.1, 1.1)
    ax_time.set_xlabel("time from drive start (s)")
    ax_time.set_ylabel("normalized value")
    ax_time.set_title("Time-domain synchronization")
    ax_time.grid(True, alpha=0.3)
    ax_time.legend(loc="upper right")

    # 2) Model-natural coordinates.  For E3 with phi2 ~ pi/2, the ideal path is
    #    the unit circle: (S2, S3) = (cos(phi1), -sin(phi1)).
    line_plane, = ax_plane.plot([], [], ".-", markersize=3, linewidth=0.7)
    circle_x = [math.cos(TWO_PI * i / 240.0) for i in range(241)]
    circle_y = [math.sin(TWO_PI * i / 240.0) for i in range(241)]
    ax_plane.plot(circle_x, circle_y, "--", linewidth=1.0, alpha=0.55, label="unit circle")
    ax_plane.set_xlim(-1.05, 1.05)
    ax_plane.set_ylim(-1.05, 1.05)
    ax_plane.set_aspect("equal", adjustable="box")
    ax_plane.set_xlabel("S2")
    ax_plane.set_ylabel("S3")
    ax_plane.set_title("S2-S3 phase plane")
    ax_plane.grid(True, alpha=0.3)
    ax_plane.legend(loc="best")

    # 3) The central diagnostic: phi_meas(V), split by sweep direction.
    rise_points, = ax_phase_v.plot([], [], ".", markersize=4, label="dV/dt > 0")
    fall_points, = ax_phase_v.plot([], [], ".", markersize=4, label="dV/dt < 0")
    ax_phase_v.set_xlabel(f"{axis} RP command voltage (V)")
    ax_phase_v.set_ylabel("unwrapped atan2(-S3, S2) (rad)")
    ax_phase_v.set_title("Measured polarization phase vs command")
    ax_phase_v.grid(True, alpha=0.3)
    ax_phase_v.legend(loc="best")

    # 4) Sanity checks. S1 should remain modest and radius should remain near 1.
    line_s1, = ax_quality.plot([], [], label="S1")
    line_radius, = ax_quality.plot([], [], label="sqrt(S2^2 + S3^2)")
    ax_quality.axhline(1.0, linestyle="--", linewidth=1.0, alpha=0.55)
    ax_quality.set_ylim(-0.25, 1.15)
    ax_quality.set_xlabel("time from drive start (s)")
    ax_quality.set_ylabel("Stokes quantity")
    ax_quality.set_title("Great-circle / input-state sanity check")
    ax_quality.grid(True, alpha=0.3)
    ax_quality.legend(loc="best")

    figure.suptitle(
        f"Live polarization diagnostic: {axis}, {frequency_hz:g} Hz, "
        f"recorded V_pi={v_pi_rp:.6f} RP V "
        f"(estimated actuator gain {actuator_gain:.3f} V/V)"
    )
    figure.show()

    artists = {
        "cmd": line_cmd,
        "s2": line_s2,
        "s3": line_s3,
        "plane": line_plane,
        "rise": rise_points,
        "fall": fall_points,
        "s1": line_s1,
        "radius": line_radius,
    }
    return plt, figure, axes, artists


def run(args: argparse.Namespace) -> Path:
    config = PolarizationLockConfig()
    config.pax_measurement_wait_s = LIVE_PAX_WAIT_S
    config.pax_retry_wait_s = LIVE_PAX_WAIT_S
    offset, amplitude = _validate(args, config)

    actuator_gain = getattr(config, f"{args.axis}_actuator_volts_per_rp_volt")
    if actuator_gain is None:
        actuator_gain = float("nan")

    output = args.output.expanduser() if args.output else _default_output(config, args.axis)
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Logging live polarization diagnostic data to {output}", flush=True)
    print("Press Ctrl-C to stop; the RP outputs are returned to zero.", flush=True)

    plot = None
    if not args.no_plot:
        plot = _make_plot(args.axis, args.frequency, args.v_pi_rp, actuator_gain)
        plt, figure, axes, artists = plot

        plot_t: list[float] = []
        plot_commands: list[float] = []
        plot_cmd_norm: list[float] = []
        plot_s1: list[float] = []
        plot_s2: list[float] = []
        plot_s3: list[float] = []
        plot_radius: list[float] = []
        plot_phase: list[float] = []
        plot_rising: list[bool] = []

    rp = RPController(config)
    pax = PAXController(config)
    sample = 0

    header = [
        "sample",
        "utc",
        "elapsed_s",
        "drive_elapsed_s",
        "pax_read_duration_s",
        "pax_timestamp",
        "axis",
        "frequency_hz",
        "rp_command_v",
        "actuator_voltage_est_v",
        "drive_offset_v",
        "drive_amplitude_v",
        "sweep_direction",
        "s1",
        "s2",
        "s3",
        "s23_radius",
        "stokes_phase_wrapped_rad",
        "stokes_phase_unwrapped_rad",
        "model_phase_rad",
        "dop",
        "theta",
        "eta",
        "ptotal",
        "revisions",
        "adc_min",
        "adc_max",
        "rev_time",
    ]

    drive_started: float | None = None
    acquisition_started: float | None = None
    previous_wrapped: float | None = None
    previous_unwrapped: float | None = None

    try:
        pax.connect()
        rp.connect()

        # Establish the software phase origin at the command that starts the RP
        # waveform.  This removes the connection-time phase error in the original
        # script, where elapsed time began before either instrument was connected.
        drive_started = time.monotonic()
        if args.axis == "phi1":
            rp.set_phi1_sine(offset=offset, amplitude=amplitude, frequency_hz=args.frequency)
        else:
            rp.set_phi2_sine(offset=offset, amplitude=amplitude, frequency_hz=args.frequency)
        acquisition_started = time.monotonic()

        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            handle.flush()

            while True:
                loop_now = time.monotonic()
                elapsed = loop_now - acquisition_started
                if args.duration is not None and elapsed >= args.duration:
                    break

                # The PAX read is blocking.  Associate the sample with the midpoint
                # of the read interval rather than the return time.
                read_start = time.monotonic()
                reading = pax.read_polarization()
                read_end = time.monotonic()
                sample_time = 0.5 * (read_start + read_end)

                drive_elapsed = sample_time - drive_started
                omega_t = TWO_PI * args.frequency * drive_elapsed
                command_v = offset + amplitude * math.sin(omega_t)
                command_derivative_sign = math.cos(omega_t)
                sweep_direction = "rising" if command_derivative_sign >= 0.0 else "falling"

                s1 = float(reading.s1)
                s2 = float(reading.s2)
                s3 = float(reading.s3)
                radius = math.hypot(s2, s3)

                # For the E3 model under discussion:
                #   S2 ~ cos(phi1), S3 ~ -sin(phi1)
                # so this directly estimates optical phase from both components.
                wrapped_phase = math.atan2(-s3, s2)
                unwrapped_phase = _unwrap_next(
                    wrapped_phase,
                    previous_wrapped,
                    previous_unwrapped,
                )
                previous_wrapped = wrapped_phase
                previous_unwrapped = unwrapped_phase

                # Nominal phase implied by the recorded command-side V_pi.  This is
                # logged as a reference, not assumed to be a validated calibration.
                model_phase = math.pi * command_v / args.v_pi_rp

                row = {
                    "sample": sample,
                    "utc": _utc_now(),
                    "elapsed_s": sample_time - acquisition_started,
                    "drive_elapsed_s": drive_elapsed,
                    "pax_read_duration_s": read_end - read_start,
                    "pax_timestamp": reading.timestamp,
                    "axis": args.axis,
                    "frequency_hz": args.frequency,
                    "rp_command_v": command_v,
                    "actuator_voltage_est_v": command_v * actuator_gain,
                    "drive_offset_v": offset,
                    "drive_amplitude_v": amplitude,
                    "sweep_direction": sweep_direction,
                    "s1": s1,
                    "s2": s2,
                    "s3": s3,
                    "s23_radius": radius,
                    "stokes_phase_wrapped_rad": wrapped_phase,
                    "stokes_phase_unwrapped_rad": unwrapped_phase,
                    "model_phase_rad": model_phase,
                    "dop": reading.dop,
                    "theta": reading.theta,
                    "eta": reading.eta,
                    "ptotal": reading.ptotal,
                    "revisions": reading.revisions,
                    "adc_min": reading.adc_min,
                    "adc_max": reading.adc_max,
                    "rev_time": reading.rev_time,
                }
                writer.writerow(row)
                handle.flush()
                sample += 1

                if plot is not None:
                    t = sample_time - acquisition_started
                    if amplitude > 0.0:
                        cmd_norm = (command_v - offset) / amplitude
                    else:
                        cmd_norm = 0.0

                    plot_t.append(t)
                    plot_commands.append(command_v)
                    plot_cmd_norm.append(cmd_norm)
                    plot_s1.append(s1)
                    plot_s2.append(s2)
                    plot_s3.append(s3)
                    plot_radius.append(radius)
                    plot_phase.append(unwrapped_phase)
                    plot_rising.append(sweep_direction == "rising")

                    buffers = (
                        plot_t,
                        plot_commands,
                        plot_cmd_norm,
                        plot_s1,
                        plot_s2,
                        plot_s3,
                        plot_radius,
                        plot_phase,
                        plot_rising,
                    )
                    for values in buffers:
                        del values[:-args.plot_points]

                    # Time traces.
                    artists["cmd"].set_data(plot_t, plot_cmd_norm)
                    artists["s2"].set_data(plot_t, plot_s2)
                    artists["s3"].set_data(plot_t, plot_s3)

                    # Phase plane.
                    artists["plane"].set_data(plot_s2, plot_s3)

                    # Rising/falling branches in phase-vs-voltage space.
                    rise_v = [v for v, rising in zip(plot_commands, plot_rising) if rising]
                    rise_phi = [p for p, rising in zip(plot_phase, plot_rising) if rising]
                    fall_v = [v for v, rising in zip(plot_commands, plot_rising) if not rising]
                    fall_phi = [p for p, rising in zip(plot_phase, plot_rising) if not rising]
                    artists["rise"].set_data(rise_v, rise_phi)
                    artists["fall"].set_data(fall_v, fall_phi)

                    # S1 and radius sanity checks.
                    artists["s1"].set_data(plot_t, plot_s1)
                    artists["radius"].set_data(plot_t, plot_radius)

                    if plot_t:
                        t_min, t_max = min(plot_t), max(plot_t)
                        if t_max <= t_min:
                            t_max = t_min + 1.0
                        axes[0, 0].set_xlim(t_min, t_max)
                        axes[1, 1].set_xlim(t_min, t_max)

                    if plot_commands:
                        v_min, v_max = min(plot_commands), max(plot_commands)
                        pad_v = max(0.01, 0.05 * max(v_max - v_min, args.v_pi_rp))
                        axes[1, 0].set_xlim(v_min - pad_v, v_max + pad_v)

                    if plot_phase:
                        p_min, p_max = min(plot_phase), max(plot_phase)
                        pad_p = max(0.2, 0.08 * max(p_max - p_min, 1.0))
                        axes[1, 0].set_ylim(p_min - pad_p, p_max + pad_p)

                    plt.pause(0.001)

    except KeyboardInterrupt:
        print(f"Stopped after {sample} samples.", flush=True)
    finally:
        try:
            rp.disconnect()
        finally:
            pax.disconnect()
            if plot is not None:
                plot[0].close(plot[1])

    print(f"Saved {sample} samples to {output}", flush=True)
    return output


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run(args)
    except (RuntimeError, ValueError) as exc:
        _parser().error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
