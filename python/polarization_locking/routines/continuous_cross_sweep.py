"""Continuous sine/bias coupling diagnostic with PAX and dual RP references."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
from typing import Any

import numpy as np

from .visibility import _scope_sleep


CSV_FIELDS = (
    "utc", "elapsed_s", "pax_transaction_midpoint_s", "pax_latency_correction_s",
    "pax_timestamp", "pax_requested_s", "pax_received_s",
    "pax_timing_uncertainty_s", "sub_run", "target_actuator", "segment_id",
    "bias_index", "fixed_actuator", "fixed_command_v", "sine_frequency_hz",
    "sine_center_v", "sine_amplitude_v", "nominal_target_command_v",
    "measured_in1_v", "measured_in2_v", "measured_target_v", "measured_fixed_v",
    "reference_interpolation_gap_s", "reference_valid", "reference_input_clipped",
    "sine_phase_rad", "cycle_index", "voltage_direction", "phase",
    "analysis_valid", "pax_valid", "pax_status", "s1", "s2", "s3", "dop",
    "pax_ptotal", "theta", "eta", "pax_revisions", "pax_adc_min",
    "pax_adc_max", "pax_rev_time", "pax_raw_json",
)

TRACE_DTYPES = {
    "time_s": np.float64,
    "in1_v": np.float32,
    "in2_v": np.float32,
    "commanded_target_v": np.float32,
    "commanded_fixed_v": np.float32,
    "segment_id": np.int32,
    "actuator_id": np.int8,
    "bias_index": np.int16,
    "cycle_index": np.int16,
    "capture_id": np.int32,
    "valid_measurement": np.bool_,
    "input_clipped": np.bool_,
    "time_uncertainty_s": np.float32,
}


def _boxcar_decimate(
    in1: np.ndarray, in2: np.ndarray, dt: float, target_rate_hz: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Return block-relative times and channel means at a compact saved rate."""
    if in1.ndim != 1 or in2.ndim != 1 or len(in1) != len(in2) or not len(in1):
        raise RuntimeError("RP scope did not return equal nonempty IN1/IN2 traces")
    if not np.all(np.isfinite(in1)) or not np.all(np.isfinite(in2)):
        raise RuntimeError("RP scope returned non-finite reference samples")
    factor = max(1, int(round((1.0 / dt) / target_rate_hz)))
    count = (len(in1) // factor) * factor
    if count == 0:
        raise RuntimeError("RP reference capture is too short to decimate")
    in1 = in1[:count].reshape(-1, factor).mean(axis=1)
    in2 = in2[:count].reshape(-1, factor).mean(axis=1)
    relative = (np.arange(len(in1), dtype=float) * factor + (factor - 1) / 2) * dt
    return relative, in1, in2, 1.0 / (dt * factor)


def _empty_trace() -> dict[str, list[np.ndarray]]:
    return {name: [] for name in TRACE_DTYPES}


def _concatenate_trace(trace: dict[str, list[np.ndarray]]) -> dict[str, np.ndarray]:
    return {
        name: (np.concatenate(parts).astype(dtype, copy=False)
               if parts else np.asarray([], dtype=dtype))
        for name, dtype in TRACE_DTYPES.items()
        for parts in (trace[name],)
    }


def _save_trace(path: Path, trace: dict[str, list[np.ndarray]], metadata: dict[str, Any]) -> None:
    """Atomically replace the one trace and its small commit-friendly metadata."""
    arrays = _concatenate_trace(trace)
    metadata_text = json.dumps(metadata, indent=2, allow_nan=False) + "\n"
    temporary = path.with_suffix(".tmp.npz")
    with temporary.open("wb") as handle:
        np.savez_compressed(handle, **arrays, metadata_json=np.asarray(metadata_text))
    os.replace(temporary, path)
    metadata_path = path.with_name("acquisition.json")
    metadata_temporary = metadata_path.with_suffix(".tmp.json")
    metadata_temporary.write_text(metadata_text)
    os.replace(metadata_temporary, metadata_path)


def _interpolate_reference(
    times: np.ndarray, values: np.ndarray, captures: np.ndarray, query: float,
) -> tuple[float, float, bool]:
    """Interpolate only within one capture, never across an acquisition gap."""
    if len(times) < 2 or query < times[0] or query > times[-1]:
        return float("nan"), float("nan"), False
    right = int(np.searchsorted(times, query, side="right"))
    right = min(max(right, 1), len(times) - 1)
    left = right - 1
    gap = float(times[right] - times[left])
    if captures[left] != captures[right] or gap <= 0:
        return float("nan"), gap, False
    fraction = (query - times[left]) / gap
    return float(values[left] + fraction * (values[right] - values[left])), gap, True


def _write_csv(path: Path, pax_rows: list[dict[str, Any]], trace: dict[str, list[np.ndarray]]) -> None:
    arrays = _concatenate_trace(trace)
    temporary = path.with_suffix(".tmp.csv")
    with temporary.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for sample in pax_rows:
            mask = arrays["segment_id"] == sample["segment_id"]
            times = arrays["time_s"][mask]
            captures = arrays["capture_id"][mask]
            in1, gap1, valid1 = _interpolate_reference(times, arrays["in1_v"][mask], captures, sample["elapsed_s"])
            in2, gap2, valid2 = _interpolate_reference(times, arrays["in2_v"][mask], captures, sample["elapsed_s"])
            reference_valid = valid1 and valid2
            target = in1 if sample["target_actuator"] == "phi1" else in2
            fixed = in2 if sample["target_actuator"] == "phi1" else in1
            if reference_valid:
                right = min(max(int(np.searchsorted(times, sample["elapsed_s"], side="right")), 1), len(times) - 1)
                left = right - 1
                target_values = arrays["in1_v"][mask] if sample["target_actuator"] == "phi1" else arrays["in2_v"][mask]
                slope = float((target_values[right] - target_values[left]) / (times[right] - times[left]))
                direction = "rising" if slope > 0 else "falling" if slope < 0 else "stationary"
                clipped = bool(arrays["input_clipped"][mask][left] or arrays["input_clipped"][mask][right])
            else:
                direction, clipped = "unknown", False
            row = dict(sample)
            row.update(
                measured_in1_v=in1, measured_in2_v=in2, measured_target_v=target,
                measured_fixed_v=fixed, reference_interpolation_gap_s=max(gap1, gap2),
                reference_valid=reference_valid, reference_input_clipped=clipped,
                voltage_direction=direction,
            )
            writer.writerow({name: row.get(name, "") for name in CSV_FIELDS})
        handle.flush()
    os.replace(temporary, path)


def _pax_sample(
    pax: Any, origin: float, segment: dict[str, Any], latency_correction_s: float,
) -> dict[str, Any]:
    requested = time.monotonic() - origin
    reading = pax.read_fresh_polarization()
    received = time.monotonic() - origin
    transaction_midpoint = (requested + received) / 2
    elapsed = transaction_midpoint - latency_correction_s
    local = elapsed - segment["drive_started_s"]
    phase_rad = (2 * math.pi * segment["frequency_hz"] * local) % (2 * math.pi)
    raw = dict(pax.last_raw_record or {})
    finite = all(math.isfinite(value) for value in (reading.s1, reading.s2, reading.s3, reading.dop))
    if not finite:
        status = "nonfinite"
    elif not 0 <= reading.dop <= 1:
        status = "dop_outside_0_1"
    elif reading.dop < segment["minimum_dop"]:
        status = "low_dop"
    else:
        status = "ok"
    warmup_s = segment["warmup_cycles"] / segment["frequency_hz"]
    analysis_end_s = (segment["warmup_cycles"] + segment["recorded_cycles"]) / segment["frequency_hz"]
    phase = "warmup" if local < warmup_s else "analysis" if local < analysis_end_s else "post"
    cycle = math.floor(local * segment["frequency_hz"]) - segment["warmup_cycles"]
    return {
        "utc": datetime.now(timezone.utc).isoformat(), "elapsed_s": elapsed,
        "pax_transaction_midpoint_s": transaction_midpoint,
        "pax_latency_correction_s": latency_correction_s,
        "pax_timestamp": reading.timestamp, "pax_requested_s": requested,
        "pax_received_s": received, "pax_timing_uncertainty_s": (received - requested) / 2,
        "sub_run": segment["sub_run"], "target_actuator": segment["target_actuator"],
        "segment_id": segment["segment_id"], "bias_index": segment["bias_index"],
        "fixed_actuator": segment["fixed_actuator"], "fixed_command_v": segment["fixed_v"],
        "sine_frequency_hz": segment["frequency_hz"], "sine_center_v": segment["center_v"],
        "sine_amplitude_v": segment["amplitude_v"],
        "nominal_target_command_v": segment["center_v"] + segment["amplitude_v"] * math.sin(phase_rad),
        "sine_phase_rad": phase_rad, "cycle_index": cycle, "phase": phase,
        "analysis_valid": phase == "analysis" and finite, "pax_valid": finite,
        "pax_status": status, "s1": reading.s1, "s2": reading.s2, "s3": reading.s3,
        "dop": reading.dop, "pax_ptotal": reading.ptotal, "theta": reading.theta,
        "eta": reading.eta, "pax_revisions": reading.revisions,
        "pax_adc_min": reading.adc_min, "pax_adc_max": reading.adc_max,
        "pax_rev_time": reading.rev_time,
        "pax_raw_json": json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str),
    }


def _capture_start_timing(
    scope: Any, origin: float, host_start: float, host_finish: float,
    sample_count: int, dt: float,
) -> tuple[float, float, dict[str, Any]]:
    """Associate the first ADC sample with host monotonic time.

    Pyrpl's FPGA exposes a 64-bit 125-MHz trigger counter and a current
    counter in the same clock domain. Reading the current counter with a
    bracketed host timestamp maps the exact trigger into host time without
    using network-delayed scope completion. The older host request/completion
    bracket remains an explicit fallback for test doubles or older FPGA builds.
    """
    trace_span = (sample_count - 1) * dt
    end_derived_start = host_finish - trace_span
    fallback_start = (host_start + end_derived_start) / 2
    fallback_uncertainty = abs(end_derived_start - host_start) / 2 + dt
    detail: dict[str, Any] = {
        "source": "host_request_completion_fallback",
        "host_scope_request_s": host_start,
        "host_scope_result_s": host_finish,
        "trace_span_s": trace_span,
        "capture_start_s": fallback_start,
        "uncertainty_s": fallback_uncertainty,
    }
    try:
        current_requested = time.monotonic() - origin
        current_tick = int(scope.current_timestamp)
        current_received = time.monotonic() - origin
        trigger_tick = int(scope.trigger_timestamp)
        trigger_received = time.monotonic() - origin
        correction = float(getattr(scope.parent, "frequency_correction", 1.0))
        if not math.isfinite(correction) or correction <= 0:
            raise ValueError(f"invalid FPGA frequency correction {correction!r}")
        tick_period = 8e-9 / correction
        trigger_age = ((current_tick - trigger_tick) & 0xFFFFFFFFFFFFFFFF) * tick_period
        current_midpoint = (current_requested + current_received) / 2
        capture_start = current_midpoint - trigger_age
        if not (
            0 <= trigger_age <= trace_span + 5.0
            and host_start - 1.0 <= capture_start <= host_finish
        ):
            raise ValueError(
                f"implausible FPGA timing: age={trigger_age:g}s, start={capture_start:g}s"
            )
        # The counter read is bracketed to sub-millisecond precision. Retain a
        # conservative 100-ppm allowance for an uncalibrated RP oscillator and
        # half a raw scope interval for the first-sample time convention.
        uncertainty = (
            (current_received - current_requested) / 2
            + trigger_age * 100e-6
            + dt / 2
        )
        detail.update({
            "source": "fpga_trigger_timestamp",
            "capture_start_s": capture_start,
            "uncertainty_s": uncertainty,
            "fpga_current_tick": current_tick,
            "fpga_trigger_tick": trigger_tick,
            "fpga_tick_period_s": tick_period,
            "fpga_trigger_age_s": trigger_age,
            "host_current_query_requested_s": current_requested,
            "host_current_query_received_s": current_received,
            "host_trigger_query_received_s": trigger_received,
            "host_result_after_trace_end_s": host_finish - (capture_start + trace_span),
            "fallback_capture_start_s": fallback_start,
            "fallback_uncertainty_s": fallback_uncertainty,
        })
        return capture_start, uncertainty, detail
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        detail["fpga_timing_error"] = f"{type(exc).__name__}: {exc}"
        return fallback_start, fallback_uncertainty, detail


def _append_capture(
    scope: Any, pax: Any, config: Any, origin: float, segment: dict[str, Any],
    capture_id: int, trace: dict[str, list[np.ndarray]], pax_rows: list[dict[str, Any]],
) -> tuple[int, bool, float]:
    host_start = time.monotonic() - origin
    future = scope.single_async()
    deadline = time.monotonic() + float(scope.duration) + config.pax_fresh_read_timeout_s + 3
    try:
        _scope_sleep(0.001)
        while not future.done():
            if time.monotonic() >= deadline:
                raise TimeoutError("Dual RP reference capture timed out")
            pax_rows.append(_pax_sample(
                pax, origin, segment, config.cross_sweep_pax_latency_s,
            ))
            _scope_sleep(0.001)
        result = future.result()
    finally:
        if not future.done():
            future.cancel()
    host_finish = time.monotonic() - origin
    if len(result) < 2:
        raise RuntimeError("RP scope did not return both IN1 and IN2")
    raw1, raw2 = (np.asarray(result[index], dtype=float).copy() for index in (0, 1))
    dt = float(scope.sampling_time)
    relative, in1, in2, saved_rate = _boxcar_decimate(
        raw1, raw2, dt, config.cross_sweep_reference_sample_rate_hz,
    )
    capture_start, uncertainty, timing = _capture_start_timing(
        scope, origin, host_start, host_finish, len(raw1), dt,
    )
    segment.setdefault("reference_captures", []).append({"capture_id": capture_id, **timing})
    times = capture_start + relative
    clipped = bool(np.any(np.abs(raw1) >= 8190 / 8192) or np.any(np.abs(raw2) >= 8190 / 8192))
    local = times - segment["drive_started_s"]
    cycle = np.floor(local * segment["frequency_hz"]).astype(np.int16) - segment["warmup_cycles"]
    valid = (
        (local >= segment["warmup_cycles"] / segment["frequency_hz"])
        & (local < (segment["warmup_cycles"] + segment["recorded_cycles"]) / segment["frequency_hz"])
    )
    target_command = segment["center_v"] + segment["amplitude_v"] * np.sin(
        2 * math.pi * segment["frequency_hz"] * local
    )
    values = {
        "time_s": times, "in1_v": in1, "in2_v": in2,
        "commanded_target_v": target_command,
        "commanded_fixed_v": np.full(len(times), segment["fixed_v"]),
        "segment_id": np.full(len(times), segment["segment_id"]),
        "actuator_id": np.full(len(times), 1 if segment["target_actuator"] == "phi1" else 2),
        "bias_index": np.full(len(times), segment["bias_index"]),
        "cycle_index": cycle, "capture_id": np.full(len(times), capture_id),
        "valid_measurement": valid, "input_clipped": np.full(len(times), clipped),
        "time_uncertainty_s": np.full(len(times), uncertainty),
    }
    for name, value in values.items():
        trace[name].append(np.asarray(value, dtype=TRACE_DTYPES[name]))
    return capture_id + 1, clipped, saved_rate


def _reference_validation(
    trace: dict[str, list[np.ndarray]], segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Describe whether each target loopback represents the commanded sine.

    This does not calibrate delivered actuator voltage. It rejects gross
    amplitude/polarity failures so downstream reports cannot treat a present
    but physically misleading RP input as a trustworthy voltage reference.
    """
    arrays = _concatenate_trace(trace)
    results = []
    for segment in segments:
        segment_id = segment["segment_id"]
        mask = (arrays["segment_id"] == segment_id) & arrays["valid_measurement"]
        target = arrays["in1_v"][mask] if segment["target_actuator"] == "phi1" else arrays["in2_v"][mask]
        times = arrays["time_s"][mask]
        if len(target) < 4:
            results.append({
                "segment_id": segment_id, "target_actuator": segment["target_actuator"],
                "status": "insufficient_data",
            })
            continue
        phase = 2 * math.pi * segment["frequency_hz"] * (
            times - segment["drive_started_s"]
        )
        design = np.column_stack((np.ones(len(target)), np.sin(phase), np.cos(phase)))
        coefficients = np.linalg.lstsq(design, target, rcond=None)[0]
        fitted = design @ coefficients
        measured_amplitude = float(np.hypot(coefficients[1], coefficients[2]))
        gain = measured_amplitude / segment["amplitude_v"]
        phase_deg = float(np.degrees(np.arctan2(coefficients[2], coefficients[1])))
        phase_error_deg = float((phase_deg + 180.0) % 360.0 - 180.0)
        residual_rms = float(np.sqrt(np.mean((target - fitted) ** 2)))
        trustworthy = (
            0.5 <= gain <= 1.5
            and abs(phase_error_deg) <= 45.0
            and residual_rms <= max(0.02, 0.1 * measured_amplitude)
        )
        results.append({
            "segment_id": segment_id, "target_actuator": segment["target_actuator"],
            "status": "trustworthy" if trustworthy else "failed_sanity_check",
            "measured_center_v": float(coefficients[0]),
            "measured_amplitude_v": measured_amplitude,
            "amplitude_gain_measured_per_command": gain,
            "phase_deg_relative_to_command_clock": phase_error_deg,
            "residual_rms_v": residual_rms,
            "criteria": "gain 0.5..1.5; absolute phase <=45 deg; residual <=max(0.02 V, 10% measured amplitude)",
        })
    return results


def acquire_continuous_cross_sweep(
    rp: Any, pax: Any, config: Any, output_file: str | Path, actuator: str,
) -> dict[str, Any]:
    """Run one or both continuous diagnostics and persist partial data safely."""
    if actuator not in {"phi1", "phi2", "both"}:
        raise ValueError("actuator must be phi1, phi2, or both")
    path = Path(output_file)
    trace_path = path.parent / "drive_trace.npz"
    axes = ("phi1", "phi2") if actuator == "both" else (actuator,)
    frequency = config.cross_sweep_sine_frequency_hz
    trace = _empty_trace()
    pax_rows: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {
        "format_version": 1, "status": "running", "requested_actuator": actuator,
        "requested_reference_sample_rate_hz": config.cross_sweep_reference_sample_rate_hz,
        "time_origin": "host_monotonic experiment origin; not persisted as an absolute clock",
        "synchronization": (
            "RP sample times use the FPGA scope trigger/current 125-MHz counters mapped into host "
            "monotonic time by a bracketed counter read. A host request/completion bracket is "
            "retained per capture and used only as an explicit fallback. PAX elapsed_s is the host "
            "request/receive midpoint minus the configured empirical latency. Raw timestamps, "
            "uncertainties, counter values, and capture gaps are retained."
        ),
        "reference_timing_primary": "fpga_trigger_timestamp",
        "reference_timing_fallback": "host_request_completion_bracket",
        "fpga_counter_scale_uncertainty_assumption": "100 ppm plus half a raw scope interval",
        "pax_latency_correction_s": config.cross_sweep_pax_latency_s,
        "pax_latency_correction_basis": (
            "Empirical 2026-09-29 cross-sweep lag sensitivity: branch separation was minimized "
            "with PAX records associated 0.10--0.13 s before the transaction midpoint. This is "
            "an empirical association estimate, not an instrument-provided timestamp; "
            "branch matching cannot distinguish timing from physical hysteresis or drift."
        ),
        "reference_channels": {"in1": "OUT1 / phi1", "in2": "OUT2 / phi2"},
        "segments": [],
    }
    origin = time.monotonic()
    capture_id = 0
    segment_id = 0
    try:
        pax.read_fresh_polarization()  # establish advancing data before starting the first waveform
        with rp.dual_reference_monitor(block_duration_s=config.cross_sweep_scope_block_s) as scope:
            for sub_run, target_axis in enumerate(axes):
                fixed_axis = "phi2" if target_axis == "phi1" else "phi1"
                biases = getattr(config, f"cross_sweep_{fixed_axis}_bias_voltages")
                for bias_index, bias in enumerate(biases):
                    transition_started = time.monotonic() - origin
                    v1, v2 = ((config.cross_sweep_sine_center_voltage, bias)
                              if target_axis == "phi1" else (bias, config.cross_sweep_sine_center_voltage))
                    transition_ramp = rp.ramp_output_voltage(
                        v1, v2, duration_s=config.cross_sweep_bias_ramp_s,
                        updates_per_s=config.cross_sweep_bias_ramp_updates_per_s,
                    )
                    transition_finished = time.monotonic() - origin
                    time.sleep(config.cross_sweep_settle_s)
                    settle_finished = time.monotonic() - origin
                    settings = rp.set_continuous_sine(
                        target_axis=target_axis, fixed_voltage=bias,
                        center=config.cross_sweep_sine_center_voltage,
                        amplitude=config.cross_sweep_sine_amplitude_voltage,
                        frequency_hz=frequency,
                    )
                    segment = {
                        "sub_run": sub_run, "target_actuator": target_axis,
                        "fixed_actuator": fixed_axis, "segment_id": segment_id,
                        "bias_index": bias_index, "fixed_v": settings["fixed_v"],
                        "frequency_hz": settings["frequency_hz"], "center_v": settings["center_v"],
                        "amplitude_v": settings["amplitude_v"],
                        "warmup_cycles": config.cross_sweep_warmup_cycles,
                        "recorded_cycles": config.cross_sweep_recorded_cycles,
                        "minimum_dop": config.minimum_dop,
                        "transition_started_s": transition_started,
                        "transition_finished_s": transition_finished,
                        "transition_ramp": transition_ramp,
                        "settle_s": config.cross_sweep_settle_s,
                        "settle_finished_s": settle_finished,
                        "scope_sampling_time_s": float(scope.sampling_time),
                        "scope_duration_s": float(scope.duration),
                        "scope_decimation": int(scope.decimation),
                        "scope_fpga_average": bool(scope.average),
                        "drive_started_s": time.monotonic() - origin,
                        "status": "running", "input_clipped": False,
                    }
                    metadata["segments"].append(segment)
                    print(
                        f"Segment {segment_id}: {target_axis} sine {settings['frequency_hz']:.6g} Hz, "
                        f"{fixed_axis}={settings['fixed_v']:.6g} V; "
                        f"{config.cross_sweep_warmup_cycles} warm-up + "
                        f"{config.cross_sweep_recorded_cycles} recorded cycles."
                    )
                    clipped = False
                    saved_rates: list[float] = []
                    total_s = (
                        config.cross_sweep_warmup_cycles + config.cross_sweep_recorded_cycles
                    ) / segment["frequency_hz"]
                    while time.monotonic() - origin - segment["drive_started_s"] < total_s:
                        capture_id, block_clipped, saved_rate = _append_capture(
                            scope, pax, config, origin, segment, capture_id, trace, pax_rows,
                        )
                        clipped |= block_clipped
                        saved_rates.append(saved_rate)
                    elapsed_cycles = (
                        time.monotonic() - origin - segment["drive_started_s"]
                    ) * segment["frequency_hz"]
                    next_center_cycles = math.ceil(2 * elapsed_cycles) / 2
                    center_wait_s = max(
                        0.0, (next_center_cycles - elapsed_cycles) / segment["frequency_hz"]
                    )
                    time.sleep(center_wait_s)
                    # End near a commanded center crossing before the next
                    # fixed-bias ramp. This remains host-timed and is recorded
                    # as such; it is not treated as measured phase lock.
                    rp.set_output_voltage(v1, v2)
                    segment.update(
                        status="completed", input_clipped=clipped,
                        finished_s=time.monotonic() - origin,
                        stop_center_wait_s=center_wait_s,
                        achieved_reference_rate_hz=float(np.median(saved_rates)),
                    )
                    if clipped:
                        print(f"Warning: segment {segment_id} contains a clipped RP input reference; data retained and flagged.")
                    _save_trace(trace_path, trace, metadata)
                    _write_csv(path, pax_rows, trace)
                    segment_id += 1
        metadata["status"] = "completed"
    except BaseException as exc:
        metadata["status"] = "interrupted" if isinstance(exc, (KeyboardInterrupt, EOFError)) else "failed"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        if metadata["segments"] and metadata["segments"][-1]["status"] == "running":
            metadata["segments"][-1]["status"] = metadata["status"]
        raise
    finally:
        try:
            cleanup_ramp = rp.ramp_output_voltage(
                0.0, 0.0, duration_s=config.cross_sweep_bias_ramp_s,
                updates_per_s=config.cross_sweep_bias_ramp_updates_per_s,
            )
            metadata["cleanup_ramp"] = cleanup_ramp
        finally:
            _save_trace(trace_path, trace, metadata)
            _write_csv(path, pax_rows, trace)
    intervals = []
    for current_segment in range(segment_id):
        times = [row["elapsed_s"] for row in pax_rows
                 if row["analysis_valid"] and row["segment_id"] == current_segment]
        intervals.extend(np.diff(times).tolist())
    positive_intervals = [value for value in intervals if value > 0]
    pax_rate = 1 / float(np.median(positive_intervals)) if positive_intervals else float("nan")
    samples_per_cycle = pax_rate / frequency
    metadata["measured_pax_rate_hz"] = pax_rate if math.isfinite(pax_rate) else None
    metadata["estimated_pax_samples_per_cycle"] = samples_per_cycle if math.isfinite(samples_per_cycle) else None
    metadata["reference_validation"] = _reference_validation(trace, metadata["segments"])
    metadata["all_target_references_trustworthy"] = bool(metadata["reference_validation"]) and all(item["status"] == "trustworthy" for item in metadata["reference_validation"])
    if math.isfinite(samples_per_cycle) and samples_per_cycle < config.cross_sweep_min_pax_samples_per_cycle:
        print(
            f"Warning: measured PAX density is {samples_per_cycle:.1f} samples/cycle, below "
            f"the configured {config.cross_sweep_min_pax_samples_per_cycle:g} trajectory threshold."
        )
    elif not math.isfinite(samples_per_cycle):
        print("Warning: too few analysis-window PAX samples to estimate samples per cycle; trajectory resolution is unresolved.")
    _save_trace(trace_path, trace, metadata)
    return metadata
