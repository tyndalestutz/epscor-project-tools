#!/usr/bin/env python3
"""Verify OUT1/OUT2 loopbacks with held DC levels and bounded sines.

This is an electrical-reference diagnostic, not an actuator calibration. It
preserves raw dual-input scope traces, records ASG readbacks and host-side ramp
timing, and always returns both RP outputs to zero.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

import numpy as np

from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.rp_interface import RPController


def capture(scope: Any) -> tuple[np.ndarray, np.ndarray, float, float]:
    started = time.monotonic()
    result = scope.single(timeout=float(scope.duration) + 5.0)
    elapsed = time.monotonic() - started
    if len(result) < 2:
        raise RuntimeError("Dual-input scope capture did not return IN1 and IN2")
    in1, in2 = (np.asarray(result[index], dtype=float).copy() for index in (0, 1))
    if in1.size == 0 or in1.shape != in2.shape:
        raise RuntimeError("Dual-input scope capture returned empty or unequal traces")
    return in1, in2, float(scope.sampling_time), elapsed


def linear_summary(command: np.ndarray, measured: np.ndarray) -> dict[str, float]:
    design = np.column_stack((np.ones(len(command)), command))
    intercept, slope = np.linalg.lstsq(design, measured, rcond=None)[0]
    residual = measured - design @ np.asarray((intercept, slope))
    return {
        "intercept_v": float(intercept),
        "slope_measured_per_command": float(slope),
        "residual_rms_v": float(np.sqrt(np.mean(residual ** 2))),
        "correlation": float(np.corrcoef(command, measured)[0, 1]),
    }


def sine_summary(values: np.ndarray, dt: float, frequency_hz: float) -> dict[str, float]:
    time_s = np.arange(len(values), dtype=float) * dt
    phase = 2.0 * np.pi * frequency_hz * time_s
    design = np.column_stack((np.ones(len(values)), np.sin(phase), np.cos(phase)))
    coefficients = np.linalg.lstsq(design, values, rcond=None)[0]
    fitted = design @ coefficients
    return {
        "center_v": float(coefficients[0]),
        "amplitude_v": float(np.hypot(coefficients[1], coefficients[2])),
        "phase_deg_relative_to_capture_start": float(
            np.degrees(np.arctan2(coefficients[2], coefficients[1]))
        ),
        "residual_rms_v": float(np.sqrt(np.mean((values - fitted) ** 2))),
    }


def run(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    config = PolarizationLockConfig()
    levels = np.linspace(0.0, config.cross_sweep_sine_center_voltage * 2.0, 5)
    rp = RPController(config)
    summary: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Electrical OUT1/OUT2 loopback verification; not delivered actuator-voltage calibration.",
        "safe_command_range_v": [float(levels[0]), float(levels[-1])],
        "reference_map_under_test": {"out1": "in1", "out2": "in2"},
        "static": {},
        "sine": {},
    }
    static_rows: list[dict[str, Any]] = []
    raw: dict[str, np.ndarray] = {}
    try:
        rp.connect()
        with rp.dual_reference_monitor(block_duration_s=1.0) as scope:
            scope.decimation = 8192
            for axis in ("phi1", "phi2"):
                axis_rows = []
                for level in levels:
                    v1, v2 = (float(level), 0.0) if axis == "phi1" else (0.0, float(level))
                    ramp = rp.ramp_output_voltage(
                        v1, v2,
                        duration_s=config.cross_sweep_bias_ramp_s,
                        updates_per_s=config.cross_sweep_bias_ramp_updates_per_s,
                    )
                    time.sleep(0.5)
                    in1, in2, dt, capture_elapsed = capture(scope)
                    row = {
                        "axis": axis,
                        "command_v": float(level),
                        "asg1_readback_v": float(rp.asg1.offset),
                        "asg2_readback_v": float(rp.asg2.offset),
                        "in1_mean_v": float(np.mean(in1)),
                        "in1_std_v": float(np.std(in1)),
                        "in2_mean_v": float(np.mean(in2)),
                        "in2_std_v": float(np.std(in2)),
                        "scope_dt_s": dt,
                        "scope_samples": len(in1),
                        "scope_capture_elapsed_s": capture_elapsed,
                        "ramp_requested_duration_s": ramp["requested_duration_s"],
                        "ramp_host_elapsed_s": ramp["host_elapsed_s"],
                        "ramp_offset_writes": ramp["offset_writes"],
                    }
                    static_rows.append(row)
                    axis_rows.append(row)
                    key = f"static_{axis}_{len(axis_rows) - 1}"
                    raw[f"{key}_in1_v"] = in1
                    raw[f"{key}_in2_v"] = in2
                    raw[f"{key}_dt_s"] = np.asarray(dt)
                command = np.asarray([row["command_v"] for row in axis_rows])
                summary["static"][axis] = {
                    "in1": linear_summary(command, np.asarray([row["in1_mean_v"] for row in axis_rows])),
                    "in2": linear_summary(command, np.asarray([row["in2_mean_v"] for row in axis_rows])),
                    "ramp_host_elapsed_s": [row["ramp_host_elapsed_s"] for row in axis_rows],
                }
                rp.ramp_output_voltage(
                    0.0, 0.0,
                    duration_s=config.cross_sweep_bias_ramp_s,
                    updates_per_s=config.cross_sweep_bias_ramp_updates_per_s,
                )

            scope.decimation = 65536
            for axis in ("phi1", "phi2"):
                settings = rp.set_continuous_sine(
                    target_axis=axis, fixed_voltage=0.0,
                    center=config.cross_sweep_sine_center_voltage,
                    amplitude=config.cross_sweep_sine_amplitude_voltage,
                    frequency_hz=config.cross_sweep_sine_frequency_hz,
                )
                time.sleep(0.25)
                in1, in2, dt, capture_elapsed = capture(scope)
                key = f"sine_{axis}"
                raw[f"{key}_in1_v"] = in1
                raw[f"{key}_in2_v"] = in2
                raw[f"{key}_dt_s"] = np.asarray(dt)
                summary["sine"][axis] = {
                    "settings": settings,
                    "scope_samples": len(in1),
                    "scope_dt_s": dt,
                    "scope_capture_elapsed_s": capture_elapsed,
                    "in1": sine_summary(in1, dt, settings["frequency_hz"]),
                    "in2": sine_summary(in2, dt, settings["frequency_hz"]),
                }
                rp.ramp_output_voltage(
                    0.0, 0.0,
                    duration_s=config.cross_sweep_bias_ramp_s,
                    updates_per_s=config.cross_sweep_bias_ramp_updates_per_s,
                )
        summary["status"] = "completed"
    except BaseException as exc:
        summary["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            rp.disconnect()
        finally:
            summary["finished_at"] = datetime.now(timezone.utc).isoformat()
            (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
            if static_rows:
                with (output / "static.csv").open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=static_rows[0].keys())
                    writer.writeheader()
                    writer.writerows(static_rows)
            if raw:
                np.savez_compressed(output / "raw_traces.npz", **raw)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new output directory; must not already exist")
    args = parser.parse_args()
    result = run(args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
