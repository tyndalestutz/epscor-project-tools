#!/usr/bin/env python3
"""Report fixed-state A/B/both input-path isolation measured at first-NPBS D."""
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


def _values(rows: list[dict[str, str]], key: str) -> np.ndarray:
    return np.asarray([float(row[key]) for row in rows], dtype=float)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    parser.add_argument("--format", choices=("png", "pdf", "both"), default="png")
    args = parser.parse_args()
    with args.csv.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"condition", "elapsed_s", "s1", "s2", "s3", "dop", "pax_ptotal"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Expected a first-npbs-d-isolation CSV")
    conditions = ("path_a_only", "path_b_only", "both_paths")
    labels = {"path_a_only": "A only", "path_b_only": "B only", "both_paths": "both open"}
    colors = {"path_a_only": "tab:blue", "path_b_only": "tab:orange", "both_paths": "tab:green"}
    summary: dict[str, object] = {"ideal": {"path_a_only": [-1, 0, 0], "path_b_only": [1, 0, 0], "both_paths": "S1=0 equator"}, "conditions": {}}
    parsed: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for condition in conditions:
        group = [row for row in rows if row["condition"] == condition]
        if not group:
            continue
        s = np.column_stack([_values(group, key) for key in ("s1", "s2", "s3")])
        t = _values(group, "elapsed_s")
        z = s[:, 2] - 1j * s[:, 1]
        summary["conditions"][condition] = {
            "samples": len(group), "mean_stokes": np.mean(s, axis=0).tolist(), "std_stokes": np.std(s, axis=0).tolist(),
            "s1_rms": float(np.sqrt(np.mean(s[:, 0] ** 2))), "equatorial_phase_concentration": float(abs(np.mean(z))),
            "mean_dop": float(np.mean(_values(group, "dop"))), "mean_ptotal": float(np.mean(_values(group, "pax_ptotal"))),
        }
        parsed[condition] = (t, s)
    output = args.csv.parent / "first-npbs-d-isolation-analysis"
    output.mkdir(exist_ok=True)
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), constrained_layout=True)
    for condition, (time, stokes) in parsed.items():
        for index, name in enumerate((r"$S_1$", r"$S_2$", r"$S_3$")):
            axes[0, 0].plot(time, stokes[:, index], ".", ms=2.5, color=colors[condition], alpha=.45,
                            label=f"{labels[condition]} {name}" if index == 0 else None)
        axes[0, 1].plot(stokes[:, 1], stokes[:, 2], ".", ms=2.5, color=colors[condition], alpha=.6, label=labels[condition])
    axes[0, 0].set(xlabel="condition-local time (s)", ylabel="Stokes", title="Fixed-state Stokes stability")
    axes[0, 0].legend(fontsize=7, ncol=3)
    circle = np.linspace(0, 2 * np.pi, 300)
    axes[0, 1].plot(np.cos(circle), np.sin(circle), "k--", lw=1, label="equator")
    axes[0, 1].set(aspect="equal", xlim=(-1.1, 1.1), ylim=(-1.1, 1.1), xlabel=r"$S_2$", ylabel=r"$S_3$", title="Equatorial projection")
    axes[0, 1].legend(fontsize=8)
    means = [summary["conditions"][condition]["mean_stokes"] for condition in conditions if condition in parsed]
    x = np.arange(len(means)); width = .25
    for index, name in enumerate((r"$S_1$", r"$S_2$", r"$S_3$")):
        axes[1, 0].bar(x + (index - 1) * width, [value[index] for value in means], width, label=name)
    axes[1, 0].set(xticks=x, xticklabels=[labels[c] for c in conditions if c in parsed], ylim=(-1.1, 1.1), ylabel="mean Stokes", title="Mean state versus ideal A/B references")
    axes[1, 0].legend()
    table_rows = []
    for condition in conditions:
        if condition not in parsed:
            continue
        metrics = summary["conditions"][condition]
        table_rows.append([labels[condition], f"{metrics['std_stokes'][0]:.3f}", f"{metrics['std_stokes'][1]:.3f}", f"{metrics['std_stokes'][2]:.3f}", f"{metrics['equatorial_phase_concentration']:.3f}", f"{metrics['mean_dop']:.3f}"])
    axes[1, 1].axis("off")
    axes[1, 1].table(cellText=table_rows, colLabels=["condition", "std S1", "std S2", "std S3", "phase R", "mean DOP"], loc="center")
    axes[1, 1].set_title("Stability summary")
    fig.suptitle("First-NPBS D-port A/B isolation — both RP outputs held at zero")
    if args.format in {"png", "both"}:
        fig.savefig(output / "d-port-isolation-analysis.png", dpi=180)
    if args.format in {"pdf", "both"}:
        with PdfPages(output / "d-port-isolation-report.pdf") as pdf:
            pdf.savefig(fig)
    plt.close(fig)
    print(f"Wrote D-port isolation analysis to {output}")


if __name__ == "__main__":
    main()
