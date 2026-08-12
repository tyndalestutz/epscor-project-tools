#!/usr/bin/env python3
"""Plot guided phi2 power-balance measurements and their calculation table."""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-power-balance")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

CONDITIONS = ("path_a_only", "path_b_only", "both_paths")
LABELS = {"path_a_only": "path A only", "path_b_only": "path B only", "both_paths": "both paths"}
COLORS = {"path_a_only": "tab:blue", "path_b_only": "tab:orange", "both_paths": "tab:green"}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{path} contains no power-balance rows")
    return rows


def _title(path: Path) -> str:
    return path.parent.name if path.name == "data.csv" else path.name


def _field(row: dict[str, str], new: str, old: str) -> float:
    return float(row[new]) if new in row else float(row[old])


def _summary(rows: list[dict[str, str]]) -> list[list[str]]:
    table: list[list[str]] = []
    for condition in CONDITIONS:
        group = [row for row in rows if row["condition"] == condition]
        if not group:
            continue
        pd = np.asarray([float(row["pd_mean_v"]) for row in group])
        pax = np.asarray([float(row["pax_ptotal"]) for row in group])
        pd_mean = _field(group[0], "pd_condition_mean_v", "pd_mean_v")
        pax_mean = _field(group[0], "pax_ptotal_condition_mean", "pax_ptotal")
        pd_contrast = _field(group[0], "pd_contrast_5_95", "pd_contrast")
        pax_contrast = _field(group[0], "pax_ptotal_contrast_5_95", "pax_ptotal_contrast")
        table.append([
            LABELS[condition], str(len(group)), f"{pd_mean:.6g}", f"{np.std(pd):.3g}", f"{pd_contrast:.4f}",
            f"{pax_mean:.6g}", f"{np.std(pax):.3g}", f"{pax_contrast:.4f}",
        ])
    return table


def add_scope_page(pdf: PdfPages, input_file: Path, png_file: Path | None = None) -> None:
    rows = load_rows(input_file)
    figure, axes = plt.subplots(3, 1, figsize=(11.5, 8.5), sharex=False)
    figure.subplots_adjust(left=0.09, right=0.85, top=0.90, bottom=0.10, hspace=0.40)
    figure.suptitle(f"Phi2 power balance — time-domain normalized scope traces — {_title(input_file)}", y=0.965, fontsize=14, fontweight="bold")
    for axis, condition in zip(axes, CONDITIONS):
        group = sorted((row for row in rows if row["condition"] == condition), key=lambda row: float(row["elapsed_s"]))
        if not group:
            axis.set_visible(False)
            continue
        time_s = np.asarray([float(row["elapsed_s"]) for row in group])
        command = np.asarray([float(row["phi2_rp_command_estimated_v"]) for row in group])
        pd = np.asarray([float(row["pd_mean_v"]) for row in group])
        pax = np.asarray([float(row["pax_ptotal"]) for row in group])
        # Mean-normalized signals preserve the actual fringe modulation and
        # make multiple cycles read like a scope trace. They are not a claim
        # that the two detector units are absolutely power calibrated.
        axis.plot(time_s, pd / np.mean(pd), color="tab:blue", linewidth=1.05, label="PD / condition mean")
        axis.plot(time_s, pax / np.mean(pax), color="tab:red", linewidth=1.05, label="PAX ptotal / condition mean")
        command_axis = axis.twinx()
        command_axis.plot(time_s, command, color="black", alpha=0.35, linewidth=0.9, label="phi2 RP command")
        command_axis.set_ylabel("phi2 RP command (V)", color="#4b5563")
        command_axis.tick_params(axis="y", colors="#4b5563")
        axis.set(title=LABELS[condition], xlabel="elapsed time (s)", ylabel="signal / condition mean")
        axis.grid(alpha=0.22)
        handles, labels = axis.get_legend_handles_labels()
        command_handles, command_labels = command_axis.get_legend_handles_labels()
        axis.legend(handles + command_handles, labels + command_labels, fontsize=8, loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0.0)
    if png_file:
        figure.savefig(png_file, dpi=180)
    pdf.savefig(figure)
    plt.close(figure)


def add_summary_table_page(pdf: PdfPages, input_file: Path, png_file: Path | None = None) -> None:
    rows = load_rows(input_file)
    figure, axis = plt.subplots(figsize=(11.5, 5.5))
    figure.subplots_adjust(left=0.04, right=0.96, top=0.80, bottom=0.12)
    figure.suptitle(f"Phi2 power-balance calculations — {_title(input_file)}", y=0.94, fontsize=14, fontweight="bold")
    axis.axis("off")
    headers = ["condition", "samples", "mean PD (V)", "PD σ (V)", "PD contrast\n(5–95%)", "mean PAX ptotal", "PAX σ", "PAX contrast\n(5–95%)"]
    table = axis.table(cellText=_summary(rows), colLabels=headers, loc="center", cellLoc="center", colLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.05, 1.9)
    for (row, _), cell in table.get_celld().items():
        if row == 0:
            cell.set_facecolor("#dbeafe")
            cell.set_text_props(weight="bold")
        elif row % 2:
            cell.set_facecolor("#f8fafc")
    axis.text(
        0.5, 0.06,
        "Contrast uses robust 5th/95th-percentile extrema. PD volts and PAX ptotal are different detector units; "
        "a physical 40/60 port split requires a relative detector-power calibration.",
        ha="center", va="center", fontsize=9, color="#374151", wrap=True,
    )
    if png_file:
        figure.savefig(png_file, dpi=180)
    pdf.savefig(figure)
    plt.close(figure)


def write_summary_csv(input_file: Path, output_file: Path) -> None:
    rows = load_rows(input_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["condition", "samples", "mean_pd_v", "pd_std_v", "pd_contrast_5_95", "mean_pax_ptotal", "pax_ptotal_std", "pax_ptotal_contrast_5_95"])
        writer.writerows(_summary(rows))


def create_report(input_file: Path, pdf_file: Path | None = None, png_directory: Path | None = None) -> None:
    if png_directory is not None:
        png_directory.mkdir(parents=True, exist_ok=True)
        write_summary_csv(input_file, png_directory / "power-summary.csv")
    if pdf_file is not None:
        pdf_file.parent.mkdir(parents=True, exist_ok=True)
        with PdfPages(pdf_file) as pdf:
            add_scope_page(pdf, input_file, png_directory / "normalized-scope.png" if png_directory else None)
            add_summary_table_page(pdf, input_file, png_directory / "power-summary.png" if png_directory else None)
    else:
        with PdfPages(os.devnull) as pdf:
            add_scope_page(pdf, input_file, png_directory / "normalized-scope.png")
            add_summary_table_page(pdf, input_file, png_directory / "power-summary.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--png-dir", type=Path)
    args = parser.parse_args()
    if args.pdf is None and args.png_dir is None:
        parser.error("select --pdf, --png-dir, or both")
    create_report(args.input_file, args.pdf, args.png_dir)
    if args.pdf:
        print(f"Wrote {args.pdf}")
    if args.png_dir:
        print(f"Wrote PNG plots and power-summary.csv to {args.png_dir}")


if __name__ == "__main__":
    main()
