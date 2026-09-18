#!/usr/bin/env python3
"""One-page report for the PAX-assisted, FPGA-PD phi1 lock experiment."""
from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-phi1-pd-lock")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


def _wrap(value: np.ndarray | float) -> np.ndarray | float:
    return (np.asarray(value) + math.pi) % (2 * math.pi) - math.pi


def _values(rows: list[dict[str, str]], key: str) -> np.ndarray:
    return np.asarray([float(row[key]) for row in rows], dtype=float)


def add_page(pdf: PdfPages, input_file: Path) -> None:
    with input_file.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"stage", "elapsed_s", "rp_out1_v", "pd_mean_v", "pd_target_v", "pid_output_v", "u", "pax_sample"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{input_file} is not a phi1-pd-lock-test CSV")
    rough = [row for row in rows if row["stage"] == "pax-rough"]
    local = [row for row in rows if row["stage"] == "pd-local-calibration"]
    handoff = [row for row in rows if row["stage"] == "handoff-target"]
    hold = [row for row in rows if row["stage"] in {"pd-fpga-p-only", "pd-fpga-pid"}]
    if not rough or not local or not handoff or not hold:
        raise ValueError("PD lock report requires rough, local-calibration, handoff, and PID stages")
    target_row = handoff[-1]
    target_pd = float(target_row["pd_target_v"])
    target_u = float(target_row["u"])
    slope = float(target_row["local_pd_slope_v_per_rp_v"])
    pid_p = float(hold[0]["pid_p"])
    active_i = [row for row in hold if row["stage"] == "pd-fpga-pid"]
    pid_i = float(active_i[0]["pid_i_hz"]) if active_i else 0.0
    t = _values(hold, "elapsed_s")
    pd = _values(hold, "pd_mean_v")
    error = _values(hold, "pd_error_v")
    output = _values(hold, "pid_output_v")
    pd_std = _values(hold, "pd_std_v")
    setpoint = _values(hold, "pd_target_v")
    pax_hold = [row for row in hold if int(row["pax_sample"])]
    pax_t = _values(pax_hold, "elapsed_s") if pax_hold else np.asarray([])
    pax_u = _values(pax_hold, "u") if pax_hold else np.asarray([])
    duration = t[-1] - t[0]
    late = t >= t[-1] - min(30.0, duration / 2.0)

    figure, axes = plt.subplots(5, 2, figsize=(12.5, 14.4))
    figure.subplots_adjust(left=.075, right=.975, top=.885, bottom=.155, hspace=.55, wspace=.28)
    figure.suptitle(f"Phi1 PD FPGA lock — {input_file.parent.name}", y=.970, fontsize=15, fontweight="bold")

    axes[0, 0].plot(_values(rough, "rp_out1_v"), np.unwrap(_values(rough, "u")), "o-", ms=4, label="PAX-measured u")
    axes[0, 0].axhline(target_u, color="k", ls="--", lw=1, label=f"handoff u = {target_u:+.3f}")
    axes[0, 0].set(title="PAX-assisted φ1 rough map", xlabel="OUT1 / phi1 RP command (V)", ylabel="u (unwrapped rad)")
    axes[0, 0].legend(fontsize=8)

    axes[0, 1].plot(_values(rough, "rp_out1_v"), _values(rough, "pd_mean_v"), "o-", ms=4, label="simultaneous PD")
    axes[0, 1].axhline(target_pd, color="k", ls="--", lw=1, label=f"PD target = {target_pd:.5f} V")
    axes[0, 1].set(title="PD value associated with rough u map", xlabel="OUT1 / phi1 RP command (V)", ylabel="PD mean (V)")
    axes[0, 1].legend(fontsize=8)

    local_v, local_pd = _values(local, "rp_out1_v"), _values(local, "pd_mean_v")
    intercept = float(np.mean(local_pd - slope * local_v))
    axes[1, 0].plot(local_v, local_pd, "o", color="tab:blue", label="local PD samples")
    axes[1, 0].plot(local_v, slope * local_v + intercept, "--", color="tab:orange", label=f"fit: dPD/dOUT1={slope:+.3f} V/V")
    axes[1, 0].axhline(target_pd, color="k", ls=":", lw=1, label="hardware PID setpoint")
    axes[1, 0].set(title="Automatic local PD calibration", xlabel="OUT1 / phi1 RP command (V)", ylabel="PD mean (V)")
    axes[1, 0].legend(fontsize=8)

    axes[1, 1].plot(t, pd, color="tab:blue", lw=1.0, label="PD mean")
    axes[1, 1].plot(t, setpoint, color="k", ls="--", lw=1, label="PD setpoint")
    axes[1, 1].fill_between(t, pd - pd_std, pd + pd_std, color="tab:blue", alpha=.15, label="scope-window σ")
    axes[1, 1].set(title="FPGA PD lock", xlabel="elapsed time (s)", ylabel="PD voltage (V)")
    axes[1, 1].legend(fontsize=8)

    integral_start = [float(row["elapsed_s"]) for row in active_i]
    if integral_start:
        for axis in (axes[1, 1], axes[2, 0], axes[2, 1]):
            axis.axvline(integral_start[0], color="tab:purple", ls=":", lw=1.0)

    axes[2, 0].plot(t, error, color="tab:blue", lw=1.0, label="PD − setpoint")
    axes[2, 0].axhline(0, color="k", lw=.8)
    axes[2, 0].set(title="PD locking error", xlabel="elapsed time (s)", ylabel="PD error (V)")
    axes[2, 0].legend(fontsize=8)

    axes[2, 1].plot(t, output, color="tab:orange", lw=1.1, label="FPGA PID OUT1")
    axes[2, 1].set(title="Continuous FPGA actuator output", xlabel="elapsed time (s)", ylabel="RP OUT1 (V)", ylim=(-.03, 1.03))
    axes[2, 1].legend(fontsize=8, loc="upper left")

    # PAX is deliberately a slow, independent validation channel: it does not
    # command the FPGA PID.  Give it dedicated panels instead of hiding it on
    # a secondary axis of the actuator plot.
    if pax_hold:
        u_error = _wrap(pax_u - target_u)
        pax_v = _values(pax_hold, "v")
        target_v = float(target_row["v"])
        axes[3, 0].plot(pax_t, u_error, "o-", ms=3, lw=.9, color="tab:green", label="PAX u − handoff u")
        axes[3, 0].axhline(0, color="k", lw=.8)
        axes[3, 0].set(title="Independent PAX azimuth validation", xlabel="elapsed time (s)", ylabel="Δu (rad)")
        axes[3, 0].legend(fontsize=8)
        axes[3, 1].plot(pax_t, pax_v, "o-", ms=3, lw=.9, color="tab:purple", label="PAX v")
        axes[3, 1].axhline(target_v, color="k", ls="--", lw=.9, label=f"handoff v={target_v:.3f}")
        axes[3, 1].set(title="PAX polar-angle cross-check", xlabel="elapsed time (s)", ylabel="v (rad)")
        axes[3, 1].legend(fontsize=8)
    else:
        for axis in axes[3]:
            axis.set_axis_off()
        axes[3, 0].text(.5, .5, "No PAX validation snapshots were logged during the FPGA hold.", ha="center", va="center")

    # Express the same held PAX states geometrically.  For this phi1 test the
    # expected trajectory is the S1=0 equator, so S2/S3 makes branch slips or
    # genuine off-equator excursions visible at a glance.
    equator_axis = axes[4, 0]
    angle = np.linspace(0.0, 2.0 * math.pi, 401)
    equator_axis.plot(np.cos(angle), np.sin(angle), color="0.55", lw=1.0, label="ideal S1=0 equator")
    if pax_hold:
        s2 = _values(pax_hold, "s2")
        s3 = _values(pax_hold, "s3")
        color = pax_t - pax_t[0]
        trajectory = equator_axis.scatter(s2, s3, c=color, cmap="viridis", s=20, zorder=3, label="held PAX samples")
        equator_axis.plot(s2, s3, color="tab:green", alpha=.35, lw=.75)
        target_s2 = float(target_row["s2"])
        target_s3 = float(target_row["s3"])
        target_norm = math.hypot(target_s2, target_s3)
        if target_norm > 0.0:
            # A short radial line is the local normal to the equator at the
            # target point.  It is clearer than a star when the held points
            # form a tight cluster near that point.
            normal_s2, normal_s3 = target_s2 / target_norm, target_s3 / target_norm
            equator_axis.plot(
                [0.90 * normal_s2, 1.10 * normal_s2], [0.90 * normal_s3, 1.10 * normal_s3],
                color="crimson", lw=2.2, solid_capstyle="round", zorder=4,
            )
        figure.colorbar(trajectory, ax=equator_axis, pad=.02, label="hold time (s)")
    equator_axis.axhline(0, color="0.85", lw=.8)
    equator_axis.axvline(0, color="0.85", lw=.8)
    equator_axis.set(title="Held lock trajectory on the Poincare equator", xlabel="$S_2$", ylabel="$S_3$", xlim=(-1.08, 1.08), ylim=(-1.08, 1.08))
    equator_axis.set_aspect("equal", adjustable="box")
    axes[4, 1].set_axis_off()
    for axis in axes.flat:
        axis.grid(alpha=.22)

    u_error = _wrap(pax_u - target_u) if pax_hold else np.asarray([])
    u_summary = "no PAX snapshots" if not pax_hold else f"PAX median |Δu|={np.median(abs(u_error)):.3f} rad ({len(pax_hold)} snapshots)"
    summary = (
        f"PAX handoff target u={target_u:+.4f} rad   |   initial PD setpoint={target_pd:.5f} V   |   local slope={slope:+.4f} V/V\n"
        f"FPGA PID: P={pid_p:+.3f}, I={pid_i:+.0f} Hz   |   duration={duration:.1f} s   |   "
        f"median |PD error|={np.median(abs(error)):.5f} V (late {np.median(abs(error[late])):.5f} V)\n"
        f"OUT1={output.min():.3f}…{output.max():.3f} V   |   PD setpoint={setpoint.min():.5f}…{setpoint.max():.5f} V   |   {u_summary}"
    )
    summary_axis = figure.add_axes((.075, .020, .90, .105))
    summary_axis.set_axis_off()
    summary_axis.text(.5, .5, summary, ha="center", va="center", fontsize=8.7, linespacing=1.55,
                      bbox={"boxstyle": "round,pad=.45", "facecolor": "#f1f4f8", "edgecolor": "#aeb8c2"})
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
