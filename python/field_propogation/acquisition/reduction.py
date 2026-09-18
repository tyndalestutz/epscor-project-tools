"""Independent measurement reductions; no global-output fitting objective."""
from __future__ import annotations

import numpy as np
from ..configuration import ConfigurationError
from ..observables import STOKES_MATRICES

POWER_SCALE = {'W': 1.0, 'mW': 1e-3, 'uW': 1e-6, 'nW': 1e-9}
POWER_KEYS = {'power_in_before', 'power_in_after', 'power_out', 'dark_in', 'dark_out', 'power', 'dark', 'free_power', 'fiber_power'}
REQUIRED = {
    'power_ratio': {'power_in_before', 'power_in_after', 'power_out', 'dark_in', 'dark_out'},
    'jones_response': {'power_in_before', 'power_in_after', 'power_out', 'dark_in', 'dark_out', 's1', 's2', 's3', 'dop'},
    'input_state': {'power', 'dark', 's1', 's2', 's3', 'dop'},
    'phase': {'phase_rad'},
    'phase_law': {'command_v', 'phase_rad', 'settle_s'},
    'spatial': {'free_power', 'fiber_power', 'x_um', 'y_um', 'z1_m', 'z2_m', 'cx1_m', 'cy1_m', 'cx2_m', 'cy2_m', 'width_x_m', 'width_y_m'},
    'complex_overlap': {'real', 'imag'},
}


def validate_reading(reading: dict, kind: str) -> None:
    if not isinstance(reading, dict) or not {'values', 'uncertainties', 'power_unit', 'detectors', 'utc', 'notes'} <= reading.keys():
        raise ConfigurationError('Reading needs values, uncertainties, power_unit, detectors, utc and notes')
    if reading['power_unit'] not in POWER_SCALE:
        raise ConfigurationError('Power unit must be W, mW, uW or nW')
    if not REQUIRED[kind] <= reading['values'].keys():
        raise ConfigurationError(f'{kind}: missing readings {sorted(REQUIRED[kind] - reading["values"].keys())}')
    if set(reading['uncertainties']) != set(reading['values']):
        raise ConfigurationError('Every raw value needs an uncertainty in the same units')
    if not reading['detectors'] or not reading['utc'] or not reading['notes']:
        raise ConfigurationError('Detector IDs, acquisition UTC and setup notes must be recorded')
    for key, value in reading['values'].items():
        sigma = reading['uncertainties'][key]
        if type(value) not in (int, float) or not np.isfinite(value):
            raise ConfigurationError(f'{key}: enter a finite raw reading')
        if type(sigma) not in (int, float) or not np.isfinite(sigma) or sigma < 0:
            raise ConfigurationError(f'{key}: enter a nonnegative standard uncertainty')


def si_values(reading: dict) -> tuple[dict, dict]:
    scale = POWER_SCALE[reading['power_unit']]
    values, errors = {}, {}
    for key, value in reading['values'].items():
        factor = scale if key in POWER_KEYS else 1
        values[key] = value * factor
        errors[key] = reading['uncertainties'][key] * factor
    return values, errors


def power_fraction(reading: dict, max_drift: float = 0.02) -> tuple[float, float]:
    """Dark-subtracted, bracketed power ratio and propagated standard uncertainty."""
    values, sigma = si_values(reading)
    before, after = values['power_in_before'] - values['dark_in'], values['power_in_after'] - values['dark_in']
    pin = (before + after) / 2
    pout = values['power_out'] - values['dark_out']
    if pin <= 0 or pout < 0:
        raise ConfigurationError('Nonpositive incident power or negative dark-subtracted output; repeat the measurement')
    if abs(after - before) / pin > max_drift:
        raise ConfigurationError('Input reference drift exceeds the declared acquisition threshold')
    var_in = (sigma['power_in_before']**2 + sigma['power_in_after']**2) / 4 + sigma['dark_in']**2
    var_out = sigma['power_out']**2 + sigma['dark_out']**2
    ratio = pout / pin
    error = np.sqrt(var_out / pin**2 + pout**2 * var_in / pin**4)
    if ratio > 1:
        raise ConfigurationError('Measured passive power ratio exceeds one; resolve reference/detector calibration, do not renormalize')
    return float(ratio), float(error)


def combine(values: list[tuple[float, float]]) -> tuple[float, float]:
    """Mean with repeatability plus instrument uncertainty kept conservatively.

    Instrument standard uncertainties are treated as fully correlated across
    repeats; repeatability contributes standard error. This prevents averaging
    away a common calibration uncertainty. Individual covariance is not inferred.
    """
    means, errors = np.array(values).T
    repeatability = np.std(means, ddof=1) / np.sqrt(len(means)) if len(means) > 1 else 0
    return float(np.mean(means)), float(np.hypot(repeatability, np.max(errors)))


def field_transmission(power: float, uncertainty: float) -> tuple[float, float]:
    if not 0 < power <= 1:
        raise ConfigurationError('Zero/leakage below detection needs an upper bound, not a precise zero field estimate')
    return float(np.sqrt(power)), float(uncertainty / (2 * np.sqrt(power)))


def state_matrix(reading: dict, quality: dict, *, transmission: bool) -> np.ndarray:
    """Measured 2x2 coherency, rejecting inadequate pure-Jones evidence."""
    values, _ = si_values(reading)
    stokes = np.array([values[k] for k in ('s1', 's2', 's3')])
    if not quality['min_dop'] <= values['dop'] <= 1 + quality['max_stokes_norm_error']:
        raise ConfigurationError('DOP is incompatible with a pure Jones reconstruction; check units or use a coherency model')
    norm = np.linalg.norm(stokes)
    if abs(norm - 1) > quality['max_stokes_norm_error']:
        raise ConfigurationError('Stokes norm fails the declared pure-state tolerance')
    # Only small instrument noise is projected onto the pure-state sphere.
    stokes = stokes / norm
    if transmission:
        power, _ = power_fraction(reading, quality['max_reference_drift'])
    else:
        power = values['power'] - values['dark']
        if power <= 0:
            raise ConfigurationError('Input power must exceed background')
    return power * (np.eye(2) + np.einsum('j,jab->ab', stokes, STOKES_MATRICES[1:])) / 2


def representative(matrix: np.ndarray) -> np.ndarray:
    eigenvalues, eigenvectors = np.linalg.eigh(matrix)
    vector = np.sqrt(max(eigenvalues[-1], 0)) * eigenvectors[:, -1]
    pivot = np.argmax(np.abs(vector))
    return vector * np.exp(-1j * np.angle(vector[pivot]))


def reconstruct_jones(states: dict[str, np.ndarray], tolerance: float) -> tuple[np.ndarray, dict]:
    """H,V,D dedicated measurements determine a Jones matrix up to global phase.

    A linear two-coordinate solve determines relative column phase, never an
    optic retarder fit to final output powers. R is an unused-input prediction
    check. Degenerate preparations are rejected rather than assigned precision.
    """
    u, v = representative(states['H']), representative(states['V'])
    cross = np.outer(u, v.conj())
    basis_cos = cross + cross.conj().T
    basis_sin = 1j * (cross.conj().T - cross)
    residual = 2 * states['D'] - states['H'] - states['V']
    def flatten(matrix):
        return np.r_[matrix.real.ravel(), matrix.imag.ravel()]
    design = np.column_stack([flatten(basis_cos), flatten(basis_sin)])
    singular = np.linalg.svd(design, compute_uv=False)
    if singular[-1] <= singular[0] * 1e-6:
        raise ConfigurationError('H/V/D responses do not identify the Jones relative column phase')
    cosine, sine = np.linalg.lstsq(design, flatten(residual), rcond=None)[0]
    if abs(np.hypot(cosine, sine) - 1) > tolerance:
        raise ConfigurationError('H/V/D responses are inconsistent with one deterministic Jones matrix')
    phase = np.arctan2(sine, cosine)
    matrix = np.column_stack([u, np.exp(1j * phase) * v])
    if np.linalg.svd(matrix, compute_uv=False)[0] > 1 + 1e-10:
        raise ConfigurationError('Reconstructed section matrix is not passive; inspect reference powers and uncertainty')
    errors = {}
    for label, input_vector in [('D', np.array([1, 1])/np.sqrt(2)), ('R', np.array([1, 1j])/np.sqrt(2))]:
        output = matrix @ input_vector
        predicted = np.outer(output, output.conj())
        errors[label] = float(np.linalg.norm(predicted - states[label]) / max(np.linalg.norm(states[label]), 1e-30))
        if errors[label] > tolerance:
            raise ConfigurationError(f'{label} response disagrees with the reconstructed section Jones matrix')
    return matrix, dict(relative_phase_rad=float(phase), singular_values=singular.tolist(), validation_errors=errors,
                        global_phase='unmeasured; reference gauge here, physical inter-arm phase remains in phase offsets')


def path_power_summary(powers_uw: dict[str, float]) -> dict:
    """Descriptive isolated-route power ratios; not a splitter/Jones calibration.

    Ratios use collected output sums because input reference powers are absent.
    Downstream ratios include section transmission and collection, not just NPBS2.
    Interpretation requires route isolation and a stable source/alignment.
    """
    required = {'AC', 'AD', 'BC', 'BD', 'ACE', 'ACF', 'ADE', 'ADF',
                'BCE', 'BCF', 'BDE', 'BDF'}
    if set(powers_uw) != required or any(type(v) not in (int, float) or not np.isfinite(v) or v <= 0
                                       for v in powers_uw.values()):
        raise ConfigurationError('Supply all twelve positive, finite route powers in uW')
    first, second, totals = {}, {}, {}
    for source in ('A', 'B'):
        c, d = powers_uw[source+'C'], powers_uw[source+'D']
        first[source] = dict(collected_sum_uw=c+d, c_fraction=c/(c+d), d_fraction=d/(c+d),
                             transmitted_fraction=(c if source=='A' else d)/(c+d))
        e_sum = f_sum = 0.0
        for branch in ('C', 'D'):
            route = source + branch
            e, f = powers_uw[route+'E'], powers_uw[route+'F']
            first_power = powers_uw[route]
            second[route] = dict(e_uw=e, f_uw=f, collected_sum_uw=e+f,
                                transmitted_fraction=(e if branch=='C' else f)/(e+f),
                                downstream_collected_ratio=(e+f)/first_power,
                                effective_e_power_ratio=e/first_power,
                                effective_f_power_ratio=f/first_power)
            e_sum += e
            f_sum += f
        totals[source] = dict(sum_isolated_e_uw=e_sum, sum_isolated_f_uw=f_sum,
                              sum_isolated_outputs_uw=e_sum+f_sum)
    return dict(npbs1_collected_splits=first, downstream_routes=second, incoherent_route_sums=totals,
                interpretation='Single-reading descriptive ratios; no uncertainty supplied. Requires stable source and isolated routes. '
                'Output-normalized splits are not absolute transmissions. Downstream ratios include intervening optics. '
                'Sums of isolated-route powers are not predictions for coherently recombined open paths. '
                'No phases, Jones matrices, or independent H/V splitter coefficients are identified.')
