"""Run-local, fit-free summary of ../data.csv. No instrument or model imports.

Run with: python path/to/polarization-report/analyze.py
Outputs stay beside this file; original acquisition files are never rewritten.
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import platform

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pax-measurement-matplotlib")
os.environ.setdefault("XDG_CACHE_HOME", "/tmp")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "data.csv"
CONDITIONS = ("path_a_only", "path_b_only", "both_paths")
LABELS = ("A only (H-like)", "B only (V-like)", "A+B (external phi1 drive)")
REFERENCE = "https://www.thorlabs.com/newgrouppage9.cfm?objectgroup_id=12211"


def stats(values):
    a = np.asarray(values, dtype=float)
    if a.size < 2 or not np.isfinite(a).all():
        raise ValueError("Every analyzed series must have >=2 finite samples; no silent row exclusion")
    return {"n": int(a.size), "mean": float(a.mean()), "sample_sd": float(a.std(ddof=1)),
            "min": float(a.min()), "max": float(a.max())}


def angle_to_axis(vectors, axis, sign):
    unit = vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)
    return np.degrees(np.arccos(np.clip(sign * unit[..., axis], -1, 1)))


def write_csv(name, rows):
    with (HERE / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def analyze():
    with SOURCE.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if set(row["condition"] for row in rows) != set(CONDITIONS):
        raise ValueError("Expected A-only, B-only and both-path conditions")
    groups, arrays, stat_rows, axis_rows, blocks = {}, {}, [], [], []
    for condition in CONDITIONS:
        selected = [r for r in rows if r["condition"] == condition]
        a = {key: np.asarray([float(row[key]) for row in selected]) for key in rows[0] if key != "condition"}
        if any(not np.isfinite(x).all() for x in a.values()):
            raise ValueError(f"Nonfinite source data in {condition}")
        t = a["elapsed_s"]
        if np.any(np.diff(t) <= 0):
            raise ValueError("Elapsed times must increase within each condition")
        v = np.column_stack([a[key] for key in ("s1", "s2", "s3")])
        predicted = np.column_stack((np.cos(2*a["eta"])*np.cos(2*a["theta"]),
                                     np.cos(2*a["eta"])*np.sin(2*a["theta"]), np.sin(2*a["eta"])))
        if not np.allclose(v, predicted, rtol=0, atol=1e-12):
            raise ValueError("CSV Stokes values no longer match the recorded theta/eta convention")
        a.update(theta_deg=np.degrees(a["theta"]), eta_deg=np.degrees(a["eta"]),
                 abs_eta_deg=np.degrees(abs(a["eta"])),
                 signed_axis_ratio=np.tan(a["eta"]), minor_major_ratio=abs(np.tan(a["eta"])),
                 s1_circle_latitude_deg=np.degrees(np.arcsin(np.clip(v[:, 0], -1, 1))),
                 s2s3_radius=np.hypot(v[:, 1], v[:, 2]),
                 around_s1_deg=np.degrees(np.arctan2(v[:, 2], v[:, 1])))
        units = {"theta_deg": "deg (ellipse orientation, arithmetic average)", "eta_deg": "deg (signed ellipticity)",
                 "abs_eta_deg": "deg", "signed_axis_ratio": "signed b/a", "minor_major_ratio": "abs(b/a)",
                 "s1": "unit orientation Stokes", "s2": "unit orientation Stokes", "s3": "unit orientation Stokes",
                 "dop": "raw fraction", "pax_ptotal": "PAX native units", "pd_mean_v": "V",
                 "s1_circle_latitude_deg": "Poincare deg", "s2s3_radius": "unit orientation Stokes"}
        measurements = {key: stats(a[key]) for key in units}
        for key, summary in measurements.items():
            stat_rows.append({"condition": condition, "quantity": key, "unit": units[key], **summary})
        mean_v = v.mean(axis=0)
        mean_length = float(np.linalg.norm(mean_v))
        mean_direction = mean_v / mean_length if condition != "both_paths" else None
        for axis in range(3):
            for sign in (1, -1):
                axis_rows.append({"condition": condition, "axis": f"{'+' if sign == 1 else '-'}S{axis+1}",
                                  "unit": "Poincare deg", **stats(angle_to_axis(v, axis, sign)),
                                  "angle_of_static_mean_direction_deg": float(angle_to_axis(mean_direction, axis, sign)) if mean_direction is not None else ""})
        phase = np.sort(np.mod(a["around_s1_deg"], 360))
        gaps = np.diff(np.r_[phase, phase[0] + 360])
        groups[condition] = {"samples": len(selected), "first_elapsed_s": float(t[0]), "last_elapsed_s": float(t[-1]),
                             "duration_span_s": float(t[-1] - t[0]), "sample_interval_s": stats(np.diff(t)),
                             "duplicate_pax_timestamps": int(len(t) - len(np.unique(a["pax_timestamp"]))),
                             "measurements": measurements, "mean_orientation_stokes": mean_v.tolist(),
                             "mean_orientation_vector_length": mean_length,
                             "static_mean_direction": mean_direction.tolist() if mean_direction is not None else None,
                             "dop_above_one_count": int(np.count_nonzero(a["dop"] > 1)),
                             "dop_below_zero_count": int(np.count_nonzero(a["dop"] < 0)),
                             "max_unit_vector_norm_error": float(abs(np.linalg.norm(v, axis=1)-1).max()),
                             "max_stokes_reconstruction_error": float(abs(v-predicted).max()),
                             "rp_command_columns_all_zero": bool(np.all(a["rp_out1_v"] == 0) and np.all(a["rp_out2_v"] == 0)),
                             "largest_unsampled_around_s1_gap_deg": float(gaps.max()),
                             "sampled_around_s1_covering_arc_deg": float(360-gaps.max()),
                             "s1_circle_latitude_rms_deg": float(np.sqrt(np.mean(a["s1_circle_latitude_deg"]**2))),
                             "fraction_within_5deg_s1_circle": float(np.mean(abs(a["s1_circle_latitude_deg"]) <= 5)),
                             "fraction_within_10deg_s1_circle": float(np.mean(abs(a["s1_circle_latitude_deg"]) <= 10))}
        # Six contiguous equal-duration bins, including the last endpoint.
        edges = np.linspace(t[0], t[-1], 7)
        membership = np.clip(np.searchsorted(edges, t, side="right") - 1, 0, 5)
        for block in range(6):
            mask = membership == block
            blocks.append({"condition": condition, "block": block+1, "n": int(mask.sum()),
                           "first_elapsed_s": float(t[mask][0]), "last_elapsed_s": float(t[mask][-1]),
                           **{f"{key}_mean": float(a[key][mask].mean()) for key in units}})
        arrays[condition] = a
    dot = float(np.clip(np.dot(groups["path_a_only"]["static_mean_direction"], groups["path_b_only"]["static_mean_direction"]), -1, 1))
    separation = float(np.degrees(np.arccos(dot)))
    result = {"source": "../data.csv", "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "analysis_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "versions": {"python": platform.python_version(), "numpy": np.__version__, "matplotlib": matplotlib.__version__},
              "operator_context": "User reports external phi1 drive during A+B; RP command columns are zero and are not external actuator voltages.",
              "method": "All rows; equal sample weights; arithmetic means; sample SD with ddof=1; no fits, filtering, detrending, DOP correction/weighting, drive reconstruction or model export.",
              "convention": "theta and eta in radians in CSV; s=(cos(2eta)cos(2theta),cos(2eta)sin(2theta),sin(2eta)); these are unit orientation vectors, not DOP-scaled total Stokes.",
              "angle_definitions": {"ellipticity_deg": "eta*180/pi", "minor_major_ratio": "abs(tan(eta)) per sample, then averaged",
                                    "axis_distance": "acos(sign*s_j/||s||)*180/pi; Poincare sphere angle, not ellipse azimuth",
                                    "s1_circle_latitude": "asin(s1)*180/pi; signed angular distance from S1=0 plane",
                                    "around_s1": "atan2(s3,s2), wrapped; no unwrapping or cycle count",
                                    "static_mean_direction": "normalize(arithmetic mean of recorded unit Stokes); A and B only"},
              "groups": groups, "static_pair": {"poincare_separation_deg": separation, "departure_from_antipodal_deg": 180-separation},
              "coordinate_reference": REFERENCE}
    write_csv("statistics.csv", stat_rows)
    write_csv("axis-angles.csv", axis_rows)
    write_csv("block-means.csv", blocks)
    (HERE / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result, arrays, axis_rows


def report(result, arrays, axes_data):
    groups = result["groups"]

    def m(condition, key, digits=6):
        s = groups[condition]["measurements"][key]
        return f"{s['mean']:.{digits}f} +/- {s['sample_sd']:.{digits}f}"

    table = [("Samples", *[str(groups[c]["samples"]) for c in CONDITIONS]),
             ("Span (s)", *[f"{groups[c]['duration_span_s']:.6f}" for c in CONDITIONS])]
    for key, label in [("theta_deg", "theta / ellipse azimuth (deg)"), ("eta_deg", "eta / signed ellipticity (deg)"),
                       ("abs_eta_deg", "absolute ellipticity (deg)"), ("minor_major_ratio", "minor/major ellipse axis ratio"),
                       ("s1", "S1 orientation"), ("s2", "S2 orientation"), ("s3", "S3 orientation"), ("dop", "Raw DOP")]:
        table.append((label, *[m(c, key) for c in CONDITIONS]))
    table.append(("Length of mean orientation vector", *[f"{groups[c]['mean_orientation_vector_length']:.9f}" for c in CONDITIONS]))
    static_axes = []
    for pole in ("+S1", "-S1", "+S2", "-S2", "+S3", "-S3"):
        static_axes.append((pole, *[next(row["angle_of_static_mean_direction_deg"] for row in axes_data if row["condition"] == c and row["axis"] == pole) for c in CONDITIONS[:2]]))
    both = groups["both_paths"]
    notes = [
        "All values are mean +/- sample SD (N-1), not uncertainty of the mean or absolute accuracy.",
        "External phi1 drive during A+B is operator-supplied context; no drive waveform, phase, or frequency is inferred.",
        "A/B are H-like/V-like in the recorded PAX frame (+S1/-S1); no axis rotation or model transfer is applied.",
        "A+B averages describe time occupancy of a moving state; signed ellipticity can cancel. No single ellipse represents it.",
        "CSV Stokes are reconstructed unit directions. Their mean length is not the PAX DOP or an intensity-weighted DOP.",
        f"Raw DOP >1: A {groups['path_a_only']['dop_above_one_count']}/241, B {groups['path_b_only']['dop_above_one_count']}/241. Retained unchanged; absolute accuracy is not established.",
        "Run metadata says PAX at C; catalog setup says final output. Location was not independently verified in this report.",
        "No fitting, row rejection, smoothing, detrending, drive reconstruction, DOP clipping, or model updates."
    ]
    text = "# Measured polarization: A, B, and externally driven A+B\n\n"
    text += "Source: `../data.csv`. All 723 rows retained. Each condition spans about 60 s at 4 samples/s.\n\n"
    text += "## Direct averages\n\nMean ± sample SD; degree values are ellipse angles unless marked Poincare.\n\n"
    text += "| Quantity | A only (H-like) | B only (V-like) | A+B (moving) |\n| --- | --- | --- | --- |\n"
    text += "".join("| " + " | ".join(row) + " |\n" for row in table)
    text += "\n## Static-state axis distances\n\nAngles of normalized mean orientation vectors to signed Stokes poles, in **Poincare degrees**. These are not ellipse-angle rotations. H/V correspond to +S1/-S1 in this coordinate convention.\n\n"
    text += "| Pole | A only | B only |\n| --- | --- | --- |\n"
    text += "".join(f"| {pole} | {a:.6f} | {b:.6f} |\n" for pole, a, b in static_axes)
    pair = result["static_pair"]
    text += f"\nA–B mean-direction separation: **{pair['poincare_separation_deg']:.6f}°**; departure from antipodal: **{pair['departure_from_antipodal_deg']:.6f}°**.\n"
    text += "\n## Driven A+B and the S1=0 circle\n\n"
    text += f"S1 = **{m('both_paths', 's1')}**; observed range **{both['measurements']['s1']['min']:.6f} to {both['measurements']['s1']['max']:.6f}**.\n\n"
    text += f"Signed latitude from S1=0 = **{m('both_paths', 's1_circle_latitude_deg')}°**; RMS distance **{both['s1_circle_latitude_rms_deg']:.6f}°**; range **{both['measurements']['s1_circle_latitude_deg']['min']:.6f} to {both['measurements']['s1_circle_latitude_deg']['max']:.6f}°**.\n\n"
    text += f"S2–S3 radius = **{m('both_paths', 's2s3_radius')}**. The samples lie near, but not exactly on, the S1=0 circle. The shortest arc containing sampled `atan2(S3,S2)` angles is **{both['sampled_around_s1_covering_arc_deg']:.6f}°**, leaving a largest unsampled gap of **{both['largest_unsampled_around_s1_gap_deg']:.6f}°**. This is sampled coverage, not the number or extent of rotations. No phase unwrapping is used.\n"
    text += "\n## Definitions and limits\n\n" + "\n".join(f"- {note}" for note in notes)
    text += "\n- Ellipse minor/major ratio is `abs(tan(eta))`, computed per sample before averaging; signed ratio is also in `statistics.csv`.\n"
    text += "- Axis distances are `acos(±s_j/||s||)`; circle latitude is `asin(s1)`. Stokes use twice the ellipse angles.\n"
    text += f"- Coordinate convention: [Thorlabs Poincare-sphere reference]({REFERENCE}); acquisition daemon declares theta/eta in radians. No vendor accuracy is assumed.\n"
    text += "\n## Files and repeatability\n\n`statistics.csv`: per-condition means, SD, extrema. `axis-angles.csv`: every signed-axis distance distribution plus static mean-direction angles. `block-means.csv`: six contiguous approximately 10 s blocks per condition; compare their means for slow variation without fitting. `summary.json`: full numerical summary, formulas, checks, source/script hashes and versions. `report.pdf`, `static-paths.png`, `combined-paths.png`: figures.\n\nRebuild offline with `python path/to/polarization-report/analyze.py`. Only files inside this analysis directory are regenerated; original CSV, recipe, run status and report remain unchanged.\n"
    (HERE / "summary.md").write_text(text)

    with PdfPages(HERE / "report.pdf") as pdf:
        fig = plt.figure(figsize=(11.7, 8.3))
        fig.text(.05, .95, "Measured polarization — 241 samples / ~60 s per condition", fontsize=16, weight="bold")
        fig.text(.05, .91, "Arithmetic mean +/- sample SD; no fits. A+B externally driven. Values are in the recorded PAX frame.", fontsize=10)
        ax = fig.add_axes([.04, .48, .92, .39]); ax.axis("off")
        tab = ax.table(cellText=table, colLabels=["Quantity", "A only", "B only", "A+B (moving)"], cellLoc="left", bbox=[0, 0, 1, 1], colWidths=[.31,.23,.23,.23])
        tab.auto_set_font_size(False); tab.set_fontsize(8)
        for i, note in enumerate(notes):
            fig.text(.05, .43 - .032*i, note, fontsize=8)
        fig.text(.05, .08, "Full precision, axis distances, block means and formulas: summary.md / statistics.csv / axis-angles.csv / block-means.csv", fontsize=8)
        pdf.savefig(fig); plt.close(fig)

        fig, axs = plt.subplots(2, 2, figsize=(11.7, 8.3), constrained_layout=True)
        fig.suptitle("Static paths: measured orientation and ellipticity", fontsize=16)
        for col, (condition, label) in enumerate(zip(CONDITIONS[:2], LABELS[:2])):
            a = arrays[condition]
            # B's -87 degree azimuth is compared with the equivalent V azimuth -90.
            offset = a["theta_deg"] - (0 if col == 0 else -90)
            axs[0,col].scatter(a["elapsed_s"], offset, s=6)
            axs[0,col].axhline(offset.mean(), color="black", lw=.8, label="Arithmetic mean")
            axs[0,col].set(title=label, ylabel="Azimuth offset from H / V (deg)")
            axs[1,col].scatter(a["elapsed_s"], a["eta_deg"], s=6)
            axs[1,col].axhline(a["eta_deg"].mean(), color="black", lw=.8)
            axs[1,col].set(ylabel="Signed ellipticity eta (deg)")
        for ax in axs.flat:
            ax.set_xlabel("Recorded elapsed time (s)"); ax.grid(alpha=.2); ax.ticklabel_format(useOffset=False, axis="y")
        fig.savefig(HERE / "static-paths.png", dpi=160)
        pdf.savefig(fig); plt.close(fig)

        a = arrays["both_paths"]
        fig, axs = plt.subplots(2, 2, figsize=(11.7, 8.3), constrained_layout=True)
        fig.suptitle("A+B: sampled movement near the S1=0 circle (external phi1 drive)", fontsize=15)
        axs[0,0].scatter(a["elapsed_s"], a["s1"], s=8)
        axs[0,0].axhline(0, color="black", ls="--", lw=.8, label="S1=0 reference")
        axs[0,0].axhline(a["s1"].mean(), color="tab:orange", label="Measured mean")
        axs[0,0].set(xlabel="Recorded elapsed time (s)", ylabel="S1"); axs[0,0].legend(fontsize=8)
        phi = np.linspace(0, 2*np.pi, 361)
        axs[0,1].plot(np.cos(phi), np.sin(phi), "k--", lw=.8, label="Ideal S1=0 circle")
        scatter = axs[0,1].scatter(a["s2"], a["s3"], c=a["elapsed_s"], s=14, cmap="viridis")
        axs[0,1].set(xlabel="S2", ylabel="S3", aspect="equal")
        fig.colorbar(scatter, ax=axs[0,1], label="Recorded elapsed time (s)")
        axs[0,1].legend(fontsize=8)
        axs[1,0].scatter(a["elapsed_s"], a["eta_deg"], s=8)
        axs[1,0].set(xlabel="Recorded elapsed time (s)", ylabel="Signed ellipticity eta (deg)")
        axs[1,1].scatter(a["elapsed_s"], a["around_s1_deg"], s=8)
        axs[1,1].set(xlabel="Recorded elapsed time (s)", ylabel="atan2(S3,S2), wrapped (deg)", ylim=(-185,185))
        for ax in axs.flat: ax.grid(alpha=.2)
        fig.savefig(HERE / "combined-paths.png", dpi=160)
        pdf.savefig(fig); plt.close(fig)

        fig = plt.figure(figsize=(11.7, 8.3))
        fig.text(.08, .91, "Static mean-state axis distances", fontsize=18, weight="bold")
        fig.text(.08, .86, "Poincare degrees; unit direction of the averaged Stokes vector. H/V reference = +S1/-S1.", fontsize=10)
        ax = fig.add_axes([.1,.41,.8,.37]); ax.axis("off")
        tab = ax.table(cellText=[[pole,f"{x:.6f}",f"{y:.6f}"] for pole,x,y in static_axes], colLabels=["Signed pole", "A only", "B only"], bbox=[0,0,1,1])
        tab.auto_set_font_size(False); tab.set_fontsize(12)
        fig.text(.1,.34,f"A–B separation: {pair['poincare_separation_deg']:.6f} deg; departure from antipodal: {pair['departure_from_antipodal_deg']:.6f} deg.",fontsize=11)
        fig.text(.1,.28,f"A+B latitude from S1=0: {m('both_paths','s1_circle_latitude_deg')} deg (sample SD).",fontsize=11)
        fig.text(.1,.23,f"Sampled around-S1 covering arc: {both['sampled_around_s1_covering_arc_deg']:.3f} deg; no rotation count inferred.",fontsize=11)
        fig.text(.1,.15,"Mean A+B direction is intentionally not presented as a static state; see its trajectory and per-sample axis statistics.",fontsize=9)
        pdf.savefig(fig); plt.close(fig)
    print(text.split("## Definitions and limits")[0])


if __name__ == "__main__":
    report(*analyze())
