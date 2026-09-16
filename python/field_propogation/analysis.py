"""Phase scans and analytic sensitivities of the compiled Jones network."""
from __future__ import annotations

import numpy as np

from .observables import describe, STOKES_MATRICES
from .propagation import Network


def evaluate_with_sensitivity(network: Network, values: dict, phase: str) -> dict:
    """Return observables, dI/dphi and |ds/dphi|, without angle wrapping artifacts."""
    index = network.phases.index(phase)
    phase_values = dict(network.config.phase_values, **values)
    arrays = np.broadcast_arrays(*[np.asarray(phase_values[p]) for p in network.phases])
    results = {}
    for port, field in network.evaluate(values).items():
        derivative = np.zeros_like(field)
        for mode, amplitude in network.fields[port].items():
            argument = sum(k * a for k, a in zip(mode, arrays))
            derivative += (1j * mode[index] * np.exp(1j * argument))[..., None] * amplitude
        obs = describe(field, dark_threshold=network.config.scan['dark_threshold'])
        draw = 2 * np.einsum('...a,jab,...b->...j', field.conj(), STOKES_MATRICES, derivative).real
        ds = np.full(obs['stokes'].shape, np.nan)
        np.divide(draw[..., 1:] - obs['stokes'] * draw[..., :1], obs['intensity'][..., None],
                  out=ds, where=obs['defined'][..., None])
        results[port] = dict(**obs, intensity_derivative=draw[..., 0],
                             stokes_derivative=ds, stokes_speed=np.linalg.norm(ds, axis=-1))
    return results


def phase_scans(network: Network) -> dict:
    """One-axis cuts hold every other phase at the declared operating point."""
    config = network.config.scan
    axis = np.linspace(config['start_rad'], config['stop_rad'], config['points'])
    cuts = {}
    for phase in network.phases:
        cuts[phase] = evaluate_with_sensitivity(network, {phase: axis}, phase)
    maps = {}
    # Every phase pair gets a map; all remaining phases are held fixed.
    for i, first in enumerate(network.phases):
        for second in network.phases[i+1:]:
            x, y = np.meshgrid(axis, axis, indexing='xy')
            maps[(first, second)] = {port: describe(field, dark_threshold=config['dark_threshold'])
                                    for port, field in network.evaluate({first: x, second: y}).items()}
    return dict(axis=axis, cuts=cuts, maps=maps)


def finite_max(values: np.ndarray) -> float | None:
    valid = np.asarray(values)[np.isfinite(values)]
    return float(np.max(valid)) if valid.size else None


def summarize(network: Network, scans: dict) -> dict:
    operating = {}
    for port, field in network.evaluate().items():
        operating[port] = dict(jones=field, **describe(field, dark_threshold=network.config.scan['dark_threshold']))
    cuts = {}
    for phase, ports in scans['cuts'].items():
        cuts[phase] = {}
        for port, obs in ports.items():
            low, high = float(np.min(obs['intensity'])), float(np.max(obs['intensity']))
            cuts[phase][port] = dict(min_intensity=low, max_intensity=high,
                sampled_visibility=(high-low)/(high+low) if high+low > network.config.scan['dark_threshold'] else None,
                max_abs_dI_dphase=finite_max(np.abs(obs['intensity_derivative'])),
                max_stokes_speed=finite_max(obs['stokes_speed']), dark_samples=int(np.sum(~obs['defined'])))
    maps = {}
    for phases, ports in scans['maps'].items():
        maps[','.join(phases)] = {port: dict(min_intensity=float(np.min(obs['intensity'])),
            max_intensity=float(np.max(obs['intensity'])), dark_samples=int(np.sum(~obs['defined']))) for port, obs in ports.items()}
    return dict(operating_point=operating, phase_cuts=cuts, phase_maps=maps,
                unused_parameters=network.unused_parameters,
                dark_threshold=network.config.scan['dark_threshold'])
