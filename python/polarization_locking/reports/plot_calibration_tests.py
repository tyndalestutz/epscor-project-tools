#!/usr/bin/env python3
"""Create a compact PDF report for sweep, bidirectional, or diagnostic CSV data."""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-calibration-tests")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


def _rows(input_file: Path) -> list[dict[str, str]]:
    with input_file.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{input_file} contains no data")
    return rows


def _base_figure(title: str):
    figure, axes = plt.subplots(3, 1, figsize=(11.5, 8.5), sharex=True)
    figure.subplots_adjust(left=0.09, right=0.97, top=0.89, bottom=0.10, hspace=0.32)
    figure.suptitle(title, y=0.965, fontsize=15, fontweight="bold")
    return figure, axes


def _finish(pdf: PdfPages, figure, axes, xlabel: str) -> None:
    axes[0].set(ylabel="u (rad)", title="Azimuthal response (unwrapped)")
    axes[1].set(ylabel="v (rad)", title="Polar response")
    axes[2].set(ylabel="raw DOP", xlabel=xlabel, title="PAX DOP (diagnostic)")
    for axis in axes:
        axis.grid(alpha=0.22)
        axis.legend(loc="best", fontsize=8)
    pdf.savefig(figure)
    plt.close(figure)


def add_axis_sweep_page(pdf: PdfPages, input_file: Path, axis: str) -> None:
    rows = _rows(input_file)
    voltage_name = "rp_out1_v" if axis == "phi1" else "rp_out2_v"
    rows.sort(key=lambda row: float(row[voltage_name]))
    x = np.asarray([float(row[voltage_name]) for row in rows])
    u = np.unwrap(np.asarray([float(row["u"]) for row in rows]))
    v = np.asarray([float(row["v"]) for row in rows])
    dop = np.asarray([float(row["dop"]) for row in rows])
    figure, axes = _base_figure(f"Calibration sweep — {input_file.name} — swept {axis}")
    axes[0].plot(x, u, marker="o", ms=3, linewidth=1.2, label="measured u")
    axes[1].plot(x, v, marker="o", ms=3, linewidth=1.2, color="tab:green", label="measured v")
    axes[2].plot(x, dop, marker="o", ms=3, linewidth=1.2, color="tab:purple", label="raw PAX DOP")
    _finish(pdf, figure, axes, f"{axis} RP command (V)")


def add_bidirectional_page(pdf: PdfPages, input_file: Path) -> None:
    rows = _rows(input_file)
    axis = rows[0]["sweep_axis"]
    figure, axes = _base_figure(f"Bidirectional sweep — {input_file.name} — swept {axis}")
    for direction, color in (("forward", "tab:blue"), ("reverse", "tab:orange")):
        group = sorted((row for row in rows if row["direction"] == direction), key=lambda row: float(row["sweep_rp_v"]))
        x = np.asarray([float(row["sweep_rp_v"]) for row in group])
        axes[0].plot(x, np.unwrap([float(row["u"]) for row in group]), color=color, label=direction)
        axes[1].plot(x, [float(row["v"]) for row in group], color=color, label=direction)
        axes[2].plot(x, [float(row["dop"]) for row in group], color=color, label=direction)
    _finish(pdf, figure, axes, f"{axis} RP command (V)")


def add_diagnostic_page(pdf: PdfPages, input_file: Path) -> None:
    rows = _rows(input_file)
    figure, axes = _base_figure(f"Static diagnostic — {input_file.name}")
    labels = sorted({row["test_name"] for row in rows})
    colors = plt.get_cmap("tab10")(np.linspace(0, 1, max(1, len(labels))))
    for label, color in zip(labels, colors):
        group = [row for row in rows if row["test_name"] == label]
        x = np.arange(len(group))
        axes[0].plot(x, np.unwrap([float(row["u"]) for row in group]), color=color, label=label)
        axes[1].plot(x, [float(row["v"]) for row in group], color=color, label=label)
        axes[2].plot(x, [float(row["dop"]) for row in group], color=color, label=label)
    _finish(pdf, figure, axes, "sample index within condition")


def add_intensity_diagnostic_page(pdf: PdfPages, input_file: Path) -> None:
    rows = _rows(input_file)
    has_ptotal = "pax_ptotal" in rows[0]
    figure, axes = plt.subplots(3 if has_ptotal else 2, 2, figsize=(11.5, 10.2 if has_ptotal else 8.5))
    figure.subplots_adjust(left=0.09, right=0.97, top=0.90, bottom=0.08, hspace=0.38, wspace=0.28)
    title = input_file.parent.name if input_file.name == "data.csv" else input_file.name
    figure.suptitle(f"Final-port intensity / PAX diagnostic — {title}", y=0.955, fontsize=15, fontweight="bold")
    colors = {"phi1": "tab:blue", "phi2": "tab:orange"}
    has_nd_metadata = "pd_pre_nd_equivalent_v" in rows[0]

    def normalize(values: np.ndarray) -> np.ndarray:
        span = float(np.max(values) - np.min(values))
        return (values - np.min(values)) / span if span > 0.0 else np.full_like(values, 0.5)
    for axis in ("phi1", "phi2"):
        group = sorted((row for row in rows if row["sweep_axis"] == axis), key=lambda row: float(row["sweep_rp_v"]))
        x = np.asarray([float(row["sweep_rp_v"]) for row in group])
        pd = np.asarray([float(row["pd_mean_v"]) for row in group])
        dop = np.asarray([float(row["dop"]) for row in group])
        u = np.unwrap(np.asarray([float(row["u"]) for row in group]))
        v = np.asarray([float(row["v"]) for row in group])
        color = colors[axis]
        pd_norm = np.asarray([float(row["pd_normalized"]) for row in group]) if "pd_normalized" in rows[0] else normalize(pd)
        correlation = float(np.corrcoef(pd_norm, dop)[0, 1]) if len(pd_norm) > 1 and np.std(pd_norm) > 0 and np.std(dop) > 0 else float("nan")
        if has_ptotal:
            ptotal = np.asarray([float(row["pax_ptotal"]) for row in group])
            ptotal_norm = np.asarray([float(row["pax_ptotal_normalized"]) for row in group]) if "pax_ptotal_normalized" in rows[0] else normalize(ptotal)
            ptotal_correlation = float(np.corrcoef(ptotal, dop)[0, 1]) if np.all(np.isfinite(ptotal)) and np.std(ptotal) > 0 and np.std(dop) > 0 else float("nan")
            port_correlation = float(np.corrcoef(pd_norm, ptotal_norm)[0, 1]) if np.std(pd_norm) > 0 and np.std(ptotal_norm) > 0 else float("nan")
            response_axis = axes[0, 0] if axis == "phi1" else axes[0, 1]
            response_axis.plot(x, pd_norm, color="tab:blue", linewidth=1.3, label="PD port")
            response_axis.plot(x, ptotal_norm, color="tab:red", linewidth=1.3, label="PAX port")
            response_axis.set(title=f"{axis}: normalized port responses (r={port_correlation:.3f})", xlabel="swept RP command (V)", ylabel="normalized instrument response")
            response_axis.set_ylim(-0.05, 1.05)
            axes[1, 0].scatter(pd_norm, dop, color=color, s=16, alpha=0.8, label=f"{axis}: r={correlation:.3f}")
            axes[1, 1].scatter(ptotal_norm, dop, color=color, s=16, alpha=0.8, label=f"{axis}: r={ptotal_correlation:.3f}")
            axes[2, 0].plot(x, u, color=color, linewidth=1.3, label=f"{axis} sweep")
            axes[2, 1].plot(x, v, color=color, linewidth=1.3, label=f"{axis} sweep")
        else:
            axes[0, 0].plot(x, pd_norm, color=color, linewidth=1.3, label=f"{axis} sweep")
            axes[0, 1].scatter(pd_norm, dop, color=color, s=16, alpha=0.8, label=f"{axis}: r={correlation:.3f}")
            axes[1, 0].plot(x, u, color=color, linewidth=1.3, label=f"{axis} sweep")
            axes[1, 1].plot(x, v, color=color, linewidth=1.3, label=f"{axis} sweep")
    if has_ptotal:
        axes[1, 0].axhspan(0.0, 1.0, color="tab:green", alpha=0.10, label="physical DOP range")
        axes[1, 0].set(title="Does raw DOP covary with normalized PD-port power?", xlabel="normalized PD response", ylabel="raw PAX DOP")
        axes[1, 1].axhspan(0.0, 1.0, color="tab:green", alpha=0.10, label="physical DOP range")
        axes[1, 1].set(title="Does raw DOP covary with normalized PAX-port power?", xlabel="normalized PAX ptotal", ylabel="raw PAX DOP")
        axes[2, 0].set(title="Azimuth response", xlabel="swept RP command (V)", ylabel="unwrapped u (rad)")
        axes[2, 1].set(title="Polar response", xlabel="swept RP command (V)", ylabel="v (rad)")
    else:
        axes[0, 0].set(title="Normalized final-port photodiode response", xlabel="swept RP command (V)", ylabel="normalized PD response")
        axes[0, 1].axhspan(0.0, 1.0, color="tab:green", alpha=0.10, label="physical DOP range")
        axes[0, 1].set(title="Does raw DOP covary with normalized PD power?", xlabel="normalized PD response", ylabel="raw PAX DOP")
        axes[1, 0].set(title="Azimuth response", xlabel="swept RP command (V)", ylabel="unwrapped u (rad)")
        axes[1, 1].set(title="Polar response", xlabel="swept RP command (V)", ylabel="v (rad)")
    for axis in axes.flat:
        axis.grid(alpha=0.22)
        axis.legend(fontsize=8)
    pdf.savefig(figure)
    plt.close(figure)


def add_phi2_path_balance_page(pdf: PdfPages, input_file: Path) -> None:
    """Show the three manual blocking conditions in detector-independent units."""
    rows = _rows(input_file)
    conditions = ("path_a_only", "path_b_only", "both_paths")
    labels = {"path_a_only": "path A only", "path_b_only": "path B only", "both_paths": "both paths"}
    colors = {"path_a_only": "tab:blue", "path_b_only": "tab:orange", "both_paths": "tab:green"}

    def normalize(values: np.ndarray) -> np.ndarray:
        span = float(np.max(values) - np.min(values))
        return (values - np.min(values)) / span if span > 0.0 else np.full_like(values, 0.5)

    figure, axes = plt.subplots(3, 1, figsize=(11.5, 8.5), sharex=True)
    figure.subplots_adjust(left=0.09, right=0.97, top=0.90, bottom=0.10, hspace=0.33)
    title = input_file.parent.name if input_file.name == "data.csv" else input_file.name
    figure.suptitle(f"Guided phi2 path-balance test — {title}", y=0.965, fontsize=15, fontweight="bold")
    for condition in conditions:
        group = sorted((row for row in rows if row["condition"] == condition), key=lambda row: float(row["sweep_rp_v"]))
        if not group:
            continue
        x = np.asarray([float(row["sweep_rp_v"]) for row in group])
        pd = normalize(np.asarray([float(row["pd_mean_v"]) for row in group]))
        ptotal = normalize(np.asarray([float(row["pax_ptotal"]) for row in group]))
        dop = np.asarray([float(row["dop"]) for row in group])
        color = colors[condition]
        label = labels[condition]
        axes[0].plot(x, pd, color=color, linestyle="-", linewidth=1.35, label=f"{label}: PD")
        axes[0].plot(x, ptotal, color=color, linestyle="--", linewidth=1.35, label=f"{label}: PAX ptotal")
        axes[1].plot(x, dop, color=color, linewidth=1.35, label=label)
        axes[2].plot(x, np.unwrap(np.asarray([float(row["u"]) for row in group])), color=color, linewidth=1.35, label=label)
    axes[0].set(title="Per-condition normalized port responses (solid PD; dashed PAX)", ylabel="normalized response")
    axes[1].axhspan(0.0, 1.0, color="tab:green", alpha=0.10, label="physical DOP range")
    axes[1].set(title="Raw PAX DOP", ylabel="raw DOP")
    axes[2].set(title="Phi2-driven azimuth response", xlabel="phi2 RP command (V)", ylabel="unwrapped u (rad)")
    for axis in axes:
        axis.grid(alpha=0.22)
        axis.legend(ncol=2, fontsize=8, loc="best")
    pdf.savefig(figure)
    plt.close(figure)


def add_pax_path_hold_page(pdf: PdfPages, input_file: Path) -> None:
    """Plot raw PAX telemetry while neither actuator is moving."""
    rows = _rows(input_file)
    conditions = ("path_a_only", "path_b_only", "both_paths")
    labels = {"path_a_only": "path A only", "path_b_only": "path B only", "both_paths": "both paths"}
    colors = {"path_a_only": "tab:blue", "path_b_only": "tab:orange", "both_paths": "tab:green"}
    figure, axes = plt.subplots(3, 1, figsize=(11.5, 8.5), sharex=True)
    figure.subplots_adjust(left=0.09, right=0.97, top=0.90, bottom=0.10, hspace=0.33)
    title = input_file.parent.name if input_file.name == "data.csv" else input_file.name
    figure.suptitle(f"Fixed-state PAX path diagnostic — {title}", y=0.965, fontsize=15, fontweight="bold")
    for condition in conditions:
        group = [row for row in rows if row["condition"] == condition]
        if not group:
            continue
        x = np.asarray([float(row["elapsed_s"]) for row in group])
        color, label = colors[condition], labels[condition]
        axes[0].plot(x, [float(row["dop"]) for row in group], color=color, linewidth=1.25, label=label)
        axes[1].plot(x, [float(row["pax_ptotal"]) for row in group], color=color, linewidth=1.25, label=f"{label}: PAX ptotal")
        axes[1].plot(x, [float(row["pd_mean_v"]) for row in group], color=color, linestyle="--", linewidth=1.25, label=f"{label}: PD")
        axes[2].plot(x, [float(row["pax_adc_min"]) for row in group], color=color, linewidth=1.1, label=f"{label}: ADC min")
        axes[2].plot(x, [float(row["pax_adc_max"]) for row in group], color=color, linestyle="--", linewidth=1.1, label=f"{label}: ADC max")
    axes[0].axhspan(0.0, 1.0, color="tab:green", alpha=0.10, label="physical DOP range")
    axes[0].set(title="Raw DOP while actuator commands are fixed at 0 V", ylabel="raw PAX DOP")
    axes[1].set(title="Concurrent, uncalibrated power telemetry", ylabel="native instrument units")
    axes[2].set(title="PAX primary-record ADC extrema", xlabel="elapsed time within condition (s)", ylabel="raw ADC units")
    for axis in axes:
        axis.grid(alpha=0.22)
        axis.legend(ncol=2, fontsize=8, loc="best")
    pdf.savefig(figure)
    plt.close(figure)


def add_stokes_phase_sweep_page(pdf: PdfPages, input_file: Path) -> None:
    rows = _rows(input_file)
    def values(key):
        return np.asarray([float(row[key]) for row in rows])
    figure, axes = plt.subplots(3, 2, figsize=(11.7, 8.3), constrained_layout=True)
    figure.suptitle("Power and Stokes versus phase-actuator drive", fontsize=16)
    panels = (
        ("Voltage reference", ("in1_reference_v", "out1_command_estimated_v"), "V", "elapsed_s"),
        ("PAX total power", ("pax_ptotal",), "W", "pax_received_s"),
        ("Polarization direction (unit norm)", ("s1", "s2", "s3"), "Stokes direction", "pax_received_s"),
        ("Degree of polarization", ("dop",), "DOP", "pax_received_s"),
        ("Ellipse angles", ("theta", "eta"), "rad", "pax_received_s"),
        ("Total-power-normalized Stokes", ("s1_over_s0", "s2_over_s0", "s3_over_s0"), "Si / S0", "pax_received_s"),
    )
    for ax, (title, fields, unit, time_key) in zip(axes.flat, panels):
        for field in fields:
            ax.plot(values(time_key), values(field), lw=.8, label=field)
        ax.set(title=title, xlabel="Host elapsed time (s)", ylabel=unit)
        ax.grid(alpha=.2)
        ax.legend(fontsize=7)
    pdf.savefig(figure)
    plt.close(figure)


def create_report(input_file: Path, output_file: Path, kind: str, axis: str | None = None) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(output_file) as pdf:
        if kind == "sweep":
            if axis not in {"phi1", "phi2"}:
                raise ValueError("An axis is required for a sweep report")
            add_axis_sweep_page(pdf, input_file, axis)
        elif kind == "bidirectional":
            add_bidirectional_page(pdf, input_file)
        elif kind == "diagnostic":
            add_diagnostic_page(pdf, input_file)
        elif kind == "intensity":
            add_intensity_diagnostic_page(pdf, input_file)
        elif kind == "phi2-path-test":
            add_phi2_path_balance_page(pdf, input_file)
        elif kind == "pax-path-hold":
            add_pax_path_hold_page(pdf, input_file)
        else:
            raise ValueError(f"Unsupported report kind: {kind}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_file", type=Path)
    parser.add_argument("--output", "-o", type=Path, required=True)
    parser.add_argument("--kind", choices=("sweep", "bidirectional", "diagnostic", "intensity", "phi2-path-test", "pax-path-hold"), required=True)
    parser.add_argument("--axis", choices=("phi1", "phi2"))
    args = parser.parse_args()
    create_report(args.input_file, args.output, args.kind, args.axis)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
