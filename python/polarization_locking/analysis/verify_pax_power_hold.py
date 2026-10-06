#!/usr/bin/env python3
"""Compare static PAX stability at measured high/low/high power states."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
from typing import Any

import numpy as np

from polarization_locking.config import PolarizationLockConfig
from polarization_locking.hardware.pax_interface import PAXController
from polarization_locking.hardware.rp_interface import RPController


FIELDS = (
    "condition", "sequence_index", "utc", "elapsed_s", "pax_requested_s",
    "pax_received_s", "pax_timing_uncertainty_s", "phi1_command_v",
    "phi2_command_v", "s1", "s2", "s3", "dop", "pax_ptotal", "theta", "eta",
    "pax_revisions", "pax_adc_min", "pax_adc_max", "pax_rev_time",
    "pax_raw_json",
)


def summarize(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    stokes = np.asarray([[row[key] for key in ("s1", "s2", "s3")] for row in rows])
    stokes /= np.linalg.norm(stokes, axis=1, keepdims=True)
    mean = np.mean(stokes, axis=0)
    mean /= np.linalg.norm(mean)
    angles = np.degrees(np.arccos(np.clip(stokes @ mean, -1.0, 1.0)))
    power = np.asarray([row["pax_ptotal"] for row in rows])
    dop = np.asarray([row["dop"] for row in rows])
    return {
        "samples": len(rows),
        "mean_power_w": float(np.mean(power)),
        "power_std_w": float(np.std(power)),
        "power_5th_w": float(np.quantile(power, 0.05)),
        "power_95th_w": float(np.quantile(power, 0.95)),
        "mean_dop": float(np.mean(dop)),
        "dop_std": float(np.std(dop)),
        "dop_5th": float(np.quantile(dop, 0.05)),
        "fraction_dop_below_0_9": float(np.mean(dop < 0.9)),
        "mean_stokes": mean.tolist(),
        "angular_rms_deg": float(np.sqrt(np.mean(angles ** 2))),
        "angular_95th_deg": float(np.quantile(angles, 0.95)),
    }


def mean_angle(first: dict[str, Any], second: dict[str, Any]) -> float:
    a = np.asarray(first["mean_stokes"])
    b = np.asarray(second["mean_stokes"])
    return float(np.degrees(np.arccos(np.clip(a @ b, -1.0, 1.0))))


def run(output: Path, *, duration_s: float, settle_s: float) -> dict[str, Any]:
    if not math.isfinite(duration_s) or duration_s <= 0 or not math.isfinite(settle_s) or settle_s < 0:
        raise ValueError("duration_s must be positive and settle_s nonnegative, both finite")
    output.mkdir(parents=True, exist_ok=False)
    config = PolarizationLockConfig()
    phi1 = config.cross_sweep_sine_center_voltage
    # Commands selected from the corrected phi2 trajectory: about 1.07 mW and
    # 0.29 mW respectively. These are measured operating points, not fit values.
    conditions = (
        ("high_before", phi1, 0.155),
        ("low", phi1, 0.5425),
        ("high_after", phi1, 0.155),
    )
    rp, pax = RPController(config), PAXController(config)
    rows: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Static PAX high/low/high power stability; distinguishes dynamic trajectory error from low-power static measurement/system instability.",
        "duration_s_per_condition": duration_s,
        "settle_s": settle_s,
        "conditions": [],
    }
    origin = time.monotonic()
    try:
        rp.connect()
        pax.connect()
        pax.read_fresh_polarization()
        for index, (name, v1, v2) in enumerate(conditions):
            ramp = rp.ramp_output_voltage(
                v1, v2, duration_s=config.cross_sweep_bias_ramp_s,
                updates_per_s=config.cross_sweep_bias_ramp_updates_per_s,
            )
            time.sleep(settle_s)
            condition_rows = []
            stop = time.monotonic() + duration_s
            while time.monotonic() < stop:
                requested = time.monotonic() - origin
                reading = pax.read_fresh_polarization()
                received = time.monotonic() - origin
                raw = dict(pax.last_raw_record or {})
                row = {
                    "condition": name, "sequence_index": index,
                    "utc": datetime.now(timezone.utc).isoformat(),
                    "elapsed_s": (requested + received) / 2,
                    "pax_requested_s": requested, "pax_received_s": received,
                    "pax_timing_uncertainty_s": (received - requested) / 2,
                    "phi1_command_v": v1, "phi2_command_v": v2,
                    "s1": reading.s1, "s2": reading.s2, "s3": reading.s3,
                    "dop": reading.dop, "pax_ptotal": reading.ptotal,
                    "theta": reading.theta, "eta": reading.eta,
                    "pax_revisions": reading.revisions,
                    "pax_adc_min": reading.adc_min, "pax_adc_max": reading.adc_max,
                    "pax_rev_time": reading.rev_time,
                    "pax_raw_json": json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str),
                }
                rows.append(row)
                condition_rows.append(row)
            metadata["conditions"].append({
                "name": name, "phi1_command_v": v1, "phi2_command_v": v2,
                "ramp": ramp, "summary": summarize(condition_rows),
            })
        before, low, after = (item["summary"] for item in metadata["conditions"])
        metadata["comparisons"] = {
            "high_before_to_after_mean_stokes_deg": mean_angle(before, after),
            "low_to_high_before_mean_stokes_deg": mean_angle(low, before),
            "low_to_high_after_mean_stokes_deg": mean_angle(low, after),
            "low_vs_mean_high_angular_rms_ratio": (
                low["angular_rms_deg"]
                / ((before["angular_rms_deg"] + after["angular_rms_deg"]) / 2)
            ),
        }
        metadata["status"] = "completed"
    except BaseException as exc:
        metadata["status"] = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        metadata["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            if rp.p is not None:
                metadata["cleanup_ramp"] = rp.ramp_output_voltage(
                    0.0, 0.0, duration_s=config.cross_sweep_bias_ramp_s,
                    updates_per_s=config.cross_sweep_bias_ramp_updates_per_s,
                )
        finally:
            try:
                rp.disconnect()
            finally:
                try:
                    pax.disconnect()
                except BaseException as exc:
                    metadata["pax_cleanup_error"] = f"{type(exc).__name__}: {exc}"
                    raise
                finally:
                    # Cleanup failure must not prevent retained raw rows from
                    # being written, including after interrupted acquisition.
                    metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
                    if rows:
                        with (output / "data.csv").open("w", newline="") as handle:
                            writer = csv.DictWriter(handle, fieldnames=FIELDS)
                            writer.writeheader()
                            writer.writerows(rows)
                    (output / "summary.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new output directory")
    parser.add_argument("--duration", type=float, default=20.0, help="seconds per state")
    parser.add_argument("--settle", type=float, default=5.0, help="seconds after each ramp")
    args = parser.parse_args()
    print(json.dumps(run(args.output, duration_s=args.duration, settle_s=args.settle), indent=2))


if __name__ == "__main__":
    main()
