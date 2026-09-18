#!/usr/bin/env python3
"""Time-domain report for a slow phi1 scan: raw PAX at D, analyzer in C, PD at F.

This report intentionally does not flatten a time-dependent experiment into a
scatter against nominal command.  It asks the experimental questions directly:
as phi1 is driven slowly, how does the raw D-port Stokes state move, and is
there simultaneous phase-synchronous power response at final-port F?
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


def _column(rows: list[dict[str, str]], name: str) -> np.ndarray:
    return np.asarray([float(row[name]) for row in rows], dtype=float)


def _fit_sine(time: np.ndarray, signal: np.ndarray, frequency_hz: float) -> tuple[np.ndarray, dict[str, float]]:
    """Fit one known-frequency response in time; preserves phase information."""
    omega_time = 2.0 * np.pi * frequency_hz * time
    design = np.column_stack((np.ones_like(time), np.cos(omega_time), np.sin(omega_time)))
    base, cosine, sine = np.linalg.lstsq(design, signal, rcond=None)[0]
    fitted = design @ np.asarray((base, cosine, sine))
    amplitude = float(np.hypot(cosine, sine)); variance = np.sum((signal - np.mean(signal)) ** 2)
    return fitted, {"baseline": float(base), "amplitude": amplitude,
                    "contrast": amplitude / abs(float(base)) if base else float("nan"),
                    "phase_rad": float(np.arctan2(-sine, cosine)),
                    "r_squared": float(1 - np.sum((signal - fitted) ** 2) / variance) if variance else float("nan")}


def create_report(csv_file: Path, output_format: str = "pdf", document=None) -> None:
    from types import SimpleNamespace
    args = SimpleNamespace(csv=Path(csv_file), format=output_format)
    with args.csv.open(newline="") as handle: rows = list(csv.DictReader(handle))
    required = {"stage", "elapsed_s", "phi1_rp_command_estimated_v", "phi1_ideal_rad", "sine_frequency_hz", "pd_mean_v", "pax_ptotal", "s1", "s2", "s3", "dop"}
    if not rows or not required.issubset(rows[0]): raise ValueError("Expected d-polarizer-phi1-test data.csv")
    driven = np.asarray([row["stage"] == "phi1_sine" for row in rows]); static = ~driven
    time, command, phase, pd, ptotal = (_column(rows, key) for key in ("elapsed_s", "phi1_rp_command_estimated_v", "phi1_ideal_rad", "pd_mean_v", "pax_ptotal"))
    s = np.column_stack([_column(rows, key) for key in ("s1", "s2", "s3")])
    frequency = float(_column(rows, "sine_frequency_hz")[driven][0])
    t, c, p, pd_drive, total_drive, s_drive = time[driven], command[driven], phase[driven], pd[driven], ptotal[driven], s[driven]
    pd_fit, pd_metrics = _fit_sine(t, pd_drive, frequency)
    total_fit, total_metrics = _fit_sine(t, total_drive, frequency)
    # D's raw equatorial angle. PAX convention gives u=atan2(S3,S2); unwrap
    # only for a readable continuous time trace, while retaining raw circle.
    d_angle_raw = np.arctan2(s_drive[:, 2], s_drive[:, 1])
    d_angle_unwrapped = np.unwrap(d_angle_raw)
    # Compare raw D polarization directly to the *actual logged timing* of
    # the command, allowing one arbitrary static phase delta.
    z = s_drive[:, 2] - 1j * s_drive[:, 1]
    phasor = np.mean(z * np.exp(-1j * p))
    d_coherence, d_delta = float(abs(phasor)), float(np.angle(phasor))
    summary = {
        "geometry": "Both input paths open. PAX at raw D. Linear polarizer in C immediately before final NPBS. PD at final port F.",
        "drive": {"frequency_hz": frequency, "duration_s": float(t[-1] - t[0]), "samples": int(len(t))},
        "pd_final_f_known_frequency_fit": pd_metrics,
        "pax_d_total_power_known_frequency_fit": total_metrics,
        "pax_d": {"equator_s1_rms": float(np.sqrt(np.mean(s_drive[:, 0] ** 2))), "command_phase_coherence": d_coherence,
                  "best_static_phase_offset_rad": d_delta, "equatorial_angle_span_rad": float(np.ptp(d_angle_unwrapped))},
        "static": {"pd_mean_std": [float(np.mean(pd[static])), float(np.std(pd[static]))],
                   "pax_ptotal_mean_std": [float(np.mean(ptotal[static])), float(np.std(ptotal[static]))],
                   "stokes_std": np.std(s[static], axis=0).tolist()},
    }
    out = args.csv.parent / "d-polarizer-phi1-analysis"; out.mkdir(exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    # Primary plot: the hardware command and analyzer response in actual time.
    command_axis = axes[0, 0]
    command_axis.plot(t, c, color="black", lw=1.2, label="OUT1 command (RP V)")
    command_axis.set(xlabel="driven-stage time (s)", ylabel="OUT1 / phi1 command (RP V)", title="Slow phi1 drive and final-F PD response")
    power_axis = command_axis.twinx(); power_axis.plot(t, pd_drive, ".", ms=3, color="tab:red", alpha=.6, label="PD at final F")
    power_axis.plot(t, pd_fit, color="tab:red", lw=1.5, label=f"{frequency:.3f}-Hz fit; R²={pd_metrics['r_squared']:.3f}")
    power_axis.set_ylabel("PD voltage", color="tab:red")
    lines = command_axis.get_lines() + power_axis.get_lines(); command_axis.legend(lines, [line.get_label() for line in lines], fontsize=8, loc="upper right")
    # Raw PAX Stokes in time, with command phase cycles visibly aligned.
    for index, label in enumerate((r"$S_1$", r"$S_2$", r"$S_3$")): axes[0, 1].plot(t, s_drive[:, index], ".", ms=2.5, label=label)
    axes[0, 1].set(xlabel="driven-stage time (s)", ylabel="raw PAX Stokes", ylim=(-1.1, 1.1), title="Raw D-port polarization during phi1 drive")
    axes[0, 1].legend(fontsize=8)
    # Geometric answer: does it stay close to D's equator and how does it move?
    dots = axes[1, 0].scatter(s_drive[:, 1], s_drive[:, 2], c=t, s=10, cmap="viridis", label="D-port samples")
    ring = np.linspace(0, 2 * np.pi, 300); axes[1, 0].plot(np.cos(ring), np.sin(ring), "k--", lw=1, label="ideal D equator")
    axes[1, 0].set(aspect="equal", xlim=(-1.1, 1.1), ylim=(-1.1, 1.1), xlabel=r"$S_2$", ylabel=r"$S_3$", title=f"D equator; command coherence={d_coherence:.3f}")
    axes[1, 0].legend(fontsize=8); fig.colorbar(dots, ax=axes[1, 0], label="driven-stage time (s)")
    # Direct timing comparison: commanded phase and measured equatorial angle.
    axes[1, 1].plot(t, np.unwrap(p), color="black", lw=1.2, label=r"commanded $\phi_1$ (unwrapped)")
    axes[1, 1].plot(t, d_angle_unwrapped - d_delta, ".", ms=2.5, color="tab:blue", label=r"PAX D equatorial angle (offset-corrected)")
    axes[1, 1].set(xlabel="driven-stage time (s)", ylabel="angle (rad)", title="Does raw D polarization follow the command?")
    axes[1, 1].legend(fontsize=8)
    fig.suptitle(f"Phi1 authority test — C-arm polarizer, raw PAX at D, final-F PD; drive = {frequency:.3f} Hz")
    if args.format in {"png", "both"}: fig.savefig(out / "c-polarizer-phi1-analysis.png", dpi=180)
    if document is not None:
        document.savefig(fig)
    elif args.format in {"pdf", "both"}:
        with PdfPages(out / "c-polarizer-phi1-report.pdf") as pdf: pdf.savefig(fig)
    plt.close(fig)
    print(f"Wrote C-polarizer / D-port time-domain analysis to {out}")
    print(f"PD-F: contrast={pd_metrics['contrast']:.3f}, R2={pd_metrics['r_squared']:.3f}; PAX-D command coherence={d_coherence:.3f}")

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path); parser.add_argument("--format", choices=("png", "pdf", "both"), default="pdf")
    args = parser.parse_args()
    create_report(args.csv, args.format)


if __name__ == "__main__": main()
