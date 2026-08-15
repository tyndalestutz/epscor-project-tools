#!/usr/bin/env python3
"""One-page report for the held-step first-NPBS D-port phi1 u-lock test."""
from __future__ import annotations

import argparse
import csv
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-phi1-d-lock")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages


def _wrap(angle: np.ndarray | float) -> np.ndarray | float:
    return (np.asarray(angle) + math.pi) % (2.0 * math.pi) - math.pi


def _load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    base_required = {"stage", "elapsed_s", "s1", "s2", "s3", "rp_out1_v"}
    if not rows or not base_required.issubset(rows[0]):
        raise ValueError(f"{path} is not a phi1-lock-test CSV")
    # Permit regenerating reports for the first D-phase-labelled run.  At D,
    # q=atan2(S3,-S2)=pi-u (mod 2pi), so converting to u reverses the error
    # and the voltage slope.
    if "u_rad" not in rows[0]:
        old_required = {"d_phase_rad", "target_d_phase_rad", "phase_error_rad", "phase_slope_rad_per_rp_v"}
        if not old_required.issubset(rows[0]):
            raise ValueError(f"{path} lacks the u-lock fields")
        for row in rows:
            s1, s2, s3 = (float(row[key]) for key in ("s1", "s2", "s3"))
            norm = math.sqrt(s1 * s1 + s2 * s2 + s3 * s3)
            row["u_rad"] = str(float(_wrap(math.atan2(s3, s2))))
            row["v_rad"] = str(float(math.acos(max(-1.0, min(1.0, s1 / norm)))))
            row["target_u_rad"] = "" if not row["target_d_phase_rad"] else str(float(_wrap(math.pi - float(row["target_d_phase_rad"]))))
            row["u_error_rad"] = str(-float(row["phase_error_rad"])) if row["phase_error_rad"] else ""
            row["u_slope_rad_per_rp_v"] = str(-float(row["phase_slope_rad_per_rp_v"])) if row["phase_slope_rad_per_rp_v"] else ""
    return rows


def add_page(pdf: PdfPages, input_file: Path) -> None:
    rows = _load(input_file)
    calibration = [row for row in rows if row["stage"] == "calibration"]
    hold = [row for row in rows if row["stage"] == "pid"]
    if not calibration or not hold:
        raise ValueError("Phi1 u-lock report requires calibration and PID rows")
    cal_v = np.asarray([float(row["rp_out1_v"]) for row in calibration])
    cal_u = np.unwrap(np.asarray([float(row["u_rad"]) for row in calibration]))
    time_s = np.asarray([float(row["elapsed_s"]) for row in hold])
    target = float(hold[0]["target_u_rad"])
    error = np.asarray([float(row["u_error_rad"]) for row in hold])
    output = np.asarray([float(row["rp_out1_v"]) for row in hold])
    s1 = np.asarray([float(row["s1"]) for row in hold])
    s2 = np.asarray([float(row["s2"]) for row in hold])
    s3 = np.asarray([float(row["s3"]) for row in hold])
    v = np.asarray([float(row["v_rad"]) for row in hold])
    radius = np.asarray([float(row["equatorial_radius"]) for row in hold])
    integral = np.asarray([float(row["integral_rad_s"]) for row in hold])
    delta = np.asarray([float(row["delta_rp_v"]) for row in hold])
    pd = np.asarray([float(row["pd_mean_v"]) for row in hold])
    saturated = np.asarray([float(row["saturated"]) for row in hold])
    slope = float(hold[0]["u_slope_rad_per_rp_v"])
    kp = hold[0].get("kp", "")
    ki = hold[0].get("ki_per_s", "")
    average_count = hold[0].get("pax_average_count", "")
    filter_alpha = hold[0].get("stokes_filter_alpha", "")
    duration = float(time_s[-1] - time_s[0])
    late = time_s >= time_s[-1] - min(30.0, duration / 2.0)

    figure, axes = plt.subplots(3, 2, figsize=(12.5, 10.0))
    figure.subplots_adjust(left=.075, right=.975, top=.89, bottom=.17, hspace=.52, wspace=.28)
    figure.suptitle(f"Phi1 u-lock at first-NPBS D — {input_file.parent.name}", y=.965, fontsize=15, fontweight="bold")

    axes[0, 0].plot(cal_v, cal_u, "o-", color="tab:blue", ms=4, label="held measured u")
    axes[0, 0].axhline(target, color="k", ls="--", lw=1, label=f"midpoint u target = {target:+.3f}")
    axes[0, 0].set(title="Fresh one-Vλ u calibration", xlabel="OUT1 / phi1 RP command (V)", ylabel="u (unwrapped rad)")
    axes[0, 0].legend(fontsize=8)

    axes[1, 0].plot(time_s, error, color="tab:blue", lw=1.1, label=r"wrapped $u_{target}-u_{measured}$")
    axes[1, 0].axhline(0, color="k", lw=.8)
    axes[1, 0].axhspan(-.25, .25, color="tab:green", alpha=.10, label="±0.25 rad")
    axes[1, 0].set(title="Azimuth locking error", xlabel="elapsed time (s)", ylabel="u error (rad)")
    axes[1, 0].legend(fontsize=8)

    axes[0, 1].plot(time_s, output, color="tab:orange", lw=1.2, label="OUT1 command")
    axes[0, 1].scatter(time_s[saturated > 0], output[saturated > 0], color="tab:red", marker="x", s=18, label="rail limited")
    axes[0, 1].set(title="Phi1 actuator command", xlabel="elapsed time (s)", ylabel="RP command (V)", ylim=(-.03, 1.03))
    axes[0, 1].legend(fontsize=8)

    axes[1, 1].plot(time_s, v, color="tab:green", lw=1.1, label=r"measured $v$")
    axes[1, 1].axhline(np.pi / 2.0, color="k", ls="--", lw=.9, label=r"D equator: $v=\pi/2$")
    axes[1, 1].set(title="Uncontrolled polar coordinate", xlabel="elapsed time (s)", ylabel="v (rad)")
    axes[1, 1].legend(fontsize=8)

    ring = np.linspace(0, 2 * np.pi, 300)
    axes[2, 0].plot(np.cos(ring), np.sin(ring), "k--", lw=.9, label="S1=0 equator")
    axes[2, 0].scatter(s2, s3, c=time_s, s=10, cmap="viridis", label="PID samples")
    axes[2, 0].scatter([math.cos(target)], [math.sin(target)], marker="*", s=120, color="tab:red", label="u target")
    axes[2, 0].set(aspect="equal", xlim=(-1.1, 1.1), ylim=(-1.1, 1.1), xlabel=r"$S_2$", ylabel=r"$S_3$", title="Held lock trajectory on the Poincare equator")
    axes[2, 0].legend(fontsize=8)

    axes[2, 1].plot(time_s, integral, color="tab:purple", lw=1.1, label="integral")
    axes[2, 1].plot(time_s, delta, color="tab:orange", lw=.9, alpha=.85, label="OUT1 update (V)")
    axes[2, 1].plot(time_s, pd, color="tab:gray", lw=.9, alpha=.8, label="PD mean (V)")
    axes[2, 1].axhline(0, color="k", lw=.8)
    axes[2, 1].set(title="PI state, command increment, and PD", xlabel="elapsed time (s)", ylabel="native units")
    axes[2, 1].legend(fontsize=8)
    for axis in axes.flat:
        axis.grid(alpha=.22)

    gain_line = "" if not kp else (
        f"     Kp={float(kp):.3f}     Ki={float(ki):.4f}/s     raw PAX reads/update={int(float(average_count))}"
        + (f"     Stokes IIR α={float(filter_alpha):.2f}" if filter_alpha else "")
    )
    summary = (
        f"Target u: {target:+.4f} rad     Calibrated du/dV: {slope:+.3f} rad/RP V     "
        f"Vλ estimate: {2 * math.pi / abs(slope):.4f} RP V{gain_line}\n"
        f"Duration: {duration:.1f} s     Median |error|: {np.median(abs(error)):.3f} rad (full), "
        f"{np.median(abs(error[late])):.3f} rad (late)     "
        f"v excursion (5–95%): {np.quantile(v, .95) - np.quantile(v, .05):.3f} rad     "
        f"mean |S1|: {np.mean(abs(s1)):.3f}     Rail-limited: {int(np.sum(saturated))}/{len(saturated)}"
    )
    summary_axis = figure.add_axes((.075, .035, .90, .095))
    summary_axis.set_axis_off()
    summary_axis.text(.5, .5, summary, ha="center", va="center", fontsize=10, linespacing=1.55,
                      bbox={"boxstyle": "round,pad=.55", "facecolor": "#f1f4f8", "edgecolor": "#aeb8c2"})
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
