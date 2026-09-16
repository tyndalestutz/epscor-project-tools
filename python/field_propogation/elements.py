"""Jones elements in the repository's (x, y), transmitted-first convention.

All angles are radians. Transmission factors multiply field amplitude.
Matrices act on column vectors; a sequence J1 then J2 is J2 @ J1.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class Retarder:
    """Lossless linear retarder, retaining the historical arm-axis convention."""

    axis_rad: float = 0.0
    retardance_rad: float = 0.0

    def matrix(self) -> np.ndarray:
        return diattenuator(1.0, 1.0, -self.retardance_rad / 2,
                            self.retardance_rad / 2, self.axis_rad)


def diattenuator(amplitude_x: float, amplitude_y: float,
                 phase_x_rad: float = 0.0, phase_y_rad: float = 0.0,
                 axis_rad: float = 0.0) -> np.ndarray:
    """Rotated element with independently measured principal-axis amplitudes/phases.

    J = R(axis).T diag(ax exp(i px), ay exp(i py)) R(axis).
    Positive axis follows the existing Retarder convention, not an inferred
    mechanical rotation convention for a particular bench mount.
    """
    c, s = np.cos(axis_rad), np.sin(axis_rad)
    rotation = np.array(((c, -s), (s, c)))
    diagonal = np.diag((amplitude_x * np.exp(1j * phase_x_rad),
                        amplitude_y * np.exp(1j * phase_y_rad)))
    return rotation.T @ diagonal @ rotation


def pbs_matrices(leakage_rad: float) -> tuple[np.ndarray, np.ndarray]:
    """One illuminated PBS input; lossless leakage into two outgoing paths."""
    c, s = np.cos(leakage_rad), np.sin(leakage_rad)
    return np.diag((c, 1j * s)), np.diag((1j * s, c))


def npbs_matrices(mixing_x_rad: float, mixing_y_rad: float | None = None
                  ) -> tuple[np.ndarray, np.ndarray]:
    """Return T,R for the unitary two-port scattering matrix [[T,R],[R,T]]."""
    angles = np.array((mixing_x_rad, mixing_x_rad if mixing_y_rad is None else mixing_y_rad))
    return np.diag(np.cos(angles)), 1j * np.diag(np.sin(angles))
