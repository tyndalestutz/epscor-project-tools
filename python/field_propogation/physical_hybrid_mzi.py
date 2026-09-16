#!/usr/bin/env python3
"""Historical compatibility API for the former effective Jones-network fit.

Not an independently characterized physical model. Active acquisition uses
acquire.py and measured section matrices; this API is retained for regression
and archived fit reproduction only.

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
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np


if not __package__:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "field_propogation"

from .elements import Retarder
from .observables import intensity, normalized_stokes
from .configuration import from_dicts, read_json
from .propagation import compile_network

Port = Literal["E", "F"]


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


@lru_cache(maxsize=1)
def _compatibility_flow() -> dict:
    return read_json(Path(__file__).parent / "archive/effective_fits/configs/flows/hybrid_mzi_relative_fit.json")


@lru_cache(maxsize=128)
def _compiled_model(parameters: PhysicalParameters):
    """Reuse a compiled configuration across the many phase samples of a fit."""
    values = dict(
        ax=parameters.ax, ay=parameters.ay,
        pbs_leakage_rad=parameters.pbs_leakage_rad,
        npbs1_mixing_rad=parameters.npbs1_mixing_rad,
        npbs2_mixing_rad=parameters.npbs2_mixing_rad,
        phi1_offset_rad=0, phi2_offset_rad=0,
    )
    # The historical input delta is an x-only input phase, including PBS
    # leakage paths. Put it in the input field, not on arm A after the PBS.
    values["ax"] = parameters.ax * np.exp(1j * parameters.delta_rad)
    for arm in "abcd":
        retarder = getattr(parameters, "retarder_" + arm)
        values.update({"loss_" + arm: getattr(parameters, "loss_" + arm),
                       "axis_" + arm: retarder.axis_rad,
                       "retardance_" + arm: retarder.retardance_rad})
    config = from_dicts(_compatibility_flow(), dict(schema_version=1, values=values,
                       phase_values={"phi1": 0, "phi2": 0}))
    return compile_network(config)


def propagate(phi1_rad: float, phi2_rad: float, parameters: PhysicalParameters = PhysicalParameters()) -> dict[str, np.ndarray]:
    """Compatibility API backed by the shared configurable propagation engine.

    Order and checkpoint names retain the historical calibration contract.
    New studies should select a flow and parameter file with run_simulation.py.
    """
    fields = _compiled_model(parameters).evaluate({"phi1": phi1_rad, "phi2": phi2_rad}, checkpoints=True)
    return {name: fields[name] for name in (
        "Ein", "A_before_phi1", "A_after_phi1", "B", "C", "C_after_phi2", "D", "E", "F")}


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
