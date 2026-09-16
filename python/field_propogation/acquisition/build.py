"""Build the forward configuration only from independently registered readings."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import numpy as np

from ..configuration import ConfigurationError, from_dicts, read_json
from ..propagation import compile_network
from .reduction import (combine, field_transmission, power_fraction, reconstruct_jones,
                        representative, si_values, state_matrix, validate_reading)
from .session import readiness, records, write_json, read_section


def complex_text(value: complex) -> str:
    return f'{value.real:.17g} + ({value.imag:.17g})*i'


def build_parameters(path: Path) -> tuple[dict, dict, dict]:
    status = readiness(path)
    if not status['complete']:
        raise ConfigurationError('Acquisition is incomplete; run status for missing records, metadata and phase choices')
    plan = read_section(path, 'plan')
    quality = plan['quality']
    grouped = records(path)
    choices = read_section(path, 'choices')
    steps = {step['id']: step for step in plan['steps']}
    for name, entries in grouped.items():
        for entry in entries:
            validate_reading(entry['reading'], steps[name]['kind'])
    values, provenance, diagnostics = {}, {}, {}
    def add(name, value, units, quantity, plane, sources, error, notes, status='derived', **extra):
        values[name] = complex_text(value) if isinstance(value, complex) else float(value)
        provenance[name] = dict(status=status, units=units, quantity=quantity, plane=plane,
                                sources=sources, uncertainty=error, notes=notes, **extra)
    def ids(name):
        return [entry['id'] for entry in grouped[name]]
    def phase(name):
        if len(grouped.get(name, [])) >= quality['min_repeats']:
            samples = [entry['reading'] for entry in grouped[name]]
            angles = np.array([r['values']['phase_rad'] for r in samples])
            resultant = np.mean(np.exp(1j * angles))
            if abs(resultant) < .5:
                raise ConfigurationError(f'{name}: inconsistent phase repeats')
            value = np.angle(resultant)
            spread = np.std(np.angle(np.exp(1j * (angles-value))), ddof=1) / np.sqrt(len(angles))
            error = float(np.hypot(spread, max(r['uncertainties']['phase_rad'] for r in samples)))
            add(name, value, 'rad', 'relative_optical_phase', steps[name]['input_plane']+' -> '+steps[name]['output_plane'],
                ids(name), error, 'Dedicated independent coherent reference measurement; circular mean.', status='measured')
        else:
            choice = choices[name]
            if choice.get('status') != 'nuisance' or not choice.get('reason'):
                raise ConfigurationError(f'{name}: unresolved physical phase must be a bounded, explained nuisance')
            add(name, choice['value'], 'rad', 'relative_optical_phase', steps[name]['input_plane']+' -> '+steps[name]['output_plane'],
                [], None, choice['reason'], status='nuisance', bounds=choice['bounds'])
        return name
    def response(entries, transmission=True):
        return np.mean([state_matrix(entry['reading'], quality, transmission=transmission) for entry in entries], axis=0)
    input_matrix = response(grouped['input'], transmission=False)
    eigenvalues = np.linalg.eigvalsh(input_matrix)
    if eigenvalues[0] / eigenvalues.sum() > quality['max_stokes_norm_error'] / 2:
        raise ConfigurationError('Input repeats do not support a stable pure input state; characterize source drift/depolarization')
    input_field = representative(input_matrix)
    # Conservative component uncertainty via independently specified reading uncertainties.
    input_error = max(np.sqrt(sum(value**2 for key, value in si_values(e['reading'])[1].items() if key in {'power', 'dark'}))
                      for e in grouped['input']) / (2 * np.sqrt(np.trace(input_matrix).real))
    input_pol_error = max(max(e['reading']['uncertainties'][k] for k in ('s1','s2','s3')) for e in grouped['input'])
    input_error += np.sqrt(np.trace(input_matrix).real) * input_pol_error
    for i, key in enumerate(('input_x', 'input_y')):
        add(key, complex(input_field[i]), 'sqrt(W)', 'input_field', 'P0_before_PBS', ids('input'), float(input_error),
            'Input coherency from free-space power and Stokes; largest component phase chosen real. Approximate first-order scalar uncertainty; not a guaranteed bound or full complex covariance, especially near zero components.')
    elements = []
    def splitter(name, input_names, output_names, input_labels, output_labels, kind):
        count = len(input_names)
        amplitude = [[0 for _ in range(2*count)] for _ in range(4)]
        angles = [[0 for _ in range(2*count)] for _ in range(4)]
        for out_index, out_label in enumerate(output_labels):
            for in_index, in_label in enumerate(input_labels):
                for pol_index, polarization in enumerate(('H', 'V')):
                    step_id = f'{name}_{in_label}{out_label}_{polarization}'
                    if name == 'PBS':
                        step_id = f'PBS_{out_label}_{polarization}'
                    fraction, error = combine([power_fraction(e['reading'], quality['max_reference_drift']) for e in grouped[step_id]])
                    field, field_error = field_transmission(fraction, error)
                    parameter = 'amplitude_'+step_id
                    add(parameter, field, '1', 'field_transmission', steps[step_id]['input_plane']+' -> '+steps[step_id]['output_plane'],
                        ids(step_id), field_error, f'sqrt(dark-subtracted Pout / bracketed Pin); measured power fraction {fraction:.12g} +/- {error:.4g}. No fiber coupling included.')
                    amplitude[2*out_index+pol_index][2*in_index+pol_index] = parameter
                    angles[2*out_index+pol_index][2*in_index+pol_index] = phase('phase_'+step_id)
        elements.append(dict(name=name, type=kind, inputs=input_names, outputs=output_names,
                             amplitudes=amplitude, phases_rad=angles,
                             description='Measured H/V powers converted to field; zero cross-polarization entries are an explicit diagonal-element assumption.'))
    splitter('PBS', ['Ein'], ['A0', 'B0'], [''], ['A', 'B'], 'splitter')
    def arm(label):
        states = {state: response(grouped[f'arm_{label}_{state}']) for state in ('H','V','D','R')}
        matrix, diagnostic = reconstruct_jones(states, quality['max_jones_validation_error'])
        diagnostics[label] = diagnostic
        # Reconstruct independent repeats to expose repeatability; add a
        # conservative sensitivity bound for raw Stokes/power instrument error.
        repeat_matrices = []
        count = min(len(grouped[f'arm_{label}_{state}']) for state in states)
        for index in range(count):
            sample_states = {state: state_matrix(grouped[f'arm_{label}_{state}'][index]['reading'], quality, transmission=True) for state in states}
            sample_matrix, _ = reconstruct_jones(sample_states, quality['max_jones_validation_error'])
            # Align the irrelevant overall phase to the central matrix.
            sample_matrix *= np.exp(-1j*np.angle(np.vdot(matrix, sample_matrix)))
            repeat_matrices.append(sample_matrix)
        repeat_error = np.sqrt(np.mean(np.abs(np.array(repeat_matrices)-matrix)**2, axis=0)) / np.sqrt(count)
        raw_error = 0.0
        for state in states:
            for entry in grouped[f'arm_{label}_{state}']:
                ratio, sigma = power_fraction(entry['reading'], quality['max_reference_drift'])
                raw_error = max(raw_error, sigma/max(ratio,1e-30), *[entry['reading']['uncertainties'][k] for k in ('s1','s2','s3')])
        condition = diagnostic['singular_values'][0] / diagnostic['singular_values'][-1]
        error_bound = raw_error * condition * np.linalg.norm(matrix)
        names = []
        sources = sum([ids(f'arm_{label}_{state}') for state in ('H','V','D')], [])
        for row in range(2):
            names.append([])
            for col in range(2):
                key = f'J_{label}_{row}{col}'
                add(key, complex(matrix[row,col]), '1', 'complex_field_transmission', label+'0 -> '+label+'1', sources,
                    float(np.hypot(repeat_error[row,col], error_bound)),
                    'Dedicated H/V/D Jones reconstruction including section loss. R is a separate check. Approximate conditioning-based uncertainty estimate, not a guaranteed bound or confidence interval; shared covariance is not estimated. Global scalar phase is unresolved and represented in phase origins.')
                names[-1].append(key)
        end = label+'1'
        elements.append(dict(name='section_'+label, type='matrix', inputs=[label+'0'], outputs=[end], matrix=names))
        return end
    arm('A'); arm('B')
    phase1 = phase('phi1_origin')
    elements.append(dict(name='phase1', type='phase', inputs=['A1'], outputs=['A_phase'], phase='phi1', offset_rad=phase1))
    splitter('NPBS1', ['A_phase','B1'], ['C0','D0'], ['A','B'], ['C','D'], 'scattering')
    arm('C'); arm('D')
    phase2 = phase('phi2_origin')
    elements.append(dict(name='phase2', type='phase', inputs=['C1'], outputs=['C_phase'], phase='phi2', offset_rad=phase2))
    splitter('NPBS2', ['C_phase','D1'], ['E','F'], ['C','D'], ['E','F'], 'scattering')
    flow = dict(schema_version=1, name='Independently characterized hybrid MZI',
                description='Input plane P0; arm Jones matrices include transmission; measured splitter magnitudes and explicit phase provenance. Diagonal splitter assumption must be checked independently.',
                phases=['phi1','phi2'], input=dict(name='Ein', jones=['input_x','input_y']), elements=elements, outputs=['E','F'])
    parameters = dict(schema_version=2, name='Measurement-derived parameter set', values=values, provenance=provenance,
                      normalization=dict(plane='P0_before_PBS', field_units='sqrt(W)'), phase_values=dict(phi1=0,phi2=np.pi/2))
    compile_network(from_dicts(flow, parameters))
    return flow, parameters, diagnostics


def save_build(path: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise ConfigurationError('Build destination must be new or empty')
    flow, parameters, diagnostic = build_parameters(path)
    destination.mkdir(parents=True, exist_ok=True)
    write_json(destination/'flow.json', flow)
    write_json(destination/'parameters.json', parameters)
    write_json(destination/'section_identifiability.json', diagnostic)
