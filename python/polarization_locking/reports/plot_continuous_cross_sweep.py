"""Report pages and descriptive metrics for continuous coupling trajectories."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


def _bool(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for key in (
            "elapsed_s", "segment_id", "bias_index", "fixed_command_v",
            "sine_frequency_hz", "sine_center_v", "sine_amplitude_v",
            "nominal_target_command_v", "measured_in1_v", "measured_in2_v",
            "measured_target_v", "measured_fixed_v", "sine_phase_rad",
            "cycle_index", "s1", "s2", "s3", "dop", "pax_ptotal",
        ):
            try:
                row[key] = float(row[key])
            except (KeyError, TypeError, ValueError):
                row[key] = float("nan")
        for key in ("analysis_valid", "reference_valid", "reference_input_clipped", "pax_valid"):
            row[key] = _bool(row.get(key, False))
    return rows


def _array(rows: list[dict[str, Any]], key: str) -> np.ndarray:
    return np.asarray([row[key] for row in rows], dtype=float)


def fit_plane(rows: list[dict[str, Any]]) -> tuple[np.ndarray, float]:
    stokes = np.column_stack([_array(rows, key) for key in ("s1", "s2", "s3")])
    stokes = stokes[np.all(np.isfinite(stokes), axis=1)]
    if len(stokes) < 3:
        return np.full(3, np.nan), float("nan")
    centered = stokes - stokes.mean(axis=0)
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    normal = vh[-1]
    if normal[np.argmax(np.abs(normal))] < 0:
        normal = -normal
    return normal, float(np.sqrt(np.mean((centered @ normal) ** 2)))


def _phase_interpolate(rows: list[dict[str, Any]], keys: tuple[str, ...], grid: np.ndarray) -> np.ndarray:
    phase = _array(rows, "sine_phase_rad")
    order = np.argsort(phase)
    phase = phase[order]
    unique, indices = np.unique(phase, return_index=True)
    if len(unique) < 4:
        return np.full((len(grid), len(keys)), np.nan)
    return np.column_stack([
        np.interp(grid, unique, _array(rows, key)[order][indices]) for key in keys
    ])


def _normalize_stokes(values: np.ndarray) -> np.ndarray:
    result = values.copy()
    norms = np.linalg.norm(result[:, :3], axis=1)
    good = np.isfinite(norms) & (norms > 0)
    result[good, :3] /= norms[good, None]
    result[~good, :3] = np.nan
    return result


def cycle_repeatability(rows: list[dict[str, Any]]) -> tuple[float, float, float]:
    cycles = sorted({int(row["cycle_index"]) for row in rows if row["cycle_index"] >= 0})
    grid = np.linspace(0, 2 * math.pi, 181, endpoint=False)
    curves = []
    for cycle in cycles:
        selected = [row for row in rows if int(row["cycle_index"]) == cycle]
        curve = _normalize_stokes(
            _phase_interpolate(selected, ("s1", "s2", "s3", "pax_ptotal", "dop"), grid)
        )
        if np.all(np.isfinite(curve[:, :3])):
            curves.append(curve)
    if len(curves) < 2:
        return float("nan"), float("nan"), float("nan")
    reference = curves[0]
    angles, powers, dops = [], [], []
    for curve in curves[1:]:
        dots = np.sum(reference[:, :3] * curve[:, :3], axis=1)
        angles.extend(np.degrees(np.arccos(np.clip(dots, -1, 1))))
        powers.extend(curve[:, 3] - reference[:, 3])
        dops.extend(curve[:, 4] - reference[:, 4])
    return (
        float(np.sqrt(np.nanmean(np.square(angles)))),
        float(np.sqrt(np.nanmean(np.square(powers)))),
        float(np.sqrt(np.nanmean(np.square(dops)))),
    )


def _one_cycle_branch_separation(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    rising = [row for row in rows if row.get("voltage_direction") == "rising"]
    falling = [row for row in rows if row.get("voltage_direction") == "falling"]
    if len(rising) < 4 or len(falling) < 4:
        return np.asarray([]), np.asarray([])
    lo = max(np.nanmin(_array(rising, "measured_target_v")), np.nanmin(_array(falling, "measured_target_v")))
    hi = min(np.nanmax(_array(rising, "measured_target_v")), np.nanmax(_array(falling, "measured_target_v")))
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        return np.asarray([]), np.asarray([])
    grid = np.linspace(lo, hi, 101)

    def voltage_curve(branch: list[dict[str, Any]], keys: tuple[str, ...]) -> np.ndarray:
        voltage = _array(branch, "measured_target_v")
        order = np.argsort(voltage)
        unique, indices = np.unique(voltage[order], return_index=True)
        return np.column_stack([
            np.interp(grid, unique, _array(branch, key)[order][indices]) for key in keys
        ])

    up = _normalize_stokes(voltage_curve(rising, ("s1", "s2", "s3", "pax_ptotal")))
    down = _normalize_stokes(voltage_curve(falling, ("s1", "s2", "s3", "pax_ptotal")))
    angle = np.degrees(np.arccos(np.clip(np.sum(up[:, :3] * down[:, :3], axis=1), -1, 1)))
    return angle, up[:, 3] - down[:, 3]


def branch_separation(rows: list[dict[str, Any]]) -> tuple[float, float]:
    """Aggregate within-cycle rising/falling differences without mixing drift."""
    angles, powers = [], []
    cycles = sorted({int(row["cycle_index"]) for row in rows if row["cycle_index"] >= 0})
    selections = ([row for row in rows if int(row["cycle_index"]) == cycle] for cycle in cycles)
    if not cycles:
        selections = (rows,)
    for selected in selections:
        angle, power = _one_cycle_branch_separation(selected)
        angles.extend(angle.tolist())
        powers.extend(power.tolist())
    if not angles:
        return float("nan"), float("nan")
    return float(np.sqrt(np.nanmean(np.square(angles)))), float(np.sqrt(np.nanmean(np.square(powers))))


def segment_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in rows if row["analysis_valid"] and row["pax_valid"]]
    referenced = [row for row in usable if row["reference_valid"]]
    normal, plane_residual = fit_plane(usable)
    cycle_planes = []
    for cycle in sorted({int(row["cycle_index"]) for row in usable if row["cycle_index"] >= 0}):
        cycle_rows = [row for row in usable if int(row["cycle_index"]) == cycle]
        cycle_normal, cycle_residual = fit_plane(cycle_rows)
        cycle_planes.append((cycle, cycle_normal, cycle_residual))
    repeat_angle, repeat_power, repeat_dop = cycle_repeatability(usable)
    branch_angle, branch_power = branch_separation(referenced)
    tracking = (
        float(np.sqrt(np.mean((_array(referenced, "measured_target_v") -
                               _array(referenced, "nominal_target_command_v")) ** 2)))
        if referenced else float("nan")
    )
    fixed_pp = (
        float(np.ptp(_array(referenced, "measured_fixed_v"))) if referenced else float("nan")
    )
    return {
        "target": rows[0]["target_actuator"], "segment_id": int(rows[0]["segment_id"]),
        "bias_v": rows[0]["fixed_command_v"], "samples": len(usable),
        "reference_fraction": len(referenced) / len(usable) if usable else 0.0,
        "tracking_rms_v": tracking, "fixed_peak_to_peak_v": fixed_pp,
        "branch_angular_rms_deg": branch_angle, "branch_power_rms": branch_power,
        "cycle_angular_rms_deg": repeat_angle, "cycle_power_rms": repeat_power,
        "cycle_dop_rms": repeat_dop, "plane_normal": normal,
        "plane_residual": plane_residual, "cycle_planes": cycle_planes,
        "low_dop_fraction": (
            sum(row.get("pax_status") == "low_dop" for row in usable) / len(usable)
            if usable else float("nan")
        ),
        "clipped": any(row["reference_input_clipped"] for row in rows),
    }


def summarize(path: Path) -> dict[str, Any]:
    rows = load_rows(path)
    groups = {
        int(segment): [row for row in rows if int(row["segment_id"]) == int(segment)]
        for segment in sorted({row["segment_id"] for row in rows if math.isfinite(row["segment_id"])})
    }
    metrics = [segment_metrics(group) for group in groups.values() if group]
    analysis = [row for row in rows if row["analysis_valid"]]
    rates = []
    for group in groups.values():
        times = _array([row for row in group if row["analysis_valid"]], "elapsed_s")
        if len(times) > 1:
            rates.extend(np.diff(times).tolist())
    pax_rate = 1 / np.median([value for value in rates if value > 0]) if any(value > 0 for value in rates) else float("nan")
    frequency = float(np.nanmedian(_array(analysis, "sine_frequency_hz"))) if analysis else float("nan")
    trace_rate = float("nan")
    trace_path = path.parent / "drive_trace.npz"
    if trace_path.exists():
        with np.load(trace_path) as trace:
            times, captures = trace["time_s"], trace["capture_id"]
            differences = np.diff(times)
            same = np.diff(captures) == 0
            if np.any(same & (differences > 0)):
                trace_rate = 1 / float(np.median(differences[same & (differences > 0)]))
    tracking_ok = [
        item["reference_fraction"] >= 0.9
        and math.isfinite(item["tracking_rms_v"])
        and item["tracking_rms_v"] <= max(0.02, 0.1 * float(rows[0]["sine_amplitude_v"]))
        and not item["clipped"]
        for item in metrics
    ]
    repeat = [item["cycle_angular_rms_deg"] for item in metrics if math.isfinite(item["cycle_angular_rms_deg"])]
    branch = [item["branch_angular_rms_deg"] for item in metrics if math.isfinite(item["branch_angular_rms_deg"])]
    normals_by_axis: dict[str, list[np.ndarray]] = {}
    for item in metrics:
        if np.all(np.isfinite(item["plane_normal"])):
            normals_by_axis.setdefault(item["target"], []).append(item["plane_normal"])
    normal_spreads = []
    for normals in normals_by_axis.values():
        for normal in normals[1:]:
            normal_spreads.append(math.degrees(math.acos(np.clip(abs(float(normal @ normals[0])), -1, 1))))
    waveform_tracking = (
        "unresolved" if not tracking_ok else
        "correct within descriptive threshold" if all(tracking_ok) else
        "deviations or missing/clipped reference present"
    )
    branch_status = "unresolved" if not branch else ("strong separation present" if max(branch) > 5 else "no strong separation observed")
    repeat_status = "unresolved" if not repeat else ("repeatable within threshold" if max(repeat) <= 5 else "nonrepeatable above threshold")
    plane_status = "unresolved" if not normal_spreads else ("obvious changes present" if max(normal_spreads) > 5 else "no obvious changes observed")
    return {
        "rows": len(rows), "analysis_rows": len(analysis), "metrics": metrics,
        "targets": sorted({row["target_actuator"] for row in rows}),
        "frequency_hz": frequency, "pax_rate_hz": pax_rate,
        "samples_per_cycle": pax_rate / frequency if frequency > 0 else float("nan"),
        "reference_rate_hz": trace_rate,
        "recorded_cycles": int(max((row["cycle_index"] for row in analysis), default=-1) + 1),
        "bias_counts": {axis: sum(item["target"] == axis for item in metrics) for axis in ("phi1", "phi2")},
        "waveform_tracking": waveform_tracking,
        "branch_status": branch_status,
        "repeatability_status": repeat_status,
        "bias_plane_status": plane_status,
        "low_dop_present": any(item["low_dop_fraction"] > 0 for item in metrics),
    }


def summary_text(path: Path) -> str:
    result = summarize(path)
    return (
        f"Target actuator(s): {', '.join(result['targets']) or 'none'}; sine: {result['frequency_hz']:.4g} Hz; "
        f"measured PAX rate: {result['pax_rate_hz']:.3g} Hz; saved RP reference rate: "
        f"{result['reference_rate_hz']:.3g} Hz; estimated PAX samples/cycle: {result['samples_per_cycle']:.2f}.\n"
        f"Recorded cycles: {result['recorded_cycles']}; bias values: phi1 target {result['bias_counts']['phi1']}, "
        f"phi2 target {result['bias_counts']['phi2']}. Waveform tracking: {result['waveform_tracking']}. "
        f"Rising/falling branches (>5° descriptive threshold): {result['branch_status']}. "
        f"Cycle repeatability (5° descriptive threshold): {result['repeatability_status']}. "
        f"Bias-dependent plane changes (>5° descriptive threshold): {result['bias_plane_status']}. "
        f"Low-DoP intervals: {result['low_dop_present']}. These thresholds are flags, not mechanism assignments."
    )


def _metric_page(pdf: Any, target: str, metrics: list[dict[str, Any]]) -> None:
    bias = np.asarray([item["bias_v"] for item in metrics])
    fig, axes = plt.subplots(2, 3, figsize=(11.7, 8.3), constrained_layout=True)
    fig.suptitle(f"Continuous {target} diagnostic — bias dependence", fontsize=17, weight="bold")
    series = (
        ("tracking_rms_v", "Requested vs measured target", "RMS voltage difference (V)"),
        ("fixed_peak_to_peak_v", "Orthogonal reference motion", "Peak-to-peak (V)"),
        ("branch_angular_rms_deg", "Rising/falling branch separation", "Stokes angular RMS (deg)"),
        ("cycle_angular_rms_deg", "Cycle repeatability", "Cycle angular RMS (deg)"),
        ("plane_residual", "Trajectory-plane residual", "Stokes RMS"),
        ("low_dop_fraction", "Low-DoP context", "Fraction of analysis samples"),
    )
    for ax, (key, title, ylabel) in zip(axes.flat, series):
        ax.plot(bias, [item[key] for item in metrics], "o-")
        ax.set(title=title, xlabel="Fixed orthogonal command (RP V)", ylabel=ylabel)
        ax.grid(alpha=.25)
    pdf.savefig(fig)
    plt.close(fig)


def _segment_page(pdf: Any, rows: list[dict[str, Any]], metric: dict[str, Any]) -> None:
    target = metric["target"]
    valid = [row for row in rows if row["analysis_valid"]]
    referenced = [row for row in valid if row["reference_valid"]]
    fig = plt.figure(figsize=(11.7, 8.3), constrained_layout=True)
    grid = fig.add_gridspec(2, 3)
    ax_sphere = fig.add_subplot(grid[0, 0], projection="3d")
    axes = [fig.add_subplot(grid[index // 3, index % 3]) for index in range(1, 6)]
    fig.suptitle(
        f"{target} sine; fixed {rows[0]['fixed_actuator']} = {metric['bias_v']:.4g} RP V "
        f"(segment {metric['segment_id']})", fontsize=15, weight="bold",
    )
    for cycle in sorted({int(row["cycle_index"]) for row in valid if row["cycle_index"] >= 0}):
        cycle_rows = [row for row in valid if int(row["cycle_index"]) == cycle]
        ax_sphere.plot(_array(cycle_rows, "s1"), _array(cycle_rows, "s2"),
                       _array(cycle_rows, "s3"), ".-", ms=2, lw=.7, label=f"cycle {cycle}")
    ax_sphere.set(xlabel="s1", ylabel="s2", zlabel="s3")
    ax_sphere.legend(fontsize=6)

    for key, color in zip(("s1", "s2", "s3"), ("tab:blue", "tab:orange", "tab:green")):
        for direction, marker in (("rising", "."), ("falling", "x")):
            selected = [row for row in referenced if row.get("voltage_direction") == direction]
            axes[0].plot(_array(selected, "measured_target_v"), _array(selected, key),
                         marker, ms=2.5, color=color, alpha=.65,
                         label=f"{key} {direction}" if key == "s1" else None)
    axes[0].set(title="Stokes branches vs measured voltage", xlabel="Measured target RP output (V)", ylabel="Normalized Stokes")
    axes[0].legend(fontsize=6)

    axes[1].plot(_array(referenced, "measured_target_v"), _array(referenced, "pax_ptotal"), ".", ms=2, label="PAX power")
    power_ax = axes[1].twinx()
    power_ax.plot(_array(referenced, "measured_target_v"), _array(referenced, "dop"), ".", ms=2, color="tab:purple", label="DoP")
    axes[1].set(title="Power and DoP", xlabel="Measured target RP output (V)", ylabel="PAX power")
    power_ax.set_ylabel("DoP")

    elapsed = _array(rows, "elapsed_s")
    axes[2].plot(elapsed, _array(rows, "nominal_target_command_v"), label="requested sine", lw=1)
    axes[2].plot(elapsed, _array(rows, "measured_target_v"), ".", ms=2, label="measured target")
    axes[2].plot(elapsed, _array(rows, "measured_fixed_v"), ".", ms=2, label="measured fixed")
    axes[2].set(title="Drive references and warm-up", xlabel="Experiment time (s)", ylabel="RP output (V)")
    axes[2].legend(fontsize=6)

    for cycle in sorted({int(row["cycle_index"]) for row in valid if row["cycle_index"] >= 0}):
        selected = [row for row in valid if int(row["cycle_index"]) == cycle]
        axes[3].plot(_array(selected, "sine_phase_rad"), _array(selected, "s1"), ".", ms=2, label=f"s1 c{cycle}")
        axes[3].plot(_array(selected, "sine_phase_rad"), _array(selected, "s2"), ".", ms=2, label=f"s2 c{cycle}")
        axes[3].plot(_array(selected, "sine_phase_rad"), _array(selected, "s3"), ".", ms=2, label=f"s3 c{cycle}")
    axes[3].set(title="Cycle overlays", xlabel="Nominal sine phase (rad)", ylabel="Normalized Stokes")

    normal = metric["plane_normal"]
    expected = ("Ideal reference: phi1 should keep s1 fixed and total power independent of drive."
                if target == "phi1" else
                "Ideal reference: phi2 changes polar coordinate; fixed phi1 sets azimuth orientation.")
    lines = [
        expected,
        f"Plane normal: [{normal[0]:.3f}, {normal[1]:.3f}, {normal[2]:.3f}]",
        f"Plane residual RMS: {metric['plane_residual']:.4g}",
        f"Rising/falling angular RMS: {metric['branch_angular_rms_deg']:.3g}°",
        f"Rising/falling power RMS: {metric['branch_power_rms']:.3g} PAX units",
        f"Cycle angular RMS: {metric['cycle_angular_rms_deg']:.3g}°",
        f"Cycle power RMS: {metric['cycle_power_rms']:.3g} PAX units",
        f"Cycle DoP RMS: {metric['cycle_dop_rms']:.3g}",
        f"Tracking RMS: {metric['tracking_rms_v']:.4g} V",
        f"Reference coverage: {100 * metric['reference_fraction']:.1f}%",
        "Branch separation is reported descriptively; it is not assigned to piezo hysteresis.",
    ]
    lines.extend(
        f"Cycle {cycle} plane: [{normal[0]:.2f}, {normal[1]:.2f}, {normal[2]:.2f}], residual {residual:.3g}"
        for cycle, normal, residual in metric["cycle_planes"]
    )
    axes[4].axis("off")
    axes[4].text(0, 1, "\n".join(lines), va="top", fontsize=9, wrap=True)
    for ax in axes[:4]:
        ax.grid(alpha=.2)
    pdf.savefig(fig)
    plt.close(fig)


def add_report(pdf: Any, input_file: Path) -> None:
    rows = load_rows(input_file)
    groups = {
        int(segment): [row for row in rows if int(row["segment_id"]) == int(segment)]
        for segment in sorted({row["segment_id"] for row in rows if math.isfinite(row["segment_id"])})
    }
    metrics = {segment: segment_metrics(group) for segment, group in groups.items()}
    for target in ("phi1", "phi2"):
        selected = [item for item in metrics.values() if item["target"] == target]
        if selected:
            _metric_page(pdf, target, selected)
            for item in selected:
                _segment_page(pdf, groups[item["segment_id"]], item)
