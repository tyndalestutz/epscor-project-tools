from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .analyzer import AnalyzerController, AnalyzerError
from .basler import BaslerCamera, BaslerError, FrameRecord, save_record
from .config import CameraConfig, SYSTEM_TEST_ANALYZER_CONFIG


def _states(axis: str) -> list[float]:
    if axis == "eom":
        return [0.0, 4.0, 8.0, 12.0, 16.0, 0.0]
    return [0.0, 1.0, 2.0, 3.0, 4.0, 0.0]


def _spatial_metrics(image: np.ndarray, reference: np.ndarray) -> tuple[float, float]:
    a = np.asarray(image, dtype=float)
    b = np.asarray(reference, dtype=float)
    a_norm = a / max(float(np.mean(a)), 1e-12)
    b_norm = b / max(float(np.mean(b)), 1e-12)
    difference_rms = float(np.sqrt(np.mean((a_norm - b_norm) ** 2)))
    a_flat, b_flat = a_norm.ravel(), b_norm.ravel()
    correlation = float(np.corrcoef(a_flat, b_flat)[0, 1]) if np.std(a_flat) and np.std(b_flat) else float("nan")
    return difference_rms, correlation


def _camera_config(args: argparse.Namespace) -> CameraConfig:
    return CameraConfig(
        serial_number=args.serial,
        exposure_us=args.exposure_us,
        gain=0.0,
        pixel_format="Mono12",
        timeout_ms=5_000,
        grab_retries=2,
    )


def _preflight_payload(args: argparse.Namespace) -> dict:
    config = SYSTEM_TEST_ANALYZER_CONFIG
    return {
        "rp_hostname": config.rp_hostname,
        "routes": {"out1": "EOM DC", "out2": "LCVR zero-mean square", "in2": "photodiode"},
        "documented_gains": {
            "eom_device_v_per_rp_command_v": config.eom_device_volts_per_rp_volt,
            "lcvr_cell_vpp_per_rp_amplitude_v": config.lcvr_cell_vpp_per_rp_volt,
        },
        "hard_limits": {
            "driver_output_v": config.eom_max_device_v,
            "lcvr_cell_vpp": config.lcvr_max_cell_vpp,
        },
        "test_limits": {
            "eom_device_v": max(_states("eom")),
            "eom_rp_command_v": max(_states("eom")) / float(config.eom_device_volts_per_rp_volt),
            "lcvr_cell_vpp": max(_states("lcvr")),
            "lcvr_rp_peak_amplitude_v": max(_states("lcvr")) / float(config.lcvr_cell_vpp_per_rp_volt),
            "lcvr_frequency_hz": config.lcvr_drive_frequency_hz,
            "lcvr_dc_offset_v": 0.0,
            "lcvr_terminal_gain_verified": config.lcvr_terminal_gain_verified,
            "lcvr_bipolar_output_verified": config.lcvr_bipolar_output_verified,
            "lcvr_unverified_command_ceiling_vpp": config.lcvr_unverified_max_cell_vpp,
        },
        "camera": asdict(_camera_config(args)),
    }


def run_baseline(args: argparse.Namespace) -> int:
    """Connect with both outputs off and verify IN2 plus camera acquisition."""
    payload = _preflight_payload(args)
    print(json.dumps(payload, indent=2))
    with AnalyzerController(SYSTEM_TEST_ANALYZER_CONFIG) as analyzer, BaslerCamera(_camera_config(args)) as camera:
        pd = analyzer.read_photodiode()
        frame = camera.acquire_average(args.camera_average)
        print(f"IN2 PD: mean={pd.mean_voltage:.6g} V std={pd.std_voltage:.3g} V range=[{pd.min_voltage:.6g}, {pd.max_voltage:.6g}]")
        print(
            f"camera: mean={frame.statistics.mean:.3f} max={frame.statistics.maximum:g} "
            f"sat={frame.statistics.saturation_fraction:.3%}"
        )
    print("Baseline complete; OUT1 and OUT2 are off")
    return 0


def run_axis(args: argparse.Namespace) -> int:
    axis = args.axis
    values = _states(axis)
    # Validate the entire requested sweep before connecting to either device.
    validator = AnalyzerController(SYSTEM_TEST_ANALYZER_CONFIG)
    for value in values:
        if axis == "eom":
            validator.eom_rp_command_for_device_voltage(value)
        else:
            validator.lcvr_rp_amplitude_for_cell_vpp(value)
    run_dir = args.output_root / datetime.now().strftime(f"%Y%m%d_%H%M%S_{axis}-response")
    frames_dir = run_dir / "frames"
    run_dir.mkdir(parents=True, exist_ok=False)
    preflight = _preflight_payload(args)
    (run_dir / "run.json").write_text(
        json.dumps({"created_utc": datetime.now(timezone.utc).isoformat(), "axis": axis, **preflight}, indent=2) + "\n",
        encoding="utf-8",
    )

    rows: list[dict] = []
    reference: FrameRecord | None = None
    with AnalyzerController(SYSTEM_TEST_ANALYZER_CONFIG) as analyzer, BaslerCamera(_camera_config(args)) as camera:
        for index, value in enumerate(values):
            if axis == "eom":
                command = analyzer.set_eom_voltage(value)
                analyzer.set_lcvr_vpp(0.0)
                dwell = args.eom_settle_s
            else:
                analyzer.set_eom_voltage(0.0)
                command = analyzer.set_lcvr_vpp(value)
                dwell = args.lcvr_settle_s
            analyzer.settle(dwell)
            pd_samples = [analyzer.read_photodiode() for _ in range(args.pd_average)]
            frame, sources = camera.acquire_average_with_sources(args.camera_average)
            for raw_index, source in enumerate(sources):
                save_record(source, run_dir / "raw_frames" / f"state_{index:02d}" / f"frame_{raw_index:03d}")
            save_record(frame, frames_dir / f"state_{index:02d}")
            if reference is None:
                reference = frame
            difference_rms, correlation = _spatial_metrics(frame.image, reference.image)
            pd_means = np.asarray([sample.mean_voltage for sample in pd_samples])
            row = {
                "state_index": index,
                "axis": axis,
                "requested_device_value": value,
                "device_units": "V" if axis == "eom" else "Vpp",
                "rp_command_or_amplitude_v": command,
                "lcvr_frequency_hz": SYSTEM_TEST_ANALYZER_CONFIG.lcvr_drive_frequency_hz if axis == "lcvr" else 0.0,
                "pd_mean_v": float(np.mean(pd_means)),
                "pd_repeat_std_v": float(np.std(pd_means)),
                "pd_trace_std_v": float(np.mean([sample.std_voltage for sample in pd_samples])),
                "camera_mean": frame.statistics.mean,
                "camera_std": frame.statistics.std,
                "camera_max": frame.statistics.maximum,
                "camera_saturation_fraction": frame.statistics.saturation_fraction,
                "normalized_difference_rms": difference_rms,
                "normalized_correlation_to_reference": correlation,
                "timestamp_utc": frame.timestamp_utc,
            }
            rows.append(row)
            print(
                f"{axis}={value:g} {row['device_units']} command={command:.7f} RP V "
                f"PD={row['pd_mean_v']:.6g} V camera_mean={row['camera_mean']:.3f} "
                f"sat={row['camera_saturation_fraction']:.3%} corr={correlation:.5f}"
            )

    with (run_dir / "measurements.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    unique_rows = rows[:-1]
    pd_values = np.asarray([row["pd_mean_v"] for row in unique_rows])
    camera_values = np.asarray([row["camera_mean"] for row in unique_rows])
    summary = {
        "axis": axis,
        "pd_range_v": float(np.ptp(pd_values)),
        "camera_mean_range": float(np.ptp(camera_values)),
        "zero_return_pd_delta_v": float(rows[-1]["pd_mean_v"] - rows[0]["pd_mean_v"]),
        "zero_return_camera_delta": float(rows[-1]["camera_mean"] - rows[0]["camera_mean"]),
        "maximum_saturation_fraction": max(row["camera_saturation_fraction"] for row in rows),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved response test to {run_dir}; OUT1 and OUT2 are off")
    return 0


def hold_lcvr(args: argparse.Namespace) -> int:
    """Hold one prevalidated LCVR level for a human-readable driver check."""
    validator = AnalyzerController(SYSTEM_TEST_ANALYZER_CONFIG)
    command = validator.lcvr_rp_amplitude_for_cell_vpp(args.lcvr_vpp)
    print(
        f"Preparing nominal {args.lcvr_vpp:g} Vpp LCVR drive: +/-{command:.7f} RP V "
        f"at {SYSTEM_TEST_ANALYZER_CONFIG.lcvr_drive_frequency_hz:g} Hz, zero DC",
        flush=True,
    )
    with AnalyzerController(SYSTEM_TEST_ANALYZER_CONFIG) as analyzer:
        analyzer.set_eom_voltage(0.0)
        analyzer.set_lcvr_vpp(args.lcvr_vpp)
        print(f"LCVR drive active for up to {args.hold_s:g} s; OUT1 is zero", flush=True)
        deadline = time.monotonic() + args.hold_s
        while time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    print("LCVR drive ended; OUT1 and OUT2 are off", flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Conservative EOM/LCVR response system test")
    parser.add_argument("command", choices=("preflight", "baseline", "run", "hold-lcvr"))
    parser.add_argument("--axis", choices=("eom", "lcvr"), default="eom")
    parser.add_argument("--serial", default="24934175")
    parser.add_argument("--exposure-us", type=float, default=100.0)
    parser.add_argument("--camera-average", type=int, default=4)
    parser.add_argument("--pd-average", type=int, default=3)
    parser.add_argument("--eom-settle-s", type=float, default=0.3)
    parser.add_argument("--lcvr-settle-s", type=float, default=0.75)
    parser.add_argument("--lcvr-vpp", type=float, default=0.5)
    parser.add_argument("--hold-s", type=float, default=300.0)
    parser.add_argument("--output-root", type=Path, default=Path("experiments/polarization_visualization"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.camera_average < 1 or args.pd_average < 1:
            raise ValueError("average counts must be positive")
        if args.command == "preflight":
            print(json.dumps(_preflight_payload(args), indent=2))
            return 0
        if args.command == "baseline":
            return run_baseline(args)
        if args.command == "hold-lcvr":
            return hold_lcvr(args)
        return run_axis(args)
    except (AnalyzerError, BaslerError, ValueError) as exc:
        print(f"systems test failed safely: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nSystems test interrupted; outputs and camera closed", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
