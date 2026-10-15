"""Explicit fixed-axis rotation model; no edits to source measurements."""
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

from ..config import load_recipe
from ..io import read_json, write_json
from .baseline import summarize


def estimate(volts, forward, backward, config):
    """Inputs are point means, in increasing voltage order for both branches."""
    xyz = np.concatenate([forward, backward])
    if not np.isfinite(xyz).all() or len(volts) < 5:
        return {'status': 'UNAVAILABLE', 'reason': 'Incomplete/nonfinite Stokes points; no samples discarded.'}
    _, singular, basis = np.linalg.svd(xyz-xyz.mean(axis=0))
    axis, u = basis[-1], basis[0]
    v = np.cross(axis, u)
    plane_rms = float(np.std(xyz@axis))
    radius = float(np.sqrt(np.mean((xyz@u)**2+(xyz@v)**2)))
    if singular[1] < 1e-6 or radius < .1 or plane_rms > config.vpi_max_plane_rms:
        reason = (f'Trajectory radius {radius:.4g} is below conservative 0.1 model-acceptance guard; no reliable Vpi claimed.'
                  if radius < .1 else 'Insufficient plane identification or plane residual above configured model threshold.')
        return {'status': 'UNAVAILABLE', 'reason': reason, 'plane_rms': plane_rms, 'radius': radius,
                'acceptance_guards': {'minimum_circle_radius':.1,'minimum_secondary_singular':1e-6,'maximum_plane_rms':config.vpi_max_plane_rms}}
    wrapped = np.arctan2(xyz@v, xyz@u)
    size = len(volts)
    phases = [np.unwrap(wrapped[:size]), np.unwrap(wrapped[size:])]
    phases[1] += 2*np.pi*round((phases[0][0]-phases[1][0])/(2*np.pi))
    origin = phases[0][0]
    sign = 1 if phases[0][-1] >= origin else -1
    phases = [(p-origin)*sign for p in phases]
    if any(np.max(np.abs(np.diff(p))) >= np.pi/2 for p in phases):
        return {'status': 'UNAVAILABLE', 'reason': 'Phase increments >=90 deg: resolve ambiguity with a finer sweep.'}
    spans = [float(np.degrees(np.ptp(p))) for p in phases]
    if min(spans) < config.vpi_min_phase_span_deg:
        return {'status': 'UNAVAILABLE', 'reason': 'Insufficient observed phase span to support a circle-phase estimate.', 'phase_span_deg': spans}
    mask = volts <= config.vpi_fit_max_command_v+1e-12
    if mask.sum() < 4:
        return {'status': 'UNAVAILABLE', 'reason': 'Fewer than four points in fit interval.'}
    fits = []
    for phase, span in zip(phases, spans):
        slope, intercept = np.polyfit(volts[mask], phase[mask], 1)
        rms = float(np.degrees(np.sqrt(np.mean((phase[mask]-(slope*volts[mask]+intercept))**2))))
        valid_fit = bool(slope > 0 and rms <= config.vpi_max_linear_rms_deg
                         and np.degrees(np.ptp(phase[mask])) >= config.vpi_min_phase_span_deg/2)
        # Empirical crossing is explicitly interpolated, separately from a linear-branch extrapolation.
        reached = bool(np.max(phase-phase[0]) >= np.pi)
        crossing = None
        if reached:
            for i in range(1, size):
                if phase[i-1]-phase[0] < np.pi <= phase[i]-phase[0]:
                    crossing = float(volts[i-1]+(np.pi-(phase[i-1]-phase[0]))*(volts[i]-volts[i-1])/(phase[i]-phase[i-1]))
                    break
        fits.append(dict(vpi_linear_branch_command_v=float(np.pi/slope) if valid_fit else None,
                         slope_rad_per_v=float(slope), intercept_rad=float(intercept), fit_rms_deg=rms,
                         fit_supported=valid_fit, pi_reached=reached, pi_crossing_command_v=crossing,
                         observed_span_deg=span))
    difference = phases[1]-phases[0]
    slope = np.mean([fit['slope_rad_per_v'] for fit in fits])
    supported = all(fit['fit_supported'] for fit in fits)
    return dict(status='MODEL_SUPPORTED' if supported else 'LINEAR_FIT_UNSUPPORTED',
                axis_sign_arbitrary=axis.tolist(), plane_rms=plane_rms, radius=radius,
                phase_deg=[np.degrees(p).tolist() for p in phases], fits=fits,
                difference_deg=np.degrees(difference).tolist(),
                max_direction_difference_deg=float(np.degrees(np.abs(difference).max())),
                rms_direction_difference_deg=float(np.degrees(np.sqrt(np.mean(difference**2)))),
                equivalent_command_difference_v=float(np.abs(difference).max()/slope) if supported else None,
                fit_range_command_v=[float(volts[mask].min()), float(volts[mask].max())],
                return_separation_deg=float(np.degrees(np.arctan2(np.linalg.norm(np.cross(forward[0],backward[0])), forward[0]@backward[0]))))


def acquisition_errors(directory, rows):
    """Check retained acquisition evidence, without interpreting physical timing."""
    errors = []
    try:
        metadata = read_json(directory/'acquisition.json')
        if not isinstance(metadata, dict) or metadata.get('schema_version') != 1 or metadata.get('experiment') != 'eom_vpi_hysteresis':
            errors.append('Missing/unsupported Vpi acquisition metadata.')
    except (OSError, ValueError):
        errors.append('Missing or unreadable acquisition.json.')
    try:
        with (directory/'events.jsonl').open() as stream:
            events = [json.loads(line) for line in stream]
        if not events or any(not isinstance(event, dict) or 'kind' not in event for event in events):
            raise ValueError('Invalid event records')
        if events[-1]['kind'] != 'acquisition_completed':
            errors.append('Retained event log does not establish acquisition completion.')
        samples = [event for event in events if event['kind'] == 'pax_sample']
        if len(samples) != len(rows):
            errors.append('PAX event count does not match retained CSV rows.')
        else:
            for event, row in zip(samples, rows):
                reading = event.get('interface_reading', {})
                if (event.get('synthetic') is not False
                        or not isinstance(event.get('raw_record'), dict) or not event['raw_record']
                        or any(float(row[key]) != float(reading.get(key, np.nan))
                               for key in ('s1', 's2', 's3'))
                        or float(row['pax_timestamp']) != float(reading.get('timestamp', np.nan))
                        or float(row['elapsed_s']) != float(event.get('received_s', np.nan))):
                    errors.append('PAX events lack raw evidence or disagree with CSV directions/timestamps.')
                    break
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        errors.append('Missing, malformed or inconsistent events.jsonl.')
    for field in ('elapsed_s', 'pax_timestamp'):
        try:
            values = np.array([float(row[field]) for row in rows])
            valid = len(values) >= 2 and np.isfinite(values).all() and (np.diff(values) > 0).all()
            if field == 'pax_timestamp':
                valid = valid and (values > 0).all()
            if not valid:
                errors.append(f'{field} must contain finite, strictly advancing records.')
        except (ValueError, TypeError, KeyError):
            errors.append(f'Missing or invalid {field} records.')
    return errors


def analyze(directory):
    recipe = load_recipe(directory/'recipe.json')
    config = recipe.acquisition_config()
    run = read_json(directory/'run.json')
    path = directory/'data.csv'
    if path.exists():
        with path.open(newline='') as stream:
            rows = list(csv.DictReader(stream))
    else:
        rows = []
    result = dict(schema_version=1, evaluator='eom_vpi_hysteresis', evaluator_version='2',
                  source_sha256=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                  mode=run['mode'], cycles=[],
                  method='Per-point arithmetic means of all configured fresh PAX directions; common SVD plane per cycle; atan2 phase unwrapped per branch; linear fit only within configured interval.',
                  limitations='Phase is model-inferred, not measured retardance. No DoP filtering; low DoP increases direction uncertainty. Point means are explicit; source data unchanged. Forward/reverse differences include hysteresis, time drift and settling. No delivered-voltage or temperature measurement. Linear-branch Vpi is extrapolated when pi is not observed. Multiple phase turns per command step cannot be excluded by unwrapping; use sufficiently fine steps.')
    dop = np.array([float(row['dop']) for row in rows])
    finite = dop[np.isfinite(dop)]
    result['dop_range'] = [float(finite.min()),float(finite.max())] if finite.size else None
    result['dop_warning_count'] = int(np.sum(~np.isfinite(dop) | (dop < config.vpi_warn_below_dop)))
    result['dop_above_one_count'] = int(np.sum(dop > 1))
    root = Path(__file__).resolve().parents[1]
    result['evaluator_sources'] = {name:hashlib.sha256((root/name).read_bytes()).hexdigest()
        for name in ('analysis/vpi.py','analysis/baseline.py','quality_vpi.py','reports/vpi.py')}
    result['undriven_baseline'] = summarize(rows)
    if run['mode'] != 'hardware' or any(row['synthetic'].lower() == 'true' for row in rows):
        result.update(status='SYNTHETIC_ONLY', reason='Mock records establish software behavior, not optical Vpi.')
    else:
        result['acquisition_errors'] = acquisition_errors(directory, rows)
        if run['status'] != 'COMPLETED' or run.get('cleanup_status') != 'COMPLETED':
            result.update(status='INCOMPLETE', reason='Acquisition or cleanup incomplete; no calibrated result.')
        elif result['acquisition_errors']:
            result.update(status='INVALID_ACQUISITION', reason='; '.join(result['acquisition_errors']))
    if result.get('status') is None:
        for cycle in range(config.repeats):
            branches = []
            voltages = None
            for direction in ('forward', 'backward'):
                selected = [row for row in rows if int(row['cycle']) == cycle and row['direction'] == direction]
                groups = {}
                for row in selected:
                    groups.setdefault(int(row['point']), []).append(row)
                if len(groups) != config.points or any(len(group) != config.pax_samples_per_point for group in groups.values()):
                    branches = []; break
                points = sorted(groups.values(), key=lambda group: float(group[0]['command_v']))
                volts = np.array([float(group[0]['command_v']) for group in points])
                expected = np.linspace(0,config.stop_command_v,config.points)
                if not np.allclose(volts, expected, rtol=0, atol=1e-12):
                    branches = []; break
                voltages = volts
                branches.append(np.array([np.mean([[float(row[k]) for k in ('s1','s2','s3')] for row in group],axis=0) for group in points]))
            entry = estimate(voltages, *branches, config) if len(branches) == 2 else dict(status='UNAVAILABLE',reason='Incomplete sweep grid/sample counts.')
            if len(branches)==2 and all(np.isfinite(branch).all() for branch in branches):
                first, second = branches
                entry['direction_poincare_difference_deg'] = np.degrees(np.arctan2(np.linalg.norm(np.cross(first,second),axis=1),np.sum(first*second,axis=1))).tolist()
                entry['max_direction_poincare_difference_deg'] = max(entry['direction_poincare_difference_deg'])
                entry['zero_return_poincare_difference_deg'] = entry['direction_poincare_difference_deg'][0]
                baseline = result['undriven_baseline']
                if baseline['status']=='AVAILABLE' and np.linalg.norm(baseline['mean'])>1e-8:
                    mean = np.array(baseline['mean'])
                    entry['excursion_from_undriven_deg'] = [np.degrees(np.arctan2(np.linalg.norm(np.cross(branch,mean),axis=1),branch@mean)).tolist() for branch in branches]
                    entry['max_excursion_from_undriven_deg'] = max(max(branch) for branch in entry['excursion_from_undriven_deg'])
            entry['cycle'] = cycle
            if voltages is not None:
                entry['command_v'] = voltages.tolist()
            result['cycles'].append(entry)
        result['status'] = 'ANALYZED'
    write_json(directory/'analysis.json', result)
    return result
