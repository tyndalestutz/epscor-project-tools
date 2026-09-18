#!/usr/bin/env python3
"""Report a held-step phi1 voltage-to-equatorial-phase calibration at D.

At first-NPBS reflected port D the ideal two-path model predicts

    S1 = 0,     S2 = -sin(phi1 + delta),     S3 = cos(phi1 + delta).

The reported phase is therefore atan2(S3, -S2).  Each voltage is held before
measurement; forward and reverse fits remain separate so hysteresis cannot be
hidden by a single averaged calibration.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-field-propagation")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


def circular_mean(angle: np.ndarray) -> tuple[float, float]:
    phasor = np.mean(np.exp(1j * angle))
    return float(np.angle(phasor)), float(abs(phasor))


def fit_phase(voltage: np.ndarray, phase: np.ndarray) -> dict[str, float | list[float]]:
    """Fit an unwrapped phase line to one ordered forward/reverse pass."""
    unwrapped = np.unwrap(phase)
    slope, intercept = np.polyfit(voltage, unwrapped, 1)
    fitted = slope * voltage + intercept
    residual = np.angle(np.exp(1j * (unwrapped - fitted)))
    r2 = 1.0 - float(np.sum((unwrapped - fitted) ** 2) / np.sum((unwrapped - np.mean(unwrapped)) ** 2)) if np.ptp(unwrapped) > 1e-12 else float("nan")
    return {
        "slope_rad_per_rp_v": float(slope),
        "intercept_rad": float(intercept),
        "fitted_vlambda_rp_v": float(2.0 * np.pi / abs(slope)) if abs(slope) > 1e-12 else float("nan"),
        "phase_span_rad": float(unwrapped[-1] - unwrapped[0]),
        "circular_residual_rms_rad": float(np.sqrt(np.mean(residual ** 2))),
        "linear_r_squared": r2,
        "unwrapped_phase_rad": unwrapped.tolist(),
        "fit_phase_rad": fitted.tolist(),
    }


def create_report(csv_file: Path, output_format: str = "pdf", document=None) -> None:
    from types import SimpleNamespace
    args = SimpleNamespace(csv=Path(csv_file), format=output_format)
    with args.csv.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"direction", "step_index", "phi1_rp_voltage", "s1", "s2", "s3", "equatorial_phase_rad", "pd_mean_v"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Expected a phi1-step-map CSV")

    grouped: dict[str, dict[int, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["direction"]][int(row["step_index"])].append(row)
    colors = {"forward": "tab:blue", "reverse": "tab:orange"}
    summaries: dict[str, dict[str, object]] = {}
    series: dict[str, dict[str, np.ndarray]] = {}
    for direction in ("forward", "reverse"):
        step_groups = grouped.get(direction, {})
        if not step_groups:
            continue
        voltage, phase, concentration, mean_s1, s1_std, radius, pd, phase_std = ([] for _ in range(8))
        for index in sorted(step_groups):
            group = step_groups[index]
            voltage.append(float(group[0]["phi1_rp_voltage"]))
            raw_phase = np.asarray([float(row["equatorial_phase_rad"]) for row in group])
            mean_phase, r = circular_mean(raw_phase)
            phase.append(mean_phase); concentration.append(r)
            mean_s1.append(np.mean([float(row["s1"]) for row in group]))
            s1_std.append(np.std([float(row["s1"]) for row in group]))
            radius.append(np.mean([float(row["equatorial_radius"]) for row in group]))
            pd.append(np.mean([float(row["pd_mean_v"]) for row in group]))
            phase_std.append(np.sqrt(max(0.0, -2.0 * np.log(max(r, 1e-12)))))
        values = {
            "voltage": np.asarray(voltage), "phase": np.asarray(phase), "concentration": np.asarray(concentration),
            "mean_s1": np.asarray(mean_s1), "s1_std": np.asarray(s1_std), "radius": np.asarray(radius),
            "pd": np.asarray(pd), "phase_std": np.asarray(phase_std),
        }
        values["fit"] = fit_phase(values["voltage"], values["phase"])
        series[direction] = values
        summaries[direction] = {
            "steps": len(voltage),
            "mean_equatorial_s1": float(np.mean(values["mean_s1"])),
            "mean_equatorial_radius": float(np.mean(values["radius"])),
            "mean_within_step_phase_concentration": float(np.mean(values["concentration"])),
            "fit": values["fit"],
        }

    output = args.csv.parent / "phi1-step-map-analysis"
    output.mkdir(exist_ok=True)
    report = {
        "ideal_model": "At D: S=(0,-sin(phi1+delta),cos(phi1+delta)); phase=atan2(S3,-S2).",
        "candidate_phi1_vlambda_rp_v": float(rows[0]["phi1_vlambda_rp_v"]),
        "directions": summaries,
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n")

    fig, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
    for direction, values in series.items():
        color = colors[direction]
        phase_unwrapped = np.asarray(values["fit"]["unwrapped_phase_rad"])
        fit_phase_values = np.asarray(values["fit"]["fit_phase_rad"])
        axes[0, 0].errorbar(values["voltage"], phase_unwrapped, yerr=values["phase_std"], fmt="o", ms=4, color=color, label=f"{direction}: held PAX phase")
        axes[0, 0].plot(values["voltage"], fit_phase_values, color=color, lw=1.5, ls="--", label=f"{direction}: linear fit")
        axes[0, 1].plot(values["voltage"], values["mean_s1"], "o-", ms=4, color=color, label=direction)
        axes[1, 0].plot(-np.sin(values["phase"]), np.cos(values["phase"]), "o-", ms=4, color=color, label=direction)
        axes[1, 1].plot(values["voltage"], values["pd"], "o-", ms=4, color=color, label=direction)
    candidate = float(rows[0]["phi1_vlambda_rp_v"])
    axes[0, 0].axvline(candidate, color="k", ls=":", lw=1, label="candidate one V_lambda")
    axes[0, 0].set(xlabel="OUT1 / phi1 RP command (V)", ylabel="D equatorial phase (unwrapped rad)", title="Held voltage-to-phase calibration")
    axes[0, 0].legend(fontsize=7)
    axes[0, 1].axhline(0, color="k", ls="--", lw=1, label="ideal D equator")
    axes[0, 1].set(xlabel="OUT1 / phi1 RP command (V)", ylabel=r"mean $S_1$", title=r"D-port equator check ($S_1=0$ ideal)")
    axes[0, 1].legend(fontsize=8)
    ring = np.linspace(0, 2 * np.pi, 300)
    axes[1, 0].plot(np.cos(ring), np.sin(ring), "k--", lw=1, label="ideal unit equator")
    axes[1, 0].set(aspect="equal", xlim=(-1.1, 1.1), ylim=(-1.1, 1.1), xlabel=r"$S_2$", ylabel=r"$S_3$", title="Held-step equatorial means")
    axes[1, 0].legend(fontsize=8)
    axes[1, 1].set(xlabel="OUT1 / phi1 RP command (V)", ylabel="PD mean (V)", title="Simultaneous final-F PD monitor")
    axes[1, 1].legend(fontsize=8)
    fit_text = "\n".join(
        f"{direction}: Vλ fit={values['fit']['fitted_vlambda_rp_v']:.4f} V, "
        f"phase residual={values['fit']['circular_residual_rms_rad']:.3f} rad"
        for direction, values in series.items()
    )
    fig.suptitle("Phi1 held-step map at first-NPBS D\n" + fit_text, fontsize=12)
    png, pdf = output / "phi1-step-map-report.png", output / "phi1-step-map-report.pdf"
    if args.format in {"png", "both"}:
        fig.savefig(png, dpi=180)
    if document is not None:
        document.savefig(fig)
    elif args.format in {"pdf", "both"}:
        with PdfPages(pdf) as document:
            document.savefig(fig)
    plt.close(fig)
    print(f"Wrote phi1 held-step analysis to {output}")
    for direction, values in series.items():
        fit = values["fit"]
        print(f"{direction}: fitted phi1 V_lambda={fit['fitted_vlambda_rp_v']:.5f} RP V; phase residual RMS={fit['circular_residual_rms_rad']:.4f} rad")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="phi1-step-map data.csv")
    parser.add_argument("--format", choices=("png", "pdf", "both"), default="pdf")
    args = parser.parse_args()
    create_report(args.csv, args.format)


if __name__ == "__main__":
    main()
