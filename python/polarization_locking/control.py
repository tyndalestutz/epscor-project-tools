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


def hybrid_phases_from_sphere_angles(angles: SphereAngles) -> Tuple[float, float]:
    """Map the supplied hybrid-MZ model's canonical branch to phase angles.

    u = phi_eom + pi and v = phi_pzt, with phi_pzt on [0, pi].
    """
    return wrap_angle(angles.u - math.pi), angles.v


def hybrid_phase_error(
    current: PolarizationState | Tuple[float, float],
    target: PolarizationState | Tuple[float, float],
    *,
    pole_tolerance: float = 1e-6,
) -> Tuple[float, float]:
    """Return (delta_phi_eom, delta_phi_pzt) for a rough move to target.

    Azimuth is not observable at either S1 pole; no arbitrary EOM command is
    issued in that singular case.
    """
    current_uv = pax_to_sphere_angles(current)
    target_uv = pax_to_sphere_angles(target)
    _, current_pzt = hybrid_phases_from_sphere_angles(current_uv)
    _, target_pzt = hybrid_phases_from_sphere_angles(target_uv)
    at_pole = (
        abs(math.sin(current_uv.v)) <= pole_tolerance
        or abs(math.sin(target_uv.v)) <= pole_tolerance
    )
    delta_eom = 0.0 if at_pole else wrapped_angle_difference(current_uv.u, target_uv.u)
    return delta_eom, target_pzt - current_pzt


def phase_error_to_voltage(
    delta_phi_eom: float,
    delta_phi_pzt: float,
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
    return phi1_v_lambda * delta_phi_eom * scale, phi2_v_lambda * delta_phi_pzt * scale


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


def rough_voltage_correction(
    current: PolarizationState | Tuple[float, float],
    target: PolarizationState | Tuple[float, float],
    *,
    phi1_v_lambda: float,
    phi2_v_lambda: float,
    pole_tolerance: float = 1e-6,
) -> Tuple[float, float]:
    """Estimate the EOM/PZT voltage increment from a PAX state and target."""
    return phase_error_to_voltage(
        *hybrid_phase_error(current, target, pole_tolerance=pole_tolerance),
        phi1_v_lambda=phi1_v_lambda,
        phi2_v_lambda=phi2_v_lambda,
    )


def stokes_vector(state: PolarizationState | Tuple[float, float]) -> np.ndarray:
    if isinstance(state, PolarizationState):
        theta, eta = state.theta, state.eta
    else:
        theta, eta = state
    return np.array(poincare_coordinates(theta, eta), dtype=float)


def polarization_error(current: PolarizationState, target: PolarizationState) -> np.ndarray:
    """Return a Stokes-space error vector for feedback control.

    This is a more natural lock-loop error than a simple theta/eta difference
    because it tracks the polarization state on the Poincaré sphere.
    """
    return stokes_vector(target) - stokes_vector(current)


def theta_eta_error(current: PolarizationState, target: PolarizationState) -> np.ndarray:
    """Return the direct PAX angle error used by the first lock loop.

    This is intentionally simple and transparent: the controller uses the
    measured theta and eta directly as the feedback signal and maps them to
    actuator voltages with a small linear law. ** this is the current issue,
    our error to voltage mapping is unreliable at best.
    """
    return np.array([target.theta - current.theta, target.eta - current.eta], dtype=float)


def angular_error(current: PolarizationState, target: PolarizationState) -> float:
    current_stokes = stokes_vector(current)
    target_stokes = stokes_vector(target)
    cosine = np.clip(float(np.dot(current_stokes, target_stokes)), -1.0, 1.0)
    return float(math.acos(cosine))


def fit_voltage_polarization_model(voltage_pairs: np.ndarray, stokes_vectors: np.ndarray) -> np.ndarray:
    """Fit a local affine model from voltage pair to Stokes coordinates."""
    if len(voltage_pairs) != len(stokes_vectors):
        raise ValueError("voltage_pairs and stokes_vectors must have the same length")

    design = np.column_stack([voltage_pairs[:, 0], voltage_pairs[:, 1], np.ones(len(voltage_pairs))])
    return np.linalg.lstsq(design, stokes_vectors, rcond=None)[0]


def predict_stokes(coeffs: np.ndarray, v1: float, v2: float) -> np.ndarray:
    design = np.array([v1, v2, 1.0], dtype=float)
    return design @ coeffs


def voltage_correction(coeffs: np.ndarray, delta_stokes: np.ndarray) -> Tuple[float, float]:
    """Invert the local affine model for a small polarization change."""
    jacobian = coeffs[:2].T
    delta_v = np.linalg.pinv(jacobian) @ np.asarray(delta_stokes, dtype=float)
    return float(delta_v[0]), float(delta_v[1])


class PIDController:
    def __init__(self, kp: float, ki: float = 0.0, kd: float = 0.0) -> None:
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self._integral: np.ndarray | None = None
        self._previous_error: np.ndarray | None = None

    def reset(self) -> None:
        self._integral = None
        self._previous_error = None

    def update(self, error: np.ndarray | Tuple[float, float, float], dt: float) -> np.ndarray:
        error_array = np.asarray(error, dtype=float)
        if self._integral is None:
            self._integral = np.zeros_like(error_array)
            self._previous_error = np.zeros_like(error_array)

        self._integral += error_array * dt
        derivative = np.zeros_like(error_array)
        if dt > 0:
            derivative = (error_array - self._previous_error) / dt
        self._previous_error = error_array

        return self.kp * error_array + self.ki * self._integral + self.kd * derivative
