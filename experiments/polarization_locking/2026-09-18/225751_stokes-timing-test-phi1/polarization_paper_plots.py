#!/usr/bin/env python3
"""Generate paper-ready polarization-transfer figures from static/dynamic CSV logs.

The script is intentionally analysis-only: it never talks to hardware and never
modifies the source CSV files.  The measured Red Pitaya IN1 loopback voltage is
used as the electrical ground-truth coordinate.

Typical use
-----------
python polarization_paper_plots.py \
    --static static.csv \
    --dynamic dynamic.csv \
    --metadata metadata.json \
    --output-dir paper_plots

Outputs (PNG + PDF by default)
------------------------------
01_static_stokes_transfer
02_poincare_phase_plane
03_static_phase_calibration
04_model_deviation
05_dynamic_repeatability
06_electrical_ground_truth
analysis_summary.txt
fit_summary.csv

Dependencies: numpy, pandas, matplotlib
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


TWO_PI = 2.0 * np.pi


@dataclass
class LinearFit:
    slope: float
    intercept: float
    r2: float
    rms_rad: float

    @property
    def v_pi(self) -> float:
        return np.pi / abs(self.slope)

    @property
    def v_2pi(self) -> float:
        return TWO_PI / abs(self.slope)


@dataclass
class DynamicSegment:
    index: int
    direction: str
    rows: np.ndarray
    voltage: np.ndarray
    phase: np.ndarray
    fit: LinearFit


# -----------------------------------------------------------------------------
# Plot style
# -----------------------------------------------------------------------------

def set_paper_style() -> None:
    """Apply a restrained journal-style Matplotlib theme."""
    mpl.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.dpi": 400,
            "font.family": "serif",
            "font.serif": ["STIX Two Text", "Times New Roman", "DejaVu Serif"],
            "mathtext.fontset": "stix",
            "font.size": 9.5,
            "axes.titlesize": 10.5,
            "axes.labelsize": 10,
            "xtick.labelsize": 8.5,
            "ytick.labelsize": 8.5,
            "legend.fontsize": 8.5,
            "axes.linewidth": 0.8,
            "lines.linewidth": 1.35,
            "lines.markersize": 4.0,
            "legend.frameon": False,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "xtick.direction": "out",
            "ytick.direction": "out",
            "xtick.major.width": 0.8,
            "ytick.major.width": 0.8,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def panel_label(ax: plt.Axes, label: str) -> None:
    ax.text(
        0.012,
        0.975,
        label,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontweight="bold",
        bbox={"boxstyle": "square,pad=0.08", "facecolor": "white", "alpha": 0.82, "edgecolor": "none"},
        zorder=20,
    )


def save_figure(fig: plt.Figure, outdir: Path, stem: str, formats: Iterable[str], dpi: int) -> None:
    for fmt in formats:
        path = outdir / f"{stem}.{fmt}"
        kwargs = {"bbox_inches": "tight"}
        if fmt.lower() == "png":
            kwargs["dpi"] = dpi
        fig.savefig(path, **kwargs)


# -----------------------------------------------------------------------------
# Data preparation
# -----------------------------------------------------------------------------

def require_columns(df: pd.DataFrame, required: Iterable[str], label: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"{label} CSV is missing required columns: {', '.join(missing)}")


def finite_xy(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    m = np.isfinite(x) & np.isfinite(y)
    return x[m], y[m]


def linear_fit(x: np.ndarray, y: np.ndarray) -> LinearFit:
    x, y = finite_xy(np.asarray(x, float), np.asarray(y, float))
    if len(x) < 3:
        raise ValueError("Need at least three finite points for a linear fit")
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(np.sum((y - pred) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    rms = float(np.sqrt(np.mean((y - pred) ** 2)))
    return LinearFit(float(slope), float(intercept), r2, rms)


def prepare_static(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, LinearFit]]:
    require_columns(
        df,
        ["sweep_direction", "rp_in1_voltage_v", "s1", "s2", "s3", "sample"],
        "static",
    )
    out = df.copy().sort_values("sample").reset_index(drop=True)
    out["stokes_norm_calc"] = np.sqrt(out.s1**2 + out.s2**2 + out.s3**2)
    out["s23_radius_calc"] = np.sqrt(out.s2**2 + out.s3**2)
    out["phase_wrapped_calc"] = np.arctan2(-out.s3, out.s2)
    ratio = np.divide(
        out.s1.to_numpy(float),
        out.stokes_norm_calc.to_numpy(float),
        out=np.zeros(len(out), dtype=float),
        where=out.stokes_norm_calc.to_numpy(float) > 0,
    )
    out["great_circle_latitude_deg"] = np.degrees(np.arcsin(np.clip(ratio, -1.0, 1.0)))

    fits: dict[str, LinearFit] = {}
    for direction in ("rising", "falling"):
        idx = out.index[out.sweep_direction.astype(str).str.lower() == direction].to_numpy()
        if len(idx) == 0:
            continue
        phase = np.unwrap(out.loc[idx, "phase_wrapped_calc"].to_numpy(float))
        out.loc[idx, "phase_unwrapped_sweep"] = phase
        fit = linear_fit(out.loc[idx, "rp_in1_voltage_v"].to_numpy(float), phase)
        fits[direction] = fit
        out.loc[idx, "phase_fit"] = (
            fit.slope * out.loc[idx, "rp_in1_voltage_v"].to_numpy(float) + fit.intercept
        )
        out.loc[idx, "phase_residual_deg"] = np.degrees(
            out.loc[idx, "phase_unwrapped_sweep"] - out.loc[idx, "phase_fit"]
        )

    # Shift the falling branch by an integer multiple of 2pi so it visually
    # overlays the rising branch. This changes only the plotting coordinate,
    # never the fit slope or residuals.
    out["phase_unwrapped_aligned"] = out.get("phase_unwrapped_sweep", np.nan)
    if "rising" in fits and "falling" in fits:
        rise = out[out.sweep_direction.astype(str).str.lower() == "rising"]
        fall = out[out.sweep_direction.astype(str).str.lower() == "falling"]
        v_ref = float(np.nanmedian(out.rp_in1_voltage_v))
        rise_ref = fits["rising"].slope * v_ref + fits["rising"].intercept
        fall_ref = fits["falling"].slope * v_ref + fits["falling"].intercept
        k = int(np.round((rise_ref - fall_ref) / TWO_PI))
        out.loc[fall.index, "phase_unwrapped_aligned"] = (
            fall["phase_unwrapped_sweep"].to_numpy(float) + k * TWO_PI
        )

    return out, fits


def interpolate_dynamic_voltage(df: pd.DataFrame) -> pd.DataFrame:
    """Interpolate measured IN1 voltage to the PAX midpoint timestamp.

    IN1 and PAX are sampled sequentially, not simultaneously.  The interpolation
    uses only the measured loopback trace; the nominal 0.5 Hz software clock is
    not used as a voltage coordinate.
    """
    require_columns(
        df,
        [
            "rp_in1_sample_monotonic_s",
            "rp_in1_voltage_v",
            "pax_sample_mid_monotonic_s",
            "s1",
            "s2",
            "s3",
            "sample",
        ],
        "dynamic",
    )
    out = df.copy().sort_values("sample").reset_index(drop=True)
    t_rp = out.rp_in1_sample_monotonic_s.to_numpy(float)
    v_rp = out.rp_in1_voltage_v.to_numpy(float)
    t_pax = out.pax_sample_mid_monotonic_s.to_numpy(float)

    # Drop only samples whose PAX midpoint lies outside the measured IN1 time span;
    # extrapolating at a turning point would bias the phase-voltage relation.
    valid = np.isfinite(t_pax) & (t_pax >= np.nanmin(t_rp)) & (t_pax <= np.nanmax(t_rp))
    v_at_pax = np.full(len(out), np.nan)
    v_at_pax[valid] = np.interp(t_pax[valid], t_rp, v_rp)
    out["rp_voltage_at_pax_v"] = v_at_pax
    out["phase_wrapped_calc"] = np.arctan2(-out.s3, out.s2)
    out["stokes_norm_calc"] = np.sqrt(out.s1**2 + out.s2**2 + out.s3**2)
    ratio = np.divide(
        out.s1.to_numpy(float),
        out.stokes_norm_calc.to_numpy(float),
        out=np.zeros(len(out), dtype=float),
        where=out.stokes_norm_calc.to_numpy(float) > 0,
    )
    out["great_circle_latitude_deg"] = np.degrees(np.arcsin(np.clip(ratio, -1.0, 1.0)))
    return out


def upward_mean_crossings(t: np.ndarray, v: np.ndarray) -> np.ndarray:
    m = np.isfinite(t) & np.isfinite(v)
    t = np.asarray(t[m], float)
    v = np.asarray(v[m], float)
    if len(t) < 4:
        return np.array([], dtype=float)
    center = 0.5 * (np.nanpercentile(v, 5) + np.nanpercentile(v, 95))
    y = v - center
    hits: list[float] = []
    for i in range(len(y) - 1):
        if y[i] < 0.0 <= y[i + 1] and v[i + 1] > v[i]:
            dy = y[i + 1] - y[i]
            frac = 0.0 if dy == 0 else -y[i] / dy
            hits.append(float(t[i] + frac * (t[i + 1] - t[i])))
    return np.asarray(hits, float)


def estimate_measured_frequency(df: pd.DataFrame) -> tuple[float, float]:
    crossings = upward_mean_crossings(
        df.rp_in1_sample_monotonic_s.to_numpy(float),
        df.rp_in1_voltage_v.to_numpy(float),
    )
    if len(crossings) < 2:
        raise ValueError("Could not estimate dynamic frequency from IN1 mean crossings")
    # Regress crossing time against integer cycle number.  This averages timing
    # noise over the full acquisition instead of relying on one period.
    cycle_number = np.arange(len(crossings), dtype=float)
    period, t0 = np.polyfit(cycle_number, crossings, 1)
    return 1.0 / float(period), float(t0)


def segment_dynamic(df: pd.DataFrame, min_points: int = 4) -> tuple[list[DynamicSegment], float]:
    """Segment dynamic data into monotonic half-cycles using measured IN1 timing."""
    f_meas, t_cross = estimate_measured_frequency(df)
    t = df.pax_sample_mid_monotonic_s.to_numpy(float)
    phase_elec = TWO_PI * f_meas * (t - t_cross)
    half_index = np.floor((phase_elec + np.pi / 2.0) / np.pi).astype(int)

    segments: list[DynamicSegment] = []
    for h in np.unique(half_index):
        rows = np.where(half_index == h)[0]
        if len(rows) < min_points:
            continue
        v = df.rp_voltage_at_pax_v.to_numpy(float)[rows]
        p = df.phase_wrapped_calc.to_numpy(float)[rows]
        good = np.isfinite(v) & np.isfinite(p)
        rows = rows[good]
        v = v[good]
        p = p[good]
        if len(rows) < min_points:
            continue
        # Reject partial edge segments and any accidental tiny segments.
        if np.ptp(v) < 0.45 * np.nanpercentile(df.rp_voltage_at_pax_v, 95):
            continue
        p_unwrapped = np.unwrap(p)
        fit = linear_fit(v, p_unwrapped)
        direction = "rising" if (h % 2 == 0) else "falling"
        segments.append(DynamicSegment(int(h), direction, rows, v, p_unwrapped, fit))
    return segments, f_meas


def align_dynamic_segments(
    segments: list[DynamicSegment], static_fits: dict[str, LinearFit], v_ref: float
) -> list[DynamicSegment]:
    """Shift each segment by integer 2pi for visual comparison only."""
    if not static_fits:
        return segments

    # Align static falling branch to rising branch first and use their average
    # as a modulo-2pi reference line.
    if "rising" in static_fits:
        ref_fit = static_fits["rising"]
    else:
        ref_fit = next(iter(static_fits.values()))

    aligned: list[DynamicSegment] = []
    for seg in segments:
        measured_ref = seg.fit.slope * v_ref + seg.fit.intercept
        target_ref = ref_fit.slope * v_ref + ref_fit.intercept
        k = int(np.round((target_ref - measured_ref) / TWO_PI))
        shifted = seg.phase + k * TWO_PI
        shifted_fit = LinearFit(
            seg.fit.slope,
            seg.fit.intercept + k * TWO_PI,
            seg.fit.r2,
            seg.fit.rms_rad,
        )
        aligned.append(DynamicSegment(seg.index, seg.direction, seg.rows, seg.voltage, shifted, shifted_fit))
    return aligned


# -----------------------------------------------------------------------------
# Figure generation
# -----------------------------------------------------------------------------

def direction_colors() -> dict[str, str]:
    colors = mpl.rcParams["axes.prop_cycle"].by_key().get("color", ["C0", "C1"])
    return {"rising": colors[0], "falling": colors[1 if len(colors) > 1 else 0]}


def plot_static_stokes(static: pd.DataFrame) -> plt.Figure:
    fig, axes = plt.subplots(3, 1, figsize=(7.1, 7.0), sharex=True, constrained_layout=True)
    colors = direction_colors()
    directions = (("rising", "-", "o"), ("falling", "--", "s"))
    for ax, component in zip(axes, ("s1", "s2", "s3")):
        for direction, linestyle, marker in directions:
            q = static[static.sweep_direction.astype(str).str.lower() == direction].copy()
            if q.empty:
                continue
            color = colors[direction]
            ax.scatter(q.rp_in1_voltage_v, q[component], s=8, alpha=0.22, color=color)
            # Average only repeated measurements at the same commanded setpoint;
            # retain measured IN1 voltage as the x coordinate.
            group = q.groupby("sweep_step_index", sort=False).agg(
                voltage=("rp_in1_voltage_v", "mean"),
                value=(component, "mean"),
            )
            ax.plot(
                group.voltage,
                group.value,
                linestyle=linestyle,
                marker=marker,
                markevery=max(1, len(group) // 12),
                color=color,
                label=direction.capitalize(),
            )
        ax.set_ylabel(rf"${component.upper()}$")
        ax.set_ylim(-1.08, 1.08)
        ax.axhline(0.0, linewidth=0.7, alpha=0.35)
    axes[0].legend(ncol=2, loc="best")
    axes[-1].set_xlabel("Measured Red Pitaya OUT1 voltage (V)")
    axes[0].set_title("Static polarization transfer versus measured drive voltage")
    panel_label(axes[0], "(a)")
    panel_label(axes[1], "(b)")
    panel_label(axes[2], "(c)")
    return fig

def plot_phase_plane(static: pd.DataFrame) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(5.3, 4.7), constrained_layout=True)
    ang = np.linspace(0.0, TWO_PI, 720)
    ax.plot(np.cos(ang), np.sin(ang), linestyle="--", linewidth=1.0, alpha=0.65, label="Ideal $S_1=0$ great circle")
    sc = ax.scatter(
        static.s2,
        static.s3,
        c=static.rp_in1_voltage_v,
        s=18,
        alpha=0.85,
        cmap="viridis",
        edgecolors="none",
    )
    cbar = fig.colorbar(sc, ax=ax, pad=0.02)
    cbar.set_label("Measured OUT1 voltage (V)")
    ax.set_xlabel(r"$S_2$")
    ax.set_ylabel(r"$S_3$")
    ax.set_xlim(-1.08, 1.08)
    ax.set_ylim(-1.08, 1.08)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(r"Measured trajectory in the $S_2$-$S_3$ phase plane")
    ax.legend(loc="lower left")
    return fig


def plot_static_phase(static: pd.DataFrame, fits: dict[str, LinearFit]) -> plt.Figure:
    fig, ax = plt.subplots(figsize=(6.7, 4.5), constrained_layout=True)
    styles = {"rising": ("o", "-"), "falling": ("s", "--")}
    colors = direction_colors()
    fit_lines = []
    for direction in ("rising", "falling"):
        if direction not in fits:
            continue
        q = static[static.sweep_direction.astype(str).str.lower() == direction]
        marker, linestyle = styles[direction]
        y = q.phase_unwrapped_aligned.to_numpy(float)
        v = q.rp_in1_voltage_v.to_numpy(float)
        ax.scatter(v, y, s=15, alpha=0.45, marker=marker, color=colors[direction], label=f"{direction.capitalize()} measurements")

        # Align fitted line by the same integer 2pi shift used for the displayed data.
        fit = fits[direction]
        raw_fit_at_mid = fit.slope * np.nanmedian(v) + fit.intercept
        shown_at_mid = np.nanmedian(y)
        k = int(np.round((shown_at_mid - raw_fit_at_mid) / TWO_PI))
        intercept = fit.intercept + k * TWO_PI
        grid = np.linspace(np.nanmin(v), np.nanmax(v), 300)
        ax.plot(grid, fit.slope * grid + intercept, linestyle=linestyle, color=colors[direction])
        fit_lines.append(
            f"{direction.capitalize()}: $V_{{2\\pi}}={fit.v_2pi:.3f}$ V, "
            f"$R^2={fit.r2:.4f}$, RMS={np.degrees(fit.rms_rad):.1f}°"
        )

    ax.set_xlabel("Measured Red Pitaya OUT1 voltage (V)")
    ax.set_ylabel(r"Sweep-wise unwrapped $\phi_{\rm Stokes}$ (rad)")
    ax.set_title("Static voltage-to-polarization phase calibration")
    ax.legend(loc="best")
    if fit_lines:
        ax.text(
            0.02,
            0.03,
            "\n".join(fit_lines),
            transform=ax.transAxes,
            ha="left",
            va="bottom",
            fontsize=8.3,
            bbox={"boxstyle": "round,pad=0.3", "facecolor": "white", "alpha": 0.8, "edgecolor": "0.75"},
        )
    return fig


def plot_model_deviation(static: pd.DataFrame, fits: dict[str, LinearFit]) -> plt.Figure:
    fig, axes = plt.subplots(2, 1, figsize=(7.1, 5.6), sharex=True, constrained_layout=True)
    styles = {"rising": ("o", "-"), "falling": ("s", "--")}
    colors = direction_colors()
    for direction in ("rising", "falling"):
        q = static[static.sweep_direction.astype(str).str.lower() == direction]
        if q.empty:
            continue
        marker, linestyle = styles[direction]
        axes[0].plot(
            q.rp_in1_voltage_v,
            q.great_circle_latitude_deg,
            linestyle=linestyle,
            marker=marker,
            markevery=max(1, len(q) // 14),
            color=colors[direction],
            label=direction.capitalize(),
        )
        axes[1].scatter(
            q.rp_in1_voltage_v,
            q.phase_residual_deg,
            s=14,
            alpha=0.55,
            marker=marker,
            color=colors[direction],
            label=direction.capitalize(),
        )

    axes[0].axhline(0.0, linewidth=0.8, alpha=0.45)
    axes[1].axhline(0.0, linewidth=0.8, alpha=0.45)
    axes[0].set_ylabel(r"Great-circle latitude $\sin^{-1}(S_1/|\mathbf{S}|)$ (deg)")
    axes[1].set_ylabel("Phase residual (deg)")
    axes[1].set_xlabel("Measured Red Pitaya OUT1 voltage (V)")
    axes[0].set_title(r"Deviation from the ideal $E_3$ polarization trajectory")
    axes[0].legend(ncol=2, loc="best")
    panel_label(axes[0], "(a)")
    panel_label(axes[1], "(b)")
    return fig


def plot_dynamic_repeatability(
    dynamic: pd.DataFrame,
    segments: list[DynamicSegment],
    static: pd.DataFrame,
    static_fits: dict[str, LinearFit],
    measured_frequency: float,
) -> plt.Figure:
    fig, axes = plt.subplots(2, 1, figsize=(7.1, 6.2), sharex=True, constrained_layout=True)
    ax, axr = axes
    colors = direction_colors()

    for seg in segments:
        linestyle = "-" if seg.direction == "rising" else "--"
        ax.plot(seg.voltage, seg.phase, linestyle=linestyle, linewidth=0.9, alpha=0.32, color=colors[seg.direction])
        residual = np.degrees(seg.phase - (seg.fit.slope * seg.voltage + seg.fit.intercept))
        axr.plot(seg.voltage, residual, linestyle=linestyle, linewidth=0.8, alpha=0.28, color=colors[seg.direction])

    # Static fits act as a compact reference without obscuring the dynamic traces.
    if "rising" in static_fits:
        ref = static_fits["rising"]
        vmin = float(np.nanpercentile(dynamic.rp_voltage_at_pax_v, 2))
        vmax = float(np.nanpercentile(dynamic.rp_voltage_at_pax_v, 98))
        vg = np.linspace(vmin, vmax, 300)
        ax.plot(vg, ref.slope * vg + ref.intercept, linewidth=2.0, color=colors["rising"], label="Static rising fit")

    ax.set_ylabel(r"Aligned $\phi_{\rm Stokes}$ (rad)")
    ax.set_title(f"Dynamic polarization-transfer repeatability (measured electrical frequency {measured_frequency:.4f} Hz)")
    ax.legend(loc="best")
    axr.axhline(0.0, linewidth=0.8, alpha=0.45)
    axr.set_ylabel("Per-sweep linear-fit residual (deg)")
    axr.set_xlabel("Measured OUT1 voltage interpolated to PAX time (V)")
    panel_label(ax, "(a)")
    panel_label(axr, "(b)")
    return fig


def plot_electrical_ground_truth(static: pd.DataFrame, dynamic: pd.DataFrame, measured_frequency: float) -> plt.Figure:
    fig, axes = plt.subplots(1, 2, figsize=(7.1, 3.35), constrained_layout=True)

    # Static voltage linearity.
    x = static.commanded_rp_voltage_v.to_numpy(float)
    y = static.rp_in1_voltage_v.to_numpy(float)
    fit = linear_fit(x, y)
    grid = np.linspace(np.nanmin(x), np.nanmax(x), 300)
    axes[0].scatter(x, y, s=10, alpha=0.45)
    axes[0].plot(grid, grid, linestyle="--", linewidth=1.0, label="Ideal $V_{meas}=V_{cmd}$")
    axes[0].plot(grid, fit.slope * grid + fit.intercept, linewidth=1.4, label="Measured linear fit")
    axes[0].set_xlabel("Commanded OUT1 voltage (V)")
    axes[0].set_ylabel("Measured IN1 loopback voltage (V)")
    axes[0].set_title("Static electrical transfer")
    axes[0].legend(loc="best")
    axes[0].text(
        0.04,
        0.88,
        f"slope={fit.slope:.5f}\nintercept={1e3*fit.intercept:.2f} mV\n$R^2$={fit.r2:.6f}",
        transform=axes[0].transAxes,
        va="top",
        fontsize=8.2,
    )

    # Dynamic measured waveform versus software reconstruction, showing the clock mismatch.
    t0 = float(dynamic.pax_sample_mid_monotonic_s.iloc[0])
    t = dynamic.pax_sample_mid_monotonic_s.to_numpy(float) - t0
    axes[1].plot(t, dynamic.rp_voltage_at_pax_v, label="Measured IN1 (interpolated)")
    if "reconstructed_command_voltage_v" in dynamic.columns:
        axes[1].plot(t, dynamic.reconstructed_command_voltage_v, linestyle="--", label="0.5 Hz software reconstruction")
    axes[1].set_xlim(0, min(12.0, np.nanmax(t)))
    axes[1].set_xlabel("Elapsed time (s)")
    axes[1].set_ylabel("OUT1 voltage (V)")
    axes[1].set_title(f"Dynamic electrical ground truth: $f_{{meas}}={measured_frequency:.4f}$ Hz")
    axes[1].legend(loc="best")
    panel_label(axes[0], "(a)")
    panel_label(axes[1], "(b)")
    return fig


# -----------------------------------------------------------------------------
# Summary output
# -----------------------------------------------------------------------------

def write_summaries(
    outdir: Path,
    static: pd.DataFrame,
    static_fits: dict[str, LinearFit],
    dynamic: pd.DataFrame,
    segments: list[DynamicSegment],
    measured_frequency: float,
    metadata: dict | None,
) -> None:
    rows = []
    for direction, fit in static_fits.items():
        rows.append(
            {
                "dataset": "static",
                "direction": direction,
                "slope_rad_per_v": fit.slope,
                "intercept_rad": fit.intercept,
                "r2": fit.r2,
                "rms_phase_residual_deg": np.degrees(fit.rms_rad),
                "v_pi_v": fit.v_pi,
                "v_2pi_v": fit.v_2pi,
            }
        )
    for seg in segments:
        rows.append(
            {
                "dataset": "dynamic",
                "direction": seg.direction,
                "segment": seg.index,
                "slope_rad_per_v": seg.fit.slope,
                "intercept_rad": seg.fit.intercept,
                "r2": seg.fit.r2,
                "rms_phase_residual_deg": np.degrees(seg.fit.rms_rad),
                "v_pi_v": seg.fit.v_pi,
                "v_2pi_v": seg.fit.v_2pi,
            }
        )
    pd.DataFrame(rows).to_csv(outdir / "fit_summary.csv", index=False)

    static_lat = static.great_circle_latitude_deg.to_numpy(float)
    dyn_lat = dynamic.great_circle_latitude_deg.to_numpy(float)
    dyn_v2pi = np.array([seg.fit.v_2pi for seg in segments], float)
    dyn_r2 = np.array([seg.fit.r2 for seg in segments], float)

    lines = [
        "Polarization-transfer analysis summary",
        "=====================================",
        "",
        f"Dynamic electrical frequency from measured IN1 crossings: {measured_frequency:.6f} Hz",
        f"Usable dynamic monotonic half-sweeps: {len(segments)}",
        f"Dynamic mean V_2pi: {np.nanmean(dyn_v2pi):.6f} V" if len(dyn_v2pi) else "Dynamic mean V_2pi: n/a",
        f"Dynamic std V_2pi: {np.nanstd(dyn_v2pi, ddof=1):.6f} V" if len(dyn_v2pi) > 1 else "Dynamic std V_2pi: n/a",
        f"Dynamic mean per-sweep R^2: {np.nanmean(dyn_r2):.6f}" if len(dyn_r2) else "Dynamic mean per-sweep R^2: n/a",
        "",
        "Static phase calibration:",
    ]
    for direction, fit in static_fits.items():
        lines.extend(
            [
                f"  {direction}:",
                f"    slope = {fit.slope:.6f} rad/V",
                f"    V_pi = {fit.v_pi:.6f} V",
                f"    V_2pi = {fit.v_2pi:.6f} V",
                f"    R^2 = {fit.r2:.6f}",
                f"    RMS phase residual = {np.degrees(fit.rms_rad):.3f} deg",
            ]
        )
    lines.extend(
        [
            "",
            f"Static mean |great-circle latitude|: {np.nanmean(np.abs(static_lat)):.3f} deg",
            f"Static 95th percentile |latitude|: {np.nanpercentile(np.abs(static_lat), 95):.3f} deg",
            f"Dynamic mean |great-circle latitude|: {np.nanmean(np.abs(dyn_lat)):.3f} deg",
            f"Dynamic 95th percentile |latitude|: {np.nanpercentile(np.abs(dyn_lat), 95):.3f} deg",
        ]
    )
    if metadata:
        lines.extend(
            [
                "",
                "Metadata:",
                f"  started_at: {metadata.get('started_at', 'n/a')}",
                f"  finished_at: {metadata.get('finished_at', 'n/a')}",
                f"  actuator_gain_v_per_v: {metadata.get('actuator_gain_v_per_v', 'n/a')}",
                f"  loopback: {metadata.get('loopback', 'n/a')}",
            ]
        )
    (outdir / "analysis_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", type=Path, required=True, help="Static staircase CSV")
    parser.add_argument("--dynamic", type=Path, required=True, help="Dynamic sine-sweep CSV")
    parser.add_argument("--metadata", type=Path, default=None, help="Optional metadata JSON")
    parser.add_argument("--output-dir", type=Path, default=Path("paper_plots"), help="Output directory")
    parser.add_argument(
        "--formats",
        nargs="+",
        default=["png", "pdf"],
        choices=["png", "pdf", "svg"],
        help="Figure formats to save (default: png pdf)",
    )
    parser.add_argument("--dpi", type=int, default=400, help="PNG resolution (default: 400 dpi)")
    parser.add_argument("--show", action="store_true", help="Show figures interactively after saving")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    set_paper_style()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    static_raw = pd.read_csv(args.static)
    dynamic_raw = pd.read_csv(args.dynamic)
    metadata = None
    if args.metadata is not None:
        with args.metadata.open("r", encoding="utf-8") as handle:
            metadata = json.load(handle)

    static, static_fits = prepare_static(static_raw)
    dynamic = interpolate_dynamic_voltage(dynamic_raw)
    segments, measured_frequency = segment_dynamic(dynamic)
    v_ref = float(np.nanmedian(static.rp_in1_voltage_v))
    segments = align_dynamic_segments(segments, static_fits, v_ref)

    figures = [
        ("01_static_stokes_transfer", plot_static_stokes(static)),
        ("02_poincare_phase_plane", plot_phase_plane(static)),
        ("03_static_phase_calibration", plot_static_phase(static, static_fits)),
        ("04_model_deviation", plot_model_deviation(static, static_fits)),
        (
            "05_dynamic_repeatability",
            plot_dynamic_repeatability(dynamic, segments, static, static_fits, measured_frequency),
        ),
        ("06_electrical_ground_truth", plot_electrical_ground_truth(static, dynamic, measured_frequency)),
    ]

    for stem, fig in figures:
        save_figure(fig, args.output_dir, stem, args.formats, args.dpi)

    write_summaries(
        args.output_dir,
        static,
        static_fits,
        dynamic,
        segments,
        measured_frequency,
        metadata,
    )

    print(f"Saved {len(figures)} figures to {args.output_dir.resolve()}")
    print(f"Measured dynamic electrical frequency: {measured_frequency:.6f} Hz")
    for direction, fit in static_fits.items():
        print(
            f"Static {direction}: slope={fit.slope:.5f} rad/V, "
            f"V_pi={fit.v_pi:.4f} V, V_2pi={fit.v_2pi:.4f} V, "
            f"R^2={fit.r2:.5f}"
        )
    if segments:
        v2pi = np.array([seg.fit.v_2pi for seg in segments])
        print(
            f"Dynamic half-sweeps: n={len(segments)}, "
            f"mean V_2pi={np.mean(v2pi):.4f} V, "
            f"std={np.std(v2pi, ddof=1):.4f} V"
        )

    if args.show:
        plt.show()
    else:
        for _, fig in figures:
            plt.close(fig)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
