from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np


@dataclass
class PolarizationState:
    """PAX ellipse angles in radians (theta = azimuth, eta = ellipticity)."""

    theta: float
    eta: float

    def as_tuple(self) -> Tuple[float, float]:
        return (self.theta, self.eta)


def poincare_coordinates(theta: float, eta: float) -> Tuple[float, float, float]:
    """Convert the PAX theta/eta convention to normalized Stokes coordinates."""
    s1 = math.cos(2 * eta) * math.cos(2 * theta)
    s2 = math.cos(2 * eta) * math.sin(2 * theta)
    s3 = math.sin(2 * eta)
    return s1, s2, s3


@dataclass(frozen=True)
class SphereAngles:
    """Poincare angles with S1 as polar axis: u is azimuth and v is polar."""

    u: float
    v: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.v <= math.pi:
            raise ValueError("v must lie in [0, pi]")
        object.__setattr__(self, "u", wrap_angle(self.u))


def wrap_angle(angle: float) -> float:
    """Normalize an angle to [-pi, pi)."""
    return (float(angle) + math.pi) % (2 * math.pi) - math.pi


def wrapped_angle_difference(current: float, target: float) -> float:
    """Shortest signed rotation from current to target."""
    return wrap_angle(target - current)


def sphere_angles_from_stokes(s1: float, s2: float, s3: float) -> SphereAngles:
    """Return S1-polar sphere angles for a normalized Stokes vector."""
    stokes = np.asarray([s1, s2, s3], dtype=float)
    norm = float(np.linalg.norm(stokes))
    if norm == 0.0:
        raise ValueError("Stokes vector must not be zero")
    s1, s2, s3 = stokes / norm
    return SphereAngles(
        u=wrap_angle(math.atan2(float(s3), float(s2))),
        v=float(math.acos(float(np.clip(s1, -1.0, 1.0)))),
    )


def pax_to_sphere_angles(state: PolarizationState | Tuple[float, float]) -> SphereAngles:
    """Convert PAX theta/eta to hybrid-MZ (u, v) coordinates.

    PAX uses S = (cos(2eta)cos(2theta), cos(2eta)sin(2theta), sin(2eta)),
    so its reported angles must not be subtracted as though they were u and v.
    """
    return sphere_angles_from_stokes(*stokes_vector(state))


def sphere_angle_error(
    current: SphereAngles,
    target: SphereAngles,
    *,
    pole_tolerance: float = 1e-6,
) -> Tuple[float, float]:
    """Return (delta_phi1, delta_phi2) for a rough move to target.

    Azimuth is not observable at either S1 pole; no arbitrary EOM command is
    issued in that singular case.
    """
    at_pole = (
        abs(math.sin(current.v)) <= pole_tolerance
        or abs(math.sin(target.v)) <= pole_tolerance
    )
    delta_phi1 = 0.0 if at_pole else wrapped_angle_difference(current.u, target.u)
    return delta_phi1, target.v - current.v


def phase_error_to_voltage(
    delta_phi1: float,
    delta_phi2: float,
    *,
    phi1_v_lambda: float,
    phi2_v_lambda: float,
) -> Tuple[float, float]:
    """Convert phase increments to actuator-voltage increments.

    V_lambda is signed to encode actuator polarity and has magnitude equal to
    the voltage that produces a 2*pi phase shift.
    """
    if phi1_v_lambda == 0 or phi2_v_lambda == 0:
        raise ValueError("V_lambda values must be non-zero")
    scale = 1.0 / (2 * math.pi)
    return phi1_v_lambda * delta_phi1 * scale, phi2_v_lambda * delta_phi2 * scale


def actuator_voltage_to_rp_voltage(
    delta_phi1_actuator_voltage: float,
    delta_phi2_actuator_voltage: float,
    *,
    phi1_actuator_volts_per_rp_volt: float,
    phi2_actuator_volts_per_rp_volt: float,
) -> Tuple[float, float]:
    """Convert actuator-voltage increments into Red Pitaya output increments."""
    if phi1_actuator_volts_per_rp_volt == 0 or phi2_actuator_volts_per_rp_volt == 0:
        raise ValueError("Actuator-volts-per-RP-volt gains must be non-zero")
    return (
        delta_phi1_actuator_voltage / phi1_actuator_volts_per_rp_volt,
        delta_phi2_actuator_voltage / phi2_actuator_volts_per_rp_volt,
    )


def phase_error_to_rp_voltage(
    delta_phi1: float,
    delta_phi2: float,
    *,
    phi1_v_lambda: float,
    phi2_v_lambda: float,
    phi1_actuator_volts_per_rp_volt: float,
    phi2_actuator_volts_per_rp_volt: float,
) -> Tuple[float, float]:
    """Convert hybrid-phase increments to the required RP-output increments."""
    return actuator_voltage_to_rp_voltage(
        *phase_error_to_voltage(
            delta_phi1,
            delta_phi2,
            phi1_v_lambda=phi1_v_lambda,
            phi2_v_lambda=phi2_v_lambda,
        ),
        phi1_actuator_volts_per_rp_volt=phi1_actuator_volts_per_rp_volt,
        phi2_actuator_volts_per_rp_volt=phi2_actuator_volts_per_rp_volt,
    )


def stokes_vector(state: PolarizationState | Tuple[float, float]) -> np.ndarray:
    if isinstance(state, PolarizationState):
        theta, eta = state.theta, state.eta
    else:
        theta, eta = state
    return np.array(poincare_coordinates(theta, eta), dtype=float)
