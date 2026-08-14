#!/usr/bin/env python3
"""Extract the measured phi2 fringe phase as a function of phi1 command."""
from __future__ import annotations

import argparse
import csv
import os
from dataclasses import asdict, dataclass
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-field-propagation")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


DETECTORS = ("pd_mean_v", "pax_ptotal")


@dataclass(frozen=True)
class FringePoint:
    detector: str
    phi1_direction: str
    phi1_rp_v: float
    samples: int
    baseline: float
    amplitude: float
    contrast: float
    phase_rad: float
    r_squared: float
    residual_rms: float


def _load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"phi1_rp_v", "phi2_rp_v", "pd_mean_v", "pax_ptotal"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Expected a phi1-fringe-map CSV")
    fields = set(rows[0])
    if "phi1_direction" in fields:
        return rows
    # Reuse the both-path subset of a field-model calibration as a coarser,
    # four-bias precursor map. Its ``direction`` denotes both actuator scan
    # directions, which is sufficient for this comparison.
    if {"direction", "condition"}.issubset(fields):
        normalized = []
        for row in rows:
            if row["condition"] != "both_paths":
                continue
            normalized.append({**row, "phi1_direction": row["direction"]})
        if normalized:
            return normalized
    raise ValueError("Expected phi1-fringe-map columns, or a field-model calibration containing both_paths rows")
    return rows


def _fit(rows: list[dict[str, str]], detector: str, phi2_vlambda_rp: float) -> list[FringePoint]:
    result: list[FringePoint] = []
    for direction in ("forward", "reverse"):
        values = sorted({float(row["phi1_rp_v"]) for row in rows if row["phi1_direction"] == direction})
        for phi1 in values:
            group = [row for row in rows if row["phi1_direction"] == direction and float(row["phi1_rp_v"]) == phi1]
            phase = 2.0 * np.pi * np.asarray([float(row["phi2_rp_v"]) for row in group]) / phi2_vlambda_rp
            power = np.asarray([float(row[detector]) for row in group])
            design = np.column_stack((np.ones_like(phase), np.cos(phase), np.sin(phase)))
            baseline, cosine, sine = np.linalg.lstsq(design, power, rcond=None)[0]
            prediction = design @ np.asarray((baseline, cosine, sine))
            residual = power - prediction
            total = float(np.sum((power - power.mean()) ** 2))
            amplitude = float(np.hypot(cosine, sine))
            result.append(FringePoint(
                detector=detector, phi1_direction=direction, phi1_rp_v=phi1, samples=len(group),
                baseline=float(baseline), amplitude=amplitude, contrast=amplitude / float(baseline),
                phase_rad=float(np.arctan2(-sine, cosine)),
                r_squared=1.0 - float(np.sum(residual**2)) / total if total else float("nan"),
                residual_rms=float(np.sqrt(np.mean(residual**2))),
            ))
    return result


def _unwrapped(points: list[FringePoint], direction: str) -> tuple[np.ndarray, np.ndarray]:
    selected = sorted((point for point in points if point.phi1_direction == direction), key=lambda item: item.phi1_rp_v)
    voltage = np.asarray([point.phi1_rp_v for point in selected])
    phase = np.unwrap(np.asarray([point.phase_rad for point in selected]))
    return voltage, phase


def _plot(points: list[FringePoint], output: Path) -> None:
    figure, axes = plt.subplots(2, 2, figsize=(11.5, 7.2), constrained_layout=True)
    for row, detector in enumerate(DETECTORS):
        selected = [point for point in points if point.detector == detector]
        for direction, color in (("forward", "tab:blue"), ("reverse", "tab:orange")):
            voltage, phase = _unwrapped(selected, direction)
            axes[row, 0].plot(voltage, phase - phase[0], "o-", color=color, label=direction)
            direction_points = sorted((point for point in selected if point.phi1_direction == direction), key=lambda item: item.phi1_rp_v)
            axes[row, 1].plot(voltage, [point.contrast for point in direction_points], "o-", color=color, label=direction)
        axes[row, 0].set_title(f"{detector}: measured phi2 fringe phase")
        axes[row, 0].set_ylabel(r"unwrapped $\psi(\phi_1)-\psi(0)$ (rad)")
        axes[row, 1].set_title(f"{detector}: fitted phi2 fringe contrast")
        axes[row, 1].set_ylabel("fringe amplitude / baseline")
        for axis in axes[row]:
            axis.set_xlabel("phi1 RP command (V)")
            axis.grid(alpha=0.25)
            axis.legend()
    figure.suptitle("Phi1 command calibration from measured phi2 fringes", fontsize=14, fontweight="bold")
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--phi2-vlambda-rp", type=float, default=0.2)
    parser.add_argument(
        "--phi1-vlambda-rp", type=float,
        help="Current expected phi1 RP V_lambda; reported for comparison, never imposed on the fit.",
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.phi2_vlambda_rp <= 0.0:
        raise ValueError("phi2-vlambda-rp must be positive")
    rows = _load(args.input_csv)
    points = [point for detector in DETECTORS for point in _fit(rows, detector, args.phi2_vlambda_rp)]
    output = args.output_dir or args.input_csv.parent / "phi1-fringe-fit"
    output.mkdir(parents=True, exist_ok=True)
    with (output / "phi1-fringe-map-fit.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(points[0])))
        writer.writeheader()
        writer.writerows(asdict(point) for point in points)
    _plot(points, output / "phi1-fringe-map-fit.png")
    print(f"Wrote phi1 fringe-map fit to {output}")
    for detector in DETECTORS:
        forward = _unwrapped([point for point in points if point.detector == detector], "forward")
        slope, intercept = np.polyfit(forward[0], forward[1], 1)
        linear = intercept + slope * forward[0]
        total = float(np.sum((forward[1] - np.mean(forward[1])) ** 2))
        linear_r_squared = 1.0 - float(np.sum((forward[1] - linear) ** 2)) / total if total else float("nan")
        reverse = _unwrapped([point for point in points if point.detector == detector], "reverse")
        reverse_by_voltage = dict(zip(reverse[0], reverse[1]))
        phase_differences = [
            abs(np.angle(np.exp(1j * (phase - reverse_by_voltage[voltage]))))
            for voltage, phase in zip(forward[0], forward[1])
        ]
        print(
            f"{detector}: forward phase excursion={forward[1][-1] - forward[1][0]:+.4g} rad; "
            f"forward/reverse disagreement median={np.median(phase_differences):.4g} rad; "
            f"linear-map R2={linear_r_squared:.3f}"
        )
        if linear_r_squared >= 0.90 and abs(slope) > 1e-12:
            estimated_vlambda = 2.0 * np.pi / abs(slope)
            print(f"  apparent phi1 V_lambda={estimated_vlambda:.4g} RP V")
        else:
            print("  phi1 V_lambda is not identifiable from this non-linear / history-dependent phase map.")
    if args.phi1_vlambda_rp is not None:
        print(f"Reference phi1 V_lambda supplied by user: {args.phi1_vlambda_rp:.6g} RP V (not imposed).")


if __name__ == "__main__":
    main()
