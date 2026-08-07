import math
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from polarization_locking.control import (
    PolarizationState,
    SphereAngles,
    pax_to_sphere_angles,
    phase_error_to_rp_voltage,
    phase_error_to_voltage,
    sphere_angle_error,
)


def _pax_state_from_stokes(s1: float, s2: float, s3: float) -> PolarizationState:
    return PolarizationState(theta=0.5 * math.atan2(s2, s1), eta=0.5 * math.asin(s3))


def test_pax_converts_to_hybrid_mz_sphere_coordinates() -> None:
    phi1 = -0.8
    phi2 = 1.1
    state = _pax_state_from_stokes(
        math.cos(phi2),
        -math.sin(phi2) * math.cos(phi1),
        -math.sin(phi2) * math.sin(phi1),
    )

    angles = pax_to_sphere_angles(state)

    assert angles.v == pytest.approx(phi2)
    assert angles.u == pytest.approx(phi1 + math.pi)


def test_sphere_error_wraps_u_but_not_v() -> None:
    delta_phi1, delta_phi2 = sphere_angle_error(
        SphereAngles(math.pi - 0.1, 1.0),
        SphereAngles(-math.pi + 0.1, 1.3),
    )

    assert delta_phi1 == pytest.approx(0.2)
    assert delta_phi2 == pytest.approx(0.3)


def test_phi1_is_not_commanded_at_a_pole() -> None:
    delta_phi1, delta_phi2 = sphere_angle_error(SphereAngles(0.4, 0.0), SphereAngles(-1.2, 1.0))

    assert delta_phi1 == 0.0
    assert delta_phi2 == pytest.approx(1.0)


def test_phase_error_uses_the_measured_rp_to_actuator_chain() -> None:
    rp_phi1, rp_phi2 = phase_error_to_rp_voltage(
        2 * math.pi,
        2 * math.pi,
        phi1_v_lambda=11.0,
        phi2_v_lambda=30.0,
        phi1_actuator_volts_per_rp_volt=16.875,
        phi2_actuator_volts_per_rp_volt=150.0,
    )

    assert rp_phi1 == pytest.approx(11.0 / 16.875)
    assert rp_phi2 == pytest.approx(30.0 / 150.0)


def test_zero_v_lambda_is_rejected() -> None:
    with pytest.raises(ValueError, match="V_lambda"):
        phase_error_to_voltage(0.1, 0.2, phi1_v_lambda=0.0, phi2_v_lambda=1.0)
