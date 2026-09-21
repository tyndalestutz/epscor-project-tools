"""Passive, bandwidth-declared fringe contrast; retain every scope capture."""
import csv
from datetime import datetime, timezone
from pathlib import Path
import time

import numpy as np

from ..settings import write_json


def analyze_trace(trace, sampling_time, frequency_hz, dark_voltage=None):
    """Estimate extrema with nonoverlapping averages of 1/64 drive period.

    This is an observed contrast at the recorded bandwidth, not a sinusoidal
    fit or proof that the external drive traversed a whole optical fringe.
    A six-standard-error excursion is required to report resolved modulation.
    """
    trace = np.asarray(trace, dtype=float)
    if trace.ndim != 1 or trace.size < 128 or not np.all(np.isfinite(trace)):
        raise ValueError("Expected a finite one-dimensional scope trace with at least 128 samples")
    if sampling_time <= 0 or frequency_hz <= 0 or trace.size * sampling_time * frequency_hz < 2:
        raise ValueError("Capture must contain at least two external-drive periods")
    block = max(2, int(round(1 / (64 * frequency_hz * sampling_time))))
    count = trace.size // block
    if count < 64:
        raise ValueError("Insufficient temporal resolution for visibility")
    blocks = trace[:count * block].reshape(count, block)
    means = blocks.mean(axis=1)
    noise = float(np.median(blocks.std(axis=1, ddof=1)) / np.sqrt(block))
    low, high = float(means.min()), float(means.max())
    dark = 0.0 if dark_voltage is None else dark_voltage
    polarity = 1 if trace.mean() >= dark else -1
    intensity_low, intensity_high = sorted((polarity * (low - dark), polarity * (high - dark)))
    pp = high - low
    issues = []
    if np.any(np.abs(trace) >= 8190 / 8192):
        issues.append("ADC clipping; check input range")
    if intensity_low < 0 or intensity_high + intensity_low <= 6 * noise:
        issues.append("invalid intensity baseline; measure blocked-light offset and check DC coupling")
    if pp <= 6 * np.sqrt(2) * noise:
        issues.append("modulation unresolved above within-bin noise")
    contrast = None if issues else pp / (intensity_high + intensity_low)
    return {
        "min_v": float(trace.min()), "max_v": float(trace.max()),
        "mean_v": float(trace.mean()), "std_v": float(trace.std()),
        "low_v": low, "high_v": high, "peak_to_peak_v": pp,
        "intensity_min_v": intensity_low, "intensity_max_v": intensity_high,
        "polarity": polarity, "bin_s": block * sampling_time,
        "bin_noise_se_v": noise, "visibility": contrast,
        "status": "; ".join(issues) if issues else ("apparent; zero dark offset assumed" if dark_voltage is None else "dark-corrected"),
    }, means


def acquire_visibility(scope, config, output_file, duration_s):
    path = Path(output_file)
    frequency = config.visibility_frequency_hz
    # duration and decimation are the same underlying setting; set it once.
    requested_decimation = 2 / frequency / (16384 * 8e-9)
    decimation = 2 ** max(0, int(np.ceil(np.log2(requested_decimation))))
    if decimation > 65536:
        raise ValueError("External drive is too slow for two periods per capture")
    names = ("input1", "duration", "average", "trigger_source", "trigger_delay", "ch1_active", "ch2_active", "rolling_mode", "trace_average")
    previous = {name: getattr(scope, name) for name in names}
    delay_register = scope._trigger_delay_register
    rows = []
    print(f"Passive visibility on {config.pd_input}; external drive {frequency:g} Hz; RP outputs untouched.")
    print("DC coupling and a full fringe excursion are required. Extrema use 1/64-period bin averages.")
    if config.visibility_dark_voltage_v is None:
        print("Dark voltage not measured: reporting apparent contrast assuming zero offset.")
    try:
        scope.setup(input1=config.pd_input, duration=decimation * 16384 * 8e-9, average=True,
                    trigger_source="immediately", trigger_delay=0.0,
                    ch1_active=True, ch2_active=False, rolling_mode=False, trace_average=1)
        started = time.monotonic()
        with path.open("w", newline="") as handle:
            writer = None
            while not rows or time.monotonic() - started < duration_s:
                capture_started = datetime.now(timezone.utc).isoformat()
                trace = np.asarray(scope.single(timeout=max(3.0, scope.duration + 3.0))[0], dtype=float).copy()
                times = np.asarray(scope.times, dtype=float).copy()
                index = len(rows)
                # Save raw data before analysis, including failed/partial runs.
                np.savez_compressed(path.parent / f"capture-{index:03d}.npz", voltage_v=trace, time_s=times)
                result, _ = analyze_trace(trace, scope.sampling_time, frequency, config.visibility_dark_voltage_v)
                row = {"capture": index, "utc": capture_started, "elapsed_s": time.monotonic() - started,
                       "pd_input": config.pd_input, "sample_count": trace.size,
                       "sampling_time_s": scope.sampling_time, "scope_duration_s": scope.duration,
                       "decimation": scope.decimation, "fpga_average": scope.average, **result}
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                handle.flush()
                rows.append(row)
                value = "unresolved/invalid" if result["visibility"] is None else f"{100 * result['visibility']:.2f}%"
                print(f"Capture {index + 1}: {value}; low={result['low_v']:.6g} V, high={result['high_v']:.6g} V, Vpp={result['peak_to_peak_v']:.6g} V ({result['status']})")
    finally:
        try:
            scope.stop()
            scope.setup(**previous)
            scope._trigger_delay_register = delay_register
        finally:
            valid = [row["visibility"] for row in rows if row["visibility"] is not None]
            summary = {
                "pd_input": config.pd_input, "captures": len(rows), "valid_captures": len(valid),
                "visibility_mean": float(np.mean(valid)) if valid else None,
                "visibility_std": float(np.std(valid, ddof=1)) if len(valid) > 1 else None,
                "dark_voltage_v": config.visibility_dark_voltage_v,
                "external_frequency_hz": frequency,
                "definition": "(Imax-Imin)/(Imax+Imin), I = polarity*(V-Vdark); extrema of 1/64-period averages",
                "interpretation": "Observed contrast assumes DC coupling, linear unsaturated PD, and full fringe excursion. A missing dark measurement gives apparent contrast only. Scatter is not total uncertainty.",
            }
            write_json(path.parent / "visibility.json", summary)
    if valid:
        print(f"Mean observed visibility: {100 * summary['visibility_mean']:.2f}% ({len(valid)}/{len(rows)} valid captures).")
    else:
        print("No valid visibility estimate. See data.csv for acquisition diagnostics.")
