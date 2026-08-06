from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple


@dataclass
class PolarizationState:
    theta: float
    eta: float

    def as_tuple(self) -> Tuple[float, float]:
        return (self.theta, self.eta)


def poincare_coordinates(theta: float, eta: float) -> Tuple[float, float, float]:
    s1 = math.cos(2 * eta) * math.cos(2 * theta)
    s2 = math.cos(2 * eta) * math.sin(2 * theta)
    s3 = math.sin(2 * eta)
    return s1, s2, s3


def polarization_error(current: PolarizationState, target: PolarizationState) -> Tuple[float, float]:
    """Return a simple angular error in the current coordinate frame.

    This is intentionally simple and should be treated as a placeholder for a
    calibrated actuator model.
    """
    d_theta = target.theta - current.theta
    d_eta = target.eta - current.eta
    return d_theta, d_eta


def error_to_voltage(error: Tuple[float, float], gains: Tuple[float, float]) -> Tuple[float, float]:
    d_theta, d_eta = error
    g1, g2 = gains
    return (g1 * d_theta, g2 * d_eta)
