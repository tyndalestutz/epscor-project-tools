#!/usr/bin/env python3
"""Controlled parameter changes for the historical hybrid-MZI fit.

This study changes declared model parameters without refitting data. It measures
phi2 intensity visibility, not the causal attribution of a bench discrepancy.
Run directly to write JSON, CSV, Markdown and a vector PDF figure to --output.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

import numpy as np

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = 'field_propogation.archive.effective_fits'

from ...configuration import ConfigurationError, from_dicts, read_json
from ...observables import describe
from ...propagation import compile_network

GROUPS = {
    'Input amplitude ratio': ('ax', 'ay'),
    'A/B arm amplitudes': ('loss_a', 'loss_b'),
    'C/D arm amplitudes': ('loss_c', 'loss_d'),
    'PBS leakage': ('pbs_leakage_rad',),
    'NPBS1 splitting': ('npbs1_mixing_rad',),
    'NPBS2 splitting': ('npbs2_mixing_rad',),
    'C/D retarders': ('axis_c', 'retardance_c', 'axis_d', 'retardance_d'),
}


def phi2_metrics(flow: dict, parameters: dict, phi1_samples: int = 360) -> dict:
    """Exact phi2 harmonic extrema, repeated on a finite phi1 grid.

    This routine checks that the flow's intensity has no higher phi2 harmonics
    before using four quadrature phases. The across-phi1 range remains sampled.
    """
    network = compile_network(from_dicts(flow, parameters))
    if network.phases != ('phi1', 'phi2'):
        raise ConfigurationError('This study requires phi1 and phi2 controls, in that order')
    for port in network.outputs:
        harmonics = network.stokes_harmonics(port)
        scale = max(abs(c[0]) for c in harmonics.values())
        if any(abs(mode[1]) > 1 and abs(c[0]) > scale * 1e-12
               for mode, c in harmonics.items()):
            raise ConfigurationError('This study requires first-harmonic phi2 intensity')
    phi1 = np.r_[network.config.phase_values['phi1'],
                  np.linspace(0, 2 * np.pi, phi1_samples, endpoint=False)]
    fields = network.evaluate({
        'phi1': phi1[:, None],
        'phi2': np.array([0, np.pi / 2, np.pi, 3 * np.pi / 2])[None, :],
    })
    results = {}
    for port, field in fields.items():
        power = describe(field)['intensity']
        baseline = (power[:, 0] + power[:, 2]) / 2
        cosine = (power[:, 0] - power[:, 2]) / 2
        sine = (power[:, 1] - power[:, 3]) / 2
        amplitude = np.hypot(cosine, sine)
        if np.any(baseline <= network.config.scan['dark_threshold']):
            raise ConfigurationError('A dark baseline makes visibility undefined in this study')
        visibility = amplitude / baseline
        results[port] = dict(
            visibility=float(visibility[0]), mean_visibility=float(np.mean(visibility[1:])),
            min_visibility=float(np.min(visibility[1:])), max_visibility=float(np.max(visibility[1:])),
            I_min=float(baseline[0] - amplitude[0]), I_max=float(baseline[0] + amplitude[0]),
            baseline=float(baseline[0]),
        )
    total = sum(describe(field)['intensity'] for field in fields.values())
    return dict(ports=results, total_phi2_peak_to_peak=float(np.max(np.ptp(total, axis=1))))


def contribution_study(flow: dict, fitted: dict, ideal: dict) -> dict:
    """Group-at-a-time and one-parameter-at-a-time interventions, with no refit."""
    missing = {key for keys in GROUPS.values() for key in keys} - fitted['values'].keys()
    if missing:
        raise ConfigurationError(f'Historical study parameters are missing: {sorted(missing)}')
    result = dict(
        phi1_command_rad=fitted['phase_values']['phi1'], phi1_grid_samples=360,
        full=phi2_metrics(flow, fitted), ideal=phi2_metrics(flow, ideal), groups={}, individual={},
    )
    for group, keys in GROUPS.items():
        removed, alone = deepcopy(fitted), deepcopy(ideal)
        alone['phase_values'] = deepcopy(fitted['phase_values'])
        # Keep the coordinate origin the same in every intervention.
        for key in ('phi1_offset_rad', 'phi2_offset_rad'):
            alone['values'][key] = fitted['values'][key]
        for key in keys:
            removed['values'][key] = ideal['values'][key]
            alone['values'][key] = fitted['values'][key]
            single = deepcopy(fitted)
            single['values'][key] = ideal['values'][key]
            result['individual'][key] = phi2_metrics(flow, single)
        result['groups'][group] = dict(
            parameters=list(keys), reset_to_ideal=phi2_metrics(flow, removed),
            alone_on_ideal=phi2_metrics(flow, alone),
        )
    return result


def write_study(result: dict, output: Path) -> None:
    """Save an auditable study, with clearly labeled non-additive interventions."""
    output.mkdir(parents=True, exist_ok=True)
    (output / 'contributions.json').write_text(json.dumps(result, indent=2) + '\n')
    rows = []
    for group, experiment in result['groups'].items():
        for port, baseline in result['full']['ports'].items():
            removed = experiment['reset_to_ideal']['ports'][port]
            alone = experiment['alone_on_ideal']['ports'][port]
            rows.append(dict(
                group=group, port=port, full_visibility=baseline['visibility'],
                reset_visibility=removed['visibility'], alone_visibility=alone['visibility'],
                mean_full_visibility=baseline['mean_visibility'],
                mean_reset_visibility=removed['mean_visibility'], mean_alone_visibility=alone['mean_visibility'],
                visibility_drop=baseline['visibility'] - removed['visibility'],
            ))
    with (output / 'contributions.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    text = [
        '# Historical-fit intensity contributors\n',
        'Full-model phi2 visibility: ' + ', '.join(f"{port} = {100 * values['visibility']:.2f}%" for port, values in result['full']['ports'].items()) + '.\n',
        f"Full phi2 fringe at phi1 command = {result['phi1_command_rad']} rad, including the saved phase offsets. "
        'Visibility is (Imax-Imin)/(Imax+Imin). Optical intensities exclude detector gain/offset.\n',
        '| Parameter group | Reset group: E | Reset group: F | Group alone: E | Group alone: F |',
        '| --- | ---: | ---: | ---: | ---: |',
    ]
    for group, item in result['groups'].items():
        numbers = [item[k]['ports'][p]['visibility'] for k in ('reset_to_ideal', 'alone_on_ideal') for p in ('E', 'F')]
        text.append('| ' + group + ' | ' + ' | '.join(f'{100*v:.2f}%' for v in numbers) + ' |')
    text += [
        '\nReset group: restore only that group to ideal values, leaving the rest of the fitted model unchanged. '
        'Group alone: add only that group to an ideal model. These effects interact and are not additive.',
        '\nThe JSON includes individual-parameter resets and visibility ranges/means across 360 phi1 values. '
        'No measurement was refitted. These are model interventions, not experimentally identified causes.',
        '\nThe full-model output sum has maximum phi2 peak-to-peak variation '
        f"{result['full']['total_phi2_peak_to_peak']:.3g}, consistent with roundoff for this lossless final combiner.",
        '\n[Parameter comparison figure](contributions.pdf).',
    ]
    (output / 'README.md').write_text('\n'.join(text) + '\n')
    os.environ.setdefault('MPLCONFIGDIR', '/tmp/matplotlib-field-propagation')
    os.environ.setdefault('XDG_CACHE_HOME', '/tmp/field-propagation-cache')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), layout='constrained')
    labels = list(result['groups'])
    y = np.arange(len(labels))
    for axis, port in zip(axes, ('E', 'F')):
        reset = [100 * result['groups'][g]['reset_to_ideal']['ports'][port]['visibility'] for g in labels]
        alone = [100 * result['groups'][g]['alone_on_ideal']['ports'][port]['visibility'] for g in labels]
        axis.barh(y - .18, reset, .35, label='Reset group in full fit', color='#244e77')
        axis.barh(y + .18, alone, .35, label='Group alone on ideal', color='#b85b24')
        axis.axvline(100 * result['full']['ports'][port]['visibility'], color='black', ls='--', label='Full fit')
        axis.set(yticks=y, yticklabels=labels, xlabel='Phi2 intensity visibility (%)', title=f'Output {port}')
        axis.invert_yaxis()
    axes[0].legend(fontsize=8, loc='lower right')
    fig.suptitle(f"Controlled model changes; phi1 command = {result['phi1_command_rad']} rad; no refit")
    fig.savefig(output / 'contributions.pdf')
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    base = Path(__file__).resolve().parent / 'configs'
    parser.add_argument('--flow', type=Path, default=base / 'flows/hybrid_mzi_relative_fit.json')
    parser.add_argument('--params', type=Path, default=base / 'parameters/historical_fit.json')
    parser.add_argument('--reference', type=Path, default=Path(__file__).resolve().parents[2] / 'configs/parameters/ideal.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = args.output or Path(__file__).resolve().parents[4] / 'experiments/field_propagation' / (
        datetime.now(timezone.utc).strftime('%Y-%m-%d_%H%M%S') + '_contributors')
    try:
        if output.exists() and any(output.iterdir()):
            raise ConfigurationError(f'Choose a new or empty output directory: {output}')
        flow, fitted, ideal = read_json(args.flow), read_json(args.params), read_json(args.reference)
        result = contribution_study(flow, fitted, ideal)
        result['inputs'] = dict(flow=flow, fitted=fitted, reference=ideal)
        write_study(result, output)
    except (ConfigurationError, OSError) as exc:
        parser.exit(2, f'Error: {exc}\n')
    print(f'Wrote parameter study to {output}')


if __name__ == '__main__':
    main()
