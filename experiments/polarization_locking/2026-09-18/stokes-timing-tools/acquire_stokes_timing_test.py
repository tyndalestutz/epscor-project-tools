"""Dynamic and quasi-static phi1 loopback acquisition for offline analysis."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

import numpy as np

try:
    from .config import PolarizationLockConfig
    from .hardware.pax_interface import PAXController
    from .hardware.rp_interface import RPController
except ImportError:
    from config import PolarizationLockConfig
    from hardware.pax_interface import PAXController
    from hardware.rp_interface import RPController


RECORDED_PHI1_COMMAND_V_PI = 0.35745170986774194
DEFAULT_MAX_VOLTAGE = 0.80
PAX_WAIT_S = 0.03


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _experiment_directory(config: PolarizationLockConfig, output: Path | None) -> Path:
    if output is not None:
        directory = output.expanduser()
    else:
        now = datetime.now()
        directory = (
            Path(config.results_directory)
            / now.strftime("%Y-%m-%d")
            / f"{now:%H%M%S}_stokes-timing-test-phi1"
        )
    directory.mkdir(parents=True, exist_ok=False)
    return directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Acquire dynamic and static phi1 Stokes loopback data.")
    parser.add_argument("--max-voltage", type=float, default=DEFAULT_MAX_VOLTAGE)
    parser.add_argument("--min-voltage", type=float, default=0.0)
    parser.add_argument("--dynamic-frequency", type=float, default=0.5)
    parser.add_argument("--dynamic-duration", type=float, default=30.0)
    parser.add_argument("--static-step-voltage", type=float, default=0.01)
    parser.add_argument("--static-cycles", type=int, default=1)
    parser.add_argument("--samples-per-step", type=int, default=2)
    parser.add_argument("--settle-time", type=float, default=0.05)
    parser.add_argument("--turnaround-settle-time", type=float, default=0.10)
    parser.add_argument("--output", type=Path, default=None, help="Experiment directory")
    parser.add_argument("--dynamic-only", action="store_true")
    parser.add_argument("--static-only", action="store_true")
    return parser


def _validate(args: argparse.Namespace, config: PolarizationLockConfig) -> None:
    if args.dynamic_only and args.static_only:
        raise ValueError("--dynamic-only and --static-only cannot be used together")
    for name in ("min_voltage", "max_voltage", "static_step_voltage", "settle_time", "turnaround_settle_time"):
        if not math.isfinite(getattr(args, name)):
            raise ValueError(f"--{name.replace('_', '-')} must be finite")
    if not 0 <= args.min_voltage < args.max_voltage <= config.rp_output_max_voltage:
        raise ValueError("Voltage range must satisfy configured RP limits")
    if args.dynamic_frequency <= 0 or args.dynamic_duration <= 0:
        raise ValueError("Dynamic frequency and duration must be positive")
    if args.static_step_voltage <= 0 or args.settle_time < 0 or args.turnaround_settle_time < 0:
        raise ValueError("Step voltage must be positive and waits cannot be negative")
    if args.static_cycles < 1 or args.samples_per_step < 1:
        raise ValueError("Static cycles and samples per step must be at least one")
    intervals = (args.max_voltage - args.min_voltage) / args.static_step_voltage
    if not math.isclose(intervals, round(intervals), abs_tol=1e-9):
        raise ValueError("static-step-voltage must divide the voltage range")


def _scope_setup(rp: RPController) -> tuple[object, dict[str, object]]:
    scope = rp.p.rp.scope
    names = ("input1", "input2", "duration", "decimation", "trigger_source", "ch1_active", "ch2_active")
    previous = {name: getattr(scope, name) for name in names}
    scope.input1 = "in1"
    scope.duration = 0.01
    scope.decimation = 64
    scope.trigger_source = "immediately"
    scope.ch1_active = True
    scope.ch2_active = False
    return scope, previous


def _read_in1(scope: object) -> dict[str, float | int]:
    started = time.monotonic()
    trace = np.asarray(scope.single(timeout=3.0), dtype=float)  # type: ignore[attr-defined]
    ended = time.monotonic()
    trace = np.asarray(trace[0] if trace.ndim > 1 else trace, dtype=float).ravel()
    if trace.size == 0:
        raise RuntimeError("Red Pitaya scope returned an empty IN1 trace")
    return {
        "rp_in1_sample_monotonic_s": 0.5 * (started + ended),
        "rp_in1_read_start_monotonic_s": started,
        "rp_in1_read_end_monotonic_s": ended,
        "rp_in1_read_duration_s": ended - started,
        "rp_in1_voltage_v": float(np.median(trace)),
        "rp_in1_mean_v": float(np.mean(trace)),
        "rp_in1_median_v": float(np.median(trace)),
        "rp_in1_min_v": float(np.min(trace)),
        "rp_in1_max_v": float(np.max(trace)),
        "rp_in1_std_v": float(np.std(trace)),
        "rp_in1_n_samples": int(trace.size),
    }


def _read_pax(pax: PAXController) -> tuple[object, dict[str, float]]:
    started = time.monotonic()
    reading = pax.read_polarization()
    ended = time.monotonic()
    return reading, {
        "pax_sample_mid_monotonic_s": 0.5 * (started + ended),
        "pax_read_start_monotonic_s": started,
        "pax_read_end_monotonic_s": ended,
        "pax_read_duration_s": ended - started,
    }


def _pax_fields(reading: object, gain: float | None, measured_voltage: float) -> dict[str, object]:
    s1, s2, s3 = reading.s1, reading.s2, reading.s3  # type: ignore[attr-defined]
    return {
        "actuator_gain_v_per_v": gain,
        "actuator_voltage_est_v": measured_voltage * gain if gain is not None else float("nan"),
        "s1": s1, "s2": s2, "s3": s3,
        "s23_radius": math.hypot(s2, s3),
        "stokes_norm": math.sqrt(s1 * s1 + s2 * s2 + s3 * s3),
        "stokes_phase_wrapped_rad": math.atan2(-s3, s2),
        "dop": reading.dop, "theta": reading.theta, "eta": reading.eta,  # type: ignore[attr-defined]
        "ptotal": reading.ptotal, "pax_timestamp": reading.timestamp,  # type: ignore[attr-defined]
        "revisions": reading.revisions, "adc_min": reading.adc_min,  # type: ignore[attr-defined]
        "adc_max": reading.adc_max, "rev_time": reading.rev_time,  # type: ignore[attr-defined]
    }


def _dynamic_header() -> list[str]:
    return [
        "sample", "utc", "rp_commanded_frequency_hz", "nominal_drive_offset_v",
        "nominal_drive_amplitude_v", "rp_in1_sample_monotonic_s", "rp_in1_voltage_v",
        "rp_in1_read_start_monotonic_s", "rp_in1_read_end_monotonic_s", "rp_in1_read_duration_s",
        "rp_in1_mean_v", "rp_in1_median_v", "rp_in1_min_v", "rp_in1_max_v", "rp_in1_std_v",
        "rp_in1_n_samples", "pax_sample_mid_monotonic_s", "pax_read_start_monotonic_s",
        "pax_read_end_monotonic_s", "pax_read_duration_s", "drive_elapsed_s",
        "reconstructed_command_phase_rad", "reconstructed_command_phase_wrapped_rad",
        "reconstructed_command_voltage_v", "s1", "s2", "s3", "dop", "theta", "eta", "ptotal",
        "s23_radius", "stokes_norm", "stokes_phase_wrapped_rad", "pax_timestamp", "revisions",
        "adc_min", "adc_max", "rev_time", "actuator_voltage_est_v",
        "actuator_gain_v_per_v",
    ]


def _static_header() -> list[str]:
    return [
        "sample", "utc", "cycle_index", "sweep_direction", "sweep_step_index", "global_step_index",
        "replicate_index", "is_turnaround_point", "commanded_rp_voltage_v", "rp_in1_voltage_v",
        "voltage_error_v", "rp_in1_sample_monotonic_s", "rp_in1_read_start_monotonic_s",
        "rp_in1_read_end_monotonic_s", "rp_in1_read_duration_s", "rp_in1_mean_v", "rp_in1_median_v",
        "rp_in1_min_v", "rp_in1_max_v", "rp_in1_std_v", "rp_in1_n_samples",
        "pax_sample_mid_monotonic_s", "pax_read_start_monotonic_s", "pax_read_end_monotonic_s",
        "pax_read_duration_s", "actuator_gain_v_per_v", "actuator_voltage_est_v", "s1", "s2", "s3",
        "s23_radius", "stokes_norm", "stokes_phase_wrapped_rad", "stokes_phase_wrapped_deg",
        "dop", "theta", "eta", "ptotal", "pax_timestamp", "revisions", "adc_min", "adc_max", "rev_time",
    ]


def run(args: argparse.Namespace) -> Path:
    config = PolarizationLockConfig()
    config.pax_measurement_wait_s = PAX_WAIT_S
    config.pax_retry_wait_s = PAX_WAIT_S
    _validate(args, config)
    directory = _experiment_directory(config, args.output)
    gain = config.phi1_actuator_volts_per_rp_volt
    metadata = {
        "started_at": _utc_now(),
        "configuration": vars(args),
        "dynamic_csv": str(directory / "dynamic.csv"),
        "static_csv": str(directory / "static.csv"),
        "actuator_gain_v_per_v": gain,
        "loopback": "OUT1 -> phi1 actuator and OUT1 -> IN1",
        "synchronization_note": "IN1 and PAX have independent midpoint timestamps; no correction or fitting is applied.",
    }
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    print(f"Output directory: {directory}", flush=True)
    print(f"commanded frequency: {args.dynamic_frequency:.6f} Hz", flush=True)
    print(f"dynamic duration: {args.dynamic_duration:g} s", flush=True)
    print(f"nominal voltage range: {args.min_voltage:.6f} -> {args.max_voltage:.6f} V", flush=True)
    print("RP OUT1 -> IN1 loopback measurement ENABLED", flush=True)

    rp, pax = RPController(config), PAXController(config)
    scope = None
    scope_previous = None
    sample = 0
    try:
        pax.connect()
        rp.connect()
        scope, scope_previous = _scope_setup(rp)
        if not args.static_only:
            offset = (args.min_voltage + args.max_voltage) / 2
            amplitude = (args.max_voltage - args.min_voltage) / 2
            set_before = time.monotonic()
            rp.set_phi1_sine(offset=offset, amplitude=amplitude, frequency_hz=args.dynamic_frequency)
            set_after = time.monotonic()
            drive_start = 0.5 * (set_before + set_after)
            with (directory / "dynamic.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=_dynamic_header())
                writer.writeheader()
                handle.flush()
                dynamic_start = time.monotonic()
                while time.monotonic() - dynamic_start < args.dynamic_duration:
                    adc = _read_in1(scope)
                    reading, pax_times = _read_pax(pax)
                    elapsed = pax_times["pax_sample_mid_monotonic_s"] - drive_start
                    phase = 2 * math.pi * args.dynamic_frequency * elapsed
                    row = {
                        "sample": sample, "utc": _utc_now(), "rp_commanded_frequency_hz": args.dynamic_frequency,
                        "nominal_drive_offset_v": offset, "nominal_drive_amplitude_v": amplitude,
                        **adc, **pax_times, "drive_elapsed_s": elapsed,
                        "reconstructed_command_phase_rad": phase,
                        "reconstructed_command_phase_wrapped_rad": math.atan2(math.sin(phase), math.cos(phase)),
                        "reconstructed_command_voltage_v": offset + amplitude * math.sin(phase),
                        **_pax_fields(reading, gain, float(adc["rp_in1_voltage_v"])),
                    }
                    writer.writerow(row)
                    handle.flush()
                    sample += 1
        if not args.dynamic_only:
            rp.set_output_voltage(0.0, 0.0)
            time.sleep(0.2)
            values = np.linspace(args.min_voltage, args.max_voltage,
                                 round((args.max_voltage - args.min_voltage) / args.static_step_voltage) + 1)
            with (directory / "static.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=_static_header())
                writer.writeheader()
                handle.flush()
                static_sample = 0
                for cycle in range(1, args.static_cycles + 1):
                    for direction, sequence in (("rising", values), ("falling", values[::-1])):
                        for step_index, command_v in enumerate(sequence):
                            rp.set_output_voltage(float(command_v), 0.0)
                            time.sleep(args.settle_time)
                            for replicate in range(args.samples_per_step):
                                adc = _read_in1(scope)
                                reading, pax_times = _read_pax(pax)
                                measured = float(adc["rp_in1_voltage_v"])
                                pfields = _pax_fields(reading, gain, measured)
                                row = {
                                    "sample": static_sample, "utc": _utc_now(), "cycle_index": cycle,
                                    "sweep_direction": direction, "sweep_step_index": step_index,
                                    "global_step_index": static_sample, "replicate_index": replicate,
                                    "is_turnaround_point": bool(command_v == args.max_voltage),
                                    "commanded_rp_voltage_v": float(command_v), "voltage_error_v": measured - command_v,
                                    **adc, **pax_times, **pfields,
                                    "stokes_phase_wrapped_deg": math.degrees(pfields["stokes_phase_wrapped_rad"]),
                                }
                                writer.writerow(row)
                                handle.flush()
                                static_sample += 1
                            if command_v == args.max_voltage:
                                time.sleep(args.turnaround_settle_time)
    except KeyboardInterrupt:
        print(f"Interrupted after {sample} dynamic samples.", flush=True)
    finally:
        try:
            if rp.p is not None:
                rp.set_output_zero()
                if scope_previous is not None:
                    for name, value in scope_previous.items():
                        setattr(rp.p.rp.scope, name, value)
        finally:
            rp.disconnect()
            pax.disconnect()
    metadata["finished_at"] = _utc_now()
    metadata["status"] = "completed"
    (directory / "metadata.json").write_text(json.dumps(metadata, indent=2, default=str) + "\n")
    print(f"CSV/data directory saved to {directory}", flush=True)
    return directory


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run(args)
    except (RuntimeError, ValueError) as exc:
        _parser().error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
