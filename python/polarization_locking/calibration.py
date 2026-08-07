#!/usr/bin/env python3
from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

try:
    from .config import PolarizationLockConfig
    from .control import pax_to_sphere_angles
    from .pax_interface import PAXController, PAXReading
    from .rp_interface import RPController
except ImportError:  # pragma: no cover - support direct execution
    from config import PolarizationLockConfig
    from control import pax_to_sphere_angles
    from pax_interface import PAXController, PAXReading
    from rp_interface import RPController


@dataclass
class SweepPoint:
    v1: float
    v2: float
    reading: PAXReading


class CalibrationSweep:
    def __init__(self, config: PolarizationLockConfig) -> None:
        self.config = config
        self.rp = RPController(config)
        self.pax = PAXController(config)
        self.points: List[SweepPoint] = []

    def connect(self) -> None:
        self.rp.connect()
        self.pax.connect()

    def disconnect(self) -> None:
        self.rp.set_output_zero()
        self.pax.disconnect()
        self.rp.disconnect()

    def run_grid_sweep(
        self,
        v1_values: Optional[List[float]] = None,
        v2_values: Optional[List[float]] = None,
        repeats: int = 3,
        settle_s: float = 0.2,
        output_file: Optional[str] = None,
    ) -> List[SweepPoint]:
        if v1_values is None:
            v1_values = [0.0, 0.25, 0.5, 0.75, 1.0]
        if v2_values is None:
            v2_values = [0.0, 0.25, 0.5, 0.75, 1.0]

        self.points = []
        for v1 in v1_values:
            for v2 in v2_values:
                for rep in range(repeats):
                    self.rp.set_output_voltage(v1, v2)
                    time.sleep(settle_s)
                    reading = self.pax.read_polarization()
                    self.points.append(SweepPoint(v1=v1, v2=v2, reading=reading))

        if output_file is not None:
            self.save_csv(output_file)

        return self.points

    def build_local_model(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        if not self.points:
            raise RuntimeError("No calibration points collected")

        grouped = {}
        for p in self.points:
            key = (p.v1, p.v2)
            grouped.setdefault(key, []).append(p)

        X = []
        Y = []
        for (v1, v2), items in grouped.items():
            values = np.array([[p.reading.s1, p.reading.s2, p.reading.s3] for p in items], dtype=float)
            mean = np.mean(values, axis=0)
            X.append([v1, v2])
            Y.append(mean)

        X = np.array(X, dtype=float)
        Y = np.array(Y, dtype=float)
        coeffs = fit_local_model(X, Y)
        return coeffs, X, Y

    def save_csv(self, output_file: str) -> None:
        path = Path(output_file)
        with path.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["v1", "v2", "theta", "eta", "s1", "s2", "s3", "dop", "u", "v"])
            for point in self.points:
                sphere = pax_to_sphere_angles((point.reading.theta, point.reading.eta))
                writer.writerow([
                    point.v1,
                    point.v2,
                    point.reading.theta,
                    point.reading.eta,
                    point.reading.s1,
                    point.reading.s2,
                    point.reading.s3,
                    point.reading.dop,
                    sphere.u,
                    sphere.v,
                ])

    def summarize_by_voltage(self) -> List[Tuple[float, float, np.ndarray, np.ndarray]]:
        if not self.points:
            raise RuntimeError("No calibration points collected")

        unique_pairs = sorted({(p.v1, p.v2) for p in self.points})
        summaries = []
        for v1, v2 in unique_pairs:
            subset = [p for p in self.points if p.v1 == v1 and p.v2 == v2]
            values = np.array([[p.reading.s1, p.reading.s2, p.reading.s3] for p in subset], dtype=float)
            mean = np.mean(values, axis=0)
            cov = np.cov(values.T) if len(values) > 1 else np.zeros((3, 3))
            summaries.append((v1, v2, mean, cov))
        return summaries

def fit_local_model(X: np.ndarray, Y: np.ndarray) -> np.ndarray:
    A = np.hstack([X, np.ones((len(X), 1))])
    return np.linalg.lstsq(A, Y, rcond=None)[0]


def predict_stokes(coeffs: np.ndarray, v1: float, v2: float) -> np.ndarray:
    design = np.array([v1, v2, 1.0], dtype=float)
    return design @ coeffs.T


def voltage_correction(coeffs: np.ndarray, delta_stokes: np.ndarray) -> Tuple[float, float]:
    jacobian = coeffs[:2, :].T
    delta_v = np.linalg.pinv(jacobian) @ delta_stokes
    return float(delta_v[0]), float(delta_v[1])


def load_calibration_model_from_csv(path: str | Path) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    path = Path(path)
    with path.open("r", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))

    if len(rows) < 2:
        raise ValueError("Calibration CSV is empty")

    header = rows[0]
    if header[:3] != ["v1", "v2", "theta"]:
        raise ValueError("Calibration CSV must start with v1,v2,theta columns")

    X = []
    Y = []
    for row in rows[1:]:
        if len(row) < 6:
            continue
        v1 = float(row[0])
        v2 = float(row[1])
        X.append([v1, v2])
        Y.append([float(row[4]), float(row[5]), float(row[6])])

    if not X:
        raise ValueError("No calibration rows were loaded")

    return fit_local_model(np.array(X, dtype=float), np.array(Y, dtype=float)), np.array(X, dtype=float), np.array(Y, dtype=float)
