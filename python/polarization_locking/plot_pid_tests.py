#!/usr/bin/env python3
"""Create a presentation-ready PDF report from one or more PID-test CSV logs.

Example:
    python python/polarization_locking/plot_pid_tests.py \
        pid-test-2.csv pid-test-3.csv pid-test-4.csv \
        --output pid-lock-report.pdf
"""
from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path

# The lab environment may not have a writable default Matplotlib cache.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-pid-tests")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


def wrap_angle(angle: np.ndarray | float) -> np.ndarray | float:
    return (np.asarray(angle) + math.pi) % (2 * math.pi) - math.pi


def column(rows: list[dict[str, str]], name: str, default: float = 0.0) -> np.ndarray:
    return np.asarray([float(row.get(name, default)) for row in rows], dtype=float)


def recenter_lines(axes: np.ndarray, time_s: np.ndarray, stages: list[str]) -> None:
    for event_time in time_s[np.asarray([stage == "pid-recenter" for stage in stages])]:
        for axis in axes.flat:
            axis.axvline(event_time, color="tab:red", alpha=0.35, linewidth=1.0, linestyle="--")


def add_page(pdf: PdfPages, input_file: Path) -> None:
    with input_file.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{input_file} contains no PID data")

    time_s = column(rows, "elapsed_s")
    stages = [row["stage"] for row in rows]
    u = column(rows, "u")
    v = column(rows, "v")
    target_u = float(rows[0]["target_u"])
    target_v = float(rows[0]["target_v"])
    error_u = column(rows, "error_phi1")
    error_v = column(rows, "error_phi2")
    out1 = column(rows, "rp_out1_v")
    out2 = column(rows, "rp_out2_v")
    integral1 = column(rows, "integral_phi1")
    integral2 = column(rows, "integral_phi2")
    dop = column(rows, "dop")
    sat1 = column(rows, "saturated_phi1")
    sat2 = column(rows, "saturated_phi2")

    # Keep the azimuth trace on the target's local branch rather than drawing
    # artificial 2*pi jumps at the [-pi, pi) coordinate cut.
    u_about_target = target_u + wrap_angle(u - target_u)
    recenter_count = sum(stage == "pid-recenter" for stage in stages)
    duration_s = time_s[-1] - time_s[0]
    both_small = np.mean((np.abs(error_u) < 0.25) & (np.abs(error_v) < 0.25))
    late = time_s >= time_s[-1] - min(30.0, duration_s / 2.0)

    figure, axes = plt.subplots(3, 2, figsize=(12.5, 9.0), constrained_layout=True)
    figure.suptitle(f"PID locking report — {input_file.name}", fontsize=16, fontweight="bold")

    axes[0, 0].plot(time_s, u_about_target, color="tab:blue", linewidth=1.2, label="measured u (local branch)")
    axes[0, 0].axhline(target_u, color="black", linestyle="--", linewidth=1.0, label=f"target u = {target_u:.3f}")
    axes[0, 0].set(title="Azimuthal coordinate", ylabel="u (rad)")
    axes[0, 0].legend(loc="best", fontsize=8)

    axes[1, 0].plot(time_s, v, color="tab:green", linewidth=1.2, label="measured v")
    axes[1, 0].axhline(target_v, color="black", linestyle="--", linewidth=1.0, label=f"target v = {target_v:.3f}")
    axes[1, 0].set(title="Polar coordinate", ylabel="v (rad)", ylim=(-0.05, math.pi + 0.05))
    axes[1, 0].legend(loc="best", fontsize=8)

    axes[2, 0].plot(time_s, error_u, color="tab:blue", linewidth=1.0, label="φ1 / u error")
    axes[2, 0].plot(time_s, error_v, color="tab:green", linewidth=1.0, label="φ2 / v error")
    axes[2, 0].axhline(0.0, color="black", linewidth=0.8)
    axes[2, 0].axhspan(-0.25, 0.25, color="tab:green", alpha=0.10, label="±0.25 rad band")
    axes[2, 0].set(title="Signed error", xlabel="elapsed time (s)", ylabel="error (rad)")
    axes[2, 0].legend(loc="best", fontsize=8)

    axes[0, 1].plot(time_s, out1, color="tab:blue", linewidth=1.2, label="OUT1 / φ1")
    axes[0, 1].plot(time_s, out2, color="tab:orange", linewidth=1.2, label="OUT2 / φ2")
    axes[0, 1].scatter(time_s[sat1 > 0], out1[sat1 > 0], marker="x", color="tab:red", s=16, label="rail limited")
    axes[0, 1].scatter(time_s[sat2 > 0], out2[sat2 > 0], marker="x", color="tab:red", s=16)
    axes[0, 1].set(title="RP actuator commands", ylabel="RP command (V)", ylim=(-0.03, 1.03))
    axes[0, 1].legend(loc="best", fontsize=8)

    axes[1, 1].plot(time_s, integral1, color="tab:blue", linewidth=1.0, label="φ1 integral")
    axes[1, 1].plot(time_s, integral2, color="tab:orange", linewidth=1.0, label="φ2 integral")
    axes[1, 1].axhline(0.0, color="black", linewidth=0.8)
    axes[1, 1].set(title="PI integral state", ylabel="integral (rad·s)")
    axes[1, 1].legend(loc="best", fontsize=8)

    axes[2, 1].plot(time_s, dop, color="tab:purple", linewidth=1.0, label="raw PAX dop")
    axes[2, 1].axhspan(0.0, 1.0, color="tab:green", alpha=0.10, label="physical DOP range")
    axes[2, 1].set(title="Raw PAX DOP (diagnostic only)", xlabel="elapsed time (s)", ylabel="reported DOP")
    axes[2, 1].legend(loc="best", fontsize=8)

    recenter_lines(axes, time_s, stages)
    for axis in axes.flat:
        axis.grid(alpha=0.22)

    summary = (
        f"Duration: {duration_s:.1f} s   |   recenter events: {recenter_count}   |   "
        f"both axes within ±0.25 rad: {both_small:.0%}\n"
        f"All samples median |error|: φ1={np.median(np.abs(error_u)):.3f} rad, φ2={np.median(np.abs(error_v)):.3f} rad   |   "
        f"last 30 s: φ1={np.median(np.abs(error_u[late])):.3f} rad, φ2={np.median(np.abs(error_v[late])):.3f} rad"
    )
    figure.text(0.5, 0.005, summary, ha="center", va="bottom", fontsize=9)
    pdf.savefig(figure)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_files", nargs="+", type=Path, help="PID-test CSV file(s)")
    parser.add_argument("--output", "-o", type=Path, default=Path("pid-lock-report.pdf"), help="output PDF path")
    args = parser.parse_args()
    with PdfPages(args.output) as pdf:
        for input_file in args.input_files:
            add_page(pdf, input_file)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
