"""Active/passive PD/PAX contrast; preserve signed voltage and instrument power."""
import csv
from datetime import datetime, timezone
from pathlib import Path
import time

import numpy as np

from ..settings import write_json
from .prompts import prepare_setup


PAX_FIELDS = ("capture", "utc", "elapsed_s", "pax_requested_s", "pax_received_s", "pax_ptotal",
              "pax_timestamp", "pax_revisions", "pax_adc_min", "pax_adc_max", "pax_rev_time",
              "dop", "theta", "eta", "s1", "s2", "s3")


def acquire_contrast(rp, pax, config, output_file, duration_s):
    """Select detectors and start an optional drive after optical preparation."""
    active = config.visibility_mode == "active"

    def start_drive():
        drive = {"mode": config.visibility_mode, "frequency_hz": config.visibility_frequency_hz}
        if active:
            drive.update(rp.set_phase_waveform(axis=config.visibility_axis, waveform=config.visibility_waveform,
                         offset=config.visibility_offset_v, amplitude=config.visibility_amplitude_v,
                         frequency_hz=config.visibility_frequency_hz))
            drive["requested_frequency_hz"] = config.visibility_frequency_hz
        drive["started_at"] = datetime.now(timezone.utc).isoformat()
        write_json(Path(output_file).parent / "drive.json", drive)
        print(f"Contrast drive: {drive}")
        return drive["frequency_hz"]

    try:
        if config.visibility_source == "pax":
            acquire_pax_visibility(pax, config, output_file, duration_s, start_drive=start_drive)
        else:
            acquire_visibility(rp.p.rp.scope, config, output_file, duration_s,
                               pax=pax if config.visibility_source == "both" else None, start_drive=start_drive)
    finally:
        if active:
            rp.set_output_zero()


def _pax_snapshot(pax, started, capture=0):
    requested = time.monotonic() - started
    reading = pax.read_fresh_polarization()
    received = time.monotonic() - started
    return dict(capture=capture, utc=datetime.now(timezone.utc).isoformat(), elapsed_s=received,
                pax_requested_s=requested, pax_received_s=received, pax_ptotal=reading.ptotal,
                pax_timestamp=reading.timestamp, pax_revisions=reading.revisions,
                pax_adc_min=reading.adc_min, pax_adc_max=reading.adc_max, pax_rev_time=reading.rev_time,
                dop=reading.dop, theta=reading.theta, eta=reading.eta,
                s1=reading.s1, s2=reading.s2, s3=reading.s3)


def _scope_sleep(seconds):
    # Pyrpl scope futures require its event loop; do not move Qt/YAQC into threads.
    from pyrpl.async_utils import sleep
    sleep(seconds)


def _capture_with_pax(scope, pax, started, capture, period_s, rows, writer, handle):
    """Poll PAX while a single FPGA scope capture runs, on the same thread."""
    future = scope.single_async()
    deadline = time.monotonic() + max(3.0, scope.duration + 3.0)
    try:
        _scope_sleep(.001)  # start the scheduled FPGA acquisition
        while not future.done():
            if time.monotonic() >= deadline:
                raise TimeoutError("PD scope capture timed out while polling PAX")
            before = time.monotonic()
            row = _pax_snapshot(pax, started, capture)
            writer.writerow(row)
            handle.flush()
            rows.append(row)
            _scope_sleep(max(.001, period_s - (time.monotonic() - before)))
        return np.asarray(future.result()[0], dtype=float).copy()
    finally:
        if not future.done():
            future.cancel()


def _scope_capture_duration(frequency):
    requested_decimation = 2 / frequency / (16384 * 8e-9)
    decimation = 2 ** max(0, int(np.ceil(np.log2(requested_decimation))))
    if decimation > 65536:
        raise ValueError("Drive is too slow for two periods per scope capture")
    return decimation * 16384 * 8e-9


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
    pp = high - low
    issues = []
    polarity = intensity_low = intensity_high = None
    if dark_voltage is None:
        issues.append("dark voltage not measured; signed raw voltage retained, visibility unavailable")
    else:
        if not np.isfinite(dark_voltage):
            raise ValueError("Dark voltage must be finite")
        polarity = 1 if trace.mean() >= dark_voltage else -1
        intensity_low, intensity_high = sorted((polarity * (low - dark_voltage), polarity * (high - dark_voltage)))
        if intensity_low < 0 or intensity_high + intensity_low <= 6 * noise:
            issues.append("invalid intensity baseline; measure blocked-light offset and check DC coupling")
    if np.any(np.abs(trace) >= 8190 / 8192):
        issues.append("ADC clipping; check input range")
    if pp <= 6 * np.sqrt(2) * noise:
        issues.append("modulation unresolved above within-bin noise")
    contrast = None if issues else pp / (intensity_high + intensity_low)
    return {
        "min_v": float(trace.min()), "max_v": float(trace.max()),
        "mean_v": float(trace.mean()), "std_v": float(trace.std()),
        "low_v": low, "high_v": high, "peak_to_peak_v": pp,
        "intensity_min_v": intensity_low, "intensity_max_v": intensity_high,
        "polarity": polarity, "bin_s": block * sampling_time,
        "dark_voltage_v": dark_voltage,
        "bin_noise_se_v": noise, "visibility": contrast,
        "status": "; ".join(issues) if issues else "dark-corrected",
    }, means


def acquire_visibility(scope, config, output_file, duration_s, *, pax=None, start_drive=None):
    path = Path(output_file)
    frequency = config.visibility_frequency_hz
    names = ("input1", "duration", "average", "trigger_source", "trigger_delay", "ch1_active", "ch2_active", "rolling_mode", "trace_average")
    previous = {name: getattr(scope, name) for name in names}
    delay_register = scope._trigger_delay_register
    rows = []
    pax_rows = []
    pax_handle = None
    dark_voltage = config.visibility_dark_voltage_v
    dark_source = "provided" if dark_voltage is not None else "not measured"
    print(f"{config.visibility_mode.capitalize()} contrast on {config.pd_input}; requested/declared drive {frequency:g} Hz.")
    print("DC coupling and a full fringe excursion are required. Extrema use 1/64-period bin averages.")
    print("Raw voltages retain their sign. A blocked-light baseline is required for contrast.")
    try:
        scope.setup(input1=config.pd_input, duration=_scope_capture_duration(frequency), average=True,
                    trigger_source="immediately", trigger_delay=0.0,
                    ch1_active=True, ch2_active=False, rolling_mode=False, trace_average=1)
        if dark_voltage is None:
            prepare_setup(f"Block all light reaching the PD on {config.pd_input}, keeping electronics/settings unchanged. ")
            dark_utc = datetime.now(timezone.utc).isoformat()
            dark_trace = np.asarray(scope.single(timeout=max(3.0, scope.duration + 3.0))[0], dtype=float).copy()
            np.savez_compressed(path.parent / "dark.npz", voltage_v=dark_trace, time_s=np.asarray(scope.times).copy())
            if dark_trace.size == 0 or not np.all(np.isfinite(dark_trace)) or np.any(np.abs(dark_trace) >= 8190 / 8192):
                raise RuntimeError("Invalid/clipped dark capture; raw dark.npz retained")
            dark_voltage = float(dark_trace.mean())
            dark_source = "measured"
            write_json(path.parent / "dark.json", {
                "utc": dark_utc, "pd_input": config.pd_input, "mean_v": dark_voltage,
                "min_v": float(dark_trace.min()), "max_v": float(dark_trace.max()),
                "std_v": float(dark_trace.std()), "sample_count": int(dark_trace.size),
                "sampling_time_s": float(scope.sampling_time), "scope_duration_s": float(scope.duration),
                "decimation": float(scope.decimation), "fpga_average": bool(scope.average),
            })
            print(f"Measured signed dark baseline: {dark_voltage:+.9g} V; saved dark.npz and dark.json.")
            prepare_setup("Unblock the PD and restore the measurement beam. Leave detector settings unchanged. ")
        if pax is not None:
            pax_handle = (path.parent / "pax.csv").open("w", newline="")
            pax_writer = csv.DictWriter(pax_handle, fieldnames=PAX_FIELDS)
            pax_writer.writeheader()
            pax_handle.flush()
            pax.read_fresh_polarization()
        if start_drive is not None:
            frequency = start_drive()
            # Use actual ASG frequency, including hardware quantization, for duration/binning.
            scope.duration = _scope_capture_duration(frequency)
        started = time.monotonic()
        with path.open("w", newline="") as handle:
            writer = None
            while not rows or time.monotonic() - started < duration_s:
                capture_started = datetime.now(timezone.utc).isoformat()
                capture_started_s = time.monotonic() - started
                index = len(rows)
                pax_first = len(pax_rows)
                if pax is None:
                    trace = np.asarray(scope.single(timeout=max(3.0, scope.duration + 3.0))[0], dtype=float).copy()
                else:
                    trace = _capture_with_pax(scope, pax, started, index, config.visibility_pax_sample_period_s,
                                              pax_rows, pax_writer, pax_handle)
                capture_finished_s = time.monotonic() - started
                times = np.asarray(scope.times, dtype=float).copy()
                # Save raw data before analysis, including failed/partial runs.
                np.savez_compressed(path.parent / f"capture-{index:03d}.npz", voltage_v=trace, time_s=times)
                result, _ = analyze_trace(trace, scope.sampling_time, frequency, dark_voltage)
                row = {"capture": index, "utc": capture_started, "elapsed_s": time.monotonic() - started,
                       "pd_input": config.pd_input, "sample_count": trace.size,
                       "capture_started_s": capture_started_s, "capture_finished_s": capture_finished_s,
                       "sampling_time_s": scope.sampling_time, "scope_duration_s": scope.duration,
                       "decimation": scope.decimation, "fpga_average": scope.average,
                       "dark_source": dark_source, **result}
                if pax is not None:
                    powers = [item["pax_ptotal"] for item in pax_rows[pax_first:]]
                    low, high = (min(powers), max(powers)) if powers else (None, None)
                    row.update(pax_sample_count=len(powers), pax_power_min_w=low, pax_power_max_w=high,
                               pax_sampled_visibility=(high - low) / (high + low) if len(powers) >= 3 else None)
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
            if pax_handle is not None:
                pax_handle.close()
            valid = [row["visibility"] for row in rows if row["visibility"] is not None]
            summary = {
                "source": "both" if pax is not None else "pd", "mode": config.visibility_mode,
                "pd_input": config.pd_input, "captures": len(rows), "valid_captures": len(valid),
                "visibility_mean": float(np.mean(valid)) if valid else None,
                "visibility_std": float(np.std(valid, ddof=1)) if len(valid) > 1 else None,
                "dark_voltage_v": dark_voltage, "dark_source": dark_source,
                "external_frequency_hz": frequency,
                "definition": "(Imax-Imin)/(Imax+Imin), I = polarity*(V-Vdark); extrema of 1/64-period averages",
                "interpretation": "Signed raw voltage is retained. Contrast uses a measured or explicitly provided blocked-light baseline; none is inferred from fringe minima. DC coupling, linear response and full fringe excursion are required. Capture scatter is not total uncertainty.",
            }
            if pax is not None:
                summary["pax"] = summarize_pax_visibility(pax_rows, frequency)
                summary["comparison"] = "Concurrent PD captures and PAX polling; linked capture IDs and common host time origin in data.csv/pax.csv. PD uses binned extrema, PAX uses sampled extrema; bandwidth and integration windows differ. No hardware-trigger synchronization is claimed."
            write_json(path.parent / "visibility.json", summary)
    if valid:
        print(f"Mean observed visibility: {100 * summary['visibility_mean']:.2f}% ({len(valid)}/{len(rows)} valid captures).")
    else:
        print("No valid visibility estimate. See data.csv for acquisition diagnostics.")


def summarize_pax_visibility(rows, frequency_hz):
    """Observed contrast of fresh instrument power samples; no voltage baseline."""
    power = np.asarray([row["pax_ptotal"] for row in rows], dtype=float)
    times = np.asarray([row["elapsed_s"] for row in rows], dtype=float)
    intervals = np.diff(times)
    span = float(times[-1] - times[0]) if len(times) else 0.0
    low, high = (float(power.min()), float(power.max())) if power.size else (None, None)
    valid = power.size >= 3 and np.all(np.isfinite(power)) and np.all(power > 0) and span * frequency_hz >= 2
    return {
        "source": "pax", "samples": len(rows), "recorded_duration_s": span,
        "external_frequency_hz": frequency_hz,
        "power_min_w": low, "power_max_w": high,
        "power_mean_w": float(power.mean()) if power.size else None,
        "visibility": (high - low) / (high + low) if valid else None,
        "sample_interval_median_s": float(np.median(intervals)) if intervals.size else None,
        "sample_interval_max_s": float(intervals.max()) if intervals.size else None,
        "samples_per_drive_cycle": float(1 / (np.median(intervals) * frequency_hz)) if intervals.size else None,
        "status": "observed PAX total-power contrast" if valid else "insufficient valid data or fewer than two recorded drive periods",
        "definition": "(Pmax-Pmin)/(Pmax+Pmin), extrema of recorded PAX total optical power in watts",
        "interpretation": "Uses instrument-reported total power, with no additional optical-background subtraction. No PD voltage zero or polarity is applied. Finite sampling may miss narrow extrema; full fringe coverage and suitable drive speed must be checked. This is not a noise-corrected fit.",
    }


def acquire_pax_visibility(pax, config, output_file, duration_s, *, start_drive=None):
    """Log fresh power snapshots through the shared PAX readiness checks."""
    path = Path(output_file)
    rows = []
    frequency = config.visibility_frequency_hz
    print(f"{config.visibility_mode.capitalize()} PAX contrast: {duration_s:g} s, requested/declared drive {frequency:g} Hz.")
    try:
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PAX_FIELDS)
            writer.writeheader()
            handle.flush()
            pax.read_fresh_polarization()  # startup/readiness outside the timed acquisition
            if start_drive is not None:
                frequency = start_drive()
            duration_s = max(duration_s, 2 / frequency)
            started = next_sample = time.monotonic()
            while not rows or rows[-1]["elapsed_s"] - rows[0]["elapsed_s"] < duration_s:
                time.sleep(max(0.0, next_sample - time.monotonic()))
                row = _pax_snapshot(pax, started)
                writer.writerow(row)
                handle.flush()
                rows.append(row)
                next_sample = max(next_sample + config.visibility_pax_sample_period_s, time.monotonic())
    finally:
        summary = summarize_pax_visibility(rows, frequency)
        summary["mode"] = config.visibility_mode
        write_json(path.parent / "visibility.json", summary)
    if summary["visibility"] is None:
        print(f"PAX visibility unavailable: {summary['status']}")
    else:
        print(f"PAX observed power visibility: {100 * summary['visibility']:.2f}%; "
              f"Pmin={summary['power_min_w']:.9g} W, Pmax={summary['power_max_w']:.9g} W; "
              f"{summary['samples_per_drive_cycle']:.1f} samples per declared drive cycle.")
