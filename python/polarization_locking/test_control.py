from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import math

import pytest

from polarization_locking.control import (
    PolarizationState,
    hybrid_phase_error,
    pax_to_sphere_angles,
    phase_error_to_rp_voltage,
    phase_error_to_voltage,
    rough_voltage_correction,
)


def _pax_state_from_stokes(s1: float, s2: float, s3: float) -> PolarizationState:
    """Inverse of the PAX convention on its principal theta/eta branch."""
    return PolarizationState(
        theta=0.5 * math.atan2(s2, s1),
        eta=0.5 * math.asin(s3),
    )


def test_pax_angles_convert_to_the_hybrid_mz_sphere_coordinates() -> None:
    phi_eom = -0.8
    phi_pzt = 1.1
    state = _pax_state_from_stokes(
        math.cos(phi_pzt),
        -math.sin(phi_pzt) * math.cos(phi_eom),
        -math.sin(phi_pzt) * math.sin(phi_eom),
    )

    angles = pax_to_sphere_angles(state)

    assert angles.v == pytest.approx(phi_pzt)
    assert angles.u == pytest.approx(phi_eom + math.pi)


def test_rough_correction_uses_shortest_azimuth_and_v_lambda_scaling() -> None:
    current = _pax_state_from_stokes(
        math.cos(1.0),
        math.sin(1.0) * math.cos(math.pi - 0.1),
        math.sin(1.0) * math.sin(math.pi - 0.1),
    )
    target = _pax_state_from_stokes(
        math.cos(1.3),
        math.sin(1.3) * math.cos(-math.pi + 0.1),
        math.sin(1.3) * math.sin(-math.pi + 0.1),
    )

    delta_eom, delta_pzt = hybrid_phase_error(current, target)
    eom_voltage, pzt_voltage = rough_voltage_correction(
        current, target, phi1_v_lambda=4.0, phi2_v_lambda=-10.0
    )

    assert delta_eom == pytest.approx(0.2)
    assert delta_pzt == pytest.approx(0.3)
    assert eom_voltage == pytest.approx(4.0 * 0.2 / (2 * math.pi))
    assert pzt_voltage == pytest.approx(-10.0 * 0.3 / (2 * math.pi))


def test_zero_v_lambda_is_rejected() -> None:
    with pytest.raises(ValueError, match="V_lambda"):
        phase_error_to_voltage(0.1, 0.2, phi1_v_lambda=0.0, phi2_v_lambda=1.0)


def test_phase_error_is_scaled_from_actuator_volts_to_rp_volts() -> None:
    rp_phi1, rp_phi2 = phase_error_to_rp_voltage(
        math.pi,
        math.pi,
        phi1_v_lambda=11.0,
        phi2_v_lambda=30.0,
        phi1_actuator_volts_per_rp_volt=15.0,
        phi2_actuator_volts_per_rp_volt=30.0,
    )

    assert rp_phi1 == pytest.approx(11.0 / 30.0)
    assert rp_phi2 == pytest.approx(0.5)
