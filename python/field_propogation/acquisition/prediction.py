"""Freeze independent data, predict first, then compare untouched held-out data."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np

from ..configuration import ConfigurationError, load_configuration, read_json
from ..detection import detected_outputs
from ..propagation import compile_network
from ..provenance import interpretation
from .build import save_build
from .session import write_json, read_section, records


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_bundle(bundle: Path) -> dict:
    manifest = read_json(bundle/'manifest.json')
    actual = {str(p.relative_to(bundle)): digest(p) for p in bundle.rglob('*')
              if p.is_file() and p.name != 'manifest.json'}
    if actual != manifest['files']:
        raise ConfigurationError('Frozen bundle files differ from their manifest; make a new freeze, never silently replace inputs')
    return manifest


def validate_detectors(detectors: dict) -> None:
    if detectors.get('status') not in {'measured','derived','assumed','nuisance'} or not detectors.get('notes'):
        raise ConfigurationError('Detector model requires measured/derived/assumed/nuisance status and notes')
    if detectors['status'] in {'measured','derived'} and not detectors.get('sources'):
        raise ConfigurationError('Measured detector model needs independent source record IDs')


def freeze(session: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        raise ConfigurationError('Freeze destination must be new or empty')
    detectors = read_section(session, 'detectors')
    validate_detectors(detectors)
    # Build before publishing a bundle; failed measurement reductions leave no claim of readiness.
    import tempfile
    with tempfile.TemporaryDirectory(prefix='jones-freeze-') as folder:
        temporary = Path(folder)/'bundle'
        save_build(session, temporary)
        if (session/'campaign.json').exists():
            shutil.copy2(session/'campaign.json', temporary/'campaign.json')
            write_json(temporary/'detectors.json', detectors)
        else:
            for name in ('plan.json','session.json','choices.json','detectors.json'):
                shutil.copy2(session/name, temporary/name)
            shutil.copytree(session/'records', temporary/'records')
            shutil.copytree(session/'raw', temporary/'raw')
        shutil.copy2(session/'validation_schedule.csv', temporary/'validation_schedule.csv')
        known = {entry['id'] for entries in records(session).values() for entry in entries}
        params = read_json(temporary/'parameters.json')
        for name, metadata in params['provenance'].items():
            if set(metadata['sources']) - known:
                raise ConfigurationError(f'{name}: unknown independent measurement IDs')
        if set(detectors.get('sources', [])) - known:
            raise ConfigurationError('Detector provenance references unregistered measurements')
        network = compile_network(load_configuration(temporary/'flow.json', temporary/'parameters.json'))
        detected_outputs(network, {}, detectors)  # includes PSD/passivity checks
        # Preserve the implementation as well as its inputs for reproducibility.
        core = Path(__file__).resolve().parents[1]
        for source in core.glob('*.py'):
            target = temporary/'implementation'/source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        shutil.copytree(core/'acquisition', temporary/'implementation/acquisition',
                        ignore=shutil.ignore_patterns('__pycache__'))
        label = interpretation(params)
        if detectors['status'] in {'assumed','nuisance'}:
            label += '; detector/spatial prediction is conditional'
        write_json(temporary/'manifest.json', dict(
            schema_version=1, created_utc=datetime.now(timezone.utc).isoformat(), interpretation=label,
            files={str(p.relative_to(temporary)): digest(p) for p in temporary.rglob('*') if p.is_file()},
            holdout_policy='All parameters fixed before prediction; no final-power curve used in reduction',
        ))
        shutil.copytree(temporary, destination, dirs_exist_ok=True)


def predict(bundle: Path, destination: Path) -> None:
    manifest = verify_bundle(bundle)
    core = Path(__file__).resolve().parents[1]
    for source in (bundle/'implementation').rglob('*.py'):
        current = core/source.relative_to(bundle/'implementation')
        if not current.exists() or digest(current) != digest(source):
            raise ConfigurationError('Implementation changed since freeze; create a new frozen prediction with the current code')
    if destination.exists() and any(destination.iterdir()):
        raise ConfigurationError('Prediction destination must be new or empty')
    network = compile_network(load_configuration(bundle/'flow.json', bundle/'parameters.json'))
    detectors = read_json(bundle/'detectors.json')
    with (bundle/'validation_schedule.csv').open() as handle:
        schedule = list(csv.DictReader(handle))
    if not schedule or len({r['sample_id'] for r in schedule}) != len(schedule):
        raise ConfigurationError('Validation schedule must have unique sample IDs')
    result = []
    for row in schedule:
        phases = {p: float(row[p+'_rad']) for p in network.phases}
        local = network
        if row['kind'].startswith('isolated_'):
            route = row['kind'].split('_',1)[1]
            modes = {'AC':(1,1),'AD':(1,0),'BC':(0,1),'BD':(0,0)}
            if route not in modes or row['blocks'] != 'only_'+route:
                raise ConfigurationError('Isolated route must specify actual A/B and C/D blockers')
            # Modes in this generated flow are one-to-one with these four routes.
            # Preserve zero entries so the detector Gram/projection stays aligned.
            from dataclasses import replace
            local = replace(network, fields={name:{mode:(value if mode==modes[route] else np.zeros_like(value))
                            for mode,value in terms.items()} for name,terms in network.fields.items()})
        elif row['kind'] not in {'phi1_scan','phi2_scan'} or row['blocks'] != 'none':
            raise ConfigurationError('Unsupported held-out scan/blocking state')
        observations = detected_outputs(local, phases, detectors)
        for port, obs in observations.items():
            result.append(dict(sample_id=row['sample_id'], port=port, kind=row['kind'],
                               phi1_rad=phases['phi1'], phi2_rad=phases['phi2'], power_w=float(obs['intensity']),
                               s1=float(obs['stokes'][0]), s2=float(obs['stokes'][1]), s3=float(obs['stokes'][2]), dop=float(obs['dop'])))
    destination.mkdir(parents=True, exist_ok=True)
    with (destination/'predictions.csv').open('w',newline='') as handle:
        writer = csv.DictWriter(handle,fieldnames=list(result[0]))
        writer.writeheader(); writer.writerows(result)
    write_json(destination/'prediction_manifest.json',dict(
        created_utc=datetime.now(timezone.utc).isoformat(), bundle_manifest_sha256=digest(bundle/'manifest.json'),
        predictions_sha256=digest(destination/'predictions.csv'), interpretation=manifest['interpretation'],
        uncertainty='Parameter uncertainties are retained but not propagated to detector observables. No uncertainty-based physical-validation claim is made.',
    ))
    with (destination/'validation_readings_template.csv').open('w',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['sample_id','port','utc','power_w','power_sigma_w','s1','s2','s3','stokes_sigma','dop','detector_id','notes'])
        for row in result:
            writer.writerow([row['sample_id'],row['port'],'','','','','','','','','',''])
    (destination/'README.md').write_text(
        '# Preregistered forward predictions\n\n'+manifest['interpretation']+'\n\n'
        'Acquire the frozen schedule only after this prediction. Fill a COPY of '
        '`validation_readings_template.csv`, preserving sample IDs and timestamps. '
        'Convert detector units with an independent detector calibration. No gain, '
        'offset or phase is adjusted by the comparison tool.\n\n'
        'Parameter covariance/model uncertainty has not been propagated. Comparison '
        'reports residuals and measurement-only standardized errors, not a physical-validation verdict.\n')


def compare(bundle: Path, prediction: Path, readings: Path, destination: Path) -> dict:
    verify_bundle(bundle)
    manifest=read_json(prediction/'prediction_manifest.json')
    if manifest['bundle_manifest_sha256']!=digest(bundle/'manifest.json') or manifest['predictions_sha256']!=digest(prediction/'predictions.csv'):
        raise ConfigurationError('Prediction or frozen parameter set changed after preregistration')
    if destination.exists():
        raise ConfigurationError('Comparison destination must be new')
    with (prediction/'predictions.csv').open() as handle:
        predicted={(r['sample_id'],r['port']):r for r in csv.DictReader(handle)}
    with readings.open() as handle:
        measured=list(csv.DictReader(handle))
    if len({(r['sample_id'],r['port']) for r in measured})!=len(measured):
        raise ConfigurationError('Duplicate validation sample/port; aggregate repeated measurements with their uncertainty first')
    cutoff=datetime.fromisoformat(manifest['created_utc'])
    residuals=[]
    for row in measured:
        key=(row['sample_id'],row['port'])
        if key not in predicted:
            raise ConfigurationError(f'Unplanned validation measurement {key}')
        try:
            stamp=datetime.fromisoformat(row['utc'].replace('Z','+00:00'))
            if stamp.tzinfo is None or stamp < cutoff:
                raise ValueError('measurement predates prediction')
            power=float(row['power_w']); sigma=float(row['power_sigma_w'])
            available = any(row[k].strip() for k in ('s1','s2','s3','stokes_sigma','dop'))
            stokes = np.full(3, np.nan)
            if available:
                stokes=np.array([float(row[k]) for k in ('s1','s2','s3')])
                stokes_sigma=float(row['stokes_sigma']); dop=float(row['dop'])
                if not np.isfinite([*stokes,stokes_sigma,dop]).all() or stokes_sigma<=0 or not 0<=dop<=1.02:
                    raise ValueError('invalid polarization data')
            elif not row.get('notes', '').strip():
                raise ValueError('missing reason for unavailable polarization')
            if not np.isfinite([power,sigma]).all() or sigma<=0 or not row['detector_id']:
                raise ValueError('invalid units/uncertainty/DOP/detector')
        except (ValueError,KeyError) as exc:
            raise ConfigurationError(f'{key}: complete timestamp, calibrated readings, positive standard uncertainties and detector identity required') from exc
        ref=predicted[key]
        error=power-float(ref['power_w'])
        predicted_stokes=np.array([float(ref[k]) for k in ('s1','s2','s3')])
        s_error=stokes-predicted_stokes
        residuals.append(dict(sample_id=key[0],port=key[1],power_error_w=error,
                              measurement_only_power_z=error/sigma,
                              stokes_error_norm=float(np.linalg.norm(s_error)) if np.isfinite(s_error).all() else None,
                              polarization_note=('defined' if np.isfinite(s_error).all() else
                                                 'measured polarization unavailable: '+row.get('notes','') if not available else
                                                 'predicted dark port: polarization undefined')))
    destination.mkdir(parents=True)
    paired=[]
    measured_by_key={(r['sample_id'],r['port']):r for r in measured}
    for sample in sorted({key[0] for key in predicted}):
        if all((sample,port) in measured_by_key for port in ('E','F')):
            measured_sum=sum(float(measured_by_key[sample,port]['power_w']) for port in ('E','F'))
            predicted_sum=sum(float(predicted[sample,port]['power_w']) for port in ('E','F'))
            paired.append(dict(sample_id=sample, measured_sum_w=measured_sum,
                               predicted_sum_w=predicted_sum, error_w=measured_sum-predicted_sum))
    summary=dict(samples=len(residuals),scheduled_samples=len(predicted),complete=len(residuals)==len(predicted),
                 polarization_comparisons=sum(r['stokes_error_norm'] is not None for r in residuals),
                 complementary_output_sums=paired,
                 measured_sha256=digest(readings),prediction_manifest_sha256=digest(prediction/'prediction_manifest.json'),
                 interpretation=manifest['interpretation'],
                 limitation='No physical validation pass/fail: model uncertainty/covariance not propagated; all comparisons use fixed parameters.',
                 residuals=residuals)
    write_json(destination/'comparison.json',summary)
    return summary
