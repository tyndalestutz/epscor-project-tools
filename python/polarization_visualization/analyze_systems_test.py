from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np


def analyze(run_dir: Path) -> dict:
    import matplotlib.pyplot as plt

    with (run_dir / "measurements.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError(f"No measurements in {run_dir}")
    axis = rows[0]["axis"]
    units = rows[0]["device_units"]
    values = np.asarray([float(row["requested_device_value"]) for row in rows])
    correction_path = run_dir / "voltage_correction.json"
    correction = None
    if correction_path.exists():
        correction = json.loads(correction_path.read_text(encoding="utf-8"))
        if axis == "lcvr":
            gain = float(correction["corrected_lcvr_cell_vpp_per_rp_amplitude_v"])
        else:
            gain = float(correction["corrected_eom_device_v_per_rp_command_v"])
        values = np.asarray([float(row["rp_command_or_amplitude_v"]) * gain for row in rows])
    pd_mean = np.asarray([float(row["pd_mean_v"]) for row in rows])
    pd_repeat = np.asarray([float(row["pd_repeat_std_v"]) for row in rows])
    camera_mean = np.asarray([float(row["camera_mean"]) for row in rows])
    correlations = np.asarray([float(row["normalized_correlation_to_reference"]) for row in rows])
    saturation = np.asarray([float(row["camera_saturation_fraction"]) for row in rows])
    images = [np.load(run_dir / "frames" / f"state_{index:02d}.npy", allow_pickle=False) for index in range(len(rows))]
    component_repeat = []
    for index in range(len(rows)):
        paths = sorted((run_dir / "raw_frames" / f"state_{index:02d}").glob("*.npy"))
        component_repeat.append(float(np.std([np.mean(np.load(path, allow_pickle=False)) for path in paths])))
    component_repeat = np.asarray(component_repeat)

    driven = slice(0, -1)
    pd_noise = max(float(np.median(pd_repeat[driven])), 1e-12)
    camera_noise = max(float(np.median(component_repeat[driven])), 1e-12)
    metrics = {
        "axis": axis,
        "device_units": units,
        "pd_range_v": float(np.ptp(pd_mean[driven])),
        "pd_range_over_median_repeat_std": float(np.ptp(pd_mean[driven]) / pd_noise),
        "camera_mean_range": float(np.ptp(camera_mean[driven])),
        "camera_range_over_median_component_mean_std": float(np.ptp(camera_mean[driven]) / camera_noise),
        "zero_return_pd_fraction_of_range": float(abs(pd_mean[-1] - pd_mean[0]) / max(np.ptp(pd_mean[driven]), 1e-12)),
        "zero_return_camera_fraction_of_range": float(abs(camera_mean[-1] - camera_mean[0]) / max(np.ptp(camera_mean[driven]), 1e-12)),
        "minimum_spatial_correlation": float(np.min(correlations)),
        "maximum_saturation_fraction": float(np.max(saturation)),
        "voltage_correction_applied": correction is not None,
    }
    (run_dir / "analysis.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")

    # Use one fixed crop for every state. Raw full-sensor arrays remain on
    # disk; the crop only makes beam structure legible in the report figure.
    reference_sum = np.sum(images, axis=0)
    weights = np.clip(reference_sum - np.percentile(reference_sum, 80), 0, None)
    yy, xx = np.indices(reference_sum.shape)
    center_y = int(np.sum(yy * weights) / np.sum(weights)) if np.sum(weights) else reference_sum.shape[0] // 2
    center_x = int(np.sum(xx * weights) / np.sum(weights)) if np.sum(weights) else reference_sum.shape[1] // 2
    half_size = min(240, reference_sum.shape[0] // 2, reference_sum.shape[1] // 2)
    y0 = max(0, min(center_y - half_size, reference_sum.shape[0] - 2 * half_size))
    x0 = max(0, min(center_x - half_size, reference_sum.shape[1] - 2 * half_size))
    display_images = [image[y0 : y0 + 2 * half_size, x0 : x0 + 2 * half_size] for image in images]

    fig = plt.figure(figsize=(13.2, 8.8), constrained_layout=True)
    grid = fig.add_gridspec(3, len(rows), height_ratios=(1.0, 1.0, 2.2))
    ax_camera = fig.add_subplot(grid[0, :3])
    ax_pd = fig.add_subplot(grid[0, 3:])
    ax_corr = fig.add_subplot(grid[1, :3])
    ax_repeat = fig.add_subplot(grid[1, 3:])
    x = np.arange(len(rows))
    labels = [f"{value:g}" for value in values]
    ax_camera.errorbar(x, camera_mean, yerr=component_repeat, marker="o", capsize=3)
    ax_camera.set(title="Basler mean response", ylabel="Mean camera code", xticks=x, xticklabels=labels)
    ax_pd.errorbar(x, pd_mean * 1e3, yerr=pd_repeat * 1e3, marker="o", capsize=3, color="tab:orange")
    ax_pd.set(title="IN2 photodiode response", ylabel="PD mean (mV)", xticks=x, xticklabels=labels)
    ax_corr.plot(x, correlations, "o-", color="tab:green")
    ax_corr.set(title="Normalized spatial correlation to first state", ylabel="Correlation", xticks=x, xticklabels=labels)
    ax_repeat.plot(x, component_repeat, "o-", label="camera frame-mean std")
    ax_repeat.plot(x, pd_repeat * 1e3, "o-", label="PD repeat std (mV)")
    ax_repeat.set(title="Within-state repeatability", xticks=x, xticklabels=labels)
    ax_repeat.legend(fontsize=8)
    display_low = float(np.percentile(np.concatenate([image.ravel() for image in display_images]), 0.1))
    display_high = float(np.percentile(np.concatenate([image.ravel() for image in display_images]), 99.9))
    for index, image in enumerate(display_images):
        axis_image = fig.add_subplot(grid[2, index])
        axis_image.imshow(image, cmap="gray", vmin=display_low, vmax=display_high)
        suffix = " (return)" if index == len(images) - 1 else ""
        axis_image.set_title(f"{values[index]:g} {units}{suffix}", fontsize=9)
        axis_image.set_axis_off()
    correction_note = " — corrected OUT2 voltage scale" if correction is not None else ""
    fig.suptitle(f"{axis.upper()} response test — fixed LP, fixed camera settings{correction_note}")
    fig.supxlabel(f"Commanded {axis.upper()} device level ({units}); final point is return to zero")
    output = run_dir / "diagnostic.png"
    fig.savefig(output, dpi=220)
    plt.close(fig)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a polarization-visualization systems test")
    parser.add_argument("run_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    for run_dir in args.run_dirs:
        print(run_dir)
        print(json.dumps(analyze(run_dir), indent=2))


if __name__ == "__main__":
    main()
