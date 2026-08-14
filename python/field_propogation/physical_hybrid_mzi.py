#!/usr/bin/env python3
"""Constrained physical Jones-network model of the hybrid two-NPBS MZI.

Unlike the effective-analyzer fit, this module propagates complex Jones fields
through the actual named optical network and returns the fundamental final-port
fields ``E`` and ``F``. It is a fit-ready model family, not a controller.

The default parameters reduce exactly to the ideal transmitted-first reference
in :mod:`ideal_hybrid_mzi`:

    E = [a_x exp(i(delta+phi1)) (exp(i phi2)-1)/2,
         i a_y (exp(i phi2)+1)/2]^T.

Nonideal parameters are deliberately constrained to physically interpretable
effects: PBS polarization leakage, unequal NPBS splitting, scalar arm loss,
and rotated linear retarders in the named A/B/C/D arms. An arbitrary 2x2
matrix at every component would be unidentifiable from the current data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np


Port = Literal["E", "F"]


@dataclass(frozen=True)
class Retarder:
    """Lossless linear retarder: axis angle and relative phase in radians."""

    axis_rad: float = 0.0
    retardance_rad: float = 0.0

    def matrix(self) -> np.ndarray:
        c, s = np.cos(self.axis_rad), np.sin(self.axis_rad)
        rotate = np.array(((c, -s), (s, c)), dtype=complex)
        phase = np.diag((np.exp(-0.5j * self.retardance_rad), np.exp(0.5j * self.retardance_rad)))
        return rotate.T @ phase @ rotate


@dataclass(frozen=True)
class PhysicalParameters:
    """Interpretable parameters of the propagation network.

    ``pbs_leakage_rad=0`` sends x to A and y to B exactly. A nonzero value is
    a lossless polarization leakage angle: each PBS output then contains a
    small coherent part of the nominally rejected polarization.

    ``npbs*_mixing_rad=pi/4`` is a 50/50 splitter. The standard reflected
    amplitude is ``i sin(theta)`` and transmitted amplitude is ``cos(theta)``.
    Scalar arm losses apply to optical field amplitude, not intensity.
    """

    ax: float = 1.0
    ay: float = 1.0
    delta_rad: float = 0.0
    pbs_leakage_rad: float = 0.0
    npbs1_mixing_rad: float = np.pi / 4.0
    npbs2_mixing_rad: float = np.pi / 4.0
    loss_a: float = 1.0
    loss_b: float = 1.0
    loss_c: float = 1.0
    loss_d: float = 1.0
    retarder_a: Retarder = field(default_factory=Retarder)
    retarder_b: Retarder = field(default_factory=Retarder)
    retarder_c: Retarder = field(default_factory=Retarder)
    retarder_d: Retarder = field(default_factory=Retarder)


def phase_from_rp_voltage(command_v: float | np.ndarray, v_lambda_rp_v: float, offset_rad: float = 0.0) -> np.ndarray:
    """Convert an RP command to phase with a separately declared V_lambda."""
    if v_lambda_rp_v <= 0.0:
        raise ValueError("v_lambda_rp_v must be positive")
    return 2.0 * np.pi * np.asarray(command_v, dtype=float) / v_lambda_rp_v + offset_rad


def _npbs(field_a: np.ndarray, field_b: np.ndarray, mixing_rad: float) -> tuple[np.ndarray, np.ndarray]:
    """Return transmitted-first and reflected-second Jones fields."""
    transmitted = np.cos(mixing_rad) * field_a + 1j * np.sin(mixing_rad) * field_b
    reflected = 1j * np.sin(mixing_rad) * field_a + np.cos(mixing_rad) * field_b
    return transmitted, reflected


def _pbs_split(input_field: np.ndarray, leakage_rad: float) -> tuple[np.ndarray, np.ndarray]:
    """Return PBS-transmitted A and reflected B, including lossless leakage."""
    c, s = np.cos(leakage_rad), np.sin(leakage_rad)
    arm_a = np.array((c * input_field[0], 1j * s * input_field[1]), dtype=complex)
    arm_b = np.array((1j * s * input_field[0], c * input_field[1]), dtype=complex)
    return arm_a, arm_b


def intensity(field: np.ndarray) -> float:
    """Total power in an orthogonal Jones basis, in arbitrary field units."""
    return float(np.vdot(field, field).real)


def normalized_stokes(field: np.ndarray) -> np.ndarray:
    """Return [S1,S2,S3] using the PAX / locking convention."""
    ex, ey = field
    s0 = intensity(field)
    if s0 <= 0.0:
        raise ValueError("Stokes coordinates are undefined for a zero field")
    return np.asarray((
        (abs(ey) ** 2 - abs(ex) ** 2) / s0,
        2.0 * np.real(ex * np.conj(ey)) / s0,
        2.0 * np.imag(ex * np.conj(ey)) / s0,
    ), dtype=float)


def propagate(phi1_rad: float, phi2_rad: float, parameters: PhysicalParameters = PhysicalParameters()) -> dict[str, np.ndarray]:
    """Propagate fields through named physical checkpoints A--F.

    Order: Ein -> PBS(A,B) -> phi1 on A -> NPBS1(C,D) -> phi2 on C ->
    NPBS2(E,F). ``C``/``E`` are transmitted outputs and ``D``/``F`` reflected.
    """
    ein = np.asarray((parameters.ax * np.exp(1j * parameters.delta_rad), parameters.ay), dtype=complex)
    a_before, b_before = _pbs_split(ein, parameters.pbs_leakage_rad)
    a_after = parameters.loss_a * parameters.retarder_a.matrix() @ (np.exp(1j * phi1_rad) * a_before)
    b_after = parameters.loss_b * parameters.retarder_b.matrix() @ b_before
    c_before, d_before = _npbs(a_after, b_after, parameters.npbs1_mixing_rad)
    c_after = parameters.loss_c * parameters.retarder_c.matrix() @ (np.exp(1j * phi2_rad) * c_before)
    d_after = parameters.loss_d * parameters.retarder_d.matrix() @ d_before
    port_e, port_f = _npbs(c_after, d_after, parameters.npbs2_mixing_rad)
    return {
        "Ein": ein,
        "A_before_phi1": a_before,
        "A_after_phi1": a_after,
        "B": b_after,
        "C": c_before,
        "C_after_phi2": c_after,
        "D": d_after,
        "E": port_e,
        "F": port_f,
    }


def port_observables(phi1_rad: float, phi2_rad: float, parameters: PhysicalParameters = PhysicalParameters()) -> dict[str, object]:
    """Return final fields, powers, and normalized Stokes coordinates."""
    fields = propagate(phi1_rad, phi2_rad, parameters)
    return {
        "fields": fields,
        "I_E": intensity(fields["E"]),
        "I_F": intensity(fields["F"]),
        "S_E": normalized_stokes(fields["E"]),
        "S_F": normalized_stokes(fields["F"]),
    }


def fringe_coefficients(
    phi1_rad: float,
    parameters: PhysicalParameters = PhysicalParameters(),
    *,
    port: Port = "E",
    samples: int = 101,
) -> dict[str, float]:
    """Numerically fit physical port power to B+C cos(phi2)+D sin(phi2)."""
    if samples < 4:
        raise ValueError("At least four samples are required")
    phi2 = np.linspace(0.0, 2.0 * np.pi, samples, endpoint=False)
    power = np.asarray([intensity(propagate(phi1_rad, phase, parameters)[port]) for phase in phi2])
    design = np.column_stack((np.ones_like(phi2), np.cos(phi2), np.sin(phi2)))
    baseline, cosine, sine = np.linalg.lstsq(design, power, rcond=None)[0]
    amplitude = float(np.hypot(cosine, sine))
    return {
        "baseline": float(baseline), "cos_phi2": float(cosine), "sin_phi2": float(sine),
        "amplitude": amplitude, "contrast": amplitude / float(baseline),
        "phase_rad": float(np.arctan2(-sine, cosine)),
    }


def _verify_ideal_reduction() -> None:
    """Check the network returns constant ideal power and the expected Stokes state."""
    phi1, phi2 = 0.73, 1.18
    result = port_observables(phi1, phi2)
    expected_e = np.asarray((np.exp(1j * phi1) * (np.exp(1j * phi2) - 1.0) / 2.0, 1j * (np.exp(1j * phi2) + 1.0) / 2.0))
    assert np.allclose(result["fields"]["E"], expected_e)
    assert np.isclose(result["I_E"], 1.0)
    assert np.isclose(result["I_F"], 1.0)
    assert np.allclose(result["S_E"], (np.cos(phi2), np.sin(phi2) * np.cos(phi1), np.sin(phi2) * np.sin(phi1)))
    assert np.isclose(fringe_coefficients(phi1)["contrast"], 0.0, atol=1e-12)


if __name__ == "__main__":
    _verify_ideal_reduction()
    print("Physical Jones network: ideal reduction checks passed.")
