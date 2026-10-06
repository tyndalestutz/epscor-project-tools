#!/usr/bin/env python3
"""Audit PAX latency against command and FPGA-timed RP references.

The scan never rewrites acquisition data. It associates each PAX record's raw
host transaction midpoint with candidate physical times and measures how well
rising and falling branches agree at equal drive voltage. When a run contains
FPGA-timed RP traces, those measured voltages are the primary reference and
nominal ASG timing is retained as a comparison. Physical hysteresis can also
separate branches, so the result is a timing calibration diagnostic rather
than an instrument-provided timestamp.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import numpy as np


def _unit(values: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(values, axis=1)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 0):
        raise ValueError("Stokes rows contain a non-finite or zero vector")
    return values / norms[:, None]


def _rows_for_segment(rows: list[dict[str, str]], segment_id: int) -> list[dict[str, str]]:
    return [
        row for row in rows
        if int(row["segment_id"]) == segment_id and row["phase"] == "analysis"
    ]


def _branch_curve(
    rows: list[dict[str, str]],
    segment: dict[str, Any],
    lags_s: np.ndarray,
    observable: str,
    grid_fraction: float = 0.75,
) -> np.ndarray:
    midpoint = np.asarray([float(row["pax_transaction_midpoint_s"]) for row in rows])
    if observable == "stokes":
        values = _unit(np.asarray([
            [float(row[name]) for name in ("s1", "s2", "s3")] for row in rows
        ]))
    else:
        values = np.asarray([float(row[observable]) for row in rows])
    frequency = float(segment["frequency_hz"])
    center = float(segment["center_v"])
    amplitude = float(segment["amplitude_v"])
    drive_started = float(segment["drive_started_s"])
    grid = np.linspace(
        center - grid_fraction * amplitude,
        center + grid_fraction * amplitude,
        31,
    )
    result = []
    for lag_s in lags_s:
        unwrapped = 2 * np.pi * frequency * (midpoint - lag_s - drive_started)
        cycles = np.floor(unwrapped / (2 * np.pi)).astype(int)
        phase = np.mod(unwrapped, 2 * np.pi)
        voltage = center + amplitude * np.sin(phase)
        rising = np.cos(phase) > 0
        per_cycle = []
        for cycle in sorted(set(cycles)):
            up = (cycles == cycle) & rising
            down = (cycles == cycle) & ~rising
            if min(int(up.sum()), int(down.sum())) < 8:
                continue
            if any(
                voltage[mask].min() > grid[0] or voltage[mask].max() < grid[-1]
                for mask in (up, down)
            ):
                continue
            up_order = np.argsort(voltage[up])
            down_order = np.argsort(voltage[down])
            if observable == "stokes":
                up_value = np.column_stack([
                    np.interp(grid, np.sort(voltage[up]), values[up][up_order, index])
                    for index in range(3)
                ])
                down_value = np.column_stack([
                    np.interp(grid, np.sort(voltage[down]), values[down][down_order, index])
                    for index in range(3)
                ])
                up_value = _unit(up_value)
                down_value = _unit(down_value)
                angles = np.degrees(np.arccos(np.clip(
                    np.sum(up_value * down_value, axis=1), -1.0, 1.0,
                )))
                per_cycle.append(float(np.sqrt(np.mean(angles ** 2))))
            else:
                up_value = np.interp(
                    grid, np.sort(voltage[up]), values[up][up_order],
                )
                down_value = np.interp(
                    grid, np.sort(voltage[down]), values[down][down_order],
                )
                scale = 1.0 if observable == "dop" else float(
                    np.ptp(np.concatenate((up_value, down_value)))
                )
                if scale > 0:
                    per_cycle.append(float(
                        np.sqrt(np.mean((up_value - down_value) ** 2)) / scale
                    ))
        result.append(float(np.mean(per_cycle)) if per_cycle else np.nan)
    return np.asarray(result)


def _observable_values(
    rows: list[dict[str, str]], observable: str,
) -> np.ndarray:
    if observable == "stokes":
        return _unit(np.asarray([
            [float(row[name]) for name in ("s1", "s2", "s3")] for row in rows
        ]))
    return np.asarray([float(row[observable]) for row in rows])


def _branch_mismatch(
    voltage: np.ndarray,
    rising: np.ndarray,
    values: np.ndarray,
    observable: str,
    grid: np.ndarray,
) -> float:
    masks = (rising, ~rising)
    if min(int(mask.sum()) for mask in masks) < 8:
        return np.nan
    if any(
        voltage[mask].min() > grid[0] or voltage[mask].max() < grid[-1]
        for mask in masks
    ):
        return np.nan
    interpolated = []
    for mask in masks:
        order = np.argsort(voltage[mask])
        if observable == "stokes":
            interpolated.append(_unit(np.column_stack([
                np.interp(grid, voltage[mask][order], values[mask][order, index])
                for index in range(3)
            ])))
        else:
            interpolated.append(np.interp(
                grid, voltage[mask][order], values[mask][order],
            ))
    up_value, down_value = interpolated
    if observable == "stokes":
        angles = np.degrees(np.arccos(np.clip(
            np.sum(up_value * down_value, axis=1), -1.0, 1.0,
        )))
        return float(np.sqrt(np.mean(angles ** 2)))
    scale = 1.0 if observable == "dop" else float(
        np.ptp(np.concatenate((up_value, down_value)))
    )
    if scale <= 0:
        return np.nan
    return float(np.sqrt(np.mean((up_value - down_value) ** 2)) / scale)


def _fpga_reference_for_segment(
    trace: Any,
    segment: dict[str, Any],
) -> dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    segment_id = int(segment["segment_id"])
    actuator_id = 1 if segment["target_actuator"] == "phi1" else 2
    voltage_name = "in1_v" if actuator_id == 1 else "in2_v"
    mask = (
        (trace["segment_id"] == segment_id)
        & (trace["actuator_id"] == actuator_id)
        & trace["valid_measurement"].astype(bool)
        & ~trace["input_clipped"].astype(bool)
    )
    result = {}
    for capture_id in np.unique(trace["capture_id"][mask]):
        selected = mask & (trace["capture_id"] == capture_id)
        time_s = np.asarray(trace["time_s"][selected], dtype=float)
        voltage = np.asarray(trace[voltage_name][selected], dtype=float)
        order = np.argsort(time_s)
        time_s, voltage = time_s[order], voltage[order]
        if len(time_s) < 3 or np.any(np.diff(time_s) <= 0):
            continue
        derivative = np.gradient(voltage, time_s)
        result[int(capture_id)] = (time_s, voltage, derivative)
    return result


def _measured_branch_curve(
    rows: list[dict[str, str]],
    references: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]],
    lags_s: np.ndarray,
    observable: str,
    maximum_lag_s: float,
    grid_fraction: float = 0.75,
) -> tuple[np.ndarray, np.ndarray]:
    """Score latency against RP voltage without interpolating capture gaps.

    Rows are assigned to a capture using their raw midpoint. Requiring that
    midpoint to be at least ``maximum_lag_s`` after capture start makes the
    included row set invariant across the entire lag scan.
    """
    midpoint = np.asarray([float(row["pax_transaction_midpoint_s"]) for row in rows])
    values = _observable_values(rows, observable)
    result = []
    contributing_captures = []
    for lag_s in lags_s:
        per_capture = []
        for time_s, measured_v, derivative in references.values():
            selected = (
                (midpoint >= time_s[0] + maximum_lag_s)
                & (midpoint <= time_s[-1])
            )
            if not np.any(selected):
                continue
            physical_time = midpoint[selected] - lag_s
            voltage = np.interp(physical_time, time_s, measured_v)
            rising = np.interp(physical_time, time_s, derivative) > 0
            center = 0.5 * (float(np.nanmin(measured_v)) + float(np.nanmax(measured_v)))
            amplitude = 0.5 * (float(np.nanmax(measured_v)) - float(np.nanmin(measured_v)))
            grid = np.linspace(
                center - grid_fraction * amplitude,
                center + grid_fraction * amplitude,
                31,
            )
            score = _branch_mismatch(
                voltage, rising, values[selected], observable, grid,
            )
            if np.isfinite(score):
                per_capture.append(score)
        result.append(float(np.mean(per_capture)) if per_capture else np.nan)
        contributing_captures.append(len(per_capture))
    return np.asarray(result), np.asarray(contributing_captures)


def _metric_summary(
    curve: np.ndarray,
    lags: np.ndarray,
    configured: float,
    units: str,
) -> dict[str, Any]:
    if not np.any(np.isfinite(curve)):
        raise ValueError("No valid rising/falling comparisons in latency scan")
    configured_index = int(np.argmin(np.abs(lags - configured)))
    best_index = int(np.nanargmin(curve))
    best = float(curve[best_index])
    return {
        "units": units,
        "best_lag_s": float(lags[best_index]),
        "best_value": best,
        "zero_lag_value": float(curve[0]),
        "configured_lag_s": configured,
        "configured_lag_value": float(curve[configured_index]),
        "configured_improvement_vs_zero_fraction": float(
            (curve[0] - curve[configured_index]) / curve[0]
        ),
    }


def _combined_summary(
    curves: list[np.ndarray], lags: np.ndarray, configured: float,
) -> dict[str, Any]:
    normalized = []
    for curve in curves:
        best = float(np.nanmin(curve))
        if np.isfinite(best) and best > 0:
            normalized.append(curve / best)
    if not normalized:
        raise ValueError("No finite metric curves available for combined score")
    combined = np.nanmedian(np.vstack(normalized), axis=0)
    best_index = int(np.nanargmin(combined))
    configured_index = int(np.argmin(np.abs(lags - configured)))
    best = float(combined[best_index])
    bands = {}
    for percentage in (5, 10):
        indices = np.flatnonzero(combined <= best * (1 + percentage / 100))
        bands[f"within_{percentage}_percent_of_best_s"] = [
            float(lags[indices[0]]), float(lags[indices[-1]])
        ]
    return {
        "best_lag_s": float(lags[best_index]),
        "best_score": best,
        "zero_lag_score": float(combined[0]),
        "configured_lag_score": float(combined[configured_index]),
        "configured_improvement_vs_zero_fraction": float(
            (combined[0] - combined[configured_index]) / combined[0]
        ),
        **bands,
    }


def analyze(run_directory: Path, maximum_lag_s: float, step_s: float) -> dict[str, Any]:
    acquisition = json.loads((run_directory / "acquisition.json").read_text())
    with (run_directory / "data.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"pax_transaction_midpoint_s", "segment_id", "phase", "s1", "s2",
                "s3", "dop", "pax_ptotal"}
    missing = required - set(rows[0]) if rows else required
    if missing:
        raise ValueError(f"Run lacks latency-audit fields: {', '.join(sorted(missing))}")
    lags = np.arange(0.0, maximum_lag_s + step_s / 2, step_s)
    configured = float(acquisition["pax_latency_correction_s"])
    trace_path = run_directory / "drive_trace.npz"
    trace = np.load(trace_path) if trace_path.exists() else None
    reference_segments = acquisition["segments"]
    reference_captures = [
        capture for segment in reference_segments
        for capture in segment.get("reference_captures", [])
    ]
    fpga_available = bool(
        trace is not None
        and acquisition.get("reference_timing_primary") == "fpga_trigger_timestamp"
        and reference_captures
        and all(segment.get("reference_captures") for segment in reference_segments)
        and all(capture.get("source") == "fpga_trigger_timestamp" for capture in reference_captures)
    )
    nominal_curves = []
    measured_curves = []
    nominal_segments = []
    measured_segments = []
    for segment in acquisition["segments"]:
        selected = _rows_for_segment(rows, int(segment["segment_id"]))
        common = {
            "segment_id": int(segment["segment_id"]),
            "target_actuator": segment["target_actuator"],
            "analysis_rows": len(selected),
            "fraction_dop_below_0_9": float(np.mean([
                float(row["dop"]) < 0.9 for row in selected
            ])),
        }
        nominal_item = {**common, "metrics": {}}
        measured_item = {**common, "metrics": {}}
        references = _fpga_reference_for_segment(trace, segment) if fpga_available else {}
        if fpga_available:
            measured_item["fpga_reference_captures"] = len(references)
        for observable, label, units in (
            ("stokes", "stokes_branch_rms", "deg"),
            ("pax_ptotal", "power_branch_rms_normalized", "fraction"),
            ("dop", "dop_branch_rms", "absolute_dop"),
        ):
            curve = _branch_curve(selected, segment, lags, observable)
            nominal_curves.append(curve)
            nominal_item["metrics"][label] = _metric_summary(
                curve, lags, configured, units,
            )
            if fpga_available:
                measured_curve, counts = _measured_branch_curve(
                    selected, references, lags, observable, maximum_lag_s,
                )
                measured_curves.append(measured_curve)
                measured_item["metrics"][label] = {
                    **_metric_summary(measured_curve, lags, configured, units),
                    "contributing_captures_min": int(np.min(counts)),
                    "contributing_captures_max": int(np.max(counts)),
                }
        nominal_segments.append(nominal_item)
        if fpga_available:
            measured_segments.append(measured_item)
    analyses = {
        "nominal_command_clock": {
            "method": "Nominal ASG sine voltage and command start time.",
            "segments": nominal_segments,
            "combined": _combined_summary(nominal_curves, lags, configured),
        },
    }
    if fpga_available:
        analyses["fpga_measured_reference"] = {
            "method": (
                "Measured RP target voltage interpolated within each independently "
                "FPGA-timestamped capture; capture gaps are never interpolated."
            ),
            "segments": measured_segments,
            "combined": _combined_summary(measured_curves, lags, configured),
        }
    primary = "fpga_measured_reference" if fpga_available else "nominal_command_clock"
    result = {
        "run_directory": str(run_directory),
        "method": (
            "Per-capture/cycle rising/falling branch mismatch at equal drive voltage, "
            "using raw PAX transaction midpoints. Combined score is the median of "
            "each axis/observable curve normalized to its own minimum."
        ),
        "primary_reference": primary,
        "configured_latency_s": configured,
        "scan": {"minimum_s": 0.0, "maximum_s": maximum_lag_s, "step_s": step_s},
        "analyses": analyses,
        "segments": analyses[primary]["segments"],
        "combined": analyses[primary]["combined"],
        "limitations": [
            "Branch mismatch includes physical hysteresis, drift, and PAX error as well as timing; its optimum is an empirical association estimate, not an independently measured instrument latency.",
            "Low DoP weakens normalized-Stokes evidence and is reported per segment.",
            "PAX transaction duration and internal instrument integration impose a finite timing resolution.",
            "Recalibrate after PAX daemon, rotation rate, mode, transport, or cadence changes.",
        ],
    }
    if not fpga_available:
        result["limitations"].insert(
            1,
            "No complete FPGA-timestamped RP reference was available; nominal ASG time is primary.",
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--maximum-lag-s", type=float, default=0.25)
    parser.add_argument("--step-s", type=float, default=0.001)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = analyze(args.run_directory, args.maximum_lag_s, args.step_s)
    output = args.output or args.run_directory / "timing_calibration.json"
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
