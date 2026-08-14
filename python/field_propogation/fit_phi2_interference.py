#!/usr/bin/env python3
"""Fit the observed phi2-to-power response without changing lock control.

This is the first data-fitted extension of :mod:`ideal_hybrid_mzi`.  It does
not claim to identify every Jones element in the bench.  With phi1 held fixed,
the data can identify only the first-harmonic power response

    P(phi2) = B + C cos(phi2) + D sin(phi2).

For the ideal Poincare result, ``S1=cos(phi2)`` and
``(S2,S3)=sin(phi2) (cos(alpha), sin(alpha))``.  Therefore the fitted model is
also the most general *linear effective analyzer* response accessible in a
phi2-only scan:

    P = B + h1 S1 + (h2 cos(alpha) + h3 sin(alpha)) sin(phi2).

A perfect total-power detector has C=D=0.  Nonzero coefficients quantify the
experiment's effective phase-to-power leakage; they do not, by themselves,
locate the responsible optic or prove that the PAX is polarization selective.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-field-propagation")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


CONDITIONS = ("path_a_only", "path_b_only", "both_paths")
DETECTORS = ("pd_mean_v", "pax_ptotal")
LABELS = {
    "path_a_only": "path A only",
    "path_b_only": "path B only",
    "both_paths": "both paths",
    "pd_mean_v": "photodiode (V)",
    "pax_ptotal": "PAX total-power units",
}
COLORS = {"path_a_only": "tab:blue", "path_b_only": "tab:orange", "both_paths": "tab:green"}


@dataclass(frozen=True)
class HarmonicPowerFit:
    """Identifiable effective power response for one detector and condition."""

    detector: str
    condition: str
    samples: int
    baseline: float
    cos_phi2: float
    sin_phi2: float
    amplitude: float
    contrast: float
    phase_rad: float
    r_squared: float
    residual_rms: float

    def predict(self, phi2: np.ndarray | float) -> np.ndarray:
        phase = np.asarray(phi2, dtype=float)
        return self.baseline + self.cos_phi2 * np.cos(phase) + self.sin_phi2 * np.sin(phase)


@dataclass(frozen=True)
class EffectiveAnalyzerFit:
    """Constrained fitted power model ``P = B + h dot S_ideal``.

    This uses the ideal field-propagation Stokes vector
    ``S=(cos(phi2), sin(phi2)cos(phi1), sin(phi2)sin(phi1))``.  The four
    coefficients are only identifiable when the supplied data vary both phi1
    and phi2.  A phi2-only scan is rank deficient and must use
    :class:`HarmonicPowerFit` instead.
    """

    detector: str
    condition: str
    samples: int
    baseline: float
    h_s1: float
    h_s2: float
    h_s3: float
    r_squared: float
    residual_rms: float

    def predict(self, phi1: np.ndarray | float, phi2: np.ndarray | float) -> np.ndarray:
        first = np.asarray(phi1, dtype=float)
        second = np.asarray(phi2, dtype=float)
        return (
            self.baseline
            + self.h_s1 * np.cos(second)
            + self.h_s2 * np.sin(second) * np.cos(first)
            + self.h_s3 * np.sin(second) * np.sin(first)
        )


@dataclass(frozen=True)
class BothPathResidual:
    """Difference between the both-path fit and the sum of blocked-path fits.

    The oscillatory part is an *effective residual interference term*.  It can
    contain actual coherent interference, drift between the sequential guided
    conditions, and path-dependent detector calibration.  It is deliberately
    named a residual until a simultaneous/calibrated experiment separates
    those contributions.
    """

    detector: str
    baseline: float
    cos_phi2: float
    sin_phi2: float
    amplitude: float
    phase_rad: float
    relative_to_single_path_sum: float

    def predict(self, phi2: np.ndarray | float) -> np.ndarray:
        phase = np.asarray(phi2, dtype=float)
        return self.baseline + self.cos_phi2 * np.cos(phase) + self.sin_phi2 * np.sin(phase)


def _float(row: dict[str, str], key: str) -> float:
    try:
        return float(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"CSV row is missing a numeric {key!r} value") from exc


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{path} has no data rows")
    fields = set(rows[0])
    has_legacy_phi2_scan = {"condition", "sweep_rp_v"}.issubset(fields)
    has_two_axis_scan = {"condition", "phi1_rp_v", "phi2_rp_v"}.issubset(fields)
    if not (has_legacy_phi2_scan or has_two_axis_scan):
        raise ValueError(
            f"{path} is not a supported path calibration CSV. Use either condition/sweep_rp_v "
            "or condition/phi1_rp_v/phi2_rp_v columns."
        )
    return rows


def fit_harmonic_power(
    rows: Iterable[dict[str, str]], *, detector: str, condition: str, phi2_vlambda_rp: float
) -> HarmonicPowerFit:
    """Fit ``B + C cos(phi2) + D sin(phi2)`` by linear least squares."""
    selected = [row for row in rows if row["condition"] == condition]
    if len(selected) < 4:
        raise ValueError(f"Need at least four {condition!r} samples for {detector!r}")
    voltage = np.asarray([_float(row, "sweep_rp_v") for row in selected])
    power = np.asarray([_float(row, detector) for row in selected])
    phi2 = 2.0 * np.pi * voltage / phi2_vlambda_rp
    design = np.column_stack((np.ones_like(phi2), np.cos(phi2), np.sin(phi2)))
    baseline, cosine, sine = np.linalg.lstsq(design, power, rcond=None)[0]
    prediction = design @ np.asarray((baseline, cosine, sine))
    residual = power - prediction
    total = float(np.sum((power - np.mean(power)) ** 2))
    r_squared = 1.0 - float(np.sum(residual**2)) / total if total > 0.0 else float("nan")
    amplitude = float(np.hypot(cosine, sine))
    return HarmonicPowerFit(
        detector=detector,
        condition=condition,
        samples=len(selected),
        baseline=float(baseline),
        cos_phi2=float(cosine),
        sin_phi2=float(sine),
        amplitude=amplitude,
        contrast=amplitude / float(baseline) if baseline != 0.0 else float("nan"),
        phase_rad=float(np.arctan2(-sine, cosine)),
        r_squared=r_squared,
        residual_rms=float(np.sqrt(np.mean(residual**2))),
    )


def fit_effective_analyzer(
    rows: Iterable[dict[str, str]], *, detector: str, condition: str,
    phi1_vlambda_rp: float, phi2_vlambda_rp: float,
) -> EffectiveAnalyzerFit:
    """Fit an effective analyzer response from an intentional two-axis scan.

    The required ``phi1_rp_v`` and ``phi2_rp_v`` columns are actuator commands
    in RP units. The fitted coefficients do not identify a physical optic yet;
    they quantify the smallest model that predicts power from the ideal Stokes
    state at the selected final port.
    """
    selected = [row for row in rows if row["condition"] == condition]
    if len(selected) < 8:
        raise ValueError(f"Need at least eight {condition!r} samples for an analyzer fit")
    phi1 = 2.0 * np.pi * np.asarray([_float(row, "phi1_rp_v") for row in selected]) / phi1_vlambda_rp
    phi2 = 2.0 * np.pi * np.asarray([_float(row, "phi2_rp_v") for row in selected]) / phi2_vlambda_rp
    power = np.asarray([_float(row, detector) for row in selected])
    design = np.column_stack((
        np.ones_like(phi1), np.cos(phi2), np.sin(phi2) * np.cos(phi1), np.sin(phi2) * np.sin(phi1),
    ))
    if np.linalg.matrix_rank(design) < design.shape[1]:
        raise ValueError(
            "The effective-analyzer fit requires multiple non-degenerate phi1 biases; "
            "a phi2-only scan identifies only B + C cos(phi2) + D sin(phi2)."
        )
    baseline, h_s1, h_s2, h_s3 = np.linalg.lstsq(design, power, rcond=None)[0]
    prediction = design @ np.asarray((baseline, h_s1, h_s2, h_s3))
    residual = power - prediction
    total = float(np.sum((power - np.mean(power)) ** 2))
    return EffectiveAnalyzerFit(
        detector=detector, condition=condition, samples=len(selected), baseline=float(baseline),
        h_s1=float(h_s1), h_s2=float(h_s2), h_s3=float(h_s3),
        r_squared=1.0 - float(np.sum(residual**2)) / total if total > 0.0 else float("nan"),
        residual_rms=float(np.sqrt(np.mean(residual**2))),
    )


def both_path_residual(fits: Sequence[HarmonicPowerFit], detector: str) -> BothPathResidual:
    """Calculate ``both_paths - path_a_only - path_b_only`` coefficient-wise."""
    by_condition = {fit.condition: fit for fit in fits if fit.detector == detector}
    if set(CONDITIONS).difference(by_condition):
        raise ValueError(f"Missing a condition needed for {detector!r} residual")
    a, b, both = (by_condition[condition] for condition in CONDITIONS)
    baseline = both.baseline - a.baseline - b.baseline
    cosine = both.cos_phi2 - a.cos_phi2 - b.cos_phi2
    sine = both.sin_phi2 - a.sin_phi2 - b.sin_phi2
    amplitude = float(np.hypot(cosine, sine))
    return BothPathResidual(
        detector=detector,
        baseline=baseline,
        cos_phi2=cosine,
        sin_phi2=sine,
        amplitude=amplitude,
        phase_rad=float(np.arctan2(-sine, cosine)),
        relative_to_single_path_sum=amplitude / (a.baseline + b.baseline),
    )


def make_plot(
    rows: Sequence[dict[str, str]], fits: Sequence[HarmonicPowerFit], residuals: Sequence[BothPathResidual], *,
    phi2_vlambda_rp: float, output: Path,
) -> None:
    """Create one concise power-fit / residual figure in native instrument units."""
    figure, axes = plt.subplots(2, 2, figsize=(12, 7.4), constrained_layout=True)
    phi_grid = np.linspace(0.0, 2.0 * np.pi, 500)
    fit_by_key = {(fit.detector, fit.condition): fit for fit in fits}

    for row_index, detector in enumerate(DETECTORS):
        axis = axes[row_index, 0]
        for condition in CONDITIONS:
            selected = [row for row in rows if row["condition"] == condition]
            voltage = np.asarray([_float(row, "sweep_rp_v") for row in selected])
            phi2 = 2.0 * np.pi * voltage / phi2_vlambda_rp
            power = np.asarray([_float(row, detector) for row in selected])
            fit = fit_by_key[(detector, condition)]
            axis.plot(phi2, power, ".", color=COLORS[condition], alpha=0.6, ms=4)
            axis.plot(phi_grid, fit.predict(phi_grid), color=COLORS[condition], lw=2, label=LABELS[condition])
        axis.set_title(f"{LABELS[detector]}: stepped phi2 data and fitted response")
        axis.set_ylabel(LABELS[detector])
        axis.grid(alpha=0.25)
        axis.legend(loc="upper left", fontsize=8)

        axis = axes[row_index, 1]
        residual = next(item for item in residuals if item.detector == detector)
        a = fit_by_key[(detector, "path_a_only")]
        b = fit_by_key[(detector, "path_b_only")]
        both = fit_by_key[(detector, "both_paths")]
        axis.plot(phi_grid, both.predict(phi_grid), color=COLORS["both_paths"], lw=2, label="fitted both paths")
        axis.plot(phi_grid, a.predict(phi_grid) + b.predict(phi_grid), color="0.30", lw=2, ls="--", label="fitted A + B")
        axis.plot(phi_grid, residual.predict(phi_grid), color="tab:red", lw=2, label="both − A − B")
        axis.axhline(0.0, color="0.2", lw=0.8)
        axis.set_title(
            "Residual effective interference\n"
            f"amplitude = {residual.amplitude:.3g}; relative amplitude = {residual.relative_to_single_path_sum:.3f}"
        )
        axis.set_ylabel(LABELS[detector])
        axis.grid(alpha=0.25)
        axis.legend(loc="upper left", fontsize=8)

    for axis in axes[-1, :]:
        axis.set_xlabel(r"$\phi_2 = 2\pi V_{RP}/V_{\lambda,RP}$ (rad)")
        axis.set_xlim(0.0, 2.0 * np.pi)
        axis.set_xticks((0.0, np.pi, 2.0 * np.pi), ("0", r"$\pi$", r"$2\pi$"))
    figure.suptitle("Data-fitted phi2 power model: effective analyzer / residual interference", fontsize=14, fontweight="bold")
    figure.savefig(output, dpi=180)
    plt.close(figure)


def write_summary(path: Path, fits: Sequence[HarmonicPowerFit], residuals: Sequence[BothPathResidual]) -> None:
    """Write machine-readable fit coefficients and both-path residuals."""
    with path.open("w", newline="") as handle:
        fields = [
            "detector", "condition", "samples", "baseline", "cos_phi2", "sin_phi2", "amplitude",
            "contrast", "phase_rad", "r_squared", "residual_rms", "relative_to_single_path_sum",
        ]
        writer = csv.DictWriter(handle, fieldnames=["record_type", *fields])
        writer.writeheader()
        for fit in fits:
            writer.writerow({"record_type": "condition_fit", **asdict(fit)})
        for residual in residuals:
            writer.writerow({"record_type": "both_path_residual", **asdict(residual)})


def run_fit(input_file: Path, output_dir: Path, phi2_vlambda_rp: float) -> tuple[list[HarmonicPowerFit], list[BothPathResidual]]:
    """Fit one stepped path-balance data set and write its analysis artifacts."""
    if phi2_vlambda_rp <= 0.0:
        raise ValueError("phi2_vlambda_rp must be positive")
    rows = load_rows(input_file)
    available = set(rows[0])
    detectors = [detector for detector in DETECTORS if detector in available]
    if not detectors:
        raise ValueError("No supported power columns found (expected pd_mean_v and/or pax_ptotal)")
    missing_conditions = set(CONDITIONS).difference({row["condition"] for row in rows})
    if missing_conditions:
        raise ValueError(f"CSV is missing conditions: {', '.join(sorted(missing_conditions))}")

    fits = [
        fit_harmonic_power(rows, detector=detector, condition=condition, phi2_vlambda_rp=phi2_vlambda_rp)
        for detector in detectors
        for condition in CONDITIONS
    ]
    residuals = [both_path_residual(fits, detector) for detector in detectors]
    output_dir.mkdir(parents=True, exist_ok=True)
    write_summary(output_dir / "phi2-interference-fit.csv", fits, residuals)
    with (output_dir / "phi2-interference-fit.json").open("w") as handle:
        json.dump(
            {
                "input_file": str(input_file),
                "phi2_vlambda_rp_v": phi2_vlambda_rp,
                "model": "P = B + C cos(phi2) + D sin(phi2)",
                "condition_fits": [asdict(fit) for fit in fits],
                "both_path_residuals": [asdict(residual) for residual in residuals],
            },
            handle,
            indent=2,
        )
    make_plot(rows, fits, residuals, phi2_vlambda_rp=phi2_vlambda_rp, output=output_dir / "phi2-interference-fit.png")
    return fits, residuals


def run_effective_analyzer_fit(
    input_file: Path, output_dir: Path, *, phi1_vlambda_rp: float, phi2_vlambda_rp: float,
) -> tuple[list[EffectiveAnalyzerFit], list[dict[str, float | str]]]:
    """Fit the two-axis calibration to ``P=B+h.S_ideal`` and save coefficients."""
    if phi1_vlambda_rp <= 0.0 or phi2_vlambda_rp <= 0.0:
        raise ValueError("Both phi1_vlambda_rp and phi2_vlambda_rp must be positive")
    rows = load_rows(input_file)
    if not {"phi1_rp_v", "phi2_rp_v"}.issubset(rows[0]):
        raise ValueError("This is not a field-model calibration CSV; expected phi1_rp_v and phi2_rp_v columns")
    detectors = [detector for detector in DETECTORS if detector in rows[0]]
    fits = [
        fit_effective_analyzer(
            rows, detector=detector, condition=condition, phi1_vlambda_rp=phi1_vlambda_rp,
            phi2_vlambda_rp=phi2_vlambda_rp,
        )
        for detector in detectors for condition in CONDITIONS
    ]
    residuals: list[dict[str, float | str]] = []
    for detector in detectors:
        by_condition = {item.condition: item for item in fits if item.detector == detector}
        a, b, both = (by_condition[condition] for condition in CONDITIONS)
        residuals.append({
            "detector": detector,
            "baseline": both.baseline - a.baseline - b.baseline,
            "h_s1": both.h_s1 - a.h_s1 - b.h_s1,
            "h_s2": both.h_s2 - a.h_s2 - b.h_s2,
            "h_s3": both.h_s3 - a.h_s3 - b.h_s3,
        })
    output_dir.mkdir(parents=True, exist_ok=True)
    fields = ["detector", "condition", "samples", "baseline", "h_s1", "h_s2", "h_s3", "r_squared", "residual_rms"]
    with (output_dir / "effective-analyzer-fit.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["record_type", *fields])
        writer.writeheader()
        for fit in fits:
            writer.writerow({"record_type": "condition_fit", **asdict(fit)})
        for residual in residuals:
            writer.writerow({"record_type": "both_path_residual", **residual})
    with (output_dir / "effective-analyzer-fit.json").open("w") as handle:
        json.dump({
            "input_file": str(input_file),
            "phi1_vlambda_rp_v": phi1_vlambda_rp,
            "phi2_vlambda_rp_v": phi2_vlambda_rp,
            "ideal_stokes": ["cos(phi2)", "sin(phi2) cos(phi1)", "sin(phi2) sin(phi1)"],
            "model": "P = baseline + h_s1*S1 + h_s2*S2 + h_s3*S3",
            "condition_fits": [asdict(fit) for fit in fits],
            "both_path_residuals": residuals,
        }, handle, indent=2)
    return fits, residuals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_csv", type=Path, help="Stepped phi2-path-test CSV containing A, B, and both conditions.")
    parser.add_argument("--phi2-vlambda-rp", type=float, default=0.2, help="RP volts that produce 2pi phi2 (default: 0.2).")
    parser.add_argument("--phi1-vlambda-rp", type=float, default=12.2 / 16.875, help="RP volts that produce 2pi phi1 (default: 12.2/16.875).")
    parser.add_argument("--output-dir", type=Path, help="Destination; defaults to <CSV parent>/field-propagation-fit.")
    args = parser.parse_args()
    output_dir = args.output_dir or args.input_csv.parent / "field-propagation-fit"
    with args.input_csv.open(newline="") as handle:
        header = set(next(csv.DictReader(handle), {}))
    if {"phi1_rp_v", "phi2_rp_v"}.issubset(header):
        _, residuals = run_effective_analyzer_fit(
            args.input_csv, output_dir, phi1_vlambda_rp=args.phi1_vlambda_rp,
            phi2_vlambda_rp=args.phi2_vlambda_rp,
        )
        print(f"Wrote fitted effective-analyzer model to {output_dir}")
        for residual in residuals:
            magnitude = float(np.linalg.norm([residual["h_s1"], residual["h_s2"], residual["h_s3"]]))
            print(f"{residual['detector']}: both-path residual |h|={magnitude:.6g}")
    else:
        _, residuals = run_fit(args.input_csv, output_dir, args.phi2_vlambda_rp)
        print(f"Wrote fitted phi2 power model to {output_dir}")
        for residual in residuals:
            print(
                f"{residual.detector}: both-path residual amplitude={residual.amplitude:.6g}; "
                f"relative to A+B baseline={residual.relative_to_single_path_sum:.3f}"
            )


if __name__ == "__main__":
    main()
