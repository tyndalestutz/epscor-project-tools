#!/usr/bin/env python3
"""Render figures for the hybrid phi1 locking checkpoint report.

The report itself is intentionally tied to three short, repeated hardware
trials.  This script keeps the plotted evidence derived from the source CSVs
rather than hand-entered values in the LaTeX document.
"""
from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-hybrid-checkpoint")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "experiments" / "polarization_locking" / "2026-08-15"
OUTPUT = DATA_ROOT / "hybrid-locking-checkpoint-report"
FIGURES = OUTPUT / "figures"
RUNS = (
    "021114_phi1-pd-hybrid-test_autonomous-hybrid-gentle-0",
    "021210_phi1-pd-hybrid-test_autonomous-hybrid-gentle-1",
    "021302_phi1-pd-hybrid-test_autonomous-hybrid-gentle-2",
)


@dataclass(frozen=True)
class Run:
    name: str
    t: np.ndarray
    u_error: np.ndarray
    pd_error: np.ndarray
    output: np.ndarray


def wrap(angle: np.ndarray) -> np.ndarray:
    return (angle + math.pi) % (2 * math.pi) - math.pi


def read_run(name: str) -> Run:
    with (DATA_ROOT / name / "data.csv").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    target = float(next(row for row in rows if row["stage"] == "handoff-target")["u"])
    hold = [row for row in rows if row["stage"] == "pd-fpga-pid"]
    pax = [row for row in hold if row["pax_sample"] == "1"]
    return Run(
        name=name.rsplit("_", 1)[-1],
        t=np.asarray([float(row["elapsed_s"]) for row in pax]),
        u_error=wrap(np.asarray([float(row["u"]) for row in pax]) - target),
        pd_error=np.asarray([float(row["pd_error_v"]) for row in hold]),
        output=np.asarray([float(row["pid_output_v"]) for row in hold]),
    )


def _box(axis: plt.Axes, xy: tuple[float, float], text: str, color: str) -> None:
    box = FancyBboxPatch(xy, 1.78, .55, boxstyle="round,pad=.05", facecolor=color, edgecolor="0.35")
    axis.add_patch(box)
    axis.text(xy[0] + .89, xy[1] + .275, text, ha="center", va="center", fontsize=9)


def architecture_figure() -> None:
    figure, axis = plt.subplots(figsize=(7.25, 2.35))
    axis.set(xlim=(0, 10.7), ylim=(0, 2.3))
    axis.set_axis_off()
    _box(axis, (.2, 1.42), "PAX monitor\n(averaged Stokes)", "#e9f3ff")
    _box(axis, (3.1, 1.42), "slow outer loop\n$u$ error $\\rightarrow PD_{sp}$", "#eaf7ee")
    _box(axis, (6.0, 1.42), "FPGA PID\n$PD\\rightarrow OUT1$", "#fff3db")
    _box(axis, (8.9, 1.42), "$\\phi_1$ PZT", "#fce9ee")
    _box(axis, (8.9, .25), "F-port PD\n(IN1)", "#f3f3f3")
    for start, end in [((1.98, 1.695), (3.1, 1.695)), ((4.88, 1.695), (6.0, 1.695)), ((7.78, 1.695), (8.9, 1.695))]:
        axis.add_patch(FancyArrowPatch(start, end, arrowstyle="->", mutation_scale=12, lw=1.5))
    axis.add_patch(FancyArrowPatch((9.78, .80), (9.78, 1.42), arrowstyle="->", mutation_scale=12, lw=1.5))
    axis.text(5.35, .75, "fast, continuous hardware feedback", ha="center", color="#7a4c00", fontsize=9)
    axis.text(2.1, 2.12, "slow, polarization-referenced bias correction", ha="center", color="#176b37", fontsize=9)
    figure.tight_layout(pad=.15)
    figure.savefig(FIGURES / "architecture.pdf")
    plt.close(figure)


def results_figure(runs: list[Run]) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(7.25, 3.0), gridspec_kw={"width_ratios": (1.45, 1.0)})
    colors = ("#2c7fb8", "#41ab5d", "#88419d")
    for run, color in zip(runs, colors):
        axes[0].plot(run.t - run.t[0], run.u_error, "o-", lw=1, ms=3.5, color=color, label=run.name)
    axes[0].axhline(0, color="0.2", lw=.8)
    axes[0].axhspan(-.05, .05, color="#dff3e6", zorder=-1, label="±0.05 rad goal band")
    axes[0].set(xlabel="hold time (s)", ylabel="$\\Delta u$ (rad)", title="Three repeated hybrid holds")
    axes[0].legend(fontsize=7.0, ncol=2, frameon=False, loc="lower right")
    axes[0].grid(alpha=.22)

    medians = np.asarray([np.median(abs(run.u_error)) for run in runs])
    late = np.asarray([np.median(abs(run.u_error[run.t >= run.t[-1] - 12])) for run in runs])
    positions = np.arange(len(runs))
    axes[1].bar(positions - .18, medians, .34, color="#4c78a8", label="all hold")
    axes[1].bar(positions + .18, late, .34, color="#59a14f", label="final 12 s")
    axes[1].axhline(.05, color="#b22222", ls="--", lw=1, label="0.05 rad")
    axes[1].set(xticks=positions, xticklabels=["run 1", "run 2", "run 3"], ylabel="median $|\\Delta u|$ (rad)", title="Residual summary", ylim=(0, .075))
    axes[1].legend(fontsize=7.5, frameon=False, loc="upper right")
    axes[1].grid(axis="y", alpha=.22)
    figure.tight_layout(pad=.4)
    figure.savefig(FIGURES / "results.pdf")
    plt.close(figure)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(exist_ok=True)
    runs = [read_run(name) for name in RUNS]
    architecture_figure()
    results_figure(runs)


if __name__ == "__main__":
    main()
