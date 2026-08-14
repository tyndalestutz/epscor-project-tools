#!/usr/bin/env python3
"""Fit a constrained, component-level Jones model to a field calibration.

This is intentionally different from ``fit_phi2_interference.py``.  The
earlier script fits the observable harmonic directly.  This script first
propagates fields through PBS -> phi1 -> NPBS1 -> phi2 -> NPBS2 and then fits
only named optical nonidealities to the simultaneously logged PAX/PD data.

The fitted model is deliberately small: PBS leakage, splitter ratios, relative
arm losses, one retarder in each MZI arm, input amplitude imbalance, and two
phase-origin offsets.  It is not a free Jones matrix at every element.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-field-propagation")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import least_squares

from physical_hybrid_mzi import PhysicalParameters, Retarder, fringe_coefficients, port_observables


@dataclass(frozen=True)
class FitResult:
    """Serializable physical parameters and held-out descriptive errors."""

    pbs_leakage_rad: float
    npbs1_mixing_rad: float
    npbs2_mixing_rad: float
    loss_a: float
    loss_b: float
    loss_c: float
    loss_d: float
    retarder_c_axis_rad: float
    retarder_c_retardance_rad: float
    retarder_d_axis_rad: float
    retarder_d_retardance_rad: float
    input_ay_over_ax: float
    phi1_offset_rad: float
    phi2_offset_rad: float
    pd_gain: float
    pd_offset: float
    pax_gain: float
    pax_offset: float
    pd_port: str
    pax_port: str
    samples: int
    normalized_residual_rms: float


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"condition", "phi1_rp_v", "phi2_rp_v", "pd_mean_v", "pax_ptotal", "s1", "s2", "s3"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Expected a field-model-calibration CSV with conditions, powers, and PAX Stokes values")
    return rows


def _array(rows: list[dict[str, str]], key: str) -> np.ndarray:
    return np.asarray([float(row[key]) for row in rows], dtype=float)


def _unpack(x: np.ndarray, *, pd_port: str, pax_port: str) -> tuple[PhysicalParameters, float, float, float, float, float, float]:
    """Turn bounded optimizer coordinates into physical component parameters."""
    # log-loss coordinates ensure positive field amplitudes.  D defines the
    # overall field scale, so it remains fixed at one rather than duplicating
    # the two detector gains.
    params = PhysicalParameters(
        ay=float(x[10]),
        pbs_leakage_rad=float(x[0]),
        npbs1_mixing_rad=float(np.pi / 4.0 + x[1]),
        npbs2_mixing_rad=float(np.pi / 4.0 + x[2]),
        loss_a=float(np.exp(x[3])), loss_b=float(np.exp(x[4])), loss_c=float(np.exp(x[5])), loss_d=1.0,
        retarder_c=Retarder(float(x[6]), float(x[7])),
        retarder_d=Retarder(float(x[8]), float(x[9])),
    )
    return params, float(x[11]), float(x[12]), float(x[13]), float(x[14]), float(x[15]), float(x[16])


def _predict(x: np.ndarray, phi1: np.ndarray, phi2: np.ndarray, conditions: np.ndarray, *, pd_port: str, pax_port: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    params, phi1_offset, phi2_offset, pd_gain, pd_offset, pax_gain, pax_offset = _unpack(x, pd_port=pd_port, pax_port=pax_port)
    pd, pax, stokes = [], [], []
    for first, second, condition in zip(phi1, phi2, conditions):
        if condition == "path_a_only":
            local = replace(params, ax=1.0, ay=0.0)
        elif condition == "path_b_only":
            local = replace(params, ax=0.0, ay=params.ay)
        else:
            local = replace(params, ax=1.0)
        obs = port_observables(first + phi1_offset, second + phi2_offset, local)
        pd.append(pd_offset + pd_gain * obs[f"I_{pd_port}"])
        pax.append(pax_offset + pax_gain * obs[f"I_{pax_port}"])
        stokes.append(obs[f"S_{pax_port}"])
    return np.asarray(pd), np.asarray(pax), np.asarray(stokes)


def fit(path: Path, *, phi1_vlambda_rp: float, phi2_vlambda_rp: float, pd_port: str, pax_port: str, max_nfev: int = 400) -> tuple[FitResult, dict[str, np.ndarray]]:
    rows = _rows(path)
    conditions = np.asarray([row["condition"] for row in rows])
    phi1 = 2.0 * np.pi * _array(rows, "phi1_rp_v") / phi1_vlambda_rp
    phi2 = 2.0 * np.pi * _array(rows, "phi2_rp_v") / phi2_vlambda_rp
    measured_pd, measured_pax = _array(rows, "pd_mean_v"), _array(rows, "pax_ptotal")
    measured_s = np.column_stack([_array(rows, key) for key in ("s1", "s2", "s3")])

    # Normalize each observable so a unit residual means one observed standard
    # deviation.  Stokes receives equal total weight to the two power streams.
    pd_scale = max(float(np.std(measured_pd)), 1e-9)
    pax_scale = max(float(np.std(measured_pax)), 1e-12)
    stokes_scale = np.maximum(np.std(measured_s, axis=0), 0.08)

    x0 = np.array([
        0.02, 0.0, 0.0, 0.0, 0.0, 0.0,  # leakage, splitter offsets, log losses
        0.0, 0.0, 0.0, 0.0,              # C/D retarders axis, retardance
        1.0, 0.0, 0.0,                   # input balance and phase origins
        max(np.ptp(measured_pd), 1e-6), np.min(measured_pd),
        max(np.ptp(measured_pax), 1e-9), np.min(measured_pax),
    ])
    lower = np.array([-.35, -.35, -.35, -1.5, -1.5, -1.5, -np.pi, -np.pi, -np.pi, -np.pi, .05, -np.pi, -np.pi, 0.0, -np.inf, 0.0, -np.inf])
    upper = np.array([ .35,  .35,  .35,  .7,  .7,  .7,  np.pi,  np.pi,  np.pi,  np.pi, 4.0,  np.pi,  np.pi, np.inf,  np.inf, np.inf,  np.inf])

    both = conditions == "both_paths"

    def residual(x: np.ndarray) -> np.ndarray:
        pred_pd, pred_pax, pred_s = _predict(x, phi1, phi2, conditions, pd_port=pd_port, pax_port=pax_port)
        # The scalar 0.5 makes all three Stokes components jointly comparable
        # to the two power instruments, rather than tripling their influence.
        return np.concatenate(((pred_pd - measured_pd) / pd_scale, (pred_pax - measured_pax) / pax_scale,
                               0.5 * ((pred_s[both] - measured_s[both]) / stokes_scale).ravel()))

    solution = least_squares(residual, x0, bounds=(lower, upper), max_nfev=max_nfev, verbose=0)
    params, p1off, p2off, pd_gain, pd_offset, pax_gain, pax_offset = _unpack(solution.x, pd_port=pd_port, pax_port=pax_port)
    result = FitResult(
        pbs_leakage_rad=params.pbs_leakage_rad, npbs1_mixing_rad=params.npbs1_mixing_rad,
        npbs2_mixing_rad=params.npbs2_mixing_rad, loss_a=params.loss_a, loss_b=params.loss_b,
        loss_c=params.loss_c, loss_d=params.loss_d, retarder_c_axis_rad=params.retarder_c.axis_rad,
        retarder_c_retardance_rad=params.retarder_c.retardance_rad, retarder_d_axis_rad=params.retarder_d.axis_rad,
        retarder_d_retardance_rad=params.retarder_d.retardance_rad, input_ay_over_ax=params.ay,
        phi1_offset_rad=p1off, phi2_offset_rad=p2off, pd_gain=pd_gain, pd_offset=pd_offset,
        pax_gain=pax_gain, pax_offset=pax_offset, pd_port=pd_port, pax_port=pax_port,
        samples=len(rows), normalized_residual_rms=float(np.sqrt(np.mean(residual(solution.x) ** 2))),
    )
    predicted_pd, predicted_pax, predicted_s = _predict(solution.x, phi1, phi2, conditions, pd_port=pd_port, pax_port=pax_port)
    return result, {"phi1": phi1, "phi2": phi2, "conditions": conditions, "pd": measured_pd, "pax": measured_pax,
                    "s": measured_s, "pd_fit": predicted_pd, "pax_fit": predicted_pax, "s_fit": predicted_s}


def _plot(values: dict[str, np.ndarray], output: Path) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(10, 7), constrained_layout=True)
    colors = {"path_a_only": "tab:blue", "path_b_only": "tab:orange", "both_paths": "tab:green"}
    for condition in np.unique(values["conditions"]):
        mask = values["conditions"] == condition
        order = np.argsort(values["phi2"][mask])
        for ax, raw, fitted, label in ((axes[0, 0], values["pd"][mask], values["pd_fit"][mask], "PD voltage"),
                                       (axes[0, 1], values["pax"][mask], values["pax_fit"][mask], "PAX total power")):
            ax.scatter(values["phi2"][mask], raw, s=10, color=colors[condition], alpha=.55)
            ax.plot(values["phi2"][mask][order], fitted[order], color=colors[condition], label=condition)
            ax.set_xlabel(r"$\phi_2$ (rad)"); ax.set_ylabel(label)
    mask = values["conditions"] == "both_paths"
    for index, ax in enumerate((axes[1, 0], axes[1, 1])):
        ax.scatter(values["s"][mask, index], values["s_fit"][mask, index], s=10, alpha=.6)
        lo, hi = -1.05, 1.05
        ax.plot((lo, hi), (lo, hi), "k--", lw=1); ax.set_xlim(lo, hi); ax.set_ylim(lo, hi)
        ax.set_xlabel(f"measured S{index + 1}"); ax.set_ylabel(f"Jones-fit S{index + 1}")
    axes[0, 0].legend(fontsize=8); fig.suptitle("Constrained physical Jones-network fit")
    fig.savefig(output, dpi=180); plt.close(fig)


def _write_fringe_predictions(result: FitResult, values: dict[str, np.ndarray], output: Path) -> None:
    """Write the Jones-derived detector contrasts at the sampled phi1 biases."""
    params = PhysicalParameters(
        ay=result.input_ay_over_ax, pbs_leakage_rad=result.pbs_leakage_rad,
        npbs1_mixing_rad=result.npbs1_mixing_rad, npbs2_mixing_rad=result.npbs2_mixing_rad,
        loss_a=result.loss_a, loss_b=result.loss_b, loss_c=result.loss_c, loss_d=result.loss_d,
        retarder_c=Retarder(result.retarder_c_axis_rad, result.retarder_c_retardance_rad),
        retarder_d=Retarder(result.retarder_d_axis_rad, result.retarder_d_retardance_rad),
    )
    phi1_values = np.unique(values["phi1"])
    with output.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["phi1_command_rad", "pd_baseline", "pd_amplitude", "pd_predicted_contrast",
                         "pax_baseline", "pax_amplitude", "pax_predicted_contrast"])
        for command in phi1_values:
            pd = fringe_coefficients(command + result.phi1_offset_rad, params, port=result.pd_port)
            pax = fringe_coefficients(command + result.phi1_offset_rad, params, port=result.pax_port)
            pd_baseline = result.pd_offset + result.pd_gain * pd["baseline"]
            pax_baseline = result.pax_offset + result.pax_gain * pax["baseline"]
            writer.writerow([command, pd_baseline, result.pd_gain * pd["amplitude"],
                             result.pd_gain * pd["amplitude"] / pd_baseline,
                             pax_baseline, result.pax_gain * pax["amplitude"],
                             result.pax_gain * pax["amplitude"] / pax_baseline])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="field-model-calibration data.csv")
    parser.add_argument("--phi1-vlambda-rp", type=float, required=True)
    parser.add_argument("--phi2-vlambda-rp", type=float, required=True)
    parser.add_argument("--pd-port", choices=("E", "F"), default="F")
    parser.add_argument("--pax-port", choices=("E", "F"), default="E")
    parser.add_argument("--max-nfev", type=int, default=60, help="optimizer evaluation budget (default: 60)")
    args = parser.parse_args()
    if args.pd_port == args.pax_port:
        parser.error("PD and PAX must be assigned to different final ports")
    result, values = fit(args.csv, phi1_vlambda_rp=args.phi1_vlambda_rp, phi2_vlambda_rp=args.phi2_vlambda_rp,
                         pd_port=args.pd_port, pax_port=args.pax_port, max_nfev=args.max_nfev)
    output = args.csv.parent / "physical-jones-fit"
    output.mkdir(exist_ok=True)
    (output / "physical-jones-fit.json").write_text(json.dumps(asdict(result), indent=2) + "\n")
    _plot(values, output / "physical-jones-fit.png")
    _write_fringe_predictions(result, values, output / "jones-fringe-predictions.csv")
    print(f"Wrote constrained physical Jones fit to {output}")
    print(f"normalized residual RMS: {result.normalized_residual_rms:.3f}")


if __name__ == "__main__":
    main()
