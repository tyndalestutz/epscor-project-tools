#!/usr/bin/env python3
"""Predict measured phi2 fringe amplitude from the fitted field-model calibration.

The predictive chain is intentionally explicit:

    ideal field -> S(phi1, phi2) -> P_d = B_d + h_d . S

The field-model calibration supplies the detector vectors ``h_d``.  A
high-visibility phi1 fringe map supplies the *measured* phi1 branch / history
coordinate by jointly matching the two detectors' sine quadratures.  This is
necessary because the current phi1 voltage-to-phase map is non-linear and
hysteretic.  The script then compares the predicted phi2 contrast with the
fringe contrast measured at each phi1 command.

This is a constrained transfer test, not a claim of a fully identified Jones
model: a poor transfer score means the effective analyzer changed between runs
or an omitted non-ideal optical term is important.
"""
from __future__ import annotations

import argparse
import csv
import json
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
class AmplitudeComparison:
    detector: str
    phi1_direction: str
    phi1_rp_v: float
    empirical_alpha_rad: float
    alpha_fit_rmse_normalized: float
    predicted_contrast: float
    measured_contrast: float
    contrast_error: float
    predicted_amplitude: float
    measured_amplitude: float
    measured_cosine_normalized: float
    calibration_cosine_normalized: float
    measured_sine_normalized: float
    predicted_sine_normalized: float


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        result = list(csv.DictReader(handle))
    required = {"detector", "condition", "baseline", "h_s1", "h_s2", "h_s3"}
    if not result or not required.issubset(result[0]):
        raise ValueError(f"{path} is not an effective-analyzer-fit CSV")
    return result


def _fringes(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        result = list(csv.DictReader(handle))
    required = {"detector", "phi1_direction", "phi1_rp_v", "baseline", "amplitude", "contrast", "phase_rad"}
    if not result or not required.issubset(result[0]):
        raise ValueError(f"{path} is not a phi1-fringe-map-fit CSV")
    return result


def _analyzers(path: Path) -> dict[str, dict[str, float]]:
    rows = _rows(path)
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        if row["record_type"] == "condition_fit" and row["condition"] == "both_paths":
            result[row["detector"]] = {key: float(row[key]) for key in ("baseline", "h_s1", "h_s2", "h_s3")}
    missing = set(DETECTORS).difference(result)
    if missing:
        raise ValueError(f"Analyzer file lacks both_paths fit for: {', '.join(sorted(missing))}")
    return result


def _quadratures(row: dict[str, str]) -> tuple[float, float]:
    """Return C/B and D/B from B + C cos(phi2) + D sin(phi2)."""
    phase = float(row["phase_rad"])
    contrast = float(row["contrast"])
    # phase=atan2(-D,C), therefore C=A*cos(phase), D=-A*sin(phase).
    return contrast * np.cos(phase), -contrast * np.sin(phase)


def compare(calibration_csv: Path, fringe_csv: Path, output_dir: Path) -> list[AmplitudeComparison]:
    analyzers = _analyzers(calibration_csv)
    fringes = _fringes(fringe_csv)
    by_key = {(row["detector"], row["phi1_direction"], float(row["phi1_rp_v"])): row for row in fringes}
    voltages = sorted({float(row["phi1_rp_v"]) for row in fringes})
    comparisons: list[AmplitudeComparison] = []
    alpha_grid = np.linspace(0.0, 2.0 * np.pi, 20001)

    for direction in ("forward", "reverse"):
        for voltage in voltages:
            observed = {detector: _quadratures(by_key[(detector, direction, voltage)]) for detector in DETECTORS}
            # The normalized sine quadrature is the only part alpha changes.
            def loss(alpha: np.ndarray) -> np.ndarray:
                result = np.zeros_like(alpha)
                for detector in DETECTORS:
                    a = analyzers[detector]
                    predicted = (a["h_s2"] * np.cos(alpha) + a["h_s3"] * np.sin(alpha)) / a["baseline"]
                    result += (predicted - observed[detector][1]) ** 2
                return result
            index = int(np.argmin(loss(alpha_grid)))
            alpha = float(alpha_grid[index])
            rmse = float(np.sqrt(loss(np.asarray([alpha]))[0] / len(DETECTORS)))
            for detector in DETECTORS:
                calibration = analyzers[detector]
                fringe = by_key[(detector, direction, voltage)]
                cosine = calibration["h_s1"] / calibration["baseline"]
                sine = (calibration["h_s2"] * np.cos(alpha) + calibration["h_s3"] * np.sin(alpha)) / calibration["baseline"]
                predicted_contrast = float(np.hypot(cosine, sine))
                measured_contrast = float(fringe["contrast"])
                measured_cosine, measured_sine = observed[detector]
                comparisons.append(AmplitudeComparison(
                    detector=detector, phi1_direction=direction, phi1_rp_v=voltage,
                    empirical_alpha_rad=alpha, alpha_fit_rmse_normalized=rmse,
                    predicted_contrast=predicted_contrast, measured_contrast=measured_contrast,
                    contrast_error=predicted_contrast - measured_contrast,
                    predicted_amplitude=predicted_contrast * float(fringe["baseline"]),
                    measured_amplitude=float(fringe["amplitude"]),
                    measured_cosine_normalized=float(measured_cosine), calibration_cosine_normalized=float(cosine),
                    measured_sine_normalized=float(measured_sine), predicted_sine_normalized=float(sine),
                ))
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "phi2-amplitude-comparison.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(comparisons[0])))
        writer.writeheader()
        writer.writerows(asdict(item) for item in comparisons)
    _plot(comparisons, output_dir / "phi2-amplitude-comparison.png")
    return comparisons


def _plot(rows: list[AmplitudeComparison], output: Path) -> None:
    figure, axes = plt.subplots(2, 2, figsize=(11.5, 7.2), constrained_layout=True)
    for index, detector in enumerate(DETECTORS):
        selected = [row for row in rows if row.detector == detector]
        for direction, color in (("forward", "tab:blue"), ("reverse", "tab:orange")):
            group = sorted((row for row in selected if row.phi1_direction == direction), key=lambda row: row.phi1_rp_v)
            x = [row.phi1_rp_v for row in group]
            axes[index, 0].plot(x, [row.measured_contrast for row in group], "o-", color=color, label=f"measured {direction}")
            axes[index, 0].plot(x, [row.predicted_contrast for row in group], "--", color=color, label=f"model {direction}")
            axes[index, 1].plot(x, [row.contrast_error for row in group], "o-", color=color, label=direction)
        axes[index, 0].set_title(f"{detector}: predicted vs measured phi2 contrast")
        axes[index, 0].set_ylabel("phi2 fringe contrast")
        axes[index, 1].set_title(f"{detector}: transfer-model contrast error")
        axes[index, 1].set_ylabel("predicted − measured")
        for axis in axes[index]:
            axis.set_xlabel("phi1 RP command (V)")
            axis.grid(alpha=0.25)
            axis.legend(fontsize=8)
    figure.suptitle("Field-model prediction of phi2-dependent output amplitude", fontsize=14, fontweight="bold")
    figure.savefig(output, dpi=180)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calibration_fit_csv", type=Path, help="effective-analyzer-fit.csv from field-model calibration")
    parser.add_argument("fringe_fit_csv", type=Path, help="phi1-fringe-map-fit.csv from high-contrast map")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output = args.output_dir or args.fringe_fit_csv.parent / "phi2-amplitude-prediction"
    results = compare(args.calibration_fit_csv, args.fringe_fit_csv, output)
    print(f"Wrote phi2 amplitude prediction to {output}")
    for detector in DETECTORS:
        errors = np.asarray([row.contrast_error for row in results if row.detector == detector])
        alpha_rmse = np.asarray([row.alpha_fit_rmse_normalized for row in results if row.detector == detector])
        print(f"{detector}: contrast MAE={np.mean(np.abs(errors)):.4f}; bias={np.mean(errors):+.4f}; joint-alpha RMSE={np.mean(alpha_rmse):.4f}")


if __name__ == "__main__":
    main()
