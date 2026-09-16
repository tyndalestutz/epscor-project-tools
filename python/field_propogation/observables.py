"""Jones-to-Stokes and polarization conversion, including dark-port masks."""
from __future__ import annotations

import numpy as np

# Q_j = E^dagger H_j E. Q0 is power; s_j = Q_j/Q0.
STOKES_MATRICES = np.array([
    [[1, 0], [0, 1]], [[-1, 0], [0, 1]],
    [[0, 1], [1, 0]], [[0, 1j], [-1j, 0]],
], dtype=complex)


def intensity(field: np.ndarray) -> float:
    return float(np.vdot(field, field).real)


def normalized_stokes(field: np.ndarray) -> np.ndarray:
    """Historical scalar API: raise when the field has zero intensity."""
    if intensity(field) <= 0:
        raise ValueError("Stokes coordinates are undefined for a zero field")
    return describe(field, dark_threshold=0.0)["stokes"]


def describe(fields: np.ndarray, *, dark_threshold: float = 1e-12) -> dict[str, np.ndarray]:
    """Evaluate arbitrary batches shaped (..., 2); undefined angles are NaN.

    dark_threshold is an absolute intensity in the configured field units.
    theta is the PAX-style ellipse azimuth measured from the +y reference in
    this repository's Stokes convention. eta is signed ellipticity. We avoid
    assigning right/left handedness without an observer/time convention.
    """
    fields = np.asarray(fields, dtype=complex)
    raw = np.einsum('...a,jab,...b->...j', fields.conj(), STOKES_MATRICES, fields).real
    power = raw[..., 0]
    valid = power > dark_threshold
    stokes = np.full(raw[..., 1:].shape, np.nan)
    np.divide(raw[..., 1:], power[..., None], out=stokes, where=valid[..., None])
    s1, s2, s3 = np.moveaxis(stokes, -1, 0)
    theta = 0.5 * np.arctan2(s2, s1)
    theta = np.where(np.hypot(s1, s2) > 1e-10, theta, np.nan)
    eta = 0.5 * np.arcsin(np.clip(s3, -1, 1))
    u = np.where(np.hypot(s2, s3) > 1e-10, np.arctan2(s3, s2), np.nan)
    v = np.arccos(np.clip(s1, -1, 1))
    return dict(intensity=power, raw_stokes=raw, stokes=stokes,
                theta_rad=theta, eta_rad=eta, u_rad=u, v_rad=v, defined=valid)


def state_label(values: dict[str, np.ndarray]) -> str:
    """Human-readable classification for a single evaluated field."""
    if not bool(values['defined']):
        return 'dark (polarization undefined)'
    s1, s2, s3 = values['stokes']
    if abs(abs(s3) - 1) < 1e-8:
        return 'circular (azimuth undefined)'
    if abs(s3) < 1e-8:
        return 'linear'
    return 'elliptical'
