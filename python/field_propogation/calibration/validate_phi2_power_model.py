#!/usr/bin/env python3
"""One-command state-matched phi2-amplitude calibration and validation.

Use immediately after acquiring a high-visibility ``field-model-calibration``.
It fits that raw A/B/both data, fits a raw both-path phi1 fringe map, and
compares the resulting numerical phi2-amplitude prediction with the measured
fringe amplitudes for both the PD and PAX output ports.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

try:
    from .fit_phi1_fringe_map import DETECTORS as FRINGE_DETECTORS, _fit as fit_fringes, _load as load_fringe_rows, _plot as plot_fringes
    from .fit_phi2_interference import run_effective_analyzer_fit
    from .predict_phi2_amplitude import compare
except ImportError:  # pragma: no cover - support direct script execution
    from fit_phi1_fringe_map import DETECTORS as FRINGE_DETECTORS, _fit as fit_fringes, _load as load_fringe_rows, _plot as plot_fringes
    from fit_phi2_interference import run_effective_analyzer_fit
    from predict_phi2_amplitude import compare


def fit_raw_fringe_map(input_csv: Path, output_dir: Path, phi2_vlambda_rp: float) -> Path:
    """Fit raw fringe-map data and return its fitted CSV path."""
    rows = load_fringe_rows(input_csv)
    points = [point for detector in FRINGE_DETECTORS for point in fit_fringes(rows, detector, phi2_vlambda_rp)]
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "phi1-fringe-map-fit.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(points[0])))
        writer.writeheader()
        writer.writerows(asdict(point) for point in points)
    plot_fringes(points, output_dir / "phi1-fringe-map-fit.png")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("field_model_calibration_csv", type=Path, help="Raw data.csv from field-model-calibration.")
    parser.add_argument("phi1_fringe_map_csv", type=Path, help="Raw data.csv from a high-visibility phi1-fringe-map.")
    parser.add_argument("--phi1-vlambda-rp", type=float, help="Override metadata/default phi1 RP V_lambda.")
    parser.add_argument("--phi2-vlambda-rp", type=float, help="Override metadata/default phi2 RP V_lambda.")
    parser.add_argument("--output-dir", type=Path, help="Defaults to <field calibration>/state-matched-model-validation.")
    args = parser.parse_args()
    context_path = args.field_model_calibration_csv.with_name("field-model-context.json")
    context: dict[str, object] = {}
    if context_path.exists():
        context = json.loads(context_path.read_text())
    rp_vlambda = context.get("rp_vlambda_v", {}) if isinstance(context.get("rp_vlambda_v", {}), dict) else {}
    phi1_vlambda_rp = args.phi1_vlambda_rp if args.phi1_vlambda_rp is not None else float(rp_vlambda.get("phi1", 12.2 / 16.875))
    phi2_vlambda_rp = args.phi2_vlambda_rp if args.phi2_vlambda_rp is not None else float(rp_vlambda.get("phi2", 0.2))
    output = args.output_dir or args.field_model_calibration_csv.parent / "state-matched-model-validation"
    analyzer_dir = output / "effective-analyzer-fit"
    fringe_dir = output / "phi1-fringe-fit"
    run_effective_analyzer_fit(
        args.field_model_calibration_csv, analyzer_dir,
        phi1_vlambda_rp=phi1_vlambda_rp, phi2_vlambda_rp=phi2_vlambda_rp,
    )
    fringe_csv = fit_raw_fringe_map(args.phi1_fringe_map_csv, fringe_dir, phi2_vlambda_rp)
    comparisons = compare(analyzer_dir / "effective-analyzer-fit.csv", fringe_csv, output / "phi2-amplitude-prediction")
    print(f"Wrote state-matched model validation to {output}")
    print(f"Used phi1/phi2 RP V_lambda = {phi1_vlambda_rp:.8g}/{phi2_vlambda_rp:.8g} V")
    for detector in FRINGE_DETECTORS:
        errors = [abs(item.contrast_error) for item in comparisons if item.detector == detector]
        print(f"{detector}: phi2-contrast MAE={sum(errors) / len(errors):.4f}")


if __name__ == "__main__":
    main()
