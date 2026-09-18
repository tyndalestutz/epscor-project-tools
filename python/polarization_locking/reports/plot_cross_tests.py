#!/usr/bin/env python3
"""Plot the measured bias dependence in phi1/phi2 cross-sweep CSV logs.

Each PDF page shows the measured Poincare coordinates versus swept RP
voltage, with one curve for every bias voltage present in that input file.

Example:
    python python/polarization_locking/reports/plot_cross_tests.py \
        experiments/phi1-cross_test-0.csv \
        experiments/phi2-cross_test-0.csv \
        --output experiments/cross-test-bias-report.pdf
"""
from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path

# The lab environment may not have a writable default Matplotlib cache.
os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-cross-tests")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


REQUIRED_COLUMNS = {"sweep_axis", "bias_axis", "bias_rp_v", "sweep_rp_v", "u", "v"}


def axis_label(axis: str) -> str:
    return {"phi1": "φ1", "phi2": "φ2"}.get(axis, axis)


def display_name(input_file: Path) -> str:
    """Use the run directory label when automated runs store the CSV as data.csv."""
    return input_file.parent.name if input_file.name == "data.csv" else input_file.name


def load_rows(input_file: Path) -> list[dict[str, str]]:
    with input_file.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = REQUIRED_COLUMNS.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{input_file} is missing columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{input_file} contains no cross-sweep data")
    return rows


def _linear_phase_fit(x: np.ndarray, angle: np.ndarray) -> tuple[float, float]:
    """Return one-period RP voltage and linear-fit R² for an unwrapped phase."""
    slope, intercept = np.polyfit(x, np.unwrap(angle), 1)
    predicted = slope * x + intercept
    total = float(np.sum((np.unwrap(angle) - np.mean(np.unwrap(angle))) ** 2))
    residual = float(np.sum((np.unwrap(angle) - predicted) ** 2))
    r_squared = 1.0 - residual / total if total > 0 else float("nan")
    return 2.0 * math.pi / abs(float(slope)), r_squared


def _cosine_phase_fit(x: np.ndarray, s1: np.ndarray) -> tuple[float, float, float]:
    """Fit S1=A cos(kV)+B sin(kV)+C and return Vlambda, R², amplitude."""
    nominal_period = float(x[-1] - x[0])
    if nominal_period <= 0:
        return float("nan"), float("nan"), float("nan")
    # A one-period scan does not uniquely constrain frequency at arbitrary
    # values. Search a conservative window around its commanded Vlambda.
    candidates = np.linspace(2 * math.pi / (1.45 * nominal_period), 2 * math.pi / (0.55 * nominal_period), 1801)
    best: tuple[float, float, np.ndarray] | None = None
    for frequency in candidates:
        design = np.column_stack((np.cos(frequency * x), np.sin(frequency * x), np.ones_like(x)))
        coefficients = np.linalg.lstsq(design, s1, rcond=None)[0]
        mse = float(np.mean((s1 - design @ coefficients) ** 2))
        if best is None or mse < best[0]:
            best = (mse, frequency, coefficients)
    assert best is not None
    mse, frequency, coefficients = best
    variance = float(np.var(s1))
    r_squared = 1.0 - mse / variance if variance > 0 else float("nan")
    amplitude = float(math.hypot(coefficients[0], coefficients[1]))
    return 2 * math.pi / frequency, r_squared, amplitude


def _weighted_azimuth_spread(u: np.ndarray, v: np.ndarray) -> float:
    """Circular u spread with S1-pole samples downweighted as unobservable."""
    weights = np.sin(v)
    resultant = abs(np.sum(weights * np.exp(1j * u)) / np.sum(weights))
    return math.sqrt(max(0.0, -2.0 * math.log(max(float(resultant), 1e-12))))


def add_coupling_page(pdf: PdfPages, input_file: Path, png_file: Path | None = None) -> None:
    """Visualize Vlambda stability and off-axis response for each bias slice."""
    rows = load_rows(input_file)
    sweep_axis = rows[0]["sweep_axis"]
    bias_axis = rows[0]["bias_axis"]
    slices: dict[float, list[dict[str, str]]] = {}
    for row in rows:
        slices.setdefault(float(row["bias_rp_v"]), []).append(row)

    biases: list[float] = []
    inferred_periods: list[float] = []
    fit_quality: list[float] = []
    off_axis: list[float] = []
    median_dop: list[float] = []
    signal_amplitude: list[float] = []
    for bias, slice_rows in sorted(slices.items()):
        slice_rows.sort(key=lambda row: float(row["sweep_rp_v"]))
        x = np.asarray([float(row["sweep_rp_v"]) for row in slice_rows])
        u = np.asarray([float(row["u"]) for row in slice_rows])
        v = np.asarray([float(row["v"]) for row in slice_rows])
        s1 = np.asarray([float(row["s1"]) for row in slice_rows])
        dop = np.asarray([float(row["dop"]) for row in slice_rows])
        if sweep_axis == "phi1":
            period, r_squared = _linear_phase_fit(x, u)
            # Ideally phi1 leaves the polar coordinate v unchanged.
            cross_excursion = float(np.quantile(v, 0.95) - np.quantile(v, 0.05))
            amplitude = float("nan")
        else:
            # v folds at both S1 poles; S1=cos(phi2) is the single-valued
            # quantity appropriate for extracting the phi2 phase period.
            period, r_squared, amplitude = _cosine_phase_fit(x, s1)
            # u is undefined at the poles traversed by phi2, so use a
            # sin(v)-weighted circular spread rather than an unwrap range.
            cross_excursion = _weighted_azimuth_spread(u, v)
        biases.append(bias)
        inferred_periods.append(period)
        fit_quality.append(r_squared)
        off_axis.append(cross_excursion)
        median_dop.append(float(np.median(dop)))
        signal_amplitude.append(amplitude)

    biases_array = np.asarray(biases)
    nominal_period = float(max(float(row["sweep_rp_v"]) for row in rows) - min(float(row["sweep_rp_v"]) for row in rows))
    figure, axes = plt.subplots(2, 2, figsize=(12.5, 8.5))
    figure.subplots_adjust(left=0.08, right=0.97, top=0.86, bottom=0.22, hspace=0.38, wspace=0.28)
    figure.suptitle(
        f"Cross-coupling summary — {display_name(input_file)} — swept {axis_label(sweep_axis)}, biased {axis_label(bias_axis)}",
        y=0.955,
        fontsize=14,
        fontweight="bold",
    )

    axes[0, 0].plot(biases_array, inferred_periods, marker="o", color="tab:blue", label="inferred Vλ (RP V)")
    axes[0, 0].axhline(nominal_period, color="black", linestyle="--", linewidth=1.0, label=f"commanded span = {nominal_period:.3f} V")
    axes[0, 0].set(title="Principal-axis Vλ versus fixed-axis bias", ylabel="inferred Vλ (RP V)")

    axes[0, 1].plot(biases_array, fit_quality, marker="o", color="tab:green", label="principal response R²")
    axes[0, 1].axhline(0.9, color="black", linestyle="--", linewidth=1.0, alpha=0.6, label="0.90 guide")
    axes[0, 1].set(title="Vλ-fit quality", ylabel="R²", ylim=(-0.05, 1.05))

    if sweep_axis == "phi1":
        off_axis_label = "v 5–95% excursion (rad)"
        off_axis_title = "Off-axis polar excursion (ideal: 0)"
    else:
        off_axis_label = "weighted circular u spread (rad)"
        off_axis_title = "Off-axis azimuth spread (poles downweighted)"
    axes[1, 0].plot(biases_array, off_axis, marker="o", color="tab:orange")
    axes[1, 0].set(title=off_axis_title, ylabel=off_axis_label, xlabel=f"fixed {axis_label(bias_axis)} RP bias (V)")

    axes[1, 1].plot(biases_array, median_dop, marker="o", color="tab:purple", label="median raw PAX DOP")
    axes[1, 1].axhspan(0.0, 1.0, color="tab:green", alpha=0.10, label="physical DOP range")
    if sweep_axis == "phi2":
        twin = axes[1, 1].twinx()
        twin.plot(biases_array, signal_amplitude, marker="s", color="tab:gray", alpha=0.8, label="S1 cosine amplitude")
        twin.set_ylabel("S1 fit amplitude")
    axes[1, 1].set(title="Measurement-quality context", ylabel="reported DOP", xlabel=f"fixed {axis_label(bias_axis)} RP bias (V)")

    for axis in axes.flat:
        axis.grid(alpha=0.22)
    handles, labels = [], []
    for legend_axis in (axes[0, 0], axes[0, 1], axes[1, 1]):
        axis_handles, axis_labels = legend_axis.get_legend_handles_labels()
        handles.extend(axis_handles)
        labels.extend(axis_labels)
    if sweep_axis == "phi2":
        axis_handles, axis_labels = twin.get_legend_handles_labels()
        handles.extend(axis_handles)
        labels.extend(axis_labels)
    figure.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.025), ncol=min(3, len(handles)), fontsize=8)
    if png_file is not None:
        png_file.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(png_file, dpi=180)
    pdf.savefig(figure)
    plt.close(figure)


def add_report(pdf: PdfPages, input_file: Path, png_directory: Path | None = None) -> None:
    """Add both response curves and the coupling/Vlambda summary."""
    add_page(pdf, input_file, png_file=png_directory / "response.png" if png_directory else None)
    add_coupling_page(pdf, input_file, png_file=png_directory / "coupling-summary.png" if png_directory else None)


def add_page(pdf: PdfPages, input_file: Path, png_file: Path | None = None) -> None:
    rows = load_rows(input_file)
    sweep_axes = {row["sweep_axis"] for row in rows}
    bias_axes = {row["bias_axis"] for row in rows}
    if len(sweep_axes) != 1 or len(bias_axes) != 1:
        raise ValueError(f"{input_file} must contain one sweep-axis/bias-axis pairing")

    sweep_axis = next(iter(sweep_axes))
    bias_axis = next(iter(bias_axes))
    sweep_label = axis_label(sweep_axis)
    bias_label = axis_label(bias_axis)
    bias_values = sorted({float(row["bias_rp_v"]) for row in rows})

    figure, axes = plt.subplots(1, 2, figsize=(12.5, 6.0), sharex=True)
    # All bias curves share labels, so use one figure-level legend underneath
    # the panels rather than obscuring either measured response.
    figure.subplots_adjust(left=0.075, right=0.975, top=0.82, bottom=0.35, wspace=0.22)
    figure.suptitle(
        f"Cross-sweep bias relation — {display_name(input_file)}",
        y=0.955,
        fontsize=15,
        fontweight="bold",
    )
    figure.text(
        0.5,
        0.875,
        f"swept {sweep_label}; fixed {bias_label} bias",
        ha="center",
        fontsize=10,
        color="#4b5563",
    )

    colors = plt.get_cmap("viridis")(np.linspace(0.05, 0.95, len(bias_values)))
    for index, bias in enumerate(bias_values):
        bias_rows = [row for row in rows if float(row["bias_rp_v"]) == bias]
        bias_rows.sort(key=lambda row: float(row["sweep_rp_v"]))
        sweep_v = np.asarray([float(row["sweep_rp_v"]) for row in bias_rows])
        u = np.asarray([float(row["u"]) for row in bias_rows])
        v = np.asarray([float(row["v"]) for row in bias_rows])
        label = f"{bias_label} bias = {bias:g} V"
        color = colors[index]

        # u is circular. Unwrapping each recorded bias slice avoids drawing
        # false 2*pi transitions at the [-pi, pi) coordinate boundary.
        axes[0].plot(sweep_v, np.unwrap(u), color=color, linewidth=1.4, label=label)
        axes[1].plot(sweep_v, v, color=color, linewidth=1.4, label=label)

    axes[0].set(title="Azimuthal response (unwrapped)", ylabel="u (rad)")
    axes[1].set(title="Polar response", ylabel="v (rad)")
    for axis in axes:
        axis.set_xlabel(f"{sweep_label} RP command (V)")
        axis.grid(alpha=0.22)
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(
        handles,
        labels,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.035),
        ncol=min(4, len(handles)),
        fontsize=8,
        title="Recorded fixed bias",
        title_fontsize=8,
    )

    if png_file is not None:
        png_file.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(png_file, dpi=180)
    pdf.savefig(figure)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_files", nargs="+", type=Path, help="cross-test CSV file(s)")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("cross-test-bias-report.pdf"),
        help="output PDF path",
    )
    parser.add_argument(
        "--png-dir",
        type=Path,
        help="write response.png and coupling-summary.png beside the selected experiment data",
    )
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(args.output) as pdf:
        for input_file in args.input_files:
            # With several inputs, make one self-contained PNG folder per CSV.
            png_directory = args.png_dir / input_file.parent.name if args.png_dir and len(args.input_files) > 1 else args.png_dir
            add_report(pdf, input_file, png_directory=png_directory)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
