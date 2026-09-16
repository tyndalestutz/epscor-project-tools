"""Compile an optical flow once, then evaluate coherent fields in vectorized batches.

Fields are finite phase sums E(phi) = sum_k a_k exp(i k.phi). This keeps
propagation and analytic Stokes derivations on the same coefficients, avoids
symbolic-expression growth, and makes repeated phase scans inexpensive.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .configuration import Configuration, ConfigurationError, identifier, keys, numeric, real
from .elements import Retarder, diattenuator, npbs_matrices, pbs_matrices
from .observables import STOKES_MATRICES

Modes = dict[tuple[int, ...], np.ndarray]


@dataclass(frozen=True)
class Step:
    name: str
    kind: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    # blocks[output_index][input_index] is a 2x2 Jones transfer matrix.
    blocks: tuple[tuple[np.ndarray, ...], ...]
    phase: str | None = None
    allow_gain: bool = False


@dataclass(frozen=True)
class Network:
    config: Configuration
    fields: dict[str, Modes]
    steps: tuple[Step, ...]
    unused_parameters: tuple[str, ...]

    @property
    def phases(self) -> tuple[str, ...]:
        return tuple(self.config.flow['phases'])

    @property
    def outputs(self) -> tuple[str, ...]:
        return tuple(self.config.flow['outputs'])

    def evaluate(self, phase_values: dict[str, object] | None = None, *,
                 checkpoints: bool = False) -> dict[str, np.ndarray]:
        """Override any phase with a scalar/array; arrays follow NumPy broadcasting."""
        values = dict(self.config.phase_values)
        if phase_values is not None:
            unknown = set(phase_values) - set(values)
            if unknown:
                raise ConfigurationError(f'Unknown phases: {sorted(unknown)}')
            values.update(phase_values)
        arrays = np.broadcast_arrays(*[np.asarray(values[p], dtype=float) for p in self.phases]) if self.phases else []
        if any(not np.all(np.isfinite(a)) for a in arrays):
            raise ConfigurationError('Phase values must be finite')
        shape = arrays[0].shape if arrays else ()
        result = {}
        for name in self.fields if checkpoints else self.outputs:
            field = np.zeros(shape + (2,), dtype=complex)
            for mode, amplitude in self.fields[name].items():
                argument = sum((order * value for order, value in zip(mode, arrays)), np.zeros(shape))
                field += np.exp(1j * argument)[..., None] * amplitude
            result[name] = field
        return result

    def stokes_harmonics(self, port: str) -> dict[tuple[int, ...], np.ndarray]:
        """Analytic complex Fourier coefficients for all four unnormalized Stokes.

        Q_j = sum_d c_{j,d} exp(i d.phi),
        c_{j,d} = sum_{l-k=d} a_k^dagger H_j a_l.
        Thus c_{j,-d}=conj(c_{j,d}); no sinusoidal regression is needed.
        """
        result: dict[tuple[int, ...], np.ndarray] = {}
        for k, a in self.fields[port].items():
            for l, b in self.fields[port].items():
                mode = tuple(y - x for x, y in zip(k, l))
                term = np.einsum('a,jab,b->j', a.conj(), STOKES_MATRICES, b)
                result[mode] = result.get(mode, np.zeros(4, complex)) + term
        return result


def compile_network(config: Configuration) -> Network:
    """Validate connections and element values, then compile each named field.

    Each field can feed one element: physical branching must use a splitter.
    Every terminal field must be declared as an output, so power accounting
    cannot silently discard a port. Outputs may include diagnostic terminals.
    """
    flow, values = config.flow, config.parameters['values']
    used_parameters: set[str] = set()
    def number(value):
        import ast
        if isinstance(value, str):
            try:
                used_parameters.update(n.id for n in ast.walk(ast.parse(value, mode='eval'))
                                       if isinstance(n, ast.Name) and n.id in values)
            except SyntaxError:
                pass
        return numeric(value, values)
    def scalar(value):
        result = number(value)
        if result.imag:
            raise ConfigurationError(f'Expected real value: {value!r}')
        return result.real
    keys(flow['input'], {'name', 'jones'}, {'name', 'jones'}, 'input')
    source = identifier(flow['input']['name'])
    vector = flow['input']['jones']
    if not isinstance(vector, list) or len(vector) != 2:
        raise ConfigurationError('input.jones must contain two complex amplitudes')
    zero = (0,) * len(flow['phases'])
    fields: dict[str, Modes] = {source: {zero: np.array([number(v) for v in vector])}}
    consumed: set[str] = set()
    names: set[str] = set()
    steps = []
    if not isinstance(flow['elements'], list):
        raise ConfigurationError('elements must be a list')
    specs = {
        'pbs': ({'leakage_rad'}, set(), 1, 2),
        'splitter': ({'matrix', 'amplitudes', 'phases_rad'}, set(), 1, 2),
        'scattering': ({'matrix', 'amplitudes', 'phases_rad'}, set(), 2, 2),
        'npbs': ({'mixing_rad', 'mixing_y_rad'}, {'mixing_rad'}, 2, 2),
        'phase': ({'phase', 'offset_rad'}, {'phase'}, 1, 1),
        'retarder': ({'axis_rad', 'retardance_rad'}, {'axis_rad', 'retardance_rad'}, 1, 1),
        'diattenuator': ({'amplitude_x', 'amplitude_y', 'phase_x_rad', 'phase_y_rad', 'axis_rad'},
                        {'amplitude_x', 'amplitude_y'}, 1, 1),
        'matrix': ({'matrix'}, {'matrix'}, 1, 1),
        'attenuator': ({'amplitude'}, {'amplitude'}, 1, 1),
    }
    for node in flow['elements']:
        if not isinstance(node, dict) or node.get('type') not in specs:
            raise ConfigurationError(f'Element type must be one of {sorted(specs)}')
        kind = node['type']
        allowed, required, n_in, n_out = specs[kind]
        common = {'name', 'type', 'inputs', 'outputs', 'allow_gain', 'description'}
        keys(node, common | allowed, {'name', 'type', 'inputs', 'outputs'} | required, 'element')
        name = identifier(node['name'])
        if name in names:
            raise ConfigurationError(f'Duplicate element name: {name}')
        names.add(name)
        for key, length in [('inputs', n_in), ('outputs', n_out)]:
            if not isinstance(node[key], list) or len(node[key]) != length:
                raise ConfigurationError(f'{name}.{key} needs {length} field names')
            for value in node[key]:
                identifier(value)
            if len(set(node[key])) != length:
                raise ConfigurationError(f'{name}: repeated {key}')
        inputs, outputs = tuple(node['inputs']), tuple(node['outputs'])
        for field in inputs:
            if field not in fields:
                raise ConfigurationError(f'{name}: unknown or forward input {field}')
            if field in consumed:
                raise ConfigurationError(f'{name}: {field} already feeds an element; use a splitter')
        for field in outputs:
            if field in fields:
                raise ConfigurationError(f'{name}: field {field} already exists')
        gain = node.get('allow_gain', False)
        if type(gain) is not bool:
            raise ConfigurationError(f'{name}.allow_gain must be boolean')
        phase = None
        if kind in {'splitter', 'scattering'}:
            # Matrix rows/columns are [port0_x, port0_y, port1_x, port1_y].
            polar_form = 'amplitudes' in node and 'phases_rad' in node
            if ('matrix' in node) == polar_form or ('amplitudes' in node) != ('phases_rad' in node):
                raise ConfigurationError(f'{name}: supply matrix OR amplitudes plus phases_rad')
            data = node['amplitudes'] if polar_form else node['matrix']
            columns = 2 if kind == 'splitter' else 4
            if not isinstance(data, list) or len(data) != 4 or any(
                    not isinstance(row, list) or len(row) != columns for row in data):
                raise ConfigurationError(f'{name}.matrix must be 4 by {columns}')
            if polar_form:
                phases = node['phases_rad']
                if not isinstance(phases, list) or len(phases) != 4 or any(not isinstance(row, list) or len(row) != columns for row in phases):
                    raise ConfigurationError(f'{name}: phases_rad must match the amplitude matrix')
                amplitudes = np.array([[scalar(v) for v in row] for row in data])
                if np.any(amplitudes < 0):
                    raise ConfigurationError(f'{name}: amplitudes cannot be negative')
                scattering = amplitudes * np.exp(1j * np.array([[scalar(v) for v in row] for row in phases]))
            else:
                scattering = np.array([[number(v) for v in row] for row in data])
            if not gain and np.linalg.svd(scattering, compute_uv=False)[0] > 1 + 1e-12:
                raise ConfigurationError(f'{name}: combined port matrix is not passive; check measured phases and calibration')
            blocks = tuple(tuple(scattering[2*i:2*i+2, 2*j:2*j+2]
                                 for j in range(n_in)) for i in range(2))
        elif kind == 'pbs':
            a, b = pbs_matrices(scalar(node.get('leakage_rad', 0)))
            blocks = ((a,), (b,))
        elif kind == 'npbs':
            a, b = npbs_matrices(scalar(node['mixing_rad']), scalar(node.get('mixing_y_rad', node['mixing_rad'])))
            blocks = ((a, b), (b, a))
        else:
            if kind == 'phase':
                phase = node['phase']
                if phase not in flow['phases']:
                    raise ConfigurationError(f'{name}: unknown phase {phase!r}')
                matrix = np.eye(2) * np.exp(1j * scalar(node.get('offset_rad', 0)))
            elif kind == 'retarder':
                matrix = Retarder(scalar(node['axis_rad']), scalar(node['retardance_rad'])).matrix()
            elif kind == 'attenuator':
                amplitude = scalar(node['amplitude'])
                if amplitude < 0:
                    raise ConfigurationError(f'{name}: amplitude must be nonnegative; use phase for sign')
                matrix = np.eye(2) * amplitude
            elif kind == 'diattenuator':
                ax, ay = scalar(node['amplitude_x']), scalar(node['amplitude_y'])
                if min(ax, ay) < 0:
                    raise ConfigurationError(f'{name}: amplitudes must be nonnegative')
                matrix = diattenuator(ax, ay, scalar(node.get('phase_x_rad', 0)),
                                      scalar(node.get('phase_y_rad', 0)), scalar(node.get('axis_rad', 0)))
            else:
                data = node['matrix']
                if not isinstance(data, list) or len(data) != 2 or any(not isinstance(row, list) or len(row) != 2 for row in data):
                    raise ConfigurationError(f'{name}.matrix must be 2 by 2')
                matrix = np.array([[number(v) for v in row] for row in data])
            if not np.all(np.isfinite(matrix)):
                raise ConfigurationError(f'{name}: nonfinite Jones matrix')
            if not gain and np.linalg.svd(matrix, compute_uv=False)[0] > 1 + 1e-12:
                raise ConfigurationError(f'{name}: field gain exceeds one; check amplitude vs power, or explicitly set allow_gain=true')
            blocks = ((matrix,),)
        step = Step(name, kind, inputs, outputs, blocks, phase, gain)
        for output, row in zip(outputs, blocks):
            modes: Modes = {}
            for input_name, matrix in zip(inputs, row):
                for mode, amplitude in fields[input_name].items():
                    shifted = list(mode)
                    if phase:
                        shifted[flow['phases'].index(phase)] += 1
                    key = tuple(shifted)
                    modes[key] = modes.get(key, np.zeros(2, complex)) + matrix @ amplitude
            if len(modes) > 256:
                raise ConfigurationError('Flow exceeds 256 coherent phase terms; split the study into smaller configurations')
            fields[output] = modes
        consumed.update(inputs)
        steps.append(step)
    outputs = flow['outputs']
    if not isinstance(outputs, list) or not outputs or any(not isinstance(p, str) for p in outputs):
        raise ConfigurationError('outputs must be a nonempty list of terminal fields')
    if len(set(outputs)) != len(outputs) or set(outputs) != set(fields) - consumed:
        raise ConfigurationError(f'outputs must list every terminal field exactly once: {sorted(set(fields) - consumed)}')
    return Network(config, fields, tuple(steps), tuple(sorted(set(values) - used_parameters)))
