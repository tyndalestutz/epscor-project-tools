"""Explicit measurement planes and an ordered acquisition checklist."""
from __future__ import annotations

PLANES = {
    'P0_before_PBS': 'Input reference immediately before PBS; ax/ay live only here.',
    'A0': 'Immediately after PBS A output, before all A-section optics.',
    'B0': 'Immediately after PBS B output, before all B-section optics.',
    'A1': 'Immediately before NPBS1 input A, after all A-section optics.',
    'B1': 'Immediately before NPBS1 input B, after all B-section optics.',
    'C0': 'Immediately after NPBS1 output C, before all C-section optics.',
    'D0': 'Immediately after NPBS1 output D, before all D-section optics.',
    'C1': 'Immediately before NPBS2 input C, after all C-section optics.',
    'D1': 'Immediately before NPBS2 input D, after all D-section optics.',
    'E0': 'Immediately after NPBS2 output E, before detector optics/coupling.',
    'F0': 'Immediately after NPBS2 output F, before detector optics/coupling.',
    'fiber': 'After projection into the monitored fiber mode; never called arm transmission.',
    'camera_z1_z2': 'Two recorded longitudinal camera planes, referenced to E0/F0.',
}


def make_plan() -> dict:
    steps = []
    def add(identifier, kind, start, end, state, blocks, instructions, required=True):
        steps.append(dict(id=identifier, kind=kind, input_plane=start, output_plane=end,
                          input_state=state, blocks=blocks, instructions=instructions,
                          role='characterization', required=required, repeats=3))
    add('input', 'input_state', 'P0_before_PBS', 'P0_before_PBS', 'operating',
        'isolate source from downstream reflections',
        'Record total free-space input power and PAX Stokes/DOP for the actual operating input; record polarization basis calibration.')
    for output, end in [('A', 'A0'), ('B', 'B0')]:
        for state in ('H', 'V'):
            add(f'PBS_{output}_{state}', 'power_ratio', 'P0_before_PBS', end, state,
                'only PBS illuminated; no downstream return',
                'Inject known H or V at P0; measure both PBS outputs without moving the source. Bracket each output reading with input references.')
    for optic, inputs, outputs in [('NPBS1', [('A', 'A1'), ('B', 'B1')], [('C', 'C0'), ('D', 'D0')]),
                                   ('NPBS2', [('C', 'C1'), ('D', 'D1')], [('E', 'E0'), ('F', 'F0')])]:
        for input_name, start in inputs:
            for output_name, end in outputs:
                for state in ('H', 'V'):
                    add(f'{optic}_{input_name}{output_name}_{state}', 'power_ratio', start, end, state,
                        f'inject only {input_name}; block other incident port; downstream paths disconnected',
                        'Measure directly at the splitter. No fiber in the transmission measurement. Record incident/output powers and dark levels.')
    for arm in 'ABCD':
        for state in ('H', 'V', 'D', 'R'):
            add(f'arm_{arm}_{state}', 'jones_response', arm+'0', arm+'1', state,
                f'inject directly at {arm}0; all other paths blocked; phase actuators held at independently recorded reference',
                'Known H=(1,0), V=(0,1), D=(1,1)/sqrt(2), R=(1,i)/sqrt(2). '
                'Measure section input/output free-space powers and output Stokes/DOP. H,V,D reconstruct a Jones matrix; R tests it independently. '
                'The section Jones matrix already includes transmission: do not add another arm loss.')
    # Power readings cannot determine these coherent phases. Values stay blank.
    for step in list(steps):
        if step['kind'] == 'power_ratio':
            add('phase_'+step['id'], 'phase', step['input_plane'], step['output_plane'], step['input_state'],
                step['blocks'], 'Dedicated coherent reference measurement, referenced to a shared port/basis convention. '
                'If inaccessible, declare a bounded nuisance in choices.json; do not estimate from final validation scans.', required=False)
    for phase in ('phi1', 'phi2'):
        add(phase+'_origin', 'phase', 'A0' if phase=='phi1' else 'C0', 'A1' if phase=='phi1' else 'C1',
            'known state', 'separate dedicated phase-reference experiment',
            'Measure phase at zero command relative to the arm Jones reference. Global arm scalar phases and source origin enter this offset. '
            'Only conventionally unobservable common phases may be set to zero as a convention.', required=False)
        add(phase+'_law', 'phase_law', 'A0' if phase=='phi1' else 'C0', 'A1' if phase=='phi1' else 'C1',
            'known state', 'independent phase reference, not held-out final amplitude curve',
            'Record command V, independently measured phase rad, direction, settling time and uncertainties over forward/reverse sweeps. '
            'No assumed linear V_lambda if hysteresis is material.', required=False)
    for route in ('AC', 'AD', 'BC', 'BD'):
        for port in ('E', 'F'):
            add(f'spatial_{route}_{port}', 'spatial', 'P0_before_PBS', port+'0', 'operating',
                f'only route {route} open; record actual blocker planes',
                'At one common alignment, record integrated free-space and fiber-coupled powers, then two-plane camera centroids/widths. '
                'Repeat over a recorded x/y coupler grid without optimizing each route independently. These are receiver overlaps, not arm losses.', required=False)
    from itertools import combinations
    for port in ('E', 'F'):
        for left, right in combinations(('BD', 'BC', 'AD', 'AC'), 2):
            add(f'overlap_{port}_{left}_{right}', 'complex_overlap', port+'0', port+'0', 'shared calibrated polarization',
                f'only {left} and {right} routes in a dedicated coherent reference measurement',
                'Record normalized complex G_ab=<u_a,u_b> in the same route phase gauge as the Jones fields. '
                'Use independently characterized pairwise complex fields or coherent interference with known polarization overlap; '
                'isolated powers/centroids alone do not determine complex overlap. Document raw reference trace location/hash in notes.', required=False)
        for route in ('BD', 'BC', 'AD', 'AC'):
            add(f'projection_{port}_{route}', 'complex_overlap', port+'0', 'fiber', 'shared calibrated polarization',
                f'only {route} with a dedicated coherent receiver reference',
                'Record h_a=<u_f,u_a> for normalized modes at the SAME coupler position and route phase gauge as G. '
                'An isolated fiber/free power ratio determines |h_a|^2 only; phase needs coherent reference or an explicit nuisance. '
                'Keep complex overlap covariance/raw reference trace in supporting records.', required=False)
    return dict(schema_version=1, normalization_plane='P0_before_PBS', planes=PLANES, steps=steps,
                quality=dict(min_repeats=3, max_reference_drift=0.02, min_dop=0.98,
                             max_stokes_norm_error=0.02, max_jones_validation_error=0.05),
                assumptions=[
                    'H/V bases must be verified at each reflection/measurement plane; H maps to s1=-1 in this repository.',
                    'Power-only PBS/NPBS reduction assumes diagonal H/V coefficients. Check output polarization; use a full measured matrix if this fails.',
                    'Each arm Jones matrix includes all amplitude transmission between its two named planes.',
                    'Single-mode Jones reconstruction requires sufficiently polarized light; partial polarization is not normalized away.',
                    'Unknown inter-port/global arm phases remain explicit nuisance parameters; absolute-phase agreement is conditional until independently constrained.',
                ])
