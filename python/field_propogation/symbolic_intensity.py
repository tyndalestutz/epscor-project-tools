"""Input-amplitude-independent transfer functions for the configured optical flow.

Optical element values are held fixed. Independent input basis vectors recover
M(phi) without dividing by the configured input, so a blocked input component
still has a valid symbolic response. The intensity remains a quadratic in
independent amplitudes ax, ay and their relative input phase delta.
"""
from __future__ import annotations

from copy import deepcopy
import numpy as np

from .configuration import from_dicts
from .propagation import Network, compile_network


def input_transfer_modes(network: Network) -> dict[str, dict[tuple, np.ndarray]]:
    """Return M_k such that E_out = sum_k M_k exp(i k.phi) E_in."""
    responses = []
    for basis in ([1, 0], [0, 1]):
        flow = deepcopy(network.config.flow)
        flow['input']['jones'] = basis
        # Do not change element parameters, even if one shares an input name.
        responses.append(compile_network(from_dicts(flow, network.config.parameters)))
    transfers = {}
    for port in network.outputs:
        modes = responses[0].fields[port].keys() | responses[1].fields[port].keys()
        transfers[port] = {
            mode: np.column_stack([
                response.fields[port].get(mode, np.zeros(2, complex))
                for response in responses
            ])
            for mode in sorted(modes)
        }
    return transfers


def intensity_form_modes(transfers: dict[tuple, np.ndarray]) -> dict[tuple, np.ndarray]:
    """Fourier coefficients of the real functions A, B, Re(G), Im(G).

    M = [u, v], A = u^dagger u, B = v^dagger v, G = u^dagger v.
    For E_in = [ax exp(i delta), ay], with real nonnegative amplitudes:
      I = ax^2 A + ay^2 B + 2 ax ay [Re(G) cos(delta) + Im(G) sin(delta)].
    Each returned function obeys c[-d] = conjugate(c[d]).
    """
    gram = {}
    for k, first in transfers.items():
        for l, second in transfers.items():
            mode = tuple(b - a for a, b in zip(k, l))
            gram[mode] = gram.get(mode, np.zeros((2, 2), complex)) + first.conj().T @ second
    result = {}
    for mode, matrix in gram.items():
        opposite = tuple(-k for k in mode)
        g, opposite_g = matrix[0, 1], gram[opposite][0, 1]
        result[mode] = np.array([
            matrix[0, 0], matrix[1, 1],
            (g + opposite_g.conjugate()) / 2,
            (g - opposite_g.conjugate()) / (2j),
        ])
    return result


def symbolic_intensity_data(network: Network) -> dict:
    """Serializable analytic data; input amplitudes have not been substituted."""
    result = {}
    for port, transfers in input_transfer_modes(network).items():
        result[port] = {
            'transfer_modes': [dict(mode=mode, matrix=value) for mode, value in transfers.items()],
            'intensity_form_modes': [dict(mode=mode, coefficients=value)
                                     for mode, value in intensity_form_modes(transfers).items()],
        }
    return result
