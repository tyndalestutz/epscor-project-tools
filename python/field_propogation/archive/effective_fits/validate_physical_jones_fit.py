#!/usr/bin/env python3
"""Validate a frozen physical Jones fit against a both-path phi1 fringe map.

No optical parameter is optimized here.  This is a prediction check: component
parameters from ``fit_physical_jones_network.py`` are frozen, propagated at
each later command pair, and compared with the map's PD/PAX/Stokes readings.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib-field-propagation")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

if __package__:
    from ...physical_hybrid_mzi import PhysicalParameters, Retarder, port_observables
else:  # Support running this file directly from any working directory.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from physical_hybrid_mzi import PhysicalParameters, Retarder, port_observables


def _load(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"condition", "phi1_rp_v", "phi2_rp_v", "pd_mean_v", "pax_ptotal", "s1", "s2", "s3"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError("Expected a phi1-fringe-map CSV with both-path PAX/PD data")
    if any(row["condition"] != "both_paths" for row in rows):
        raise ValueError("This validator is intentionally for a both_paths phi1 fringe map")
    return rows


def _parameters(fit: dict[str, object]) -> PhysicalParameters:
    return PhysicalParameters(
        ay=float(fit["input_ay_over_ax"]), pbs_leakage_rad=float(fit["pbs_leakage_rad"]),
        npbs1_mixing_rad=float(fit["npbs1_mixing_rad"]), npbs2_mixing_rad=float(fit["npbs2_mixing_rad"]),
        loss_a=float(fit["loss_a"]), loss_b=float(fit["loss_b"]), loss_c=float(fit["loss_c"]), loss_d=float(fit["loss_d"]),
        retarder_c=Retarder(float(fit["retarder_c_axis_rad"]), float(fit["retarder_c_retardance_rad"])),
        retarder_d=Retarder(float(fit["retarder_d_axis_rad"]), float(fit["retarder_d_retardance_rad"])),
    )


def _harmonic(phi2: np.ndarray, values: np.ndarray) -> tuple[float, float, float, float]:
    design = np.column_stack((np.ones_like(phi2), np.cos(phi2), np.sin(phi2)))
    baseline, cosine, sine = np.linalg.lstsq(design, values, rcond=None)[0]
    amplitude = float(np.hypot(cosine, sine))
    return float(baseline), amplitude, amplitude / float(baseline), float(np.arctan2(-sine, cosine))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fit_json", type=Path, help="physical-jones-fit.json from the earlier A/B/both calibration")
    parser.add_argument("fringe_map_csv", type=Path, help="later phi1-fringe-map data.csv")
    parser.add_argument("--phi1-vlambda-rp", type=float, required=True)
    parser.add_argument("--phi2-vlambda-rp", type=float, required=True)
    args = parser.parse_args()
    fit = json.loads(args.fit_json.read_text())
    rows = _load(args.fringe_map_csv)
    parameters = _parameters(fit)
    phi1 = np.asarray([2 * np.pi * float(r["phi1_rp_v"]) / args.phi1_vlambda_rp + float(fit["phi1_offset_rad"]) for r in rows])
    phi2 = np.asarray([2 * np.pi * float(r["phi2_rp_v"]) / args.phi2_vlambda_rp + float(fit["phi2_offset_rad"]) for r in rows])
    pd, pax, stokes = [], [], []
    for first, second in zip(phi1, phi2):
        obs = port_observables(first, second, parameters)
        pd.append(float(fit["pd_offset"]) + float(fit["pd_gain"]) * obs[f"I_{fit['pd_port']}"])
        pax.append(float(fit["pax_offset"]) + float(fit["pax_gain"]) * obs[f"I_{fit['pax_port']}"])
        stokes.append(obs[f"S_{fit['pax_port']}"])
    pd, pax, stokes = np.asarray(pd), np.asarray(pax), np.asarray(stokes)
    measured_pd = np.asarray([float(r["pd_mean_v"]) for r in rows])
    measured_pax = np.asarray([float(r["pax_ptotal"]) for r in rows])
    measured_s = np.asarray([[float(r[key]) for key in ("s1", "s2", "s3")] for r in rows])

    output = args.fringe_map_csv.parent / "physical-jones-validation"
    output.mkdir(exist_ok=True)
    summary: list[dict[str, object]] = []
    with (output / "fringe-contrast-validation.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["phi1_direction", "phi1_rp_v", "detector", "measured_baseline", "predicted_baseline",
                         "measured_contrast", "predicted_contrast", "measured_phase_rad", "predicted_phase_rad"])
        for direction in sorted({r["phi1_direction"] for r in rows}):
            direction_mask = np.asarray([r["phi1_direction"] == direction for r in rows])
            for command in sorted(set(np.asarray([float(r["phi1_rp_v"]) for r in rows])[direction_mask])):
                mask = direction_mask & np.isclose(np.asarray([float(r["phi1_rp_v"]) for r in rows]), command)
                for detector, measured, predicted in (("pd", measured_pd, pd), ("pax", measured_pax, pax)):
                    mb, ma, mc, mp = _harmonic(phi2[mask], measured[mask])
                    pb, pa, pc, pp = _harmonic(phi2[mask], predicted[mask])
                    writer.writerow([direction, command, detector, mb, pb, mc, pc, mp, pp])
                    summary.append({"direction": direction, "phi1_rp_v": command, "detector": detector,
                                    "measured_contrast": mc, "predicted_contrast": pc})
    power_rms = {
        "pd": float(np.sqrt(np.mean(((pd - measured_pd) / max(np.std(measured_pd), 1e-9)) ** 2))),
        "pax": float(np.sqrt(np.mean(((pax - measured_pax) / max(np.std(measured_pax), 1e-12)) ** 2))),
        "stokes": float(np.sqrt(np.mean((stokes - measured_s) ** 2))),
    }
    (output / "validation-summary.json").write_text(json.dumps({"power_normalized_rms": power_rms, "contrast_rows": summary}, indent=2) + "\n")

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for detector, axis in (("pd", axes[0]), ("pax", axes[1])):
        subset = [row for row in summary if row["detector"] == detector]
        measured = [row["measured_contrast"] for row in subset]
        predicted = [row["predicted_contrast"] for row in subset]
        axis.scatter(measured, predicted, c=[row["phi1_rp_v"] for row in subset], cmap="viridis", s=30)
        limit = max(0.05, max(measured + predicted) * 1.1)
        axis.plot((0, limit), (0, limit), "k--", lw=1)
        axis.set(xlim=(0, limit), ylim=(0, limit), xlabel="measured contrast", ylabel="frozen Jones prediction",
                 title=f"{detector.upper()} contrast")
    fig.suptitle("High-contrast fringe-map validation (no refit)")
    fig.savefig(output / "contrast-validation.png", dpi=180)
    plt.close(fig)
    print(f"Wrote frozen physical-model validation to {output}")
    print("normalized power RMS: " + ", ".join(f"{key}={value:.3f}" for key, value in power_rms.items()))


if __name__ == "__main__":
    main()
