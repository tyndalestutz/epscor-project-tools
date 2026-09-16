"""Raw acquisition sessions with no guessed measurement values."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


from ..configuration import ConfigurationError, read_json
from .plan import make_plan
from .reduction import REQUIRED, validate_reading


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def initialize(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ConfigurationError(f'Choose a new or empty campaign directory: {path}')
    path.mkdir(parents=True, exist_ok=True)
    campaign = {'schema_version': 1}
    def section(name, value):
        campaign[name] = value
    plan = make_plan()
    section('plan', plan)
    section('session', dict(
        schema_version=1, operator='', bench_id='', wavelength_nm=None,
        instrument_calibrations={}, confirmed_planes=False, basis_verified=False,
        normalization_plane='P0_before_PBS', notes='', created_utc=datetime.now(timezone.utc).isoformat(),
    ))
    choices = {}
    templates = {}
    for step in plan['steps']:
        if step['kind'] == 'phase':
            choices[step['id']] = dict(value=None, bounds=[-3.141592653589793, 3.141592653589793],
                                      status='nuisance', reason='Unmeasured: dedicated reference or explicit conditional scenario required')
        template = dict(values={key: None for key in sorted(REQUIRED[step['kind']])},
                        uncertainties={key: None for key in sorted(REQUIRED[step['kind']])},
                        power_unit='uW', detectors={}, utc='', notes='')
        templates.setdefault(step['kind'], template)
    section('choices', choices)
    section('reading_templates', templates)
    section('measurements', {step['id']: [] for step in plan['steps']})
    # Detector assumptions never pass for measured spatial characterization.
    modes = [[0, 0], [0, 1], [1, 0], [1, 1]]
    section('detectors', dict(
        schema_version=1, status='assumed', sources=[], notes='Coherent identical-mode baseline only. Replace with independently characterized overlap/projection before claiming a spatial explanation.',
        ports={port: dict(kind='integrating', mode_keys=modes, gram=[[1]*4 for _ in range(4)]) for port in ('E', 'F')},
    ))
    write_json(path / 'campaign.json', campaign)
    import csv
    import numpy as np
    with (path / 'validation_schedule.csv').open('w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['sample_id', 'kind', 'phi1_rad', 'phi2_rad', 'phi1_command_v', 'phi2_command_v', 'blocks'])
        for axis in ('phi1', 'phi2'):
            for direction in ('forward', 'reverse'):
                phases = np.linspace(0, 2*np.pi, 41)
                if direction == 'reverse':
                    phases = phases[::-1]
                for i, value in enumerate(phases):
                    first, second = (value, np.pi/2) if axis == 'phi1' else (0, value)
                    writer.writerow([f'{axis}_{direction}_{i:03d}', axis+'_scan', first, second, '', '', 'none'])
        for route in ('AC', 'AD', 'BC', 'BD'):
            writer.writerow(['isolated_'+route, 'isolated_'+route, 0, 0, '', '', 'only_'+route])
    (path / 'README.md').write_text(
        '# Independent measurement campaign\n\n'
        'Edit **campaign.json**: all metadata, measurement plan, phase choices, detector settings and readings are together.\n\n'
        '1. Fill `session` instrument metadata and confirm planes/basis.\n'
        '2. Follow `plan.steps`. Copy the matching entry from `reading_templates` into '
        '`measurements.STEP_ID` for each repeat and fill its values, uncertainties, UTC, detectors and notes. '
        'Templates are shared by measurement kind; each step has a list of readings.\n'
        '3. Run `acquire.py status CAMPAIGN`, then `build` or `freeze`. No per-reading import is necessary.\n'
        '4. Fill `choices` and `detectors` with independently supported values or explicitly labeled assumptions.\n'
        '5. Fill `validation_schedule.csv` commands from independent phase calibration; freeze and predict before collecting validation data.\n\n'
        'The JSON is a working document. A freeze preserves its exact bytes and measurement hashes. '
        'Keep original instrument exports separately. No command connects to hardware.\n')


def read_section(path: Path, name: str):
    """Read the compact campaign; retain support for existing split campaigns."""
    if (path / 'campaign.json').exists():
        return read_json(path / 'campaign.json')[name]
    return read_json(path / (name + '.json'))


def write_section(path: Path, name: str, value) -> None:
    campaign = read_json(path / 'campaign.json')
    campaign[name] = value
    write_json(path / 'campaign.json', campaign)


def reading_id(reading: dict) -> str:
    """Stable content ID, unaffected by indentation or ordering JSON keys."""
    return hashlib.sha256(json.dumps(reading, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_timestamp(reading: dict) -> None:
    try:
        stamp = datetime.fromisoformat(reading['utc'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('timezone missing')
    except (ValueError, TypeError, AttributeError) as exc:
        raise ConfigurationError('utc must be an ISO timestamp with a timezone') from exc


def records(path: Path) -> dict[str, list[dict]]:
    if (path / 'campaign.json').exists():
        campaign = read_json(path / 'campaign.json')
        steps = {step['id']: step for step in campaign['plan']['steps']}
        groups, seen = {}, set()
        for name, readings in campaign['measurements'].items():
            if name not in steps or not isinstance(readings, list):
                raise ConfigurationError('Measurements must map planned step IDs to lists of readings')
            for reading in readings:
                validate_reading(reading, steps[name]['kind'])
                validate_timestamp(reading)
                identifier = reading_id(reading)
                if identifier in seen:
                    raise ConfigurationError('Duplicate reading: acquire an independent repeat with its own timestamp')
                seen.add(identifier)
                groups.setdefault(name, []).append(dict(id=identifier, reading=reading))
        return groups
    groups = {}
    for receipt_path in sorted((path / 'records').glob('*.json')):
        receipt = read_json(receipt_path)
        raw_path = path / receipt['raw_file']
        if hashlib.sha256(raw_path.read_bytes()).hexdigest() != receipt['sha256']:
            raise ConfigurationError(f'Raw record changed after registration: {raw_path}')
        reading = read_json(raw_path)
        groups.setdefault(receipt['step_id'], []).append(dict(id=receipt['id'], reading=reading))
    return groups


def register(path: Path, step_id: str, source: Path) -> str:
    plan = read_section(path, 'plan')
    step = next((step for step in plan['steps'] if step['id'] == step_id), None)
    if step is None or step['role'] != 'characterization':
        raise ConfigurationError('Record must name a planned independent characterization step')
    if not (path / 'campaign.json').exists():
        raise ConfigurationError('Compact this legacy campaign before adding readings')
    reading = read_json(source)
    validate_reading(reading, step['kind'])
    validate_timestamp(reading)
    identifier = reading_id(reading)
    if any(entry['id'] == identifier for entries in records(path).values() for entry in entries):
        raise ConfigurationError('This reading is already present; acquire another repeat')
    measurements = read_section(path, 'measurements')
    measurements.setdefault(step_id, []).append(reading)
    write_section(path, 'measurements', measurements)
    return identifier


def compact(path: Path) -> None:
    """Consolidate existing campaign files without dropping measurements."""
    if (path / 'campaign.json').exists():
        raise ConfigurationError('Campaign is already compact')
    grouped = records(path)
    campaign = dict(schema_version=1)
    for name in ('session', 'plan', 'choices', 'detectors'):
        campaign[name] = read_section(path, name)
    campaign['reading_templates'] = {}
    for step in campaign['plan']['steps']:
        template = path/'reading_templates'/(step['id']+'.json')
        if template.exists():
            campaign['reading_templates'].setdefault(step['kind'], read_json(template))
    campaign['measurements'] = {step['id']: [entry['reading'] for entry in grouped.get(step['id'], [])]
                                for step in campaign['plan']['steps']}
    # Preserve legacy IDs referenced by provenance outside this working document.
    campaign['legacy_record_ids'] = {entry['id']: reading_id(entry['reading'])
                                    for entries in grouped.values() for entry in entries}
    campaign['detectors']['sources'] = [campaign['legacy_record_ids'].get(source, source)
                                       for source in campaign['detectors'].get('sources', [])]
    write_json(path/'campaign.json', campaign)
    (path/'README.md').write_text(
        '# Independent measurement campaign\n\n'
        'Edit **campaign.json** for session metadata, plan, phase choices, detector settings and all readings.\n\n'
        'Copy a shared `reading_templates` entry into the appropriate `measurements.STEP_ID` list for each repeat. '
        'Fill values, uncertainties, UTC, detector IDs and notes. No separate reading files or import step are required.\n\n'
        'Use `acquire.py status`, then `build` or `freeze`. Complete actuator commands in `validation_schedule.csv` '
        'from independent phase calibration before bench scans. Freeze and predict before validation acquisition.\n\n'
        'See [the acquisition protocol](../../../../python/field_propogation/docs/measurement_acquisition.md).\n')
    # Keep original evidence and unknown files; only remove redundant blank templates/configuration.
    for name in ('session', 'plan', 'choices', 'detectors'):
        (path/(name+'.json')).unlink()
    for step in campaign['plan']['steps']:
        template = path/'reading_templates'/(step['id']+'.json')
        if template.exists(): template.unlink()
    directory = path/'reading_templates'
    if directory.exists() and not any(directory.iterdir()): directory.rmdir()
    directory = path/'records'
    if directory.exists() and not any(directory.iterdir()): directory.rmdir()


def readiness(path: Path) -> dict:
    plan = read_section(path, 'plan')
    grouped = records(path)
    missing = []
    for step in plan['steps']:
        count = len(grouped.get(step['id'], []))
        if step['required'] and count < plan['quality']['min_repeats']:
            missing.append(dict(step=step['id'], repeats=count, needed=plan['quality']['min_repeats']))
    context = read_section(path, 'session')
    incomplete = [key for key in ('operator', 'bench_id', 'wavelength_nm', 'instrument_calibrations', 'confirmed_planes', 'basis_verified') if not context.get(key)]
    if type(context.get('wavelength_nm')) not in (int, float) or not 0 < context['wavelength_nm'] < float('inf'):
        incomplete.append('positive finite wavelength_nm')
    if context.get('confirmed_planes') is not True or context.get('basis_verified') is not True:
        incomplete.append('explicit boolean plane/basis confirmation')
    if context.get('normalization_plane') != 'P0_before_PBS':
        incomplete.append('normalization_plane must be P0_before_PBS')
    calibrations = context.get('instrument_calibrations', {})
    if not isinstance(calibrations, dict) or any(not value for value in calibrations.values()):
        incomplete.append('instrument calibration mapping')
        calibrations = {}
    for entries in grouped.values():
        for entry in entries:
            instruments = entry['reading']['detectors']
            if not isinstance(instruments, dict) or any(identifier not in calibrations for identifier in instruments.values()):
                incomplete.append('calibration references for record ' + entry['id'])
    choices = read_section(path, 'choices')
    unresolved = [name for name, choice in choices.items()
                  if len(grouped.get(name, [])) < plan['quality']['min_repeats'] and choice['value'] is None]
    return dict(required_measurements_missing=missing, session_metadata_missing=incomplete,
                unresolved_phases=unresolved, complete=not (missing or incomplete or unresolved),
                optional_measurements=[s['id'] for s in plan['steps'] if not s['required'] and s['id'] not in grouped])
