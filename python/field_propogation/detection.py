"""Separate polarization from spatial overlap and detector-mode projection.

G_ab = integral u_a* u_b dA is a Hermitian positive-semidefinite spatial Gram
matrix. A single-mode receiver uses h_a = integral u_f* u_a dA; its Gram matrix
is h* h^T. G - h* h^T must be PSD for a passive projection. A scalar visibility
factor alone would miss the changed diagonal coupling efficiencies.
"""
from __future__ import annotations

import numpy as np
from .configuration import ConfigurationError, numeric
from .observables import STOKES_MATRICES


def checked_gram(values, count: int) -> np.ndarray:
    matrix = np.array([[numeric(v) for v in row] for row in values], dtype=complex)
    if matrix.shape != (count, count) or not np.isfinite(matrix).all() or not np.allclose(matrix, matrix.conj().T, atol=1e-12, rtol=0):
        raise ConfigurationError('Spatial Gram matrix must be Hermitian with one row per explicit mode')
    if not np.allclose(matrix.diagonal(), 1, atol=1e-12):
        raise ConfigurationError('Spatial modes are normalized: Gram diagonal must be one')
    if np.linalg.eigvalsh(matrix).min() < -1e-10:
        raise ConfigurationError('Spatial overlap matrix is not positive semidefinite')
    return matrix


def coherency(fields: np.ndarray, gram: np.ndarray, projection: np.ndarray | None = None) -> np.ndarray:
    """fields has shape (..., mode, polarization); return (..., 2, 2)."""
    fields = np.asarray(fields, dtype=complex)
    count = fields.shape[-2]
    gram = checked_gram(gram, count)
    if projection is not None:
        h = np.asarray(projection, dtype=complex)
        if h.shape != (count,) or not np.isfinite(h).all():
            raise ConfigurationError('Fiber projection needs one finite complex overlap per mode')
        projected_gram = np.outer(h.conj(), h)
        if np.linalg.eigvalsh(gram - projected_gram).min() < -1e-10:
            raise ConfigurationError('Fiber projection exceeds the supplied free-space Gram matrix')
        gram = projected_gram
    return np.einsum('...ap,ba,...bq->...pq', fields, gram, fields.conj())


def observables(matrix: np.ndarray, dark_threshold: float = 1e-12) -> dict:
    """Stokes normalized by total detected power, retaining partial polarization."""
    raw = np.einsum('jab,...ba->...j', STOKES_MATRICES, matrix).real
    intensity = raw[..., 0]
    stokes = np.full(raw[..., 1:].shape, np.nan)
    np.divide(raw[..., 1:], intensity[..., None], out=stokes,
              where=intensity[..., None] > dark_threshold)
    return dict(intensity=intensity, stokes=stokes, dop=np.linalg.norm(stokes, axis=-1))


def detected_outputs(network, phases: dict, detectors: dict) -> dict:
    """Apply explicit route modes to the hybrid flow.

    Route-to-phase mapping must be one-to-one. For the supplied hybrid flow:
    BD=(0,0), BC=(0,1), AD=(1,0), AC=(1,1). This interface does not recover
    distinct spatial routes that a different flow has already merged.
    """
    values = dict(network.config.phase_values, **phases)
    if set(phases) - set(network.phases):
        raise ConfigurationError('Unknown detector-evaluation phase')
    arrays = np.broadcast_arrays(*[np.asarray(values[p]) for p in network.phases])
    shape = arrays[0].shape if arrays else ()
    result = {}
    if set(detectors['ports']) != set(network.outputs):
        raise ConfigurationError('Detector settings must cover every output')
    for port, settings in detectors['ports'].items():
        modes = [tuple(mode) for mode in settings['mode_keys']]
        if len(set(modes)) != len(modes) or set(modes) != set(network.fields[port]):
            raise ConfigurationError(f'{port}: mode_keys must name every coherent route exactly once')
        terms = []
        for mode in modes:
            phase = sum((k * a for k, a in zip(mode, arrays)), np.zeros(shape))
            terms.append(np.exp(1j * phase)[..., None] * network.fields[port][mode])
        fields = np.stack(terms, axis=-2)
        gram = checked_gram(settings['gram'], len(modes))
        kind = settings['kind']
        if kind not in {'integrating', 'fiber'}:
            raise ConfigurationError('Detector kind must be integrating or fiber')
        projection = None
        if kind == 'fiber':
            projection = np.array([numeric(v) for v in settings['projection']])
        result[port] = observables(coherency(fields, gram, projection), network.config.scan['dark_threshold'])
    return result
