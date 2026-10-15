"""Analytic synthetic references; no measured optical validation."""
import json
from pathlib import Path

import numpy as np
import pytest

from field_propogation.observables import describe, normalized_stokes

FIXTURE = json.loads((Path(__file__).resolve().parents[3] /
                      'tests/fixtures/scientific/contracts.json').read_text())


@pytest.mark.parametrize('case', FIXTURE['jones_states'], ids=lambda case: case['name'])
@pytest.mark.parametrize('factor', [1, -2, 1j])
def test_analytic_stokes_sign_power_and_global_phase(case, factor):
    field = np.array([complex(*pair) for pair in case['field_re_im']]) * factor
    original = field.copy()
    expected = np.array(case['raw_stokes'], dtype=float)
    actual = describe(field)
    np.testing.assert_allclose(actual['raw_stokes'], expected * abs(factor)**2,
                               rtol=0, atol=1e-13)
    np.testing.assert_allclose(actual['stokes'], expected[1:] / expected[0],
                               rtol=0, atol=1e-14)
    np.testing.assert_allclose(actual['intensity'], expected[0] * abs(factor)**2,
                               rtol=0, atol=1e-13)
    np.testing.assert_array_equal(field, original)
    assert bool(actual['defined'])
    if case['name'] == 'positive_imaginary_y':
        assert np.isnan(actual['theta_rad'])
        assert actual['eta_rad'] == pytest.approx(-np.pi/4, rel=0, abs=1e-14)


def test_dark_threshold_does_not_invent_polarization():
    # Exact binary amplitudes avoid rounding ambiguity at the threshold.
    result = describe(np.array([[0, 0], [.5, 0], [1, 0]], dtype=complex),
                      dark_threshold=.25)
    np.testing.assert_array_equal(result['defined'], [False, False, True])
    np.testing.assert_array_equal(result['intensity'], [0, .25, 1])
    assert np.isnan(result['stokes'][:2]).all()
    assert np.isnan(result['theta_rad'][:2]).all()
    np.testing.assert_array_equal(result['stokes'][2], [-1, 0, 0])
    with pytest.raises(ValueError, match='zero field'):
        normalized_stokes(np.zeros(2))
