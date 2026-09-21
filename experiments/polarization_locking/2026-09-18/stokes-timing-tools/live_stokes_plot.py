"""Live Stokes timing diagnostic, actuator drive, and CSV logger."""
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
    from config import PolarizationLockConfig
    from hardware.pax_interface import PAXController
    from hardware.rp_interface import RPController


RECORDED_PHI1_COMMAND_V_PI = 0.35745170986774194
DEFAULT_FREQUENCY_HZ = 0.75
LIVE_PAX_WAIT_S = 0.03


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_output(config: PolarizationLockConfig, axis: str) -> Path:
    now = datetime.now()
    directory = Path(config.results_directory) / now.strftime("%Y-%m-%d")
    directory = directory / f"{now:%H%M%S}_live-s123-{axis}"
    directory.mkdir(parents=True, exist_ok=False)
    return directory / "data.csv"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive one RP axis, diagnose timing against live S1/S2/S3, and log CSV."
    )
    parser.add_argument("--axis", choices=("phi1", "phi2"), default="phi1")
    parser.add_argument("--v-pi-rp", type=float, default=RECORDED_PHI1_COMMAND_V_PI,
                        help="RP command voltage for one provisional V_pi (default: %(default).6f V)")
    parser.add_argument("--frequency", type=float, default=DEFAULT_FREQUENCY_HZ,
                        help="Drive frequency in Hz (default: %(default).2f)")
    parser.add_argument("--offset", type=float, default=None,
                        help="RP command midpoint; defaults to half of --v-pi-rp")
    parser.add_argument("--amplitude", type=float, default=None,
                        help="RP command amplitude; defaults to half of --v-pi-rp")
    parser.add_argument("--duration", type=float, default=None,
                        help="Stop after this many seconds (default: Ctrl-C)")
    parser.add_argument("--output", type=Path, default=None,
                        help="CSV path (default: dated experiments folder)")
    parser.add_argument("--plot-points", type=int, default=2000,
                        help="Maximum recent samples shown in each plot")
    parser.add_argument("--no-plot", action="store_true",
                        help="Log hardware data without opening a matplotlib window")
    parser.add_argument(
        "--timing-offset-ms", type=float, default=0.0,
        help="Analysis timing offset in ms. Positive means PAX is assumed to lag, "
             "so the command is evaluated at an earlier time (default: 0).",
    )
    parser.add_argument(
        "--estimate-delay", action="store_true",
        help="Estimate command-to-Stokes delay after enough samples; never changes raw data.",
    )
    return parser


def _validate(args: argparse.Namespace, config: PolarizationLockConfig) -> tuple[float, float]:
    for name in ("v_pi_rp", "frequency", "timing_offset_ms"):
        if not math.isfinite(getattr(args, name)):
            raise ValueError(f"--{name.replace('_', '-')} must be finite")
    if args.v_pi_rp <= 0 or args.frequency <= 0:
        raise ValueError("--v-pi-rp and --frequency must be positive")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        raise ValueError("--duration must be positive")
    if args.plot_points < 2:
        raise ValueError("--plot-points must be at least 2")
    offset = args.v_pi_rp / 2 if args.offset is None else args.offset
    amplitude = args.v_pi_rp / 2 if args.amplitude is None else args.amplitude
    if not math.isfinite(offset) or not math.isfinite(amplitude) or amplitude < 0:
        raise ValueError("--offset must be finite and --amplitude must be non-negative")
    lower, upper = config.rp_output_min_voltage, config.rp_output_max_voltage
    if offset - amplitude < lower or offset + amplitude > upper:
        raise ValueError(f"Drive must stay within [{lower:.3f}, {upper:.3f}] V")
    return float(offset), float(amplitude)


def _make_plot(axis: str, frequency_hz: float, v_pi_rp: float, timing_offset_ms: float):
    try:
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("matplotlib and numpy are required unless --no-plot is used") from exc

    figure, axes = plt.subplots(2, 2, figsize=(11, 8), constrained_layout=True)
    time_ax, sphere_ax, phase_ax, sanity_ax = axes.flat
    time_lines = [
        time_ax.plot([], [], color="black", label="RP command / amplitude")[0],
        time_ax.plot([], [], color="tab:green", label="S2")[0],
        time_ax.plot([], [], color="tab:blue", label="S3")[0],
    ]
    empty_offsets = np.empty((0, 2))
    sphere_scatter = sphere_ax.scatter([], [], s=12, c=[], cmap="viridis", vmin=0, vmax=v_pi_rp)
    sphere_current = sphere_ax.scatter(empty_offsets[:, 0], empty_offsets[:, 1], s=45, c="red", marker="x")
    phase_rise = phase_ax.scatter(empty_offsets[:, 0], empty_offsets[:, 1], s=12, c="tab:orange", marker=".")
    phase_fall = phase_ax.scatter(empty_offsets[:, 0], empty_offsets[:, 1], s=12, c="tab:purple", marker=".")
    sanity_lines = [
        sanity_ax.plot([], [], color="tab:red", label="S1")[0],
        sanity_ax.plot([], [], color="tab:green", label="sqrt(S2²+S3²)")[0],
    ]
    time_ax.set(xlabel="time since waveform start (s)", ylabel="normalized value", ylim=(-1.1, 1.1))
    sphere_ax.set(xlabel="S2", ylabel="S3", xlim=(-1.05, 1.05), ylim=(-1.05, 1.05), aspect="equal")
    sphere_ax.plot(np.cos(np.linspace(0, 2 * np.pi, 300)), np.sin(np.linspace(0, 2 * np.pi, 300)),
                   "--", color="gray", linewidth=1)
    phase_ax.set(xlabel=f"{axis} RP command voltage (V)", ylabel="atan2(-S3, S2) (rad)",
                 ylim=(-math.pi, math.pi))
    sanity_ax.set(xlabel="time since waveform start (s)", ylabel="sanity check")
    time_ax.legend(loc="upper right", fontsize="small")
    phase_ax.legend(["rising", "falling"], loc="upper right", fontsize="small")
    sanity_ax.legend(loc="upper right", fontsize="small")
    figure.suptitle(
        f"Stokes timing diagnostic: {axis}, {frequency_hz:g} Hz, provisional V_pi={v_pi_rp:.6f} V\n"
        f"timing offset = {timing_offset_ms:+.3f} ms"
    )
    figure.show()
    return plt, np, figure, (time_lines, sphere_scatter, sphere_current, phase_rise, phase_fall, sanity_lines)


def _estimate_delay(times, s2, s3, frequency_hz: float) -> float | None:
    if len(times) < 20:
        return None
    import numpy as np

    t = np.asarray(times, dtype=float)
    y2, y3 = np.asarray(s2, dtype=float), np.asarray(s3, dtype=float)
    period = 1.0 / frequency_hz
    # A periodic drive makes delays separated by one period equivalent.
    # Report the nearest representative so the result is useful as a
    # command/measurement latency diagnostic rather than a cycle alias.
    candidates = np.linspace(-0.5 * period, 0.5 * period, 401)
    scores = []
    for delay in candidates:
        phase = 2 * np.pi * frequency_hz * (t - delay)
        predicted_2, predicted_3 = np.cos(phase), -np.sin(phase)
        valid = np.isfinite(y2) & np.isfinite(y3)
        if valid.sum() < 10:
            return None
        scores.append(float(np.mean(y2[valid] * predicted_2[valid] + y3[valid] * predicted_3[valid])))
    return float(candidates[int(np.argmax(scores))])


def run(args: argparse.Namespace) -> Path:
    config = PolarizationLockConfig()
    config.pax_measurement_wait_s = LIVE_PAX_WAIT_S
    config.pax_retry_wait_s = LIVE_PAX_WAIT_S
    offset, amplitude = _validate(args, config)
    actuator_gain = getattr(config, f"{args.axis}_actuator_volts_per_rp_volt") or float("nan")
    output = args.output.expanduser() if args.output else _default_output(config, args.axis)
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"Logging live Stokes data to {output}", flush=True)
    print("Press Ctrl-C to stop; the RP outputs are returned to zero.", flush=True)

    plot = None
    if not args.no_plot:
        plot = _make_plot(args.axis, args.frequency, args.v_pi_rp, args.timing_offset_ms)
        plt, np, figure, artists = plot
        plot_commands, plot_times, plot_s2, plot_s3 = [], [], [], []
        plot_s1, plot_r23, plot_rise, plot_fall, plot_rise_phi, plot_fall_phi = [], [], [], [], [], []

    rp, pax = RPController(config), PAXController(config)
    header = [
        "sample", "utc", "elapsed_s", "drive_elapsed_s", "drive_elapsed_corrected_s",
        "pax_read_duration_s", "pax_timestamp", "axis", "frequency_hz", "rp_command_v",
        "rp_command_v_reconstructed", "actuator_voltage_est_v", "drive_offset_v",
        "drive_amplitude_v", "timing_offset_ms", "cycle_index", "half_cycle_index",
        "sweep_direction", "s1", "s2", "s3", "s23_radius", "stokes_phase_wrapped_rad",
        "stokes_phase_unwrapped_within_halfcycle_rad", "dop", "theta", "eta", "ptotal",
        "revisions", "adc_min", "adc_max", "rev_time",
    ]
    samples = []
    started = time.monotonic()
    sample = 0
    latest_delay = None
    try:
        pax.connect()
        rp.connect()
        drive_start = time.monotonic()
        if args.axis == "phi1":
            rp.set_phi1_sine(offset=offset, amplitude=amplitude, frequency_hz=args.frequency)
        else:
            rp.set_phi2_sine(offset=offset, amplitude=amplitude, frequency_hz=args.frequency)
        # Pyrpl starts the ASG with trigger_source="immediately", but the
        # controller exposes no phase-readback or deterministic reset. The
        # drive_start timestamp is therefore an analysis reference, not proof
        # of FPGA phase alignment; --timing-offset-ms can test that uncertainty.

        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=header)
            writer.writeheader()
            handle.flush()
            previous_half = None
            half_unwrapped = 0.0
            previous_phase = None
            while args.duration is None or time.monotonic() - started < args.duration:
                before = time.monotonic()
                reading = pax.read_polarization()
                after = time.monotonic()
                drive_elapsed = 0.5 * (before + after) - drive_start
                corrected = drive_elapsed - args.timing_offset_ms / 1000
                phase = 2 * math.pi * args.frequency * corrected
                command_v = offset + amplitude * math.sin(phase)
                half_index = math.floor((phase + math.pi / 2) / math.pi)
                direction = "rising" if math.cos(phase) >= 0 else "falling"
                wrapped = math.atan2(-reading.s3, reading.s2)
                if half_index != previous_half or previous_phase is None:
                    half_unwrapped, previous_phase = wrapped, wrapped
                else:
                    half_unwrapped += math.atan2(math.sin(wrapped - previous_phase),
                                                 math.cos(wrapped - previous_phase))
                    previous_phase = wrapped
                r23 = math.hypot(reading.s2, reading.s3)
                row = {
                    "sample": sample, "utc": _utc_now(), "elapsed_s": after - started,
                    "drive_elapsed_s": drive_elapsed, "drive_elapsed_corrected_s": corrected,
                    "pax_read_duration_s": after - before, "pax_timestamp": reading.timestamp,
                    "axis": args.axis, "frequency_hz": args.frequency, "rp_command_v": command_v,
                    "rp_command_v_reconstructed": command_v,
                    "actuator_voltage_est_v": command_v * actuator_gain, "drive_offset_v": offset,
                    "drive_amplitude_v": amplitude, "timing_offset_ms": args.timing_offset_ms,
                    "cycle_index": math.floor((half_index + 1) / 2), "half_cycle_index": half_index,
                    "sweep_direction": direction, "s1": reading.s1, "s2": reading.s2, "s3": reading.s3,
                    "s23_radius": r23, "stokes_phase_wrapped_rad": wrapped,
                    "stokes_phase_unwrapped_within_halfcycle_rad": half_unwrapped,
                    "dop": reading.dop, "theta": reading.theta, "eta": reading.eta,
                    "ptotal": reading.ptotal, "revisions": reading.revisions, "adc_min": reading.adc_min,
                    "adc_max": reading.adc_max, "rev_time": reading.rev_time,
                }
                writer.writerow(row)
                handle.flush()
                samples.append(row)
                if previous_half is not None and half_index != previous_half:
                    segment = [r["stokes_phase_wrapped_rad"] for r in samples if r["half_cycle_index"] == previous_half]
                    if len(segment) > 1:
                        import numpy as np
                        span = float(np.ptp(np.unwrap(segment)))
                        print(f"half-cycle {previous_half}: delta phi = {span:.3f} rad ({span / math.pi:.3f} pi)", flush=True)
                previous_half, sample = half_index, sample + 1

                if args.estimate_delay and sample % 20 == 0:
                    latest_delay = _estimate_delay(
                        [r["drive_elapsed_corrected_s"] for r in samples],
                        [r["s2"] for r in samples], [r["s3"] for r in samples], args.frequency,
                    )
                    if latest_delay is not None:
                        print(f"inferred command-to-Stokes delay: {latest_delay * 1000:+.1f} ms", flush=True)

                if plot is not None:
                    plot_times.append(corrected); plot_commands.append(command_v)
                    plot_s1.append(reading.s1); plot_s2.append(reading.s2); plot_s3.append(reading.s3)
                    plot_r23.append(r23)
                    if direction == "rising":
                        plot_rise_phi.append((command_v, wrapped))
                    else:
                        plot_fall_phi.append((command_v, wrapped))
                    for values in (plot_times, plot_commands, plot_s1, plot_s2, plot_s3, plot_r23,
                                   plot_rise_phi, plot_fall_phi):
                        del values[:-args.plot_points]
                    time_lines, sphere, current, rise, fall, sanity = artists
                    time_lines[0].set_data(plot_times, [(v - offset) / max(amplitude, 1e-12) for v in plot_commands])
                    time_lines[1].set_data(plot_times, plot_s2); time_lines[2].set_data(plot_times, plot_s3)
                    sphere.set_offsets(list(zip(plot_s2, plot_s3)))
                    sphere.set_array(np.asarray(plot_commands))
                    current.set_offsets([[reading.s2, reading.s3]])
                    rise.set_offsets(np.asarray(plot_rise_phi, dtype=float).reshape(-1, 2))
                    fall.set_offsets(np.asarray(plot_fall_phi, dtype=float).reshape(-1, 2))
                    sanity[0].set_data(plot_times, plot_s1); sanity[1].set_data(plot_times, plot_r23)
                    for ax in figure.axes:
                        ax.relim(); ax.autoscale_view()
                    sphere_ax = figure.axes[1]
                    sphere_ax.set(xlim=(-1.05, 1.05), ylim=(-1.05, 1.05), aspect="equal")
                    figure.canvas.draw_idle(); plt.pause(0.001)
    except KeyboardInterrupt:
        print(f"Stopped after {sample} samples.", flush=True)
    finally:
        try:
            rp.disconnect()
        finally:
            pax.disconnect()
            if plot is not None:
                plot[0].close(plot[2])
    print(f"Saved {sample} samples to {output}", flush=True)
    if latest_delay is not None:
        print(f"final inferred command-to-Stokes delay: {latest_delay * 1000:+.1f} ms", flush=True)
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
