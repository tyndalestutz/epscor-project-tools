#!/usr/bin/env python3
"""Analyze the isolated first-NPBS D-port polarization test.

Ideal D-port prediction (transmitted-first convention):

    D = (i exp(i (delta + phi1)), 1) / sqrt(2),
    S = (0, -sin(delta + phi1), cos(delta + phi1)).

The fitted ``delta`` absorbs cable/trigger phase and static optical phase. It
does not change the circle's equatorial prediction.  No controller code is
changed by this analysis.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-field-propagation")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


def _float(rows: list[dict[str, str]], key: str) -> np.ndarray:
    return np.asarray([float(row[key]) for row in rows], dtype=float)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="first-npbs-d-test data.csv")
    parser.add_argument("--format", choices=("png", "pdf", "both"), default="png", help="report output format (default: png)")
    args = parser.parse_args()
    with args.csv.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"stage", "phi1_ideal_rad", "s1", "s2", "s3", "dop", "pax_ptotal", "elapsed_s"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Expected a first-npbs-d-test CSV")
    stage = np.asarray([row["stage"] for row in rows])
    driven = stage == "phi1_sine"
    static = stage == "static"
    phi1 = _float(rows, "phi1_ideal_rad")
    s1, s2, s3 = (_float(rows, key) for key in ("s1", "s2", "s3"))
    radius = np.hypot(s2, s3)
    # Fit a single static phase offset to both equatorial coordinates:
    # S2=-sin(phi1+delta), S3=cos(phi1+delta).  Complex form z=S3-i*S2
    # should be exp(i(phi1+delta)); delta is its circular mean residual.
    z = s3[driven] - 1j * s2[driven]
    delta = float(np.angle(np.mean(z * np.exp(-1j * phi1[driven])))) if np.any(driven) else float("nan")
    ideal_s2 = -np.sin(phi1 + delta)
    ideal_s3 = np.cos(phi1 + delta)
    equator_rms = float(np.sqrt(np.mean(s1[driven] ** 2))) if np.any(driven) else float("nan")
    circle_rms = float(np.sqrt(np.mean((radius[driven] - 1.0) ** 2))) if np.any(driven) else float("nan")
    trajectory_rms = float(np.sqrt(np.mean((s2[driven] - ideal_s2[driven]) ** 2 + (s3[driven] - ideal_s3[driven]) ** 2))) if np.any(driven) else float("nan")
    # A phase extracted from the equatorial components makes it possible to
    # distinguish the ideal *circle* from faithful voltage-to-phase command.
    measured_phase = np.unwrap(np.angle(s3 - 1j * s2))
    time = _float(rows, "elapsed_s")
    driven_time = time[driven]
    driven_z = z
    frequencies = np.linspace(0.05, 2.5, 1500)
    coherence = np.asarray([abs(np.mean(driven_z * np.exp(-2j * np.pi * frequency * driven_time))) for frequency in frequencies])
    peak_index = int(np.argmax(coherence))
    observed_peak_hz = float(frequencies[peak_index])
    observed_peak_coherence = float(coherence[peak_index])
    commanded_frequency = float(_float(rows, "sine_frequency_hz")[driven][0]) if np.any(driven) else float("nan")
    command_coherence = float(abs(np.mean(driven_z * np.exp(-2j * np.pi * commanded_frequency * driven_time)))) if np.any(driven) else float("nan")
    static_z = s3[static] - 1j * s2[static]
    static_phase_concentration = float(abs(np.mean(static_z))) if np.any(static) else float("nan")
    summary = {
        "ideal_model": "D=(i exp(i(delta+phi1)), 1)/sqrt(2); S=(0,-sin(delta+phi1),cos(delta+phi1))",
        "samples": {"static": int(np.sum(static)), "phi1_sine": int(np.sum(driven))},
        "fitted_static_phase_offset_rad": delta,
        "driven_equator_s1_rms": equator_rms,
        "driven_s2s3_radius_rms_from_one": circle_rms,
        "driven_trajectory_rms": trajectory_rms,
        "driven_command_frequency_hz": commanded_frequency,
        "driven_command_phase_coherence": command_coherence,
        "driven_strongest_phase_frequency_hz": observed_peak_hz,
        "driven_strongest_phase_coherence": observed_peak_coherence,
        "static": {
            "mean_stokes": [float(np.mean(axis[static])) if np.any(static) else float("nan") for axis in (s1, s2, s3)],
            "std_stokes": [float(np.std(axis[static])) if np.any(static) else float("nan") for axis in (s1, s2, s3)],
            "mean_dop": float(np.mean(_float(rows, "dop")[static])) if np.any(static) else float("nan"),
            "mean_ptotal": float(np.mean(_float(rows, "pax_ptotal")[static])) if np.any(static) else float("nan"),
            "equatorial_phase_concentration": static_phase_concentration,
        },
    }
    output = args.csv.parent / "first-npbs-d-analysis"
    output.mkdir(exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    fig, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
    axes[0, 0].plot(time, s1, ".", ms=3, label=r"measured $S_1$")
    axes[0, 0].axhline(0.0, color="k", lw=1, ls="--", label="ideal equator")
    axes[0, 0].set(xlabel="stage-local time (s)", ylabel="Stokes", title=r"Equator test: ideal $S_1=0$")
    axes[0, 0].legend(fontsize=8)
    axes[0, 1].plot(s2[driven], s3[driven], ".", ms=3, label="measured driven")
    ring = np.linspace(0, 2 * np.pi, 300)
    axes[0, 1].plot(np.cos(ring), np.sin(ring), "k--", lw=1, label="ideal unit equator")
    axes[0, 1].set(aspect="equal", xlim=(-1.1, 1.1), ylim=(-1.1, 1.1), xlabel=r"$S_2$", ylabel=r"$S_3$", title="D-port equatorial trajectory")
    axes[0, 1].legend(fontsize=8)
    points = axes[1, 0].scatter(phi1[driven], s2[driven], c=driven_time, s=9, cmap="viridis", label=r"measured $S_2$")
    axes[1, 0].plot(phi1[driven], ideal_s2[driven], "-", lw=1.4, label=r"$-\sin(\phi_1+\delta)$")
    axes[1, 0].scatter(phi1[driven], s3[driven], c=driven_time, s=9, cmap="viridis", label=r"measured $S_3$")
    axes[1, 0].plot(phi1[driven], ideal_s3[driven], "-", lw=1.4, label=r"$\cos(\phi_1+\delta)$")
    axes[1, 0].set(xlabel=r"logged $phi_1$ command (rad)", ylabel="Stokes", title="Command-to-polarization transfer")
    axes[1, 0].legend(fontsize=8, ncol=2)
    axes[1, 1].plot(frequencies, coherence, lw=1.4, label="measured equatorial-phase coherence")
    axes[1, 1].axvline(commanded_frequency, color="tab:orange", ls="--", label=f"command: {commanded_frequency:.2f} Hz")
    axes[1, 1].axvline(observed_peak_hz, color="tab:red", ls=":", label=f"strongest: {observed_peak_hz:.2f} Hz")
    axes[1, 1].set(xlabel="test frequency (Hz)", ylabel="phasor coherence", title="Does D follow the phi1 drive?")
    axes[1, 1].legend(fontsize=8)
    fig.suptitle(
        f"First-NPBS D-port — equator RMS {equator_rms:.3f}; command coherence {command_coherence:.3f}; "
        f"strongest response {observed_peak_hz:.3f} Hz"
    )
    png = output / "d-port-polarization-analysis.png"
    pdf = output / "d-port-polarization-report.pdf"
    if args.format in {"png", "both"}:
        fig.savefig(png, dpi=180)
    if args.format in {"pdf", "both"}:
        with PdfPages(pdf) as report:
            report.savefig(fig)
    plt.close(fig)
    print(f"Wrote D-port analysis to {output}")
    print(
        f"equator S1 RMS={equator_rms:.4f}; S2/S3 trajectory RMS={trajectory_rms:.4f}; delta={delta:.4f} rad; "
        f"command coherence={command_coherence:.3f}; strongest phase response={observed_peak_hz:.3f} Hz"
    )


if __name__ == "__main__":
    main()
