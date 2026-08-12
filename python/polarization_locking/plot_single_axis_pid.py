#!/usr/bin/env python3
"""Report the calibration and isolated PI hold from a single-axis PID test."""
from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-single-axis-pid")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{path} contains no single-axis PID data")
    return rows


def wrap(angle: np.ndarray | float) -> np.ndarray | float:
    return (np.asarray(angle) + math.pi) % (2.0 * math.pi) - math.pi


def add_page(pdf: PdfPages, input_file: Path) -> None:
    rows = load_rows(input_file)
    axis = rows[0]["axis"]
    coordinate = rows[0]["coordinate"]
    other_coordinate = "v" if coordinate == "u" else "u"
    calibration = [row for row in rows if row["stage"] == "calibration"]
    hold = [row for row in rows if row["stage"] == "pid"]
    if not calibration or not hold:
        raise ValueError("Single-axis report requires calibration and PID rows")
    target = float(hold[0]["target_coordinate"])
    time_s = np.asarray([float(row["elapsed_s"]) for row in hold])
    controlled = np.asarray([float(row[coordinate]) for row in hold])
    local_controlled = target + wrap(controlled - target) if coordinate == "u" else controlled
    error = np.asarray([float(row["controlled_error_rad"]) for row in hold])
    output = np.asarray([float(row["rp_out1_v"] if axis == "phi1" else row["rp_out2_v"]) for row in hold])
    other = np.asarray([float(row[other_coordinate]) for row in hold])
    integral = np.asarray([float(row["integral_rad_s"]) for row in hold])
    delta = np.asarray([float(row["delta_rp_v"]) for row in hold])
    saturation = np.asarray([float(row["saturated"]) for row in hold])
    cal_command = np.asarray([
        float(row["rp_out1_v"] if axis == "phi1" else row["rp_out2_v"]) for row in calibration
    ])
    cal_coordinate = np.asarray([float(row[coordinate]) for row in calibration])
    cal_local = target + wrap(cal_coordinate - target) if coordinate == "u" else cal_coordinate
    duration = time_s[-1] - time_s[0]
    late = time_s >= time_s[-1] - min(30.0, duration / 2.0)

    figure, axes = plt.subplots(3, 2, figsize=(12.5, 10.0))
    figure.subplots_adjust(left=0.075, right=0.975, top=0.89, bottom=0.17, hspace=0.52, wspace=0.28)
    figure.suptitle(f"Single-actuator PI test — {axis} controls {coordinate} — {input_file.parent.name}", y=0.965, fontsize=15, fontweight="bold")

    axes[0, 0].plot(cal_command, cal_local, marker="o", linewidth=1.2, color="tab:blue", label=f"measured {coordinate}")
    axes[0, 0].axhline(target, color="black", linestyle="--", linewidth=1.0, label=f"midpoint target = {target:.3f}")
    axes[0, 0].set(title="Fast one-Vλ calibration", xlabel=f"{axis} RP command (V)", ylabel=f"{coordinate} (rad)")
    axes[0, 0].legend(fontsize=8)

    axes[1, 0].plot(time_s, local_controlled, color="tab:blue", linewidth=1.2, label=f"measured {coordinate}")
    axes[1, 0].axhline(target, color="black", linestyle="--", linewidth=1.0, label="target")
    axes[1, 0].set(title=f"Controlled coordinate: {axis} → {coordinate}", xlabel="elapsed time (s)", ylabel=f"{coordinate} (rad)")
    axes[1, 0].legend(fontsize=8)

    axes[2, 0].plot(time_s, error, color="tab:blue", linewidth=1.1, label=f"{coordinate} error")
    axes[2, 0].axhline(0.0, color="black", linewidth=0.8)
    axes[2, 0].axhspan(-0.25, 0.25, color="tab:green", alpha=0.10, label="±0.25 rad band")
    axes[2, 0].set(title="Controlled-coordinate error", xlabel="elapsed time (s)", ylabel="error (rad)")
    axes[2, 0].legend(fontsize=8)

    axes[0, 1].plot(time_s, output, color="tab:orange", linewidth=1.2, label=f"RP {axis} command")
    axes[0, 1].scatter(time_s[saturation > 0], output[saturation > 0], color="tab:red", marker="x", s=18, label="rail limited")
    axes[0, 1].set(title="Only commanded actuator", xlabel="elapsed time (s)", ylabel="RP command (V)", ylim=(-0.03, 1.03))
    axes[0, 1].legend(fontsize=8)

    axes[1, 1].plot(time_s, other, color="tab:green", linewidth=1.1)
    axes[1, 1].set(title=f"Uncontrolled coordinate: {other_coordinate}", xlabel="elapsed time (s)", ylabel=f"{other_coordinate} (rad)")

    axes[2, 1].plot(time_s, integral, color="tab:purple", linewidth=1.1, label="integral")
    axes[2, 1].plot(time_s, delta, color="tab:orange", linewidth=0.9, alpha=0.8, label="RP update")
    axes[2, 1].axhline(0.0, color="black", linewidth=0.8)
    axes[2, 1].set(title="PI state and incremental command", xlabel="elapsed time (s)", ylabel="native units")
    axes[2, 1].legend(fontsize=8)
    for plot_axis in axes.flat:
        plot_axis.grid(alpha=0.22)

    summary = (
        f"Target {coordinate}: {target:+.4f} rad     Duration: {duration:.1f} s     "
        f"Median |error|: {np.median(abs(error)):.3f} rad (full), {np.median(abs(error[late])):.3f} rad (late)\n"
        f"Controlled-coordinate σ: {np.std(local_controlled):.3f} rad     "
        f"Uncontrolled {other_coordinate} excursion (5–95%): {np.quantile(other, .95)-np.quantile(other, .05):.3f} rad     "
        f"Rail-limited samples: {int(np.sum(saturation))}/{len(saturation)}"
    )
    summary_axis = figure.add_axes((0.075, 0.035, 0.90, 0.095))
    summary_axis.set_axis_off()
    summary_axis.text(0.5, 0.5, summary, ha="center", va="center", fontsize=10, linespacing=1.55, bbox={"boxstyle": "round,pad=0.55", "facecolor": "#f1f4f8", "edgecolor": "#aeb8c2"})
    pdf.savefig(figure)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--output", "-o", type=Path, required=True)
    args = parser.parse_args()
    with PdfPages(args.output) as pdf:
        add_page(pdf, args.input_file)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
